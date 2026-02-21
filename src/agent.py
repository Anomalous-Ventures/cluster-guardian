"""
Cluster Guardian LangGraph Agent.

Uses LangGraph to orchestrate cluster monitoring and remediation.
The agent can:
- Analyze cluster issues via K8sGPT
- Check deep health of services
- Execute remediation actions with safety controls
- Learn from past issues and solutions
"""

from typing import Dict, List, Any, Optional, TypedDict, Literal
from datetime import datetime, timezone
import json
import structlog
import zoneinfo

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

try:
    from langfuse.callback import CallbackHandler as LangfuseCallbackHandler
except ImportError:
    LangfuseCallbackHandler = None

from .config import settings
from .k8s_client import get_k8s_client, K8sClient
from .k8sgpt_client import get_k8sgpt_client, K8sGPTClient
from .health_checks import get_health_checker, DeepHealthChecker
from .memory import get_memory
from .metrics import guardian_agent_iterations_total, guardian_rate_limit_remaining
from . import notifier
from . import github_client
from .prometheus_client import get_prometheus_client
from .loki_client import get_loki_client
from .cert_monitor import get_cert_monitor
from .storage_monitor import get_storage_monitor
from .security_client import get_crowdsec_client
from .gatus_client import get_gatus_client
from .config_store import get_config_store

logger = structlog.get_logger(__name__)


async def get_effective_setting(key: str) -> Any:
    """Check Redis overrides first, fall back to static config.

    Gracefully returns the static setting if Redis is unavailable.
    """
    try:
        store = get_config_store()
        return await store.get(key)
    except Exception:
        return getattr(settings, key)


def _is_quiet_hours() -> bool:
    """Return True if the current time falls within the configured quiet hours window.

    Quiet hours are defined by ``settings.quiet_hours_start`` and
    ``settings.quiet_hours_end`` (HH:MM format) in the timezone given by
    ``settings.quiet_hours_tz``.  If either start or end is not configured,
    quiet hours are disabled and this returns False.
    """
    if not settings.quiet_hours_start or not settings.quiet_hours_end:
        return False

    try:
        tz = zoneinfo.ZoneInfo(settings.quiet_hours_tz)
    except Exception:
        logger.warning(
            "Invalid quiet_hours_tz, defaulting to UTC", tz=settings.quiet_hours_tz
        )
        tz = timezone.utc

    now = datetime.now(tz)
    current_time = now.hour * 60 + now.minute

    start_parts = settings.quiet_hours_start.split(":")
    end_parts = settings.quiet_hours_end.split(":")
    start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
    end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

    if start_minutes <= end_minutes:
        # Same-day window, e.g. 22:00-06:00 does NOT apply here
        return start_minutes <= current_time < end_minutes
    else:
        # Overnight window, e.g. 22:00-06:00
        return current_time >= start_minutes or current_time < end_minutes


# =============================================================================
# STATE DEFINITION
# =============================================================================


class GuardianState(TypedDict):
    """State for the Cluster Guardian agent."""

    messages: List[Any]
    issues: List[Dict[str, Any]]
    health_results: List[Dict[str, Any]]
    actions_taken: List[Dict[str, Any]]
    pending_approvals: List[Dict[str, Any]]
    scan_timestamp: str
    iteration: int


# =============================================================================
# TOOLS FOR THE AGENT
# =============================================================================


def create_tools(
    k8s: K8sClient,
    k8sgpt: K8sGPTClient,
    health_checker: DeepHealthChecker,
    prometheus=None,
    loki=None,
    cert_monitor=None,
    storage_monitor=None,
    crowdsec=None,
    gatus=None,
):
    """Create tools for the Guardian agent."""

    @tool
    async def analyze_cluster() -> str:
        """
        Run K8sGPT analysis to find cluster issues.
        Returns a summary of detected issues.
        """
        issues = await k8sgpt.get_issues()
        if not issues:
            return "No issues detected by K8sGPT."

        lines = ["K8sGPT detected the following issues:"]
        for issue in issues:
            errors_str = (
                ", ".join(issue["errors"][:3]) if issue["errors"] else "Unknown error"
            )
            lines.append(
                f"- {issue['kind']}/{issue['name']} in {issue['namespace']}: {errors_str}"
            )

        return "\n".join(lines)

    @tool
    async def check_all_services() -> str:
        """
        Run deep health checks on all services.
        Returns health status including SSL, authentication, and backend connectivity.
        """
        results = await health_checker.check_all()
        healthy = [r for r in results if r.healthy]
        unhealthy = [r for r in results if not r.healthy]

        lines = [f"Health check completed at {datetime.now(timezone.utc).isoformat()}"]
        lines.append(f"Healthy: {len(healthy)}/{len(results)}")

        if unhealthy:
            lines.append("\nUnhealthy services:")
            for r in unhealthy:
                lines.append(f"- {r.service}: {', '.join(r.errors[:2])}")

        if healthy:
            lines.append(f"\nHealthy services: {', '.join(r.service for r in healthy)}")

        return "\n".join(lines)

    @tool
    async def check_service(service_name: str) -> str:
        """
        Run deep health check on a specific service.

        Args:
            service_name: Name of the service (e.g., 'grafana', 'authentik', 'plex')
        """
        result = await health_checker.check_service(service_name)
        return json.dumps(result.to_dict(), indent=2)

    @tool
    async def get_crashloopbackoff_pods() -> str:
        """
        Get all pods currently in CrashLoopBackOff state.
        Returns list of crashing pods with their namespaces and restart counts.
        """
        pods = await k8s.get_crashloopbackoff_pods()
        if not pods:
            return "No pods in CrashLoopBackOff state."

        lines = ["Pods in CrashLoopBackOff:"]
        for pod in pods:
            lines.append(
                f"- {pod['namespace']}/{pod['name']} ({pod['container']}): "
                f"{pod['restart_count']} restarts"
            )
        return "\n".join(lines)

    @tool
    async def get_pod_details(namespace: str, pod_name: str) -> str:
        """
        Get detailed status of a specific pod.

        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
        """
        status = await k8s.get_pod_status(namespace, pod_name)
        return json.dumps(status, indent=2)

    @tool
    async def get_recent_events(
        namespace: str, object_name: Optional[str] = None
    ) -> str:
        """
        Get recent Kubernetes events for a namespace or specific object.

        Args:
            namespace: Kubernetes namespace
            object_name: Optional name of specific object to filter events
        """
        events = await k8s.get_events(namespace, object_name)
        if not events:
            return f"No recent events in {namespace}"

        lines = [f"Recent events in {namespace}:"]
        for e in events[:10]:
            lines.append(f"- [{e['type']}] {e['reason']}: {e['message'][:100]}")
        return "\n".join(lines)

    @tool
    async def restart_pod(namespace: str, pod_name: str, reason: str) -> str:
        """
        Restart a pod by deleting it (assumes a controller will recreate it).

        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod to restart
            reason: Reason for restarting (for audit log)
        """
        result = await k8s.restart_pod(namespace, pod_name, reason)
        if result["success"]:
            return f"Successfully restarted pod {pod_name} in {namespace}"
        else:
            return f"Failed to restart pod: {result.get('error', 'Unknown error')}"

    @tool
    async def rollout_restart_deployment(
        namespace: str, deployment_name: str, reason: str
    ) -> str:
        """
        Trigger a rollout restart for a deployment (restarts all pods gracefully).

        Args:
            namespace: Kubernetes namespace
            deployment_name: Name of the deployment
            reason: Reason for restart (for audit log)
        """
        result = await k8s.rollout_restart(namespace, deployment_name, reason)
        if result["success"]:
            return f"Successfully triggered rollout restart for {deployment_name} in {namespace}"
        else:
            return f"Failed to rollout restart: {result.get('error', 'Unknown error')}"

    @tool
    async def scale_deployment(
        namespace: str, deployment_name: str, replicas: int, reason: str
    ) -> str:
        """
        Scale a deployment to specified number of replicas.

        Args:
            namespace: Kubernetes namespace
            deployment_name: Name of the deployment
            replicas: Target number of replicas
            reason: Reason for scaling (for audit log)
        """
        result = await k8s.scale_deployment(
            namespace, deployment_name, replicas, reason
        )
        if result["success"]:
            return f"Successfully scaled {deployment_name} to {replicas} replicas"
        elif result.get("requires_approval"):
            return f"Action requires human approval: {result.get('error')}"
        else:
            return f"Failed to scale: {result.get('error', 'Unknown error')}"

    @tool
    def get_rate_limit_status() -> str:
        """
        Get current rate limit status for remediation actions.
        Shows remaining actions allowed in the current hour.
        """
        status = k8s.get_rate_limit_status()
        return f"Rate limit: {status['remaining_actions']}/{status['max_actions_per_hour']} actions remaining this hour"

    @tool
    async def get_audit_log() -> str:
        """
        Get recent audit log entries showing all remediation actions taken.
        """
        entries = await k8s.get_audit_log()
        if not entries:
            return "No actions taken yet."

        lines = ["Recent remediation actions:"]
        for e in entries[-10:]:
            lines.append(
                f"- [{e['timestamp'][:19]}] {e['action']} on {e['namespace']}/{e['target']}: {e['result']}"
            )
        return "\n".join(lines)

    @tool
    async def create_remediation_pr(
        title: str,
        description: str,
        file_path: str,
        file_content: str,
        reason: str,
    ) -> str:
        """Create a GitHub PR with proposed infrastructure changes.
        Use for novel issues that need human review before applying.

        Args:
            title: PR title (e.g., "fix(media): increase sonarr memory limit")
            description: Detailed description of the issue and proposed fix
            file_path: Path in the repo to modify (e.g., pulumi/stacks/07-media/values.yaml)
            file_content: Proposed new content for the file
            reason: Why this change is needed
        """
        if not settings.github_token:
            return "GitHub token not configured. Cannot create PR."

        branch_name = (
            f"guardian/{title.replace(' ', '-').replace('/', '-')[:50].lower()}"
        )
        try:
            await github_client.create_branch(branch_name)
            await github_client.create_or_update_file(
                branch=branch_name,
                path=file_path,
                content=file_content,
                message=title,
            )
            pr_body = f"## Diagnosis\n{description}\n\n## Reason\n{reason}\n\n---\n*Proposed by Cluster Guardian (autonomy level: {settings.autonomy_level})*"
            pr_info = await github_client.create_pull_request(
                title=title,
                body=pr_body,
                branch=branch_name,
            )
            notifier.send_wazuh_syslog(
                "create_pr",
                "success",
                {"pr_number": pr_info["number"], "branch": branch_name},
            )
            return f"PR #{pr_info['number']} created: {pr_info['html_url']}"
        except Exception as exc:
            notifier.send_wazuh_syslog("create_pr", "failed", {"error": str(exc)})
            return f"Failed to create PR: {exc}"

    @tool
    async def notify_slack(message: str, severity: str = "info") -> str:
        """Send a notification to Slack about a finding or action.

        Args:
            message: Message to send
            severity: "info", "warning", "critical"
        """
        sent = await notifier.send_slack(message, severity)
        if sent:
            return "Slack notification sent."
        return "Slack notification skipped (not configured or failed)."

    @tool
    async def create_thehive_case(
        title: str,
        description: str,
        severity: str = "medium",
    ) -> str:
        """Create a TheHive case for incident tracking.

        Args:
            title: Case title
            description: Detailed description
            severity: "low", "medium", "high", "critical"
        """
        alert_id = await notifier.send_thehive_alert(
            title=title,
            description=description,
            severity=severity,
            tags=["cluster-guardian", "sre-agent"],
        )
        if alert_id:
            return f"TheHive alert created: {alert_id}"
        return "TheHive alert skipped (not configured or failed)."

    @tool
    async def get_node_status(node_name: str) -> str:
        """
        Get detailed status of a specific cluster node including conditions,
        allocatable resources, taints, and schedulability.

        Args:
            node_name: Name of the Kubernetes node
        """
        status = await k8s.get_node_status(node_name)
        return json.dumps(status, indent=2)

    @tool
    async def list_nodes() -> str:
        """
        List all cluster nodes with their status summary, roles, and taints.
        """
        nodes = await k8s.get_all_nodes()
        if not nodes:
            return "No nodes found in cluster."

        lines = [f"Cluster nodes ({len(nodes)} total):"]
        for node in nodes:
            roles_str = ", ".join(node["roles"]) if node["roles"] else "worker"
            ready = node["conditions"].get("Ready", "Unknown")
            sched = " (cordoned)" if node["unschedulable"] else ""
            lines.append(f"- {node['name']} [{roles_str}] Ready={ready}{sched}")
        return "\n".join(lines)

    @tool
    async def cordon_node(node_name: str, reason: str) -> str:
        """
        Cordon a node to prevent new pods from being scheduled on it.
        This requires human approval by default.

        Args:
            node_name: Name of the node to cordon
            reason: Reason for cordoning (for audit log)
        """
        result = await k8s.cordon_node(node_name, reason)
        if result["success"]:
            return f"Successfully cordoned node {node_name}"
        elif result.get("requires_approval"):
            return f"Action requires human approval: {result.get('error')}"
        else:
            return f"Failed to cordon node: {result.get('error', 'Unknown error')}"

    @tool
    async def drain_node(node_name: str, reason: str) -> str:
        """
        Drain a node by cordoning it and evicting all non-DaemonSet pods.
        Pods in protected namespaces are skipped. Requires human approval by default.

        Args:
            node_name: Name of the node to drain
            reason: Reason for draining (for audit log)
        """
        result = await k8s.drain_node(node_name, reason)
        if result["success"]:
            evicted = len(result.get("evicted", []))
            skipped = len(result.get("skipped", []))
            return f"Successfully drained node {node_name}: {evicted} pods evicted, {skipped} pods skipped"
        elif result.get("requires_approval"):
            return f"Action requires human approval: {result.get('error')}"
        else:
            return f"Failed to drain node: {result.get('error', 'Unknown error')}"

    @tool
    async def store_resolution(issue_summary: str, resolution: str) -> str:
        """
        Store an issue and its resolution for future reference.
        The agent should call this after successfully remediating an issue
        so it can learn from the experience.

        Args:
            issue_summary: Summary of the issue that was resolved
            resolution: Description of the resolution that was applied
        """
        memory = get_memory()
        if not memory.available:
            return "Memory store unavailable -- resolution not saved."
        await memory.store_issue(issue_summary, resolution)
        return f"Resolution stored for future reference: {issue_summary[:80]}"

    @tool
    async def recall_similar_issues(query: str) -> str:
        """
        Search for similar past issues and their resolutions.
        Use this to guide diagnosis and remediation based on past experience.

        Args:
            query: Description of the current issue to search for
        """
        memory = get_memory()
        if not memory.available:
            return "Memory store unavailable -- cannot recall past issues."
        results = await memory.recall_similar_issues(query)
        if not results:
            return "No similar past issues found."

        lines = ["Similar past issues:"]
        for r in results:
            lines.append(f"- [{r['score']:.2f}] {r['issue']}")
            lines.append(f"  Resolution: {r['resolution']}")
        return "\n".join(lines)

    # ----- Prometheus / Metrics Tools -----

    @tool
    async def query_prometheus(promql: str) -> str:
        """Execute a raw PromQL query against Prometheus.

        Args:
            promql: PromQL expression to evaluate
        """
        if not prometheus:
            return "Prometheus client not available."
        result = await prometheus.query(promql)
        if "error" in result:
            return f"Prometheus query error: {result['error']}"
        return json.dumps(result, indent=2, default=str)

    @tool
    async def get_pod_cpu_usage(namespace: str, pod_name: str) -> str:
        """Get CPU usage for a specific pod including percentage of request.

        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
        """
        if not prometheus:
            return "Prometheus client not available."
        result = await prometheus.get_pod_cpu_usage(namespace, pod_name)
        return json.dumps(result, indent=2, default=str)

    @tool
    async def get_pod_memory_usage(namespace: str, pod_name: str) -> str:
        """Get memory usage for a specific pod including percentage of limit.

        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
        """
        if not prometheus:
            return "Prometheus client not available."
        result = await prometheus.get_pod_memory_usage(namespace, pod_name)
        return json.dumps(result, indent=2, default=str)

    @tool
    async def get_service_error_rate(namespace: str, service: str) -> str:
        """Get HTTP 5xx error rate for a service from Traefik metrics.

        Args:
            namespace: Kubernetes namespace
            service: Service name
        """
        if not prometheus:
            return "Prometheus client not available."
        result = await prometheus.get_error_rate(namespace, service)
        return json.dumps(result, indent=2, default=str)

    @tool
    async def get_prometheus_alerts() -> str:
        """Get all currently firing Prometheus alerts."""
        if not prometheus:
            return "Prometheus client not available."
        alerts = await prometheus.get_alerts("firing")
        if not alerts:
            return "No firing Prometheus alerts."
        lines = [f"Firing alerts ({len(alerts)}):"]
        for a in alerts:
            lines.append(
                f"- [{a.get('severity', '?')}] {a['name']}: {a.get('summary', '')}"
            )
        return "\n".join(lines)

    # ----- Loki / Log Tools -----

    @tool
    async def get_pod_logs_from_loki(
        namespace: str, pod_name: str, since: str = "1h"
    ) -> str:
        """Get recent logs for a pod from Loki (centralized logging).

        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
            since: Time window, e.g. '1h', '30m', '5m'
        """
        if not loki:
            return "Loki client not available."
        return await loki.get_pod_logs(namespace, pod_name, since=since)

    @tool
    async def get_namespace_error_logs(namespace: str, since: str = "30m") -> str:
        """Get error/exception/fatal logs from a namespace via Loki.

        Args:
            namespace: Kubernetes namespace
            since: Time window, e.g. '30m', '1h'
        """
        if not loki:
            return "Loki client not available."
        return await loki.get_namespace_errors(namespace, since=since)

    @tool
    async def search_cluster_logs(
        query_text: str, namespace: Optional[str] = None, since: str = "1h"
    ) -> str:
        """Search logs across the cluster or a namespace for a text pattern.

        Args:
            query_text: Text pattern to search for in logs
            namespace: Optional namespace to scope the search
            since: Time window, e.g. '1h', '30m'
        """
        if not loki:
            return "Loki client not available."
        return await loki.search_logs(query_text, namespace=namespace, since=since)

    # ----- Kubernetes Extended Operations -----

    @tool
    async def get_pod_k8s_logs(
        namespace: str,
        pod_name: str,
        container: Optional[str] = None,
        previous: bool = False,
    ) -> str:
        """Get logs directly from a pod via the Kubernetes API.
        Use for pods not yet indexed in Loki, or to get previous container logs after a crash.

        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
            container: Optional container name (for multi-container pods)
            previous: If True, get logs from the previous (crashed) container
        """
        return await k8s.get_pod_logs(
            namespace, pod_name, container=container, previous=previous
        )

    @tool
    async def rollback_deployment(
        namespace: str, deployment_name: str, reason: str
    ) -> str:
        """Rollback a deployment to its previous revision.

        Args:
            namespace: Kubernetes namespace
            deployment_name: Name of the deployment
            reason: Reason for rollback (for audit log)
        """
        result = await k8s.rollback_deployment(namespace, deployment_name, reason)
        if result["success"]:
            return f"Successfully rolled back {deployment_name}: {result.get('message', '')}"
        return f"Rollback failed: {result.get('error', 'Unknown error')}"

    @tool
    async def rollout_restart_statefulset(
        namespace: str, statefulset_name: str, reason: str
    ) -> str:
        """Trigger a rollout restart for a StatefulSet.

        Args:
            namespace: Kubernetes namespace
            statefulset_name: Name of the StatefulSet
            reason: Reason for restart (for audit log)
        """
        result = await k8s.rollout_restart_statefulset(
            namespace, statefulset_name, reason
        )
        if result["success"]:
            return f"Successfully triggered rollout restart for StatefulSet {statefulset_name}"
        return f"Failed to restart StatefulSet: {result.get('error', 'Unknown error')}"

    @tool
    async def get_statefulset_status(namespace: str, statefulset_name: str) -> str:
        """Get status of a StatefulSet including replica counts.

        Args:
            namespace: Kubernetes namespace
            statefulset_name: Name of the StatefulSet
        """
        result = await k8s.get_statefulset_status(namespace, statefulset_name)
        return json.dumps(result, indent=2, default=str)

    @tool
    async def get_failed_jobs(namespace: Optional[str] = None) -> str:
        """Get all failed Kubernetes jobs, optionally scoped to a namespace.

        Args:
            namespace: Optional namespace filter
        """
        jobs = await k8s.get_failed_jobs(namespace)
        if not jobs:
            scope = f"namespace {namespace}" if namespace else "cluster"
            return f"No failed jobs in {scope}."
        lines = [f"Failed jobs ({len(jobs)}):"]
        for j in jobs:
            lines.append(
                f"- {j['namespace']}/{j['name']}: {j['failed']} failures (started {j['start_time']})"
            )
        return "\n".join(lines)

    @tool
    async def delete_failed_job(namespace: str, job_name: str, reason: str) -> str:
        """Delete a failed job to allow retry or cleanup.

        Args:
            namespace: Kubernetes namespace
            job_name: Name of the failed job
            reason: Reason for deletion (for audit log)
        """
        result = await k8s.delete_failed_job(namespace, job_name, reason)
        if result["success"]:
            return f"Successfully deleted failed job {job_name}"
        return f"Failed to delete job: {result.get('error', 'Unknown error')}"

    @tool
    async def get_hpa_status(namespace: str, hpa_name: str) -> str:
        """Get HorizontalPodAutoscaler status and current metrics.

        Args:
            namespace: Kubernetes namespace
            hpa_name: Name of the HPA
        """
        result = await k8s.get_hpa_status(namespace, hpa_name)
        return json.dumps(result, indent=2, default=str)

    @tool
    async def get_pdb_status(namespace: str) -> str:
        """List PodDisruptionBudgets in a namespace with disruption allowance.

        Args:
            namespace: Kubernetes namespace
        """
        pdbs = await k8s.get_pdb_status(namespace)
        if not pdbs:
            return f"No PodDisruptionBudgets in {namespace}."
        return json.dumps(pdbs, indent=2, default=str)

    # ----- Certificate Monitoring -----

    @tool
    async def check_certificates(namespace: Optional[str] = None) -> str:
        """Check cert-manager certificates for failures or upcoming expiration.

        Args:
            namespace: Optional namespace to scope the check
        """
        if not cert_monitor:
            return "cert-manager monitor not available."
        failing = await cert_monitor.get_failing_certificates()
        if not failing:
            return "All certificates are healthy and not expiring soon."
        lines = [f"Certificate issues ({len(failing)}):"]
        for c in failing:
            status = "NOT READY" if not c["ready"] else "EXPIRING SOON"
            days = (
                f" ({c['days_until_expiry']:.0f}d remaining)"
                if c["days_until_expiry"] is not None
                else ""
            )
            lines.append(
                f"- [{status}] {c['namespace']}/{c['name']}{days}: {c.get('message', '')}"
            )
        return "\n".join(lines)

    @tool
    async def get_all_certificates(namespace: Optional[str] = None) -> str:
        """List all cert-manager certificates with their status.

        Args:
            namespace: Optional namespace filter
        """
        if not cert_monitor:
            return "cert-manager monitor not available."
        certs = await cert_monitor.get_certificates(namespace)
        if not certs:
            return "No certificates found."
        return json.dumps(certs, indent=2, default=str)

    # ----- Storage Monitoring -----

    @tool
    async def get_degraded_volumes() -> str:
        """Check Longhorn for degraded, faulted, or under-replicated volumes."""
        if not storage_monitor:
            return "Longhorn storage monitor not available."
        volumes = await storage_monitor.get_degraded_volumes()
        if not volumes:
            return "All Longhorn volumes are healthy."
        lines = [f"Degraded volumes ({len(volumes)}):"]
        for v in volumes:
            lines.append(
                f"- {v['name']}: state={v['state']} robustness={v['robustness']} replicas={v['replicas']}/{v['number_of_replicas']}"
            )
        return "\n".join(lines)

    @tool
    async def get_volume_detail(volume_name: str) -> str:
        """Get detailed info for a Longhorn volume including replica status.

        Args:
            volume_name: Name of the Longhorn volume
        """
        if not storage_monitor:
            return "Longhorn storage monitor not available."
        result = await storage_monitor.get_volume_detail(volume_name)
        return json.dumps(result, indent=2, default=str)

    # ----- Security Tools -----

    @tool
    async def get_crowdsec_decisions(ip: Optional[str] = None) -> str:
        """Get active CrowdSec ban/captcha decisions.

        Args:
            ip: Optional IP address to filter decisions
        """
        if not crowdsec:
            return "CrowdSec client not available."
        decisions = await crowdsec.get_decisions(ip=ip)
        if not decisions:
            return "No active CrowdSec decisions."
        lines = [f"Active CrowdSec decisions ({len(decisions)}):"]
        for d in decisions:
            lines.append(
                f"- [{d['type']}] {d['scope']}:{d['value']} scenario={d['scenario']} duration={d['duration']}"
            )
        return "\n".join(lines)

    @tool
    async def get_crowdsec_alerts(limit: int = 25) -> str:
        """Get recent CrowdSec security alerts.

        Args:
            limit: Max number of alerts to retrieve
        """
        if not crowdsec:
            return "CrowdSec client not available."
        alerts = await crowdsec.get_alerts(limit=limit)
        if not alerts:
            return "No recent CrowdSec alerts."
        lines = [f"Recent CrowdSec alerts ({len(alerts)}):"]
        for a in alerts:
            lines.append(
                f"- [{a['created_at']}] {a['scenario']} from {a['source_ip']} ({a['events_count']} events)"
            )
        return "\n".join(lines)

    # ----- Status Page -----

    @tool
    async def check_status_page() -> str:
        """Check the Gatus status page for unhealthy services.
        Returns service status from the monitoring status page."""
        if not gatus:
            return "Gatus status page client not available."
        statuses = await gatus.get_endpoint_statuses()
        if not statuses:
            return "No endpoints found on the status page."
        unhealthy = [s for s in statuses if not s["healthy"]]
        lines = [f"Status page: {len(statuses)} endpoints, {len(unhealthy)} unhealthy"]
        if unhealthy:
            lines.append("\nUnhealthy services:")
            for s in unhealthy:
                lines.append(f"- {s['group']}/{s['name']}: uptime_7d={s['uptime_7d']}%")
        return "\n".join(lines)

    return [
        analyze_cluster,
        check_all_services,
        check_service,
        get_crashloopbackoff_pods,
        get_pod_details,
        get_recent_events,
        restart_pod,
        rollout_restart_deployment,
        scale_deployment,
        get_rate_limit_status,
        get_audit_log,
        create_remediation_pr,
        notify_slack,
        create_thehive_case,
        get_node_status,
        list_nodes,
        cordon_node,
        drain_node,
        store_resolution,
        recall_similar_issues,
        # v0.5.0 - Prometheus/Metrics
        query_prometheus,
        get_pod_cpu_usage,
        get_pod_memory_usage,
        get_service_error_rate,
        get_prometheus_alerts,
        # v0.5.0 - Loki/Logs
        get_pod_logs_from_loki,
        get_namespace_error_logs,
        search_cluster_logs,
        # v0.5.0 - K8s Extended
        get_pod_k8s_logs,
        rollback_deployment,
        rollout_restart_statefulset,
        get_statefulset_status,
        get_failed_jobs,
        delete_failed_job,
        get_hpa_status,
        get_pdb_status,
        # v0.5.0 - Certificates
        check_certificates,
        get_all_certificates,
        # v0.5.0 - Storage
        get_degraded_volumes,
        get_volume_detail,
        # v0.5.0 - Security
        get_crowdsec_decisions,
        get_crowdsec_alerts,
        # v0.7.0 - Status Page
        check_status_page,
    ]


# =============================================================================
# AGENT GRAPH
# =============================================================================


class ClusterGuardian:
    """
    Cluster Guardian agent using LangGraph.

    Monitors cluster health and takes remediation actions when issues are detected.
    """

    SYSTEM_PROMPT = """You are the Cluster Guardian, an AI agent responsible for monitoring and maintaining the health of a Kubernetes cluster.

Your responsibilities:
1. ANALYZE: Use K8sGPT and health checks to identify issues
2. DIAGNOSE: Investigate the root cause of problems
3. REMEDIATE: Take safe, measured actions to fix issues
4. REPORT: Document all findings and actions via the appropriate channels
5. LEARN: Store successful remediation patterns for future reference

Safety Rules:
- Always check rate limits before taking actions
- Never take multiple drastic actions at once
- Prefer rollout restarts over individual pod deletions
- If unsure, gather more information before acting
- Some actions require human approval (scale to zero, PVC deletion, node cordon/drain)

Node Operations:
- You can check individual node status and list all cluster nodes
- You can cordon nodes (prevent new scheduling) and drain nodes (evict pods) when needed
- Cordon and drain require human approval by default

Memory and Learning:
- After successfully remediating an issue, store the resolution using store_resolution so you can learn from it
- When diagnosing a new issue, use recall_similar_issues to check for similar past incidents and their resolutions
- This helps you apply proven remediation patterns to recurring problems

Observability:
- You can query Prometheus for CPU/memory usage, error rates, latency percentiles, and firing alerts
- You can search logs in Loki for error patterns, pod logs, and log volume analysis
- You can get pod logs directly from the Kubernetes API (useful for pods not yet in Loki, or previous container logs)

Advanced Operations:
- You can rollback deployments to their previous revision if a bad deploy caused issues
- You can restart StatefulSets (e.g., databases) with rollout restarts
- You can list failed jobs and delete them to allow retry
- You can check HPA status and PDB status for capacity planning

Infrastructure Monitoring:
- You can check cert-manager certificates for failures or approaching expiration
- You can check Longhorn storage volumes for degraded/faulted/under-replicated state

Security Awareness:
- You can check CrowdSec for active ban decisions and recent security alerts
- Falco runtime security alerts may be delivered via webhook

Status Page Monitoring:
- You can check the Gatus status page for real-time service uptime monitoring
- Use the status page to identify services that are down or degraded

When you find issues:
1. First recall similar past issues to guide your diagnosis
2. Understand what's wrong (check events, pod status, node status, Prometheus metrics, Loki logs)
3. Determine if it's safe to remediate automatically
4. Take the least disruptive action that solves the problem
5. Verify the fix worked
6. Store the resolution for future learning

Reporting Channels:
- Auto-remediated issues (known-safe fixes like pod restarts, rollout restarts):
  Send a Slack notification summarizing the action taken.
- Novel or complex issues that need human review:
  Create a GitHub PR with the proposed fix, send a Slack notification with the PR link,
  and create a TheHive case for incident tracking.
- Informational findings (healthy cluster, minor observations):
  Send a Slack notification only.

Current cluster domain: spooty.io
Protected namespaces (never modify): kube-system, kube-public, longhorn-system, calico-system

Start by analyzing the cluster state, checking service health, and reviewing Prometheus alerts."""

    def __init__(self):
        self.k8s = get_k8s_client()
        self.k8sgpt = get_k8sgpt_client()
        self.health_checker = get_health_checker()
        self.prometheus = get_prometheus_client()
        self.loki = get_loki_client()
        self.cert_monitor = get_cert_monitor()
        self.storage_monitor = get_storage_monitor()
        self.crowdsec = get_crowdsec_client(
            lapi_url=settings.crowdsec_lapi_url, api_key=settings.crowdsec_api_key
        )
        self.gatus = get_gatus_client()

        # Create LLM
        self.llm = ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=0.1,  # Low temperature for reliability
        )

        # Langfuse observability
        self.langfuse_handler = None
        if (
            LangfuseCallbackHandler
            and settings.langfuse_public_key
            and settings.langfuse_secret_key
            and settings.langfuse_host
        ):
            self.langfuse_handler = LangfuseCallbackHandler(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            logger.info("Langfuse observability enabled")

        # Create tools
        self.tools = create_tools(
            self.k8s,
            self.k8sgpt,
            self.health_checker,
            prometheus=self.prometheus,
            loki=self.loki,
            cert_monitor=self.cert_monitor,
            storage_monitor=self.storage_monitor,
            crowdsec=self.crowdsec,
            gatus=self.gatus,
        )

        # Bind tools to LLM
        self.llm_with_tools = self.llm.bind_tools(self.tools)

        # Build graph
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow."""

        MAX_ITERATIONS = settings.max_agent_iterations

        def should_continue(state: GuardianState) -> Literal["tools", "end"]:
            """Determine if we should continue to tools or end."""
            if state["iteration"] >= MAX_ITERATIONS:
                logger.warning(
                    "Max iterations reached, ending agent loop",
                    iteration=state["iteration"],
                )
                return "end"

            messages = state["messages"]
            last_message = messages[-1]

            # If the LLM made tool calls, continue to tools
            if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                return "tools"

            # Otherwise, end
            return "end"

        async def agent_node(state: GuardianState) -> GuardianState:
            """Main agent reasoning node."""
            messages = state["messages"]

            # Add system message if this is the first iteration
            if state["iteration"] == 0:
                messages = [SystemMessage(content=self.SYSTEM_PROMPT)] + messages

            # On final iteration, call LLM without tools to force a text summary
            if state["iteration"] >= MAX_ITERATIONS - 1:
                messages_with_summary = messages + [
                    HumanMessage(
                        content="Summarize your findings and actions taken in a concise report."
                    )
                ]
                response = await self.llm.ainvoke(messages_with_summary)
            else:
                response = await self.llm_with_tools.ainvoke(messages)

            guardian_agent_iterations_total.inc()
            guardian_rate_limit_remaining.set(self.k8s.rate_limiter.get_remaining())

            return {
                **state,
                "messages": messages + [response],
                "iteration": state["iteration"] + 1,
            }

        # Create tool node
        tool_node = ToolNode(self.tools)

        # Build graph
        workflow = StateGraph(GuardianState)

        workflow.add_node("agent", agent_node)
        workflow.add_node("tools", tool_node)

        workflow.set_entry_point("agent")

        workflow.add_conditional_edges(
            "agent",
            should_continue,
            {
                "tools": "tools",
                "end": END,
            },
        )
        workflow.add_edge("tools", "agent")

        # Compile with memory
        memory = MemorySaver()
        return workflow.compile(checkpointer=memory)

    async def run_scan(self, thread_id: str = "guardian-main") -> Dict[str, Any]:
        """
        Run a full cluster scan and remediation cycle.

        Returns summary of issues found and actions taken.
        """
        logger.info("Starting cluster scan", thread_id=thread_id)

        # Check quiet hours -- restrict to observation-only during quiet window
        quiet = _is_quiet_hours()
        if quiet:
            logger.info("Quiet hours active, remediation actions will be deferred")

        scan_instruction = (
            "Please scan the cluster for issues. "
            "First check for CrashLoopBackOff pods, then run deep health checks on all services. "
            "Also check the status page for any services showing unhealthy. "
            "For any issues found, investigate and take appropriate remediation actions."
        )
        if quiet:
            scan_instruction = (
                "QUIET HOURS ARE ACTIVE. Only perform observation and diagnosis. "
                "Do NOT take any remediation actions (no restarts, no scaling, no rollouts). "
                "Report findings but defer all fixes until quiet hours end. "
                "Scan the cluster: check CrashLoopBackOff pods, run health checks, "
                "and check the status page. Document issues found."
            )

        initial_state: GuardianState = {
            "messages": [HumanMessage(content=scan_instruction)],
            "issues": [],
            "health_results": [],
            "actions_taken": [],
            "pending_approvals": [],
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "iteration": 0,
        }

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        if self.langfuse_handler:
            config["callbacks"] = [self.langfuse_handler]

        try:
            # Run the graph
            final_state = None
            async for state in self.graph.astream(initial_state, config):
                final_state = state
                # Log progress
                if "agent" in state:
                    agent_state = state["agent"]
                    if agent_state.get("messages"):
                        last_msg = agent_state["messages"][-1]
                        if hasattr(last_msg, "content") and last_msg.content:
                            logger.debug(
                                "Agent response", content=last_msg.content[:200]
                            )

            # Extract summary from final state
            messages = []
            if final_state and "agent" in final_state:
                messages = final_state["agent"].get("messages", [])

            # Get last AI message as summary
            summary = "Scan completed"
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    summary = msg.content
                    break

            audit_log = (await self.k8s.get_audit_log())[-10:]
            for entry in audit_log:
                notifier.send_wazuh_syslog(
                    action=entry.get("action", "unknown"),
                    result=entry.get("result", "unknown"),
                    metadata={
                        "namespace": entry.get("namespace"),
                        "target": entry.get("target"),
                        "scan_id": thread_id,
                    },
                )

            return {
                "success": True,
                "summary": summary,
                "audit_log": audit_log,
                "rate_limit": self.k8s.get_rate_limit_status(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error("Scan failed", error=str(e))
            notifier.send_wazuh_syslog("scan", "failed", {"error": str(e)})
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def investigate_issue(
        self, description: str, thread_id: str = "guardian-investigate"
    ) -> Dict[str, Any]:
        """
        Investigate a specific issue reported by a user or alert.

        Args:
            description: Description of the issue to investigate
            thread_id: Thread ID for conversation continuity
        """
        logger.info("Investigating issue", description=description[:100])

        quiet = _is_quiet_hours()
        if quiet:
            logger.info(
                "Quiet hours active, investigation will be observation-only",
                description=description[:100],
            )

        investigate_instruction = (
            f"Please investigate this issue: {description}\n\n"
            "Gather relevant information, diagnose the root cause, "
            "and take appropriate remediation actions if safe to do so."
        )
        if quiet:
            investigate_instruction = (
                f"QUIET HOURS ARE ACTIVE. Please investigate this issue: {description}\n\n"
                "Gather relevant information and diagnose the root cause, "
                "but do NOT take any remediation actions. Report your findings only."
            )

        initial_state: GuardianState = {
            "messages": [HumanMessage(content=investigate_instruction)],
            "issues": [],
            "health_results": [],
            "actions_taken": [],
            "pending_approvals": [],
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "iteration": 0,
        }

        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
        if self.langfuse_handler:
            config["callbacks"] = [self.langfuse_handler]

        try:
            final_state = None
            async for state in self.graph.astream(initial_state, config):
                final_state = state

            # Get summary
            messages = []
            if final_state and "agent" in final_state:
                messages = final_state["agent"].get("messages", [])

            summary = "Investigation completed"
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    summary = msg.content
                    break

            audit_log = (await self.k8s.get_audit_log())[-10:]
            for entry in audit_log:
                notifier.send_wazuh_syslog(
                    action=entry.get("action", "unknown"),
                    result=entry.get("result", "unknown"),
                    metadata={
                        "namespace": entry.get("namespace"),
                        "target": entry.get("target"),
                        "investigation": description[:100],
                    },
                )

            return {
                "success": True,
                "summary": summary,
                "audit_log": audit_log,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error("Investigation failed", error=str(e))
            notifier.send_wazuh_syslog("investigate", "failed", {"error": str(e)})
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


# Global instance
_guardian: Optional[ClusterGuardian] = None


def get_guardian() -> ClusterGuardian:
    """Get or create Guardian singleton."""
    global _guardian
    if _guardian is None:
        _guardian = ClusterGuardian()
    return _guardian
