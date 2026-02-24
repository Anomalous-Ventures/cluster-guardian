"""
Incident correlation engine for Cluster Guardian.

Groups related alerts (e.g. OOMKilled + CrashLoopBackOff + high memory for the
same pod) into a single Incident, preventing duplicate investigations and
conflicting remediation actions.
"""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Alerts that are semantically related and should be grouped together.
RELATED_ALERT_GROUPS: list[set[str]] = [
    {"KubePodCrashLooping", "KubePodNotReady", "KubeContainerWaiting"},
    {"KubeNodeNotReady", "KubeNodeUnreachable", "KubeNodePressure"},
    {"KubeDeploymentReplicasMismatch", "KubeStatefulSetReplicasMismatch"},
    {"KubePersistentVolumeFillingUp", "KubePersistentVolumeErrors"},
    {"CPUThrottlingHigh", "KubeContainerOOMKilled"},
]

DEFAULT_CORRELATION_WINDOW_SECONDS = 300
DEFAULT_DEBOUNCE_SECONDS = 30
DEFAULT_EXPIRY_SECONDS = 3600


def _alerts_related(a: str, b: str) -> bool:
    """Return True if two alert names belong to the same related group."""
    for group in RELATED_ALERT_GROUPS:
        if a in group and b in group:
            return True
    return False


@dataclass
class Incident:
    """A group of correlated alerts treated as a single investigation unit."""

    id: str
    correlation_key: str
    alerts: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_alert_at: float = field(default_factory=time.time)
    investigated: bool = False

    def add_alert(self, alert: dict) -> None:
        self.alerts.append(alert)
        self.last_alert_at = time.time()

    @property
    def alert_names(self) -> set[str]:
        return {
            a.get("alertname", a.get("labels", {}).get("alertname", ""))
            for a in self.alerts
        }

    def description(self) -> str:
        """Build a combined prompt describing all correlated alerts."""
        parts = [f"Correlated incident {self.id} ({len(self.alerts)} alerts):"]
        seen = set()
        for alert in self.alerts:
            labels = alert.get("labels", {})
            alertname = labels.get("alertname", alert.get("alertname", "unknown"))
            ns = labels.get("namespace", "")
            pod = labels.get("pod", "")
            desc = alert.get("annotations", {}).get("description", "")
            key = (alertname, ns, pod)
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"  - [{alertname}] namespace={ns} pod={pod}: {desc}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "correlation_key": self.correlation_key,
            "alerts": self.alerts,
            "alert_count": len(self.alerts),
            "alert_names": sorted(self.alert_names),
            "created_at": datetime.fromtimestamp(
                self.created_at, tz=timezone.utc
            ).isoformat(),
            "last_alert_at": datetime.fromtimestamp(
                self.last_alert_at, tz=timezone.utc
            ).isoformat(),
            "investigated": self.investigated,
            "description": self.description(),
        }


def _correlation_key(alert: dict) -> str:
    """Derive a correlation key from an alert.

    Groups by (namespace, controller_name) for workload alerts or (node)
    for node-level alerts.
    """
    labels = alert.get("labels", {})
    ns = labels.get("namespace", "")
    # Try workload-level grouping first
    workload = (
        labels.get("deployment", "")
        or labels.get("statefulset", "")
        or labels.get("daemonset", "")
        or labels.get("job", "")
        or labels.get("pod", "")
    )
    if workload:
        return f"{ns}/{workload}"
    # Fall back to node-level
    node = labels.get("node", labels.get("instance", ""))
    if node:
        return f"node/{node}"
    # Generic fallback: group by alertname + namespace
    alertname = labels.get("alertname", "unknown")
    return f"{ns}/{alertname}"


def _incident_id(key: str) -> str:
    """Generate a short deterministic incident ID from the correlation key."""
    h = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"inc-{h}"


class IncidentCorrelator:
    """Groups incoming alerts into Incidents using temporal and semantic correlation.

    An alert correlates with an existing incident if:
    1. It shares the same correlation key (namespace + workload), AND
    2. It arrived within the correlation window (default 300s), OR
    3. Its alertname is semantically related to an existing alert in the incident.
    """

    def __init__(
        self,
        window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS,
        debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
        expiry_seconds: int = DEFAULT_EXPIRY_SECONDS,
    ):
        self.window_seconds = window_seconds
        self.debounce_seconds = debounce_seconds
        self.expiry_seconds = expiry_seconds
        self._incidents: dict[str, Incident] = {}
        self._debounce_tasks: dict[str, asyncio.Task] = {}
        self._investigation_callback = None

    def set_investigation_callback(self, callback):
        """Set the async callback invoked when a debounced incident fires.

        The callback receives (description: str, thread_id: str).
        """
        self._investigation_callback = callback

    def correlate(self, alert: dict) -> Incident:
        """Add an alert to an existing or new incident."""
        key = _correlation_key(alert)
        alertname = alert.get("labels", {}).get("alertname", "")
        now = time.time()

        # Check for an existing incident on the same key within window
        incident = self._incidents.get(key)
        if incident and (now - incident.last_alert_at) < self.window_seconds:
            incident.add_alert(alert)
            logger.info(
                "alert_correlated",
                incident_id=incident.id,
                alertname=alertname,
                key=key,
                alert_count=len(incident.alerts),
            )
            return incident

        # Check for related alertname on a nearby key (different alert types only)
        if alertname:
            for existing_key, existing_incident in self._incidents.items():
                if (now - existing_incident.last_alert_at) >= self.window_seconds:
                    continue
                for existing_name in existing_incident.alert_names:
                    if existing_name != alertname and _alerts_related(
                        alertname, existing_name
                    ):
                        existing_incident.add_alert(alert)
                        logger.info(
                            "alert_correlated_by_relation",
                            incident_id=existing_incident.id,
                            alertname=alertname,
                            related_to=existing_name,
                        )
                        return existing_incident

        # Create a new incident
        incident_id = _incident_id(f"{key}-{now}")
        incident = Incident(id=incident_id, correlation_key=key, alerts=[alert])
        self._incidents[key] = incident
        logger.info(
            "incident_created",
            incident_id=incident_id,
            key=key,
            alertname=alertname,
        )
        return incident

    async def schedule_investigation(self, incident: Incident) -> None:
        """Schedule a debounced investigation for an incident.

        If a new alert arrives for the same incident within debounce_seconds,
        the timer resets so the investigation sees all correlated alerts.
        """
        key = incident.correlation_key

        # Cancel existing debounce timer for this incident
        existing_task = self._debounce_tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        async def _debounced():
            await asyncio.sleep(self.debounce_seconds)
            if not incident.investigated and self._investigation_callback:
                incident.investigated = True
                description = incident.description()
                thread_id = f"incident-{incident.id}"
                logger.info(
                    "incident_investigation_triggered",
                    incident_id=incident.id,
                    alert_count=len(incident.alerts),
                )
                try:
                    await self._investigation_callback(description, thread_id)
                except Exception as exc:
                    logger.error(
                        "incident_investigation_failed",
                        incident_id=incident.id,
                        error=str(exc),
                    )

        self._debounce_tasks[key] = asyncio.create_task(_debounced())

    def get_active_incidents(self) -> list[Incident]:
        """Return incidents within the expiry window."""
        now = time.time()
        return [
            inc
            for inc in self._incidents.values()
            if (now - inc.last_alert_at) < self.expiry_seconds
        ]

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Look up an incident by ID."""
        for inc in self._incidents.values():
            if inc.id == incident_id:
                return inc
        return None

    def expire_old(self) -> int:
        """Remove incidents older than expiry_seconds. Returns count removed."""
        now = time.time()
        to_remove = [
            key
            for key, inc in self._incidents.items()
            if (now - inc.last_alert_at) >= self.expiry_seconds
        ]
        for key in to_remove:
            task = self._debounce_tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            del self._incidents[key]
        if to_remove:
            logger.info("incidents_expired", count=len(to_remove))
        return len(to_remove)

    def to_dict_list(self) -> list[dict]:
        """Serialize all active incidents."""
        return [inc.to_dict() for inc in self.get_active_incidents()]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_correlator: Optional[IncidentCorrelator] = None


def get_correlator() -> IncidentCorrelator:
    """Get or create the IncidentCorrelator singleton."""
    global _correlator
    if _correlator is None:
        _correlator = IncidentCorrelator()
    return _correlator
