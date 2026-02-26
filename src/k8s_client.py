"""
Kubernetes client wrapper with remediation actions.

Provides safe, rate-limited access to Kubernetes operations
for the Cluster Guardian agent.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from collections import deque
import structlog

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .config import settings
from .redis_client import get_redis_client, RedisClient

logger = structlog.get_logger(__name__)


class ActionRateLimiter:
    """Rate limiter for remediation actions."""

    def __init__(
        self,
        max_actions: int,
        window_seconds: int = 3600,
        redis_client: Optional[RedisClient] = None,
    ):
        self.max_actions = max_actions
        self.window_seconds = window_seconds
        self.actions: deque = deque()
        self.redis_client = redis_client

    async def _refresh_max_actions(self):
        """Refresh max_actions from runtime config store if available."""
        try:
            from .config_store import get_config_store

            store = get_config_store()
            value = await store.get("max_actions_per_hour")
            if isinstance(value, int) and value > 0:
                self.max_actions = value
        except Exception:
            pass

    async def can_act(self) -> bool:
        """Check if we can perform another action."""
        await self._refresh_max_actions()
        self._cleanup_old()
        if self.redis_client and self.redis_client.available:
            try:
                redis_count = await self.redis_client.get_actions_in_window(
                    self.window_seconds
                )
                return redis_count < self.max_actions
            except Exception:
                pass
        return len(self.actions) < self.max_actions

    async def record_action(self, action: str):
        """Record that an action was taken."""
        now = datetime.now(timezone.utc)
        self.actions.append((now, action))
        if self.redis_client and self.redis_client.available:
            await self.redis_client.record_action(action, now.isoformat())

    def _cleanup_old(self):
        """Remove actions outside the window."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.window_seconds)
        while self.actions and self.actions[0][0] < cutoff:
            self.actions.popleft()

    def get_remaining(self) -> int:
        """Get remaining actions allowed."""
        self._cleanup_old()
        return max(0, self.max_actions - len(self.actions))


class AuditLog:
    """Audit log for all remediation actions."""

    def __init__(self, redis_client: Optional[RedisClient] = None):
        self.entries: List[Dict[str, Any]] = []
        self.redis_client = redis_client

    async def log(
        self,
        action: str,
        target: str,
        namespace: str,
        reason: str,
        result: str,
        details: Optional[Dict] = None,
    ):
        """Log a remediation action."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "target": target,
            "namespace": namespace,
            "reason": reason,
            "result": result,
            "details": details or {},
        }
        self.entries.append(entry)
        if self.redis_client and self.redis_client.available:
            await self.redis_client.append_audit_entry(entry)
        logger.info(
            "Audit log",
            action=action,
            target=target,
            namespace=namespace,
            result=result,
        )

    async def get_recent(self, count: int = 50) -> List[Dict]:
        """Get recent audit entries."""
        if self.redis_client and self.redis_client.available:
            try:
                entries = await self.redis_client.get_audit_entries(count)
                if entries:
                    return entries
            except Exception:
                pass
        return self.entries[-count:]


class K8sClient:
    """
    Kubernetes client with safe remediation operations.

    All operations are:
    - Rate limited
    - Namespace protected
    - Audit logged
    """

    def __init__(self):
        # Load kubeconfig
        try:
            if settings.kubeconfig_path:
                config.load_kube_config(settings.kubeconfig_path)
            else:
                config.load_incluster_config()
        except config.ConfigException:
            logger.warning("Failed to load in-cluster config, trying kubeconfig")
            config.load_kube_config()

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()
        self.autoscaling_v2 = client.AutoscalingV2Api()
        self.policy_v1 = client.PolicyV1Api()
        self.custom_objects = client.CustomObjectsApi()
        self._redis_client = get_redis_client()
        self.rate_limiter = ActionRateLimiter(
            settings.max_actions_per_hour, redis_client=self._redis_client
        )
        self.audit_log = AuditLog(redis_client=self._redis_client)

    def _check_namespace_protected(self, namespace: str) -> bool:
        """Check if namespace is protected from remediation."""
        return namespace in settings.protected_namespaces

    async def _check_rate_limit(self) -> bool:
        """Check if we can perform another action."""
        return await self.rate_limiter.can_act()

    # =========================================================================
    # READ OPERATIONS (no rate limiting needed)
    # =========================================================================

    async def get_node_status(self, name: str) -> Dict[str, Any]:
        """Get detailed node status including conditions, resources, taints."""
        try:
            node = self.core_v1.read_node(name)
            return {
                "name": node.metadata.name,
                "conditions": [
                    {
                        "type": c.type,
                        "status": c.status,
                        "reason": c.reason,
                        "message": c.message,
                    }
                    for c in (node.status.conditions or [])
                ],
                "allocatable": {
                    k: v for k, v in (node.status.allocatable or {}).items()
                },
                "taints": [
                    {"key": t.key, "value": t.value, "effect": t.effect}
                    for t in (node.spec.taints or [])
                ],
                "unschedulable": node.spec.unschedulable or False,
            }
        except ApiException as e:
            logger.error("Failed to get node status", name=name, error=str(e))
            return {"error": str(e)}

    async def get_all_nodes(self) -> List[Dict[str, Any]]:
        """List all nodes with name, conditions summary, roles, and taints."""
        nodes = []
        try:
            node_list = self.core_v1.list_node()
            for node in node_list.items:
                roles = [
                    label.replace("node-role.kubernetes.io/", "")
                    for label in (node.metadata.labels or {})
                    if label.startswith("node-role.kubernetes.io/")
                ]
                conditions_summary = {
                    c.type: c.status for c in (node.status.conditions or [])
                }
                nodes.append(
                    {
                        "name": node.metadata.name,
                        "roles": roles,
                        "conditions": conditions_summary,
                        "taints": [
                            {"key": t.key, "value": t.value, "effect": t.effect}
                            for t in (node.spec.taints or [])
                        ],
                        "unschedulable": node.spec.unschedulable or False,
                    }
                )
        except ApiException as e:
            logger.error("Failed to list nodes", error=str(e))
        return nodes

    async def get_pod_status(self, namespace: str, name: str) -> Dict[str, Any]:
        """Get detailed pod status."""
        try:
            pod = self.core_v1.read_namespaced_pod(name, namespace)
            return {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "phase": pod.status.phase,
                "conditions": [
                    {"type": c.type, "status": c.status, "reason": c.reason}
                    for c in (pod.status.conditions or [])
                ],
                "container_statuses": [
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "state": self._get_container_state(cs.state),
                    }
                    for cs in (pod.status.container_statuses or [])
                ],
                "node": pod.spec.node_name,
            }
        except ApiException as e:
            logger.error(
                "Failed to get pod status", namespace=namespace, name=name, error=str(e)
            )
            return {"error": str(e)}

    def _get_container_state(self, state) -> Dict[str, Any]:
        """Extract container state details."""
        if state.running:
            return {"state": "running", "started_at": str(state.running.started_at)}
        elif state.waiting:
            return {
                "state": "waiting",
                "reason": state.waiting.reason,
                "message": state.waiting.message,
            }
        elif state.terminated:
            return {
                "state": "terminated",
                "reason": state.terminated.reason,
                "exit_code": state.terminated.exit_code,
            }
        return {"state": "unknown"}

    async def get_crashloopbackoff_pods(self) -> List[Dict[str, Any]]:
        """Get all pods in CrashLoopBackOff state."""
        crashing_pods = []
        try:
            pods = await asyncio.to_thread(self.core_v1.list_pod_for_all_namespaces)
            for pod in pods.items:
                if pod.metadata.namespace in settings.protected_namespaces:
                    continue
                for cs in pod.status.container_statuses or []:
                    if (
                        cs.state.waiting
                        and cs.state.waiting.reason == "CrashLoopBackOff"
                    ):
                        crashing_pods.append(
                            {
                                "name": pod.metadata.name,
                                "namespace": pod.metadata.namespace,
                                "container": cs.name,
                                "restart_count": cs.restart_count,
                                "message": cs.state.waiting.message,
                            }
                        )
        except ApiException as e:
            logger.error("Failed to list pods", error=str(e))
        return crashing_pods

    async def get_deployment_status(self, namespace: str, name: str) -> Dict[str, Any]:
        """Get deployment status."""
        try:
            deploy = self.apps_v1.read_namespaced_deployment(name, namespace)
            return {
                "name": deploy.metadata.name,
                "namespace": deploy.metadata.namespace,
                "replicas": deploy.spec.replicas,
                "available_replicas": deploy.status.available_replicas or 0,
                "ready_replicas": deploy.status.ready_replicas or 0,
                "updated_replicas": deploy.status.updated_replicas or 0,
                "conditions": [
                    {"type": c.type, "status": c.status, "reason": c.reason}
                    for c in (deploy.status.conditions or [])
                ],
            }
        except ApiException as e:
            logger.error(
                "Failed to get deployment", namespace=namespace, name=name, error=str(e)
            )
            return {"error": str(e)}

    async def get_events(
        self, namespace: str, involved_object: Optional[str] = None
    ) -> List[Dict]:
        """Get recent events for a namespace or object."""
        try:
            if involved_object:
                events = self.core_v1.list_namespaced_event(
                    namespace,
                    field_selector=f"involvedObject.name={involved_object}",
                )
            else:
                events = self.core_v1.list_namespaced_event(namespace)

            return [
                {
                    "type": e.type,
                    "reason": e.reason,
                    "message": e.message,
                    "count": e.count,
                    "last_timestamp": str(e.last_timestamp),
                    "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}",
                }
                for e in sorted(
                    events.items,
                    key=lambda x: x.last_timestamp or datetime.min,
                    reverse=True,
                )[:20]
            ]
        except ApiException as e:
            logger.error("Failed to get events", namespace=namespace, error=str(e))
            return []

    async def get_pod_logs(
        self,
        namespace: str,
        name: str,
        container: str | None = None,
        tail_lines: int = 100,
        previous: bool = False,
    ) -> str:
        """Get logs from a pod, optionally from a specific or previous container."""
        try:
            kwargs: Dict[str, Any] = {
                "name": name,
                "namespace": namespace,
                "tail_lines": tail_lines,
                "previous": previous,
            }
            if container:
                kwargs["container"] = container
            logs = self.core_v1.read_namespaced_pod_log(**kwargs)
            if len(logs) > 5000:
                logs = logs[-5000:]
            return logs
        except ApiException as e:
            logger.error(
                "Failed to get pod logs", namespace=namespace, name=name, error=str(e)
            )
            return f"Error fetching logs: {e}"

    async def get_statefulset_status(self, namespace: str, name: str) -> Dict[str, Any]:
        """Get StatefulSet status including replicas and conditions."""
        try:
            sts = self.apps_v1.read_namespaced_stateful_set(name, namespace)
            return {
                "name": sts.metadata.name,
                "namespace": sts.metadata.namespace,
                "replicas": sts.spec.replicas,
                "ready_replicas": sts.status.ready_replicas or 0,
                "updated_replicas": sts.status.updated_replicas or 0,
                "conditions": [
                    {"type": c.type, "status": c.status, "reason": c.reason}
                    for c in (sts.status.conditions or [])
                ],
            }
        except ApiException as e:
            logger.error(
                "Failed to get statefulset status",
                namespace=namespace,
                name=name,
                error=str(e),
            )
            return {"error": str(e)}

    async def get_failed_jobs(
        self, namespace: str | None = None
    ) -> List[Dict[str, Any]]:
        """Get all failed jobs, optionally filtered to a namespace."""
        failed_jobs: List[Dict[str, Any]] = []
        try:
            if namespace:
                job_list = self.batch_v1.list_namespaced_job(namespace)
            else:
                job_list = self.batch_v1.list_job_for_all_namespaces()

            for job in job_list.items:
                if job.metadata.namespace in settings.protected_namespaces:
                    continue
                for condition in job.status.conditions or []:
                    if condition.type == "Failed" and condition.status == "True":
                        failed_jobs.append(
                            {
                                "name": job.metadata.name,
                                "namespace": job.metadata.namespace,
                                "start_time": str(job.status.start_time),
                                "completions": job.spec.completions,
                                "failed": job.status.failed or 0,
                            }
                        )
                        break
        except ApiException as e:
            logger.error("Failed to list jobs", error=str(e))
        return failed_jobs

    async def get_hpa_status(self, namespace: str, name: str) -> Dict[str, Any]:
        """Get HorizontalPodAutoscaler status including metrics and conditions."""
        try:
            hpa = self.autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
                name, namespace
            )
            current_metrics = []
            for metric in hpa.status.current_metrics or []:
                entry: Dict[str, Any] = {"type": metric.type}
                if metric.resource:
                    entry["resource_name"] = metric.resource.name
                    if metric.resource.current:
                        entry["current_average_utilization"] = (
                            metric.resource.current.average_utilization
                        )
                        entry["current_average_value"] = str(
                            metric.resource.current.average_value
                        )
                current_metrics.append(entry)
            return {
                "name": hpa.metadata.name,
                "namespace": hpa.metadata.namespace,
                "min_replicas": hpa.spec.min_replicas,
                "max_replicas": hpa.spec.max_replicas,
                "current_replicas": hpa.status.current_replicas or 0,
                "current_metrics": current_metrics,
                "conditions": [
                    {
                        "type": c.type,
                        "status": c.status,
                        "reason": c.reason,
                        "message": c.message,
                    }
                    for c in (hpa.status.conditions or [])
                ],
            }
        except ApiException as e:
            logger.error(
                "Failed to get HPA status", namespace=namespace, name=name, error=str(e)
            )
            return {"error": str(e)}

    async def get_pdb_status(self, namespace: str) -> List[Dict[str, Any]]:
        """List PodDisruptionBudgets in a namespace with status details."""
        pdbs: List[Dict[str, Any]] = []
        try:
            pdb_list = self.policy_v1.list_namespaced_pod_disruption_budget(namespace)
            for pdb in pdb_list.items:
                pdbs.append(
                    {
                        "name": pdb.metadata.name,
                        "min_available": str(pdb.spec.min_available)
                        if pdb.spec.min_available is not None
                        else None,
                        "max_unavailable": str(pdb.spec.max_unavailable)
                        if pdb.spec.max_unavailable is not None
                        else None,
                        "current_healthy": pdb.status.current_healthy,
                        "disruptions_allowed": pdb.status.disruptions_allowed,
                    }
                )
        except ApiException as e:
            logger.error("Failed to list PDBs", namespace=namespace, error=str(e))
        return pdbs

    async def list_ingress_routes(
        self, namespace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List Traefik IngressRoute CRDs via CustomObjects API."""
        try:
            if namespace:
                resp = self.custom_objects.list_namespaced_custom_object(
                    group="traefik.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="ingressroutes",
                )
            else:
                resp = self.custom_objects.list_cluster_custom_object(
                    group="traefik.io",
                    version="v1alpha1",
                    plural="ingressroutes",
                )
            return [
                {
                    "name": item["metadata"]["name"],
                    "namespace": item["metadata"]["namespace"],
                    "spec": item.get("spec", {}),
                }
                for item in resp.get("items", [])
            ]
        except ApiException as e:
            logger.error("Failed to list IngressRoutes", error=str(e))
            return []

    async def get_ingress_route(self, namespace: str, name: str) -> Dict[str, Any]:
        """Get a specific Traefik IngressRoute."""
        try:
            return self.custom_objects.get_namespaced_custom_object(
                group="traefik.io",
                version="v1alpha1",
                namespace=namespace,
                plural="ingressroutes",
                name=name,
            )
        except ApiException as e:
            logger.error(
                "Failed to get IngressRoute",
                namespace=namespace,
                name=name,
                error=str(e),
            )
            return {"error": str(e)}

    async def list_services(
        self, namespace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List Services with endpoint readiness info."""
        try:
            if namespace:
                svc_list = self.core_v1.list_namespaced_service(namespace)
            else:
                svc_list = self.core_v1.list_service_for_all_namespaces()

            results = []
            for svc in svc_list.items:
                results.append(
                    {
                        "name": svc.metadata.name,
                        "namespace": svc.metadata.namespace,
                        "type": svc.spec.type,
                        "cluster_ip": svc.spec.cluster_ip,
                        "ports": [
                            {"port": p.port, "protocol": p.protocol}
                            for p in (svc.spec.ports or [])
                        ],
                    }
                )
            return results
        except ApiException as e:
            logger.error("Failed to list services", error=str(e))
            return []

    async def get_service_endpoints(
        self, namespace: str, service_name: str
    ) -> Dict[str, Any]:
        """Get endpoint addresses for a Service."""
        try:
            endpoints = self.core_v1.read_namespaced_endpoints(service_name, namespace)
            ready = []
            not_ready = []
            for subset in endpoints.subsets or []:
                for addr in subset.addresses or []:
                    ready.append(addr.ip)
                for addr in subset.not_ready_addresses or []:
                    not_ready.append(addr.ip)
            return {
                "service": service_name,
                "namespace": namespace,
                "ready_addresses": ready,
                "not_ready_addresses": not_ready,
                "total_ready": len(ready),
                "total_not_ready": len(not_ready),
            }
        except ApiException as e:
            logger.error(
                "Failed to get service endpoints",
                namespace=namespace,
                service=service_name,
                error=str(e),
            )
            return {"error": str(e)}

    async def list_daemonsets(
        self, namespace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List DaemonSets with desired vs ready status."""
        try:
            if namespace:
                ds_list = self.apps_v1.list_namespaced_daemon_set(namespace)
            else:
                ds_list = self.apps_v1.list_daemon_set_for_all_namespaces()

            results = []
            for ds in ds_list.items:
                status = ds.status
                results.append(
                    {
                        "name": ds.metadata.name,
                        "namespace": ds.metadata.namespace,
                        "desired": status.desired_number_scheduled or 0,
                        "current": status.current_number_scheduled or 0,
                        "ready": status.number_ready or 0,
                        "unavailable": status.number_unavailable or 0,
                    }
                )
            return results
        except ApiException as e:
            logger.error("Failed to list DaemonSets", error=str(e))
            return []

    async def watch_events(self, callback) -> None:
        """Stream K8s events and call callback for Warning/Error types.

        This is a blocking coroutine intended to be run as an asyncio task.
        """
        from kubernetes import watch

        w = watch.Watch()
        try:
            for event in w.stream(
                self.core_v1.list_event_for_all_namespaces,
                timeout_seconds=300,
            ):
                obj = event.get("object")
                if obj and obj.type in ("Warning", "Error"):
                    await callback(event)
        finally:
            w.stop()

    # =========================================================================
    # WRITE OPERATIONS (rate limited, audit logged)
    # =========================================================================

    async def restart_pod(
        self, namespace: str, name: str, reason: str
    ) -> Dict[str, Any]:
        """Delete a pod to trigger restart (if managed by a controller)."""
        if self._check_namespace_protected(namespace):
            return {"success": False, "error": f"Namespace {namespace} is protected"}

        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            self.core_v1.delete_namespaced_pod(name, namespace)
            await self.rate_limiter.record_action(f"restart_pod:{namespace}/{name}")
            await self.audit_log.log(
                action="restart_pod",
                target=name,
                namespace=namespace,
                reason=reason,
                result="success",
            )
            logger.info("Restarted pod", namespace=namespace, name=name, reason=reason)
            return {"success": True, "message": f"Pod {name} deleted for restart"}
        except ApiException as e:
            await self.audit_log.log(
                action="restart_pod",
                target=name,
                namespace=namespace,
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def scale_deployment(
        self, namespace: str, name: str, replicas: int, reason: str
    ) -> Dict[str, Any]:
        """Scale a deployment to specified replicas."""
        if self._check_namespace_protected(namespace):
            return {"success": False, "error": f"Namespace {namespace} is protected"}

        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        # Extra protection for scale to zero
        if replicas == 0 and "scale_to_zero" in settings.require_approval_for:
            return {
                "success": False,
                "error": "Scale to zero requires human approval",
                "requires_approval": True,
            }

        try:
            self.apps_v1.patch_namespaced_deployment_scale(
                name,
                namespace,
                {"spec": {"replicas": replicas}},
            )
            await self.rate_limiter.record_action(
                f"scale_deployment:{namespace}/{name}"
            )
            await self.audit_log.log(
                action="scale_deployment",
                target=name,
                namespace=namespace,
                reason=reason,
                result="success",
                details={"replicas": replicas},
            )
            logger.info(
                "Scaled deployment", namespace=namespace, name=name, replicas=replicas
            )
            return {
                "success": True,
                "message": f"Deployment {name} scaled to {replicas}",
            }
        except ApiException as e:
            await self.audit_log.log(
                action="scale_deployment",
                target=name,
                namespace=namespace,
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def rollout_restart(
        self, namespace: str, name: str, reason: str
    ) -> Dict[str, Any]:
        """Trigger a rollout restart for a deployment."""
        if self._check_namespace_protected(namespace):
            return {"success": False, "error": f"Namespace {namespace} is protected"}

        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            # Patch deployment with restart annotation
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "cluster-guardian/restartedAt": datetime.now(
                                    timezone.utc
                                ).isoformat()
                            }
                        }
                    }
                }
            }
            self.apps_v1.patch_namespaced_deployment(name, namespace, patch)
            await self.rate_limiter.record_action(f"rollout_restart:{namespace}/{name}")
            await self.audit_log.log(
                action="rollout_restart",
                target=name,
                namespace=namespace,
                reason=reason,
                result="success",
            )
            logger.info(
                "Rollout restart", namespace=namespace, name=name, reason=reason
            )
            return {
                "success": True,
                "message": f"Deployment {name} rollout restart triggered",
            }
        except ApiException as e:
            await self.audit_log.log(
                action="rollout_restart",
                target=name,
                namespace=namespace,
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def rollback_deployment(
        self, namespace: str, name: str, reason: str
    ) -> Dict[str, Any]:
        """Rollback a deployment to its previous ReplicaSet template."""
        if self._check_namespace_protected(namespace):
            return {"success": False, "error": f"Namespace {namespace} is protected"}

        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            # Find all ReplicaSets owned by this deployment
            rs_list = self.apps_v1.list_namespaced_replica_set(
                namespace,
                label_selector=",".join(
                    f"{k}={v}"
                    for k, v in (
                        self.apps_v1.read_namespaced_deployment(
                            name, namespace
                        ).spec.selector.match_labels
                        or {}
                    ).items()
                ),
            )

            # Filter to RS owned by this deployment and sort by revision
            owned_rs = []
            for rs in rs_list.items:
                for ref in rs.metadata.owner_references or []:
                    if ref.kind == "Deployment" and ref.name == name:
                        revision = int(
                            (rs.metadata.annotations or {}).get(
                                "deployment.kubernetes.io/revision", "0"
                            )
                        )
                        owned_rs.append((revision, rs))
                        break

            if len(owned_rs) < 2:
                return {
                    "success": False,
                    "error": "No previous revision found to rollback to",
                }

            # Sort by revision descending; second entry is the previous RS
            owned_rs.sort(key=lambda x: x[0], reverse=True)
            previous_rs = owned_rs[1][1]

            # Patch the deployment template to match the previous RS template
            patch = {
                "spec": {
                    "template": previous_rs.spec.template.to_dict(),
                }
            }
            self.apps_v1.patch_namespaced_deployment(name, namespace, patch)

            await self.rate_limiter.record_action(
                f"rollback_deployment:{namespace}/{name}"
            )
            await self.audit_log.log(
                action="rollback_deployment",
                target=name,
                namespace=namespace,
                reason=reason,
                result="success",
                details={"rolled_back_to_revision": owned_rs[1][0]},
            )
            logger.info(
                "Rolled back deployment", namespace=namespace, name=name, reason=reason
            )
            return {
                "success": True,
                "message": f"Deployment {name} rolled back to revision {owned_rs[1][0]}",
            }
        except ApiException as e:
            await self.audit_log.log(
                action="rollback_deployment",
                target=name,
                namespace=namespace,
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def rollout_restart_statefulset(
        self, namespace: str, name: str, reason: str
    ) -> Dict[str, Any]:
        """Trigger a rollout restart for a StatefulSet."""
        if self._check_namespace_protected(namespace):
            return {"success": False, "error": f"Namespace {namespace} is protected"}

        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "cluster-guardian/restartedAt": datetime.now(
                                    timezone.utc
                                ).isoformat()
                            }
                        }
                    }
                }
            }
            self.apps_v1.patch_namespaced_stateful_set(name, namespace, patch)
            await self.rate_limiter.record_action(
                f"rollout_restart_statefulset:{namespace}/{name}"
            )
            await self.audit_log.log(
                action="rollout_restart_statefulset",
                target=name,
                namespace=namespace,
                reason=reason,
                result="success",
            )
            logger.info(
                "Rollout restart statefulset",
                namespace=namespace,
                name=name,
                reason=reason,
            )
            return {
                "success": True,
                "message": f"StatefulSet {name} rollout restart triggered",
            }
        except ApiException as e:
            await self.audit_log.log(
                action="rollout_restart_statefulset",
                target=name,
                namespace=namespace,
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def delete_failed_job(
        self, namespace: str, name: str, reason: str
    ) -> Dict[str, Any]:
        """Delete a failed job to allow retry."""
        if self._check_namespace_protected(namespace):
            return {"success": False, "error": f"Namespace {namespace} is protected"}

        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            self.batch_v1.delete_namespaced_job(
                name,
                namespace,
                propagation_policy="Background",
            )
            await self.rate_limiter.record_action(
                f"delete_failed_job:{namespace}/{name}"
            )
            await self.audit_log.log(
                action="delete_failed_job",
                target=name,
                namespace=namespace,
                reason=reason,
                result="success",
            )
            logger.info(
                "Deleted failed job", namespace=namespace, name=name, reason=reason
            )
            return {"success": True, "message": f"Failed job {name} deleted"}
        except ApiException as e:
            await self.audit_log.log(
                action="delete_failed_job",
                target=name,
                namespace=namespace,
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def delete_pvc(
        self, namespace: str, name: str, reason: str
    ) -> Dict[str, Any]:
        """Delete a PVC (requires approval by default)."""
        if self._check_namespace_protected(namespace):
            return {"success": False, "error": f"Namespace {namespace} is protected"}

        if "delete_pvc" in settings.require_approval_for:
            return {
                "success": False,
                "error": "PVC deletion requires human approval",
                "requires_approval": True,
                "approval_action": f"delete_pvc:{namespace}/{name}",
            }

        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        try:
            self.core_v1.delete_namespaced_persistent_volume_claim(name, namespace)
            await self.rate_limiter.record_action(f"delete_pvc:{namespace}/{name}")
            await self.audit_log.log(
                action="delete_pvc",
                target=name,
                namespace=namespace,
                reason=reason,
                result="success",
            )
            return {"success": True, "message": f"PVC {name} deleted"}
        except ApiException as e:
            return {"success": False, "error": str(e)}

    async def cordon_node(self, name: str, reason: str) -> Dict[str, Any]:
        """Cordon a node by setting spec.unschedulable = True."""
        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        if "cordon_node" in settings.require_approval_for:
            return {
                "success": False,
                "error": "Cordoning a node requires human approval",
                "requires_approval": True,
                "approval_action": f"cordon_node:{name}",
            }

        try:
            self.core_v1.patch_node(name, {"spec": {"unschedulable": True}})
            await self.rate_limiter.record_action(f"cordon_node:{name}")
            await self.audit_log.log(
                action="cordon_node",
                target=name,
                namespace="cluster",
                reason=reason,
                result="success",
            )
            logger.info("Cordoned node", name=name, reason=reason)
            return {"success": True, "message": f"Node {name} cordoned"}
        except ApiException as e:
            await self.audit_log.log(
                action="cordon_node",
                target=name,
                namespace="cluster",
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def drain_node(self, name: str, reason: str) -> Dict[str, Any]:
        """Drain a node: cordon it, then evict all non-DaemonSet pods."""
        if not await self._check_rate_limit():
            return {"success": False, "error": "Rate limit exceeded"}

        if "drain_node" in settings.require_approval_for:
            return {
                "success": False,
                "error": "Draining a node requires human approval",
                "requires_approval": True,
                "approval_action": f"drain_node:{name}",
            }

        try:
            # Cordon the node first
            self.core_v1.patch_node(name, {"spec": {"unschedulable": True}})

            # Get all pods on this node
            pods = self.core_v1.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={name}",
            )

            evicted = []
            skipped = []
            for pod in pods.items:
                # Skip pods in protected namespaces
                if pod.metadata.namespace in settings.protected_namespaces:
                    skipped.append(
                        f"{pod.metadata.namespace}/{pod.metadata.name} (protected namespace)"
                    )
                    continue

                # Skip DaemonSet-owned pods
                owner_refs = pod.metadata.owner_references or []
                if any(ref.kind == "DaemonSet" for ref in owner_refs):
                    skipped.append(
                        f"{pod.metadata.namespace}/{pod.metadata.name} (DaemonSet)"
                    )
                    continue

                # Evict the pod
                eviction = client.V1Eviction(
                    metadata=client.V1ObjectMeta(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    ),
                )
                try:
                    self.core_v1.create_namespaced_pod_eviction(
                        pod.metadata.name,
                        pod.metadata.namespace,
                        eviction,
                    )
                    evicted.append(f"{pod.metadata.namespace}/{pod.metadata.name}")
                except ApiException as evict_err:
                    skipped.append(
                        f"{pod.metadata.namespace}/{pod.metadata.name} (eviction failed: {evict_err.reason})"
                    )

            await self.rate_limiter.record_action(f"drain_node:{name}")
            await self.audit_log.log(
                action="drain_node",
                target=name,
                namespace="cluster",
                reason=reason,
                result="success",
                details={"evicted": len(evicted), "skipped": len(skipped)},
            )
            logger.info(
                "Drained node", name=name, evicted=len(evicted), skipped=len(skipped)
            )
            return {
                "success": True,
                "message": f"Node {name} drained",
                "evicted": evicted,
                "skipped": skipped,
            }
        except ApiException as e:
            await self.audit_log.log(
                action="drain_node",
                target=name,
                namespace="cluster",
                reason=reason,
                result="failed",
                details={"error": str(e)},
            )
            return {"success": False, "error": str(e)}

    async def get_audit_log(self) -> List[Dict]:
        """Get recent audit log entries from Redis (with in-memory fallback)."""
        return await self.audit_log.get_recent(50)

    def get_rate_limit_status(self) -> Dict[str, int]:
        """Get current rate limit status."""
        return {
            "remaining_actions": self.rate_limiter.get_remaining(),
            "max_actions_per_hour": settings.max_actions_per_hour,
        }


# Global client instance
_k8s_client: Optional[K8sClient] = None


def get_k8s_client() -> K8sClient:
    """Get or create K8s client singleton."""
    global _k8s_client
    if _k8s_client is None:
        _k8s_client = K8sClient()
    return _k8s_client
