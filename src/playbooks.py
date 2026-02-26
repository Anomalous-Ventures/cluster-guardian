"""
Remediation playbooks for common Kubernetes failure patterns.

Provides structured, auditable remediation sequences instead of ad-hoc LLM
actions.  Each playbook defines match rules (to select it) and ordered steps
(tools + args templates) that the agent can execute.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class Operator(str, Enum):
    EQUALS = "equals"
    CONTAINS = "contains"
    REGEX = "regex"


@dataclass
class MatchRule:
    """Predicate evaluated against alert/issue labels."""

    field: str
    operator: Operator
    value: str

    def matches(self, data: dict[str, Any]) -> bool:
        actual = str(data.get(self.field, ""))
        if self.operator == Operator.EQUALS:
            return actual == self.value
        if self.operator == Operator.CONTAINS:
            return self.value in actual
        if self.operator == Operator.REGEX:
            return bool(re.search(self.value, actual))
        return False


@dataclass
class PlaybookStep:
    """A single step in a playbook execution plan."""

    name: str
    tool: str
    args_template: dict[str, str] = field(default_factory=dict)
    condition: Optional[str] = None
    requires_approval: bool = False

    def render_args(self, context: dict[str, Any]) -> dict[str, Any]:
        """Render Jinja2-style {{var}} placeholders from context."""
        rendered = {}
        for key, template in self.args_template.items():
            value = template
            for ctx_key, ctx_val in context.items():
                value = value.replace("{{" + ctx_key + "}}", str(ctx_val))
            rendered[key] = value
        return rendered


@dataclass
class Playbook:
    """A structured remediation sequence for a known failure pattern."""

    id: str
    name: str
    description: str
    match_rules: list[MatchRule] = field(default_factory=list)
    steps: list[PlaybookStep] = field(default_factory=list)
    severity: str = "warning"
    max_auto_executions: int = 3

    def matches(self, data: dict[str, Any]) -> bool:
        """Return True if ALL match rules are satisfied."""
        if not self.match_rules:
            return False
        return all(rule.matches(data) for rule in self.match_rules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "severity": self.severity,
            "steps": [
                {
                    "name": s.name,
                    "tool": s.tool,
                    "args_template": s.args_template,
                    "condition": s.condition,
                    "requires_approval": s.requires_approval,
                }
                for s in self.steps
            ],
        }

    def render_prompt(self, context: dict[str, Any]) -> str:
        """Render the playbook as structured instructions for the agent."""
        lines = [
            f"## Playbook: {self.name}",
            f"**Severity:** {self.severity}",
            f"**Description:** {self.description}",
            "",
            "### Steps:",
        ]
        for i, step in enumerate(self.steps, 1):
            args = step.render_args(context)
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            approval = " [REQUIRES APPROVAL]" if step.requires_approval else ""
            cond = f" (if {step.condition})" if step.condition else ""
            lines.append(
                f"{i}. **{step.name}**: `{step.tool}({args_str})`{approval}{cond}"
            )
        return "\n".join(lines)


# =============================================================================
# BUILT-IN PLAYBOOKS
# =============================================================================

BUILTIN_PLAYBOOKS: list[Playbook] = [
    # 1. CrashLoopBackOff
    Playbook(
        id="crashloop",
        name="CrashLoopBackOff Recovery",
        description="Investigate and recover a pod stuck in CrashLoopBackOff.",
        severity="critical",
        match_rules=[
            MatchRule(field="alertname", operator=Operator.CONTAINS, value="CrashLoop"),
        ],
        steps=[
            PlaybookStep(
                name="Get pod logs",
                tool="get_pod_k8s_logs",
                args_template={
                    "namespace": "{{namespace}}",
                    "pod_name": "{{pod}}",
                    "previous": "true",
                },
            ),
            PlaybookStep(
                name="Check recent events",
                tool="get_recent_events",
                args_template={"namespace": "{{namespace}}", "object_name": "{{pod}}"},
            ),
            PlaybookStep(
                name="Check memory usage",
                tool="get_pod_memory_usage",
                args_template={"namespace": "{{namespace}}", "pod_name": "{{pod}}"},
            ),
            PlaybookStep(
                name="Rollout restart deployment",
                tool="rollout_restart_deployment",
                args_template={
                    "namespace": "{{namespace}}",
                    "deployment": "{{workload}}",
                    "reason": "CrashLoopBackOff recovery",
                },
                condition="restart_count > 5",
            ),
        ],
    ),
    # 2. OOMKilled
    Playbook(
        id="oomkilled",
        name="OOMKilled Recovery",
        description="Investigate OOMKilled pods and create PR for memory limit increase.",
        severity="critical",
        match_rules=[
            MatchRule(field="alertname", operator=Operator.CONTAINS, value="OOMKilled"),
        ],
        steps=[
            PlaybookStep(
                name="Check memory usage history",
                tool="get_pod_memory_usage",
                args_template={"namespace": "{{namespace}}", "pod_name": "{{pod}}"},
            ),
            PlaybookStep(
                name="Get pod details",
                tool="get_pod_details",
                args_template={"namespace": "{{namespace}}", "pod_name": "{{pod}}"},
            ),
            PlaybookStep(
                name="Create remediation PR",
                tool="create_remediation_pr",
                args_template={
                    "title": "fix({{namespace}}): increase memory limits for {{workload}}",
                    "description": "Pod {{pod}} was OOMKilled. Increasing memory limits.",
                    "file_path": "k8s/{{namespace}}/{{workload}}.yaml",
                    "content": "# Memory limit increase needed for {{workload}}",
                    "reason": "OOMKilled recovery",
                },
                condition="memory_usage_percent > 90",
            ),
        ],
    ),
    # 3. Node NotReady
    Playbook(
        id="node-not-ready",
        name="Node NotReady Investigation",
        description="Investigate a node in NotReady state.",
        severity="critical",
        match_rules=[
            MatchRule(
                field="alertname", operator=Operator.EQUALS, value="KubeNodeNotReady"
            ),
        ],
        steps=[
            PlaybookStep(
                name="Get node status",
                tool="get_node_status",
                args_template={"node_name": "{{node}}"},
            ),
            PlaybookStep(
                name="Check node resource usage",
                tool="query_prometheus",
                args_template={
                    "promql": "node_memory_MemAvailable_bytes{instance=~'{{node}}.*'}"
                },
            ),
            PlaybookStep(
                name="Cordon node",
                tool="cordon_node",
                args_template={"node_name": "{{node}}", "reason": "Node NotReady"},
                requires_approval=True,
            ),
            PlaybookStep(
                name="Notify team",
                tool="notify_slack",
                args_template={
                    "message": "Node {{node}} is NotReady. Investigation in progress.",
                    "severity": "critical",
                },
            ),
        ],
    ),
    # 4. Certificate Expiring
    Playbook(
        id="cert-expiring",
        name="Certificate Expiry Remediation",
        description="Check and renew expiring TLS certificates.",
        severity="warning",
        match_rules=[
            MatchRule(field="alertname", operator=Operator.CONTAINS, value="Cert"),
        ],
        steps=[
            PlaybookStep(
                name="Check all certificates",
                tool="check_certificates",
                args_template={"namespace": "{{namespace}}"},
            ),
            PlaybookStep(
                name="Get certificate details",
                tool="get_all_certificates",
                args_template={"namespace": "{{namespace}}"},
            ),
            PlaybookStep(
                name="Notify team",
                tool="notify_slack",
                args_template={
                    "message": "Certificate expiring in {{namespace}}. Review required.",
                    "severity": "warning",
                },
            ),
        ],
    ),
    # 5. Volume Degraded
    Playbook(
        id="volume-degraded",
        name="Volume Degraded Investigation",
        description="Investigate degraded Longhorn volumes.",
        severity="warning",
        match_rules=[
            MatchRule(field="alertname", operator=Operator.CONTAINS, value="Volume"),
        ],
        steps=[
            PlaybookStep(
                name="Get degraded volumes",
                tool="get_degraded_volumes",
                args_template={},
            ),
            PlaybookStep(
                name="Get volume details",
                tool="get_volume_detail",
                args_template={"volume_name": "{{volume}}"},
                condition="volume is known",
            ),
            PlaybookStep(
                name="Check node disk status",
                tool="list_nodes",
                args_template={},
            ),
            PlaybookStep(
                name="Notify team",
                tool="notify_slack",
                args_template={
                    "message": "Degraded volume detected. Node disk status reviewed.",
                    "severity": "warning",
                },
            ),
        ],
    ),
    # 6. High Error Rate
    Playbook(
        id="high-error-rate",
        name="High Error Rate Investigation",
        description="Investigate high HTTP error rates for a service.",
        severity="warning",
        match_rules=[
            MatchRule(field="alertname", operator=Operator.CONTAINS, value="ErrorRate"),
        ],
        steps=[
            PlaybookStep(
                name="Check error rate",
                tool="get_service_error_rate",
                args_template={"namespace": "{{namespace}}", "service": "{{service}}"},
            ),
            PlaybookStep(
                name="Search error logs",
                tool="get_namespace_error_logs",
                args_template={"namespace": "{{namespace}}", "since": "30m"},
            ),
            PlaybookStep(
                name="Check recent deployments",
                tool="get_recent_events",
                args_template={"namespace": "{{namespace}}"},
            ),
        ],
    ),
    # 7. Failed Jobs
    Playbook(
        id="failed-jobs",
        name="Failed Job Cleanup",
        description="Investigate and clean up failed Kubernetes jobs.",
        severity="info",
        match_rules=[
            MatchRule(field="alertname", operator=Operator.CONTAINS, value="JobFailed"),
        ],
        steps=[
            PlaybookStep(
                name="List failed jobs",
                tool="get_failed_jobs",
                args_template={"namespace": "{{namespace}}"},
            ),
            PlaybookStep(
                name="Get job details and logs",
                tool="get_pod_k8s_logs",
                args_template={"namespace": "{{namespace}}", "pod_name": "{{pod}}"},
                condition="pod is known",
            ),
            PlaybookStep(
                name="Delete and retry",
                tool="delete_failed_job",
                args_template={
                    "namespace": "{{namespace}}",
                    "name": "{{job}}",
                    "reason": "Failed job cleanup",
                },
                condition="job is transient",
            ),
        ],
    ),
]


# =============================================================================
# PLAYBOOK EXECUTOR
# =============================================================================


class PlaybookExecutor:
    """Matches alerts to playbooks and renders execution plans."""

    def __init__(self, playbooks: list[Playbook] | None = None):
        self.playbooks = playbooks or list(BUILTIN_PLAYBOOKS)
        self._execution_counts: dict[str, int] = {}

    def match(self, alert_data: dict[str, Any]) -> Playbook | None:
        """Find the first playbook whose rules match the alert data."""
        for pb in self.playbooks:
            if pb.matches(alert_data):
                logger.info("playbook_matched", playbook=pb.id, alert_data=alert_data)
                return pb
        return None

    def get_playbook(self, playbook_id: str) -> Playbook | None:
        """Look up a playbook by ID."""
        for pb in self.playbooks:
            if pb.id == playbook_id:
                return pb
        return None

    def list_playbooks(self) -> list[dict[str, Any]]:
        """Return summary of all available playbooks."""
        return [pb.to_dict() for pb in self.playbooks]

    def render_for_agent(self, alert_data: dict[str, Any]) -> str | None:
        """Match alert data and render playbook instructions for the agent.

        Returns None if no playbook matches.
        """
        pb = self.match(alert_data)
        if pb is None:
            return None

        # Extract context from alert labels
        labels = alert_data.get("labels", alert_data)
        context = {
            "namespace": labels.get("namespace", "default"),
            "pod": labels.get("pod", ""),
            "workload": (
                labels.get("deployment", "")
                or labels.get("statefulset", "")
                or labels.get("daemonset", "")
                or labels.get("job", "")
            ),
            "node": labels.get("node", labels.get("instance", "")),
            "service": labels.get("service", ""),
            "alertname": labels.get("alertname", ""),
            "volume": labels.get("persistentvolumeclaim", ""),
            "job": labels.get("job_name", labels.get("job", "")),
        }

        count = self._execution_counts.get(pb.id, 0)
        if count >= pb.max_auto_executions:
            logger.warning(
                "playbook_max_executions_reached",
                playbook=pb.id,
                count=count,
            )
            return (
                f"Playbook '{pb.name}' matched but has reached max auto-executions "
                f"({pb.max_auto_executions}). Manual investigation required."
            )

        self._execution_counts[pb.id] = count + 1
        return pb.render_prompt(context)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_executor: Optional[PlaybookExecutor] = None


def get_playbook_executor() -> PlaybookExecutor:
    """Get or create the PlaybookExecutor singleton."""
    global _executor
    if _executor is None:
        _executor = PlaybookExecutor()
    return _executor
