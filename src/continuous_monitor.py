"""
Continuous monitoring loop for Cluster Guardian.

Runs lightweight, LLM-free checks every 30 seconds and streams
Kubernetes Warning events in real time.  Anomalies are deduplicated,
batched, and dispatched to the LLM agent for investigation.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

import structlog

from .config import settings
from .config_store import get_config_store

logger = structlog.get_logger(__name__)


@dataclass
class AnomalySignal:
    """A detected anomaly from lightweight checks."""

    source: str  # "k8s_events", "ingress", "app_page", "prometheus", etc.
    severity: str  # "info", "warning", "critical"
    title: str
    details: str
    namespace: str
    resource: str
    dedupe_key: str


class ContinuousMonitor:
    """Fast-loop monitor that detects anomalies without invoking the LLM."""

    def __init__(
        self,
        k8s,
        prometheus,
        health_checker,
        ingress_monitor,
        config,
        self_tuner=None,
        loki=None,
        service_discovery=None,
        escalation_classifier=None,
    ):
        self._k8s = k8s
        self._prometheus = prometheus
        self._health_checker = health_checker
        self._ingress_monitor = ingress_monitor
        self._config = config
        self._self_tuner = self_tuner
        self._loki = loki
        self._service_discovery = service_discovery
        self._escalation_classifier = escalation_classifier

        self._anomaly_queue: asyncio.Queue[AnomalySignal] = asyncio.Queue()
        self._seen_keys: dict[str, float] = {}
        self._suppression_window = config.get("anomaly_suppression_window", 300)
        self._batch_window = config.get("anomaly_batch_window", 10)
        self._fast_loop_interval = config.get("fast_loop_interval_seconds", 30)
        self._event_watch_enabled = config.get("event_watch_enabled", True)

        self._investigate_callback: Optional[
            Callable[..., Coroutine[Any, Any, Any]]
        ] = None
        self._broadcast_callback: Optional[Callable[..., Coroutine[Any, Any, Any]]] = (
            None
        )

        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Tracking for the status endpoint
        self._last_fast_loop: float = 0.0
        self._last_event_watch: float = 0.0
        self._total_anomalies: int = 0
        self._suppressed_anomalies: int = 0

    def set_callbacks(
        self,
        investigate: Callable[..., Coroutine[Any, Any, Any]],
        broadcast: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None,
    ):
        self._investigate_callback = investigate
        self._broadcast_callback = broadcast

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Launch all monitoring coroutines concurrently."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._fast_loop()),
            asyncio.create_task(self._anomaly_dispatcher()),
        ]
        if self._event_watch_enabled:
            self._tasks.append(asyncio.create_task(self._event_watcher()))
        logger.info(
            "ContinuousMonitor started",
            fast_loop_interval=self._fast_loop_interval,
            event_watch=self._event_watch_enabled,
        )

    async def stop(self):
        """Cancel all monitoring tasks."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("ContinuousMonitor stopped")

    # ------------------------------------------------------------------
    # Fast loop
    # ------------------------------------------------------------------

    async def _fast_loop(self):
        """Every N seconds: poll lightweight checks."""
        while self._running:
            try:
                await self._refresh_interval()
                await asyncio.sleep(self._fast_loop_interval)
                self._last_fast_loop = time.time()

                signals = await asyncio.gather(
                    self._check_crashloop_pods(),
                    self._check_prometheus_alerts(),
                    self._check_ingress_health(),
                    self._check_daemonset_health(),
                    self._check_pvc_usage(),
                    self._check_gatus(),
                    self._check_log_anomalies(),
                    self._check_node_conditions(),
                    self._check_deployment_rollouts(),
                    return_exceptions=True,
                )

                for result in signals:
                    if isinstance(result, BaseException):
                        logger.warning("fast_loop check raised", error=str(result))
                        continue
                    if isinstance(result, list):
                        for sig in result:
                            await self._anomaly_queue.put(sig)

                # Self-tuner: adjust intervals based on cluster stability
                if self._self_tuner:
                    try:
                        await self._self_tuner.tune_intervals()
                    except Exception as exc:
                        logger.debug("self_tuner.tune_intervals failed", error=str(exc))

                # Service discovery: refresh periodically
                if self._service_discovery:
                    try:
                        interval_loops = settings.service_discovery_interval_loops
                        if self._service_discovery.should_refresh(interval_loops):
                            await self._service_discovery.refresh()
                    except Exception as exc:
                        logger.debug("service_discovery refresh failed", error=str(exc))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("fast_loop error", error=str(exc))

    async def _refresh_interval(self):
        """Re-read the fast loop interval from config store."""
        try:
            store = get_config_store()
            val = await store.get("fast_loop_interval_seconds")
            if isinstance(val, (int, float)) and val > 0:
                self._fast_loop_interval = int(val)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def _check_crashloop_pods(self) -> list[AnomalySignal]:
        pods = await self._k8s.get_crashloopbackoff_pods()
        return [
            AnomalySignal(
                source="k8s_crashloop",
                severity="critical",
                title=f"CrashLoopBackOff: {p['namespace']}/{p['name']}",
                details=f"Container {p['container']} has {p['restart_count']} restarts",
                namespace=p["namespace"],
                resource=p["name"],
                dedupe_key=f"crashloop:{p['namespace']}/{p['name']}/{p['container']}",
            )
            for p in pods
        ]

    async def _check_prometheus_alerts(self) -> list[AnomalySignal]:
        if not self._prometheus:
            return []
        alerts = await self._prometheus.get_alerts("firing")
        if not alerts or (len(alerts) == 1 and "error" in alerts[0]):
            return []
        return [
            AnomalySignal(
                source="prometheus",
                severity=a.get("severity", "warning"),
                title=f"Alert firing: {a['name']}",
                details=a.get("summary", a.get("description", "")),
                namespace=a.get("labels", {}).get("namespace", "cluster"),
                resource=a.get("labels", {}).get("pod", a["name"]),
                dedupe_key=f"prom_alert:{a['name']}:{a.get('labels', {}).get('namespace', '')}",
            )
            for a in alerts
        ]

    async def _check_ingress_health(self) -> list[AnomalySignal]:
        if not self._ingress_monitor:
            return []
        results = await self._ingress_monitor.check_all_ingress_routes()
        return [
            AnomalySignal(
                source="ingress",
                severity="warning" if r.get("status_code") else "critical",
                title=f"Ingress unhealthy: {r['namespace']}/{r['name']}",
                details=r.get("error", f"HTTP {r.get('status_code', '?')}"),
                namespace=r["namespace"],
                resource=r["name"],
                dedupe_key=f"ingress:{r['namespace']}/{r['name']}",
            )
            for r in results
            if not r.get("healthy", True)
        ]

    async def _check_daemonset_health(self) -> list[AnomalySignal]:
        if not self._ingress_monitor:
            return []
        ds_list = await self._ingress_monitor.check_daemonset_health()
        return [
            AnomalySignal(
                source="daemonset",
                severity="warning",
                title=f"DaemonSet degraded: {ds['namespace']}/{ds['name']}",
                details=f"desired={ds['desired']} ready={ds['ready']} unavailable={ds['unavailable']}",
                namespace=ds["namespace"],
                resource=ds["name"],
                dedupe_key=f"daemonset:{ds['namespace']}/{ds['name']}",
            )
            for ds in ds_list
            if ds.get("unavailable", 0) > 0
        ]

    async def _check_pvc_usage(self) -> list[AnomalySignal]:
        if not self._ingress_monitor:
            return []
        pvcs = await self._ingress_monitor.check_pvc_usage()
        return [
            AnomalySignal(
                source="pvc_usage",
                severity="warning" if pvc["usage_percent"] < 95 else "critical",
                title=f"PVC high usage: {pvc['namespace']}/{pvc['pvc']}",
                details=f"{pvc['usage_percent']:.1f}% used",
                namespace=pvc["namespace"],
                resource=pvc["pvc"],
                dedupe_key=f"pvc:{pvc['namespace']}/{pvc['pvc']}",
            )
            for pvc in pvcs
        ]

    async def _check_gatus(self) -> list[AnomalySignal]:
        try:
            from .gatus_client import get_gatus_client

            gatus = get_gatus_client()
            statuses = await gatus.get_endpoint_statuses()
            if not statuses:
                return []
            return [
                AnomalySignal(
                    source="gatus",
                    severity="warning",
                    title=f"Status page unhealthy: {s.get('group', '')}/{s['name']}",
                    details=f"uptime_7d={s.get('uptime_7d', '?')}%",
                    namespace=s.get("group", "unknown"),
                    resource=s["name"],
                    dedupe_key=f"gatus:{s.get('group', '')}/{s['name']}",
                )
                for s in statuses
                if not s.get("healthy", True)
            ]
        except Exception as exc:
            logger.debug("gatus check skipped", error=str(exc))
            return []

    async def _check_log_anomalies(self) -> list[AnomalySignal]:
        """Check Loki for cluster-wide error log spikes."""
        if not self._loki:
            return []
        try:
            window = settings.log_anomaly_window
            min_count = settings.log_anomaly_min_count
            summaries = await self._loki.get_cluster_error_summary(
                since=window, min_count=min_count
            )
            return [
                AnomalySignal(
                    source="loki_errors",
                    severity="warning" if s["count"] < min_count * 5 else "critical",
                    title=f"Log error spike: {s['namespace']}",
                    details=f"{s['count']} errors in last {window}",
                    namespace=s["namespace"],
                    resource="logs",
                    dedupe_key=f"loki_errors:{s['namespace']}",
                )
                for s in summaries
            ]
        except Exception as exc:
            logger.debug("log anomaly check failed", error=str(exc))
            return []

    async def _check_node_conditions(self) -> list[AnomalySignal]:
        """Check all nodes for unhealthy conditions."""
        signals = []
        try:
            nodes = self._k8s.core_v1.list_node()
            for node in nodes.items:
                name = node.metadata.name
                for condition in node.status.conditions or []:
                    # Ready=False is bad; pressure conditions True is bad
                    if condition.type == "Ready" and condition.status != "True":
                        signals.append(AnomalySignal(
                            source="node_condition",
                            severity="critical",
                            title=f"Node not ready: {name}",
                            details=f"Ready={condition.status}: {condition.message or ''}",
                            namespace="cluster",
                            resource=name,
                            dedupe_key=f"node:not_ready:{name}",
                        ))
                    elif condition.type in ("MemoryPressure", "DiskPressure", "PIDPressure"):
                        if condition.status == "True":
                            signals.append(AnomalySignal(
                                source="node_condition",
                                severity="warning",
                                title=f"Node {condition.type}: {name}",
                                details=condition.message or "",
                                namespace="cluster",
                                resource=name,
                                dedupe_key=f"node:{condition.type.lower()}:{name}",
                            ))
        except Exception as exc:
            logger.debug("node condition check failed", error=str(exc))
        return signals

    async def _check_deployment_rollouts(self) -> list[AnomalySignal]:
        """Detect deployments where available < desired or Progressing=False."""
        signals = []
        try:
            deployments = self._k8s.apps_v1.list_deployment_for_all_namespaces()
            for dep in deployments.items:
                ns = dep.metadata.namespace
                if ns in settings.protected_namespaces:
                    continue
                name = dep.metadata.name
                spec_replicas = dep.spec.replicas or 1
                available = dep.status.available_replicas or 0
                if available < spec_replicas:
                    signals.append(AnomalySignal(
                        source="deployment_rollout",
                        severity="warning",
                        title=f"Deployment degraded: {ns}/{name}",
                        details=f"available={available} desired={spec_replicas}",
                        namespace=ns,
                        resource=name,
                        dedupe_key=f"deployment:{ns}/{name}",
                    ))

                # Check Progressing condition
                for condition in dep.status.conditions or []:
                    if condition.type == "Progressing" and condition.status == "False":
                        signals.append(AnomalySignal(
                            source="deployment_rollout",
                            severity="critical",
                            title=f"Deployment rollout stalled: {ns}/{name}",
                            details=condition.message or "Progressing=False",
                            namespace=ns,
                            resource=name,
                            dedupe_key=f"deployment_stalled:{ns}/{name}",
                        ))
        except Exception as exc:
            logger.debug("deployment rollout check failed", error=str(exc))
        return signals

    # ------------------------------------------------------------------
    # Event watcher
    # ------------------------------------------------------------------

    async def _event_watcher(self):
        """K8s watch API stream for Warning/Error events."""
        from kubernetes import watch

        while self._running:
            try:
                w = watch.Watch()
                self._last_event_watch = time.time()
                for event in w.stream(
                    self._k8s.core_v1.list_event_for_all_namespaces,
                    timeout_seconds=300,
                ):
                    if not self._running:
                        w.stop()
                        break

                    obj = event.get("object")
                    if obj is None:
                        continue

                    if obj.type not in ("Warning", "Error"):
                        continue

                    ns = obj.metadata.namespace or "cluster"
                    if ns in settings.protected_namespaces:
                        continue

                    involved = ""
                    if obj.involved_object:
                        involved = (
                            f"{obj.involved_object.kind}/{obj.involved_object.name}"
                        )

                    signal = AnomalySignal(
                        source="k8s_events",
                        severity="warning" if obj.type == "Warning" else "critical",
                        title=f"K8s event: {obj.reason}",
                        details=obj.message or "",
                        namespace=ns,
                        resource=involved,
                        dedupe_key=f"k8s_event:{ns}/{involved}/{obj.reason}",
                    )
                    await self._anomaly_queue.put(signal)
                    self._last_event_watch = time.time()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("event_watcher reconnecting", error=str(exc))
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Anomaly dispatcher
    # ------------------------------------------------------------------

    async def _anomaly_dispatcher(self):
        """Consumes anomaly queue, deduplicates, and triggers investigation."""
        batch: list[AnomalySignal] = []
        batch_start: float = 0.0

        while self._running:
            try:
                try:
                    signal = await asyncio.wait_for(
                        self._anomaly_queue.get(), timeout=self._batch_window
                    )
                    self._total_anomalies += 1

                    # Deduplicate
                    now = time.time()
                    last_seen = self._seen_keys.get(signal.dedupe_key, 0.0)
                    if now - last_seen < self._suppression_window:
                        self._suppressed_anomalies += 1
                        continue

                    self._seen_keys[signal.dedupe_key] = now

                    if not batch:
                        batch_start = now
                    batch.append(signal)

                except asyncio.TimeoutError:
                    pass

                # Flush batch if window elapsed
                if batch and (time.time() - batch_start >= self._batch_window):
                    await self._dispatch_batch(batch)
                    batch = []

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("anomaly_dispatcher error", error=str(exc))
                batch = []

    async def _dispatch_batch(self, batch: list[AnomalySignal]):
        """Send a batch of anomalies to the investigation callback."""
        if not batch:
            return

        # Record issues in self-tuner
        if self._self_tuner:
            for sig in batch:
                try:
                    await self._self_tuner.record_issue(
                        sig.dedupe_key, f"auto-detected:{sig.source}", True
                    )
                except Exception:
                    pass

        # Group by namespace/resource
        groups: dict[str, list[AnomalySignal]] = {}
        for sig in batch:
            key = f"{sig.namespace}/{sig.resource}"
            groups.setdefault(key, []).append(sig)

        for group_key, signals in groups.items():
            # Classify escalation level
            escalation_level = None
            if self._escalation_classifier:
                for sig in signals:
                    issue_counts = (
                        self._self_tuner._issue_counts if self._self_tuner else None
                    )
                    level = self._escalation_classifier.classify(
                        source=sig.source,
                        severity=sig.severity,
                        title=sig.title,
                        details=sig.details,
                        dedupe_key=sig.dedupe_key,
                        issue_counts=issue_counts,
                    )
                    if escalation_level is None or level.value > escalation_level.value:
                        escalation_level = level

            # Build investigation description
            lines = [f"Continuous monitor detected anomalies for {group_key}:"]
            highest_severity = "info"
            for sig in signals:
                lines.append(f"- [{sig.source}] {sig.title}: {sig.details}")
                if sig.severity == "critical":
                    highest_severity = "critical"
                elif sig.severity == "warning" and highest_severity != "critical":
                    highest_severity = "warning"

            if escalation_level:
                lines.append(f"\nEscalation classification: {escalation_level.value}")

            description = "\n".join(lines)

            # Broadcast to WebSocket clients
            if self._broadcast_callback:
                try:
                    await self._broadcast_callback(
                        {
                            "type": "anomaly_detected",
                            "data": {
                                "group": group_key,
                                "severity": highest_severity,
                                "escalation": escalation_level.value if escalation_level else None,
                                "signals": [
                                    {
                                        "source": s.source,
                                        "severity": s.severity,
                                        "title": s.title,
                                        "namespace": s.namespace,
                                        "resource": s.resource,
                                    }
                                    for s in signals
                                ],
                            },
                        }
                    )
                except Exception:
                    pass

            # Auto-escalate LONG_TERM issues to dev controller
            if escalation_level and escalation_level.value == "long_term":
                if self._self_tuner and self._self_tuner._dev_controller:
                    try:
                        await self._self_tuner.auto_escalate(
                            group_key, description
                        )
                        logger.info(
                            "Auto-escalated long-term issue",
                            group=group_key,
                        )
                        continue  # Skip LLM investigation for auto-escalated
                    except Exception as exc:
                        logger.warning(
                            "Auto-escalation failed, falling back to LLM",
                            error=str(exc),
                        )

            # Trigger investigation
            if self._investigate_callback:
                try:
                    asyncio.ensure_future(
                        self._investigate_callback(
                            description=description,
                            thread_id=f"cm-{group_key.replace('/', '-')}",
                        )
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to trigger investigation",
                        group=group_key,
                        error=str(exc),
                    )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return monitor health and stats."""
        status = {
            "running": self._running,
            "fast_loop_interval": self._fast_loop_interval,
            "last_fast_loop": self._last_fast_loop,
            "last_event_watch": self._last_event_watch,
            "anomaly_queue_depth": self._anomaly_queue.qsize(),
            "total_anomalies": self._total_anomalies,
            "suppressed_anomalies": self._suppressed_anomalies,
            "suppression_window": self._suppression_window,
            "tracked_dedupe_keys": len(self._seen_keys),
            "checks": [
                "crashloop_pods",
                "prometheus_alerts",
                "ingress_health",
                "daemonset_health",
                "pvc_usage",
                "gatus",
                "log_anomalies",
                "node_conditions",
                "deployment_rollouts",
            ],
        }
        if self._service_discovery:
            status["discovered_services"] = len(
                self._service_discovery.get_discovered()
            )
        if self._escalation_classifier:
            status["escalation_classifier"] = self._escalation_classifier.get_stats()
        return status

    def get_recent_anomalies(self) -> list[dict[str, Any]]:
        """Return currently tracked dedupe keys with timestamps."""
        now = time.time()
        return [
            {
                "dedupe_key": key,
                "last_seen": ts,
                "age_seconds": round(now - ts, 1),
                "suppressed": (now - ts) < self._suppression_window,
            }
            for key, ts in sorted(
                self._seen_keys.items(), key=lambda x: x[1], reverse=True
            )[:100]
        ]

    def cleanup_stale_keys(self):
        """Purge dedupe keys older than 2x suppression window."""
        cutoff = time.time() - (self._suppression_window * 2)
        stale = [k for k, ts in self._seen_keys.items() if ts < cutoff]
        for k in stale:
            del self._seen_keys[k]
