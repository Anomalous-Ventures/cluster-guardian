"""
FastAPI application for Cluster Guardian.

Provides:
- REST API for triggering scans and investigations
- AlertManager webhook receiver
- Health and status endpoints
- WebSocket for real-time updates
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    HTTPException,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
    APIRouter,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import structlog

from . import __version__
from .config import settings
from .agent import get_guardian, ClusterGuardian
from .k8s_client import get_k8s_client
from .k8sgpt_client import get_k8sgpt_client
from .health_checks import get_health_checker
from .metrics import (
    metrics_middleware,
    get_metrics_response,
    guardian_scans_total,
    guardian_scan_duration_seconds,
    guardian_issues_detected_total,
    guardian_active_websockets,
    guardian_health_check_status,
    guardian_info,
)
from .redis_client import get_redis_client
from .memory import get_memory
from .security_client import get_falco_processor
from .config_store import get_config_store
from .incident_correlator import get_correlator
from .log_proxy import log_router
from .ingress_monitor import get_ingress_monitor
from .dev_controller_client import get_dev_controller
from .self_tuner import get_self_tuner
from .loki_client import get_loki_client
from .service_discovery import get_service_discovery
from .escalation_classifier import EscalationClassifier

logger = structlog.get_logger(__name__)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class InvestigateRequest(BaseModel):
    """Request to investigate a specific issue."""

    description: str = Field(..., description="Description of the issue to investigate")
    thread_id: Optional[str] = Field(
        None, description="Thread ID for conversation continuity"
    )


class ScanResponse(BaseModel):
    """Response from a cluster scan."""

    success: bool
    summary: str
    audit_log: List[Dict[str, Any]]
    rate_limit: Dict[str, int]
    timestamp: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: str
    components: Dict[str, str]


class AlertManagerWebhook(BaseModel):
    """AlertManager webhook payload."""

    version: str = "4"
    groupKey: str
    status: str
    receiver: str
    groupLabels: Dict[str, str]
    commonLabels: Dict[str, str]
    commonAnnotations: Dict[str, str]
    externalURL: str
    alerts: List[Dict[str, Any]]


class FalcoWebhook(BaseModel):
    """Falco webhook alert payload."""

    uuid: Optional[str] = None
    output: str = ""
    priority: str = ""
    rule: str = ""
    time: str = ""
    output_fields: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    tags: Optional[List[str]] = None


class ConfigResetRequest(BaseModel):
    """Request to reset a config key to its default."""

    key: str = Field(..., description="Configuration key to reset to default")


class ConnectionStatus(BaseModel):
    """Status of an external service connection."""

    name: str
    status: str = Field(..., description="connected, disconnected, or error")
    last_checked: str


class ApprovalAction(BaseModel):
    """A pending approval action."""

    id: str
    action: str
    description: str
    namespace: str = ""
    timestamp: str
    status: str = Field("pending", description="pending, approved, or rejected")


# =============================================================================
# APPLICATION STATE
# =============================================================================


class AppState:
    """Global application state."""

    def __init__(self):
        self.guardian: Optional[ClusterGuardian] = None
        self.scan_task: Optional[asyncio.Task] = None
        self.continuous_monitor = None
        self.last_scan_result: Optional[Dict[str, Any]] = None
        self.websocket_connections: List[WebSocket] = []
        self.pending_approvals: List[Dict[str, Any]] = []


app_state = AppState()


# =============================================================================
# LIFECYCLE
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management."""
    logger.info("Starting Cluster Guardian", host=settings.host, port=settings.port)

    # Connect optional services (Redis, Qdrant)
    redis = get_redis_client()
    await redis.connect()
    memory = get_memory()
    await memory.connect()

    # Load persisted approvals from Redis
    try:
        persisted = await redis.get_pending_approvals()
        if persisted:
            app_state.pending_approvals = persisted
            logger.info("Loaded pending approvals from Redis", count=len(persisted))
    except Exception as exc:
        logger.warning("Failed to load pending approvals from Redis", error=str(exc))

    # Load last scan result from Redis
    try:
        last_scan = await redis.get_last_scan()
        if last_scan:
            app_state.last_scan_result = last_scan
            logger.info("Loaded last scan result from Redis")
    except Exception as exc:
        logger.warning("Failed to load last scan from Redis", error=str(exc))

    # Initialize guardian
    app_state.guardian = get_guardian()
    logger.info("Guardian initialized")

    # Start periodic scan task
    app_state.scan_task = asyncio.create_task(periodic_scan_loop())

    # Start continuous monitor
    try:
        from .continuous_monitor import ContinuousMonitor

        cm_config = {
            "fast_loop_interval_seconds": settings.fast_loop_interval_seconds,
            "event_watch_enabled": settings.event_watch_enabled,
            "anomaly_suppression_window": settings.anomaly_suppression_window,
            "anomaly_batch_window": settings.anomaly_batch_window,
        }
        # Wire optional v1.0 components (non-fatal if they fail)
        self_tuner = None
        loki = None
        sd = None
        classifier = None
        try:
            self_tuner = get_self_tuner()
        except Exception as exc:
            logger.warning("Failed to init self_tuner", error=str(exc))
        try:
            loki = get_loki_client()
        except Exception as exc:
            logger.warning("Failed to init loki client", error=str(exc))
        try:
            if settings.service_discovery_enabled:
                sd = get_service_discovery(
                    k8s=app_state.guardian.k8s,
                    health_checker=app_state.guardian.health_checker,
                )
        except Exception as exc:
            logger.warning("Failed to init service discovery", error=str(exc))
        try:
            classifier = EscalationClassifier(
                recurring_threshold=settings.escalation_threshold,
            )
        except Exception as exc:
            logger.warning("Failed to init escalation classifier", error=str(exc))

        app_state.continuous_monitor = ContinuousMonitor(
            k8s=app_state.guardian.k8s,
            prometheus=app_state.guardian.prometheus,
            health_checker=app_state.guardian.health_checker,
            ingress_monitor=get_ingress_monitor(),
            config=cm_config,
            self_tuner=self_tuner,
            loki=loki,
            service_discovery=sd,
            escalation_classifier=classifier,
        )
        app_state.continuous_monitor.set_callbacks(
            investigate=app_state.guardian.investigate_issue,
            broadcast=broadcast_update,
        )
        await app_state.continuous_monitor.start()
        logger.info("ContinuousMonitor started")
    except Exception as exc:
        logger.warning("Failed to start ContinuousMonitor", error=str(exc))

    yield

    # Shutdown
    logger.info("Shutting down Cluster Guardian")
    if app_state.continuous_monitor:
        await app_state.continuous_monitor.stop()
    if app_state.scan_task:
        app_state.scan_task.cancel()
    await get_redis_client().close()


async def periodic_scan_loop():
    """Background task for periodic cluster scans."""
    while True:
        try:
            # Use runtime-configurable scan interval from Redis/config store
            try:
                store = get_config_store()
                interval = await store.get("scan_interval_seconds")
            except Exception:
                interval = settings.scan_interval_seconds
            await asyncio.sleep(interval)
            logger.info("Running periodic scan")

            if app_state.guardian:
                result = await app_state.guardian.run_scan()
                app_state.last_scan_result = result

                # Persist scan result to Redis
                redis = get_redis_client()
                await redis.store_scan_result(result)

                # Notify websocket clients
                await broadcast_update(
                    {
                        "type": "scan_complete",
                        "result": result,
                    }
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Periodic scan failed", error=str(e))


async def broadcast_update(message: Dict[str, Any]):
    """Broadcast update to all connected WebSocket clients."""
    for ws in app_state.websocket_connections[:]:
        try:
            await ws.send_json(message)
        except Exception:
            app_state.websocket_connections.remove(ws)


# =============================================================================
# FASTAPI APP
# =============================================================================


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Cluster Guardian API",
        description="Agentic AI for Kubernetes self-healing and monitoring",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    metrics_middleware(app)

    # Register routes
    app.include_router(health_router)
    app.include_router(scan_router)
    app.include_router(webhook_router)
    app.include_router(ws_router)
    app.include_router(metrics_router)
    app.include_router(config_router)
    app.include_router(connections_router)
    app.include_router(approvals_router)
    app.include_router(incidents_router)
    app.include_router(monitor_router)
    app.include_router(escalations_router)
    app.include_router(discovery_router)
    app.include_router(tuner_router)
    app.include_router(log_router)

    # Static file mount for frontend SPA
    import os

    frontend_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "dist"
    )
    if os.path.isdir(frontend_dir):
        from starlette.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


# =============================================================================
# HEALTH ROUTES
# =============================================================================

health_router = APIRouter(tags=["Health"])


@health_router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    k8sgpt = get_k8sgpt_client()
    k8sgpt_healthy = await k8sgpt.health_check()

    guardian_info.info(
        {
            "version": __version__,
            "autonomy_level": settings.autonomy_level,
            "llm_model": settings.llm_model,
        }
    )

    return HealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.now(timezone.utc).isoformat(),
        components={
            "guardian": "ready" if app_state.guardian else "not_initialized",
            "k8sgpt": "healthy" if k8sgpt_healthy else "unhealthy",
            "k8s_client": "ready",
        },
    )


@health_router.get("/ready")
async def readiness_check():
    """Readiness probe for Kubernetes."""
    if not app_state.guardian:
        raise HTTPException(status_code=503, detail="Guardian not ready")
    return {"status": "ready"}


@health_router.get("/live")
async def liveness_check():
    """Liveness probe for Kubernetes."""
    return {"status": "alive"}


# =============================================================================
# SCAN ROUTES
# =============================================================================

scan_router = APIRouter(prefix="/api/v1", tags=["Scan"])


@scan_router.post("/scan", response_model=ScanResponse)
async def trigger_scan(background_tasks: BackgroundTasks):
    """
    Trigger a full cluster scan.

    Analyzes cluster issues and takes remediation actions as needed.
    """
    if not app_state.guardian:
        raise HTTPException(status_code=503, detail="Guardian not initialized")

    start = time.perf_counter()
    try:
        result = await app_state.guardian.run_scan()
        app_state.last_scan_result = result
        await get_redis_client().store_scan_result(result)
        guardian_scans_total.labels(result="success").inc()
        await broadcast_update({"type": "scan_complete", "result": result})
    except Exception:
        guardian_scans_total.labels(result="failure").inc()
        raise
    finally:
        guardian_scan_duration_seconds.observe(time.perf_counter() - start)

    return ScanResponse(
        success=result.get("success", False),
        summary=result.get("summary", ""),
        audit_log=result.get("audit_log", []),
        rate_limit=result.get("rate_limit", {}),
        timestamp=result.get("timestamp", datetime.now(timezone.utc).isoformat()),
    )


@scan_router.get("/scan/last", response_model=ScanResponse)
async def get_last_scan():
    """Get results of the last scan."""
    if not app_state.last_scan_result:
        raise HTTPException(status_code=404, detail="No scan results available")

    result = app_state.last_scan_result
    return ScanResponse(
        success=result.get("success", False),
        summary=result.get("summary", ""),
        audit_log=result.get("audit_log", []),
        rate_limit=result.get("rate_limit", {}),
        timestamp=result.get("timestamp", ""),
    )


@scan_router.post("/investigate")
async def investigate_issue(request: InvestigateRequest):
    """
    Investigate a specific issue.

    Provide a description and the Guardian will analyze and remediate.
    """
    if not app_state.guardian:
        raise HTTPException(status_code=503, detail="Guardian not initialized")

    thread_id = (
        request.thread_id
        or f"investigate-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )

    result = await app_state.guardian.investigate_issue(
        description=request.description,
        thread_id=thread_id,
    )

    return result


@scan_router.get("/health-checks")
async def run_health_checks():
    """Run deep health checks on all services."""
    health_checker = get_health_checker()
    results = await health_checker.check_all()

    for r in results:
        guardian_health_check_status.labels(service=r.service).set(
            1 if r.healthy else 0
        )

    healthy_count = sum(1 for r in results if r.healthy)
    unhealthy_count = sum(1 for r in results if not r.healthy)

    await broadcast_update(
        {
            "type": "health_update",
            "data": {"healthy": healthy_count, "unhealthy": unhealthy_count},
        }
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "healthy": healthy_count,
        "unhealthy": unhealthy_count,
        "results": [r.to_dict() for r in results],
    }


@scan_router.get("/health-checks/{service}")
async def check_service(service: str):
    """Run deep health check on a specific service."""
    health_checker = get_health_checker()
    result = await health_checker.check_service(service)

    return result.to_dict()


@scan_router.get("/audit-log")
async def get_audit_log():
    """Get recent audit log of remediation actions."""
    k8s = get_k8s_client()
    return {
        "entries": await k8s.get_audit_log(),
        "rate_limit": k8s.get_rate_limit_status(),
    }


@scan_router.get("/crashloopbackoff")
async def get_crashloop_pods():
    """Get all pods in CrashLoopBackOff state."""
    k8s = get_k8s_client()
    pods = await k8s.get_crashloopbackoff_pods()
    return {"pods": pods, "count": len(pods)}


@scan_router.get("/status-page")
async def get_status_page():
    """Proxy Gatus endpoint statuses for the dashboard widget."""
    from .gatus_client import get_gatus_client

    gatus = get_gatus_client()
    statuses = await gatus.get_endpoint_statuses()
    return {"endpoints": statuses}


# =============================================================================
# ALERTMANAGER WEBHOOK
# =============================================================================

webhook_router = APIRouter(prefix="/webhook", tags=["Webhooks"])


@webhook_router.post("/alertmanager")
async def alertmanager_webhook(
    payload: AlertManagerWebhook, background_tasks: BackgroundTasks
):
    """
    Receive alerts from AlertManager and trigger investigation.

    AlertManager should be configured to send webhooks to this endpoint:
    receivers:
      - name: 'cluster-guardian'
        webhook_configs:
          - url: 'http://cluster-guardian.stax-ops.svc:8900/webhook/alertmanager'
    """
    logger.info(
        "Received AlertManager webhook",
        status=payload.status,
        alert_count=len(payload.alerts),
        group_labels=payload.groupLabels,
    )

    guardian_issues_detected_total.labels(source="alertmanager").inc(
        len(payload.alerts)
    )

    if payload.status != "firing":
        return {"status": "ignored", "reason": "not firing"}

    if not app_state.guardian:
        raise HTTPException(status_code=503, detail="Guardian not initialized")

    # Correlate alerts into incidents with debounced investigation
    correlator = get_correlator()
    correlator.set_investigation_callback(app_state.guardian.investigate_issue)

    incidents = []
    for alert in payload.alerts:
        incident = correlator.correlate(alert)
        incidents.append(incident)
        asyncio.ensure_future(correlator.schedule_investigation(incident))

    correlator.expire_old()

    await broadcast_update(
        {
            "type": "alert_received",
            "data": {
                "source": "alertmanager",
                "alerts": len(payload.alerts),
                "status": payload.status,
            },
        }
    )

    return {
        "status": "accepted",
        "alerts_received": len(payload.alerts),
        "incidents": len({i.id for i in incidents}),
        "investigation_started": True,
    }


@webhook_router.post("/falco")
async def falco_webhook(payload: FalcoWebhook, background_tasks: BackgroundTasks):
    """
    Receive runtime security alerts from Falco.

    Falco should be configured to send HTTP alerts to this endpoint:
    json_output: true
    http_output:
      enabled: true
      url: http://cluster-guardian.stax-ops.svc:8900/webhook/falco
    """
    falco = get_falco_processor()
    parsed = falco.parse_alert(payload.model_dump())

    logger.info(
        "Received Falco alert",
        rule=parsed["rule"],
        severity=parsed["severity"],
        namespace=parsed.get("namespace"),
    )

    guardian_issues_detected_total.labels(source="falco").inc()

    if not app_state.guardian:
        raise HTTPException(status_code=503, detail="Guardian not initialized")

    # Investigate security alerts in background
    description = (
        f"Falco runtime security alert:\n"
        f"Rule: {parsed['rule']}\n"
        f"Severity: {parsed['severity']}\n"
        f"Namespace: {parsed.get('namespace', 'unknown')}\n"
        f"Pod: {parsed.get('pod', 'unknown')}\n"
        f"Output: {parsed['output']}"
    )

    background_tasks.add_task(
        app_state.guardian.investigate_issue,
        description=description,
        thread_id=f"falco-{parsed['rule'][:30]}",
    )

    await broadcast_update(
        {
            "type": "security_alert",
            "data": {
                "source": "falco",
                "rule": parsed["rule"],
                "severity": parsed["severity"],
            },
        }
    )

    return {
        "status": "accepted",
        "rule": parsed["rule"],
        "severity": parsed["severity"],
        "investigation_started": True,
    }


# =============================================================================
# WEBSOCKET
# =============================================================================

ws_router = APIRouter(tags=["WebSocket"])


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates."""
    await websocket.accept()
    app_state.websocket_connections.append(websocket)
    guardian_active_websockets.inc()

    logger.info("WebSocket client connected")

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "get_status":
                await websocket.send_json(
                    {
                        "type": "status",
                        "last_scan": app_state.last_scan_result,
                    }
                )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    finally:
        if websocket in app_state.websocket_connections:
            app_state.websocket_connections.remove(websocket)
        guardian_active_websockets.dec()


# =============================================================================
# METRICS ROUTES
# =============================================================================

metrics_router = APIRouter(tags=["Metrics"])


@metrics_router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return get_metrics_response()


# =============================================================================
# CONFIG ROUTES
# =============================================================================

config_router = APIRouter(prefix="/api/v1", tags=["Config"])


@config_router.get("/config")
async def get_config() -> Dict[str, Any]:
    """Return all configuration values (Redis overrides merged with defaults)."""
    store = get_config_store()
    return await store.get_all()


@config_router.patch("/config")
async def patch_config(body: Dict[str, Any]) -> Dict[str, Any]:
    """Update one or more configuration keys at runtime."""
    store = get_config_store()
    errors: List[str] = []
    updated: List[str] = []

    for key, value in body.items():
        try:
            await store.set(key, value)
            updated.append(key)
        except (ValueError, RuntimeError) as exc:
            errors.append(f"{key}: {exc}")

    if errors:
        raise HTTPException(status_code=400, detail=errors)

    return {"updated": updated}


@config_router.post("/config/reset")
async def reset_config(body: ConfigResetRequest) -> Dict[str, str]:
    """Reset a configuration key to its environment default."""
    store = get_config_store()
    try:
        await store.reset(body.key)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "ok", "key": body.key}


# =============================================================================
# CONNECTIONS ROUTES
# =============================================================================

connections_router = APIRouter(prefix="/api/v1", tags=["Connections"])


async def _check_http(url: str) -> str:
    """Perform a quick HTTP GET and return 'connected' or 'disconnected'."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            return "connected" if resp.status_code < 400 else "error"
    except Exception:
        return "disconnected"


@connections_router.get("/connections")
async def get_connections() -> Dict[str, Any]:
    """Return the health status of all external service connections."""
    now = datetime.now(timezone.utc).isoformat()
    results: List[ConnectionStatus] = []

    # Prometheus
    status = await _check_http(f"{settings.prometheus_url}/-/healthy")
    results.append(ConnectionStatus(name="prometheus", status=status, last_checked=now))

    # Loki
    status = await _check_http(f"{settings.loki_url}/ready")
    results.append(ConnectionStatus(name="loki", status=status, last_checked=now))

    # Redis
    try:
        redis = get_redis_client()
        healthy = await redis.health_check()
        status = "connected" if healthy else "disconnected"
    except Exception:
        status = "error"
    results.append(ConnectionStatus(name="redis", status=status, last_checked=now))

    # Qdrant
    status = await _check_http(f"{settings.qdrant_url}/healthz")
    results.append(ConnectionStatus(name="qdrant", status=status, last_checked=now))

    # K8sGPT
    try:
        k8sgpt = get_k8sgpt_client()
        healthy = await k8sgpt.health_check()
        status = "connected" if healthy else "disconnected"
    except Exception:
        status = "error"
    results.append(ConnectionStatus(name="k8sgpt", status=status, last_checked=now))

    # AlertManager
    status = await _check_http(f"{settings.alertmanager_url}/-/healthy")
    results.append(
        ConnectionStatus(name="alertmanager", status=status, last_checked=now)
    )

    # Longhorn
    status = await _check_http(f"{settings.longhorn_url}/v1")
    results.append(ConnectionStatus(name="longhorn", status=status, last_checked=now))

    # Langfuse (optional)
    if settings.langfuse_host:
        status = await _check_http(f"{settings.langfuse_host}/api/public/health")
        results.append(
            ConnectionStatus(name="langfuse", status=status, last_checked=now)
        )
    else:
        results.append(
            ConnectionStatus(name="langfuse", status="disconnected", last_checked=now)
        )

    # TheHive (optional)
    if settings.thehive_url and settings.thehive_api_key:
        status = await _check_http(f"{settings.thehive_url}/api/status")
        results.append(
            ConnectionStatus(name="thehive", status=status, last_checked=now)
        )
    else:
        results.append(
            ConnectionStatus(name="thehive", status="disconnected", last_checked=now)
        )

    # AI Dev Controller
    if settings.dev_controller_enabled:
        try:
            dc = get_dev_controller()
            dc_healthy = await dc.health_check()
            status = "connected" if dc_healthy else "disconnected"
        except Exception:
            status = "error"
        results.append(
            ConnectionStatus(name="dev_controller", status=status, last_checked=now)
        )
    else:
        results.append(
            ConnectionStatus(
                name="dev_controller", status="disconnected", last_checked=now
            )
        )

    # GitHub (optional)
    if settings.github_token:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{settings.github_owner}/{settings.github_repo}",
                    headers={
                        "Authorization": f"Bearer {settings.github_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                status = "connected" if resp.status_code == 200 else "error"
        except Exception:
            status = "disconnected"
        results.append(ConnectionStatus(name="github", status=status, last_checked=now))
    else:
        results.append(
            ConnectionStatus(name="github", status="disconnected", last_checked=now)
        )

    return {"connections": results}


@connections_router.post("/connections/{name}/test")
async def test_connection(name: str) -> ConnectionStatus:
    """Test a specific external service connection by name."""
    import httpx

    now = datetime.now(timezone.utc).isoformat()

    checks: Dict[str, Any] = {
        "prometheus": lambda: _check_http(f"{settings.prometheus_url}/-/healthy"),
        "loki": lambda: _check_http(f"{settings.loki_url}/ready"),
        "qdrant": lambda: _check_http(f"{settings.qdrant_url}/healthz"),
        "alertmanager": lambda: _check_http(f"{settings.alertmanager_url}/-/healthy"),
        "longhorn": lambda: _check_http(f"{settings.longhorn_url}/v1"),
    }

    if name == "redis":
        try:
            redis = get_redis_client()
            healthy = await redis.health_check()
            status = "connected" if healthy else "disconnected"
        except Exception:
            status = "error"
        return ConnectionStatus(name=name, status=status, last_checked=now)

    if name == "k8sgpt":
        try:
            k8sgpt = get_k8sgpt_client()
            healthy = await k8sgpt.health_check()
            status = "connected" if healthy else "disconnected"
        except Exception:
            status = "error"
        return ConnectionStatus(name=name, status=status, last_checked=now)

    if name == "langfuse":
        if settings.langfuse_host:
            status = await _check_http(f"{settings.langfuse_host}/api/public/health")
        else:
            status = "disconnected"
        return ConnectionStatus(name=name, status=status, last_checked=now)

    if name == "thehive":
        if settings.thehive_url and settings.thehive_api_key:
            status = await _check_http(f"{settings.thehive_url}/api/status")
        else:
            status = "disconnected"
        return ConnectionStatus(name=name, status=status, last_checked=now)

    if name == "github":
        if settings.github_token:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        f"https://api.github.com/repos/{settings.github_owner}/{settings.github_repo}",
                        headers={
                            "Authorization": f"Bearer {settings.github_token}",
                            "Accept": "application/vnd.github+json",
                        },
                    )
                    status = "connected" if resp.status_code == 200 else "error"
            except Exception:
                status = "disconnected"
        else:
            status = "disconnected"
        return ConnectionStatus(name=name, status=status, last_checked=now)

    if name not in checks:
        raise HTTPException(status_code=404, detail=f"Unknown connection: {name}")

    status = await checks[name]()
    return ConnectionStatus(name=name, status=status, last_checked=now)


# =============================================================================
# APPROVALS ROUTES
# =============================================================================

approvals_router = APIRouter(prefix="/api/v1", tags=["Approvals"])


@approvals_router.get("/approvals")
async def get_approvals() -> Dict[str, Any]:
    """Return all pending approval actions."""
    # Refresh from Redis if available
    redis = get_redis_client()
    if redis.available:
        try:
            persisted = await redis.get_pending_approvals()
            if persisted:
                app_state.pending_approvals = persisted
        except Exception:
            pass
    return {
        "approvals": [
            ApprovalAction(**a)
            for a in app_state.pending_approvals
            if a.get("status") == "pending"
        ]
    }


@approvals_router.post("/approvals/{approval_id}/approve")
async def approve_action(approval_id: str) -> Dict[str, str]:
    """Approve a pending action by ID."""
    for entry in app_state.pending_approvals:
        if entry["id"] == approval_id:
            if entry["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail=f"Action already {entry['status']}",
                )
            entry["status"] = "approved"
            redis = get_redis_client()
            await redis.update_pending_approval(approval_id, "approved")
            logger.info("approval.approved", id=approval_id, action=entry["action"])
            return {"status": "approved", "id": approval_id}

    raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")


@approvals_router.post("/approvals/{approval_id}/reject")
async def reject_action(approval_id: str) -> Dict[str, str]:
    """Reject a pending action by ID."""
    for entry in app_state.pending_approvals:
        if entry["id"] == approval_id:
            if entry["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail=f"Action already {entry['status']}",
                )
            entry["status"] = "rejected"
            redis = get_redis_client()
            await redis.update_pending_approval(approval_id, "rejected")
            logger.info("approval.rejected", id=approval_id, action=entry["action"])
            return {"status": "rejected", "id": approval_id}

    raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")


# =============================================================================
# INCIDENTS ROUTES
# =============================================================================

incidents_router = APIRouter(prefix="/api/v1", tags=["Incidents"])


@incidents_router.get("/incidents")
async def list_incidents() -> Dict[str, Any]:
    """List active and recent incidents."""
    correlator = get_correlator()
    return {
        "incidents": correlator.to_dict_list(),
        "count": len(correlator.get_active_incidents()),
    }


@incidents_router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str) -> Dict[str, Any]:
    """Get detail for a specific incident with correlated alerts."""
    correlator = get_correlator()
    incident = correlator.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return incident.to_dict()


# =============================================================================
# MONITOR ROUTES
# =============================================================================

monitor_router = APIRouter(prefix="/api/v1", tags=["Monitor"])


@monitor_router.get("/monitor/status")
async def monitor_status() -> Dict[str, Any]:
    """Get continuous monitor health, last check times, and anomaly queue depth."""
    if not app_state.continuous_monitor:
        return {"running": False, "error": "Continuous monitor not initialized"}
    return app_state.continuous_monitor.get_status()


@monitor_router.get("/monitor/anomalies")
async def monitor_anomalies() -> Dict[str, Any]:
    """Get recent anomalies with suppression state."""
    if not app_state.continuous_monitor:
        return {"anomalies": [], "error": "Continuous monitor not initialized"}
    return {"anomalies": app_state.continuous_monitor.get_recent_anomalies()}


# =============================================================================
# ESCALATIONS ROUTES
# =============================================================================

escalations_router = APIRouter(prefix="/api/v1", tags=["Escalations"])


@escalations_router.get("/escalations")
async def list_escalations() -> Dict[str, Any]:
    """List goals submitted to the AI dev controller."""
    if not settings.dev_controller_enabled:
        return {"escalations": [], "dev_controller_enabled": False}
    try:
        dc = get_dev_controller()
        status = await dc.get_loop_status()
        return {"dev_loop_status": status}
    except Exception as exc:
        return {"error": str(exc)}


# =============================================================================
# SERVICE DISCOVERY ROUTES
# =============================================================================

discovery_router = APIRouter(prefix="/api/v1", tags=["ServiceDiscovery"])


@discovery_router.get("/services/discovered")
async def discovered_services() -> Dict[str, Any]:
    """List dynamically discovered services from IngressRoute CRDs."""
    if not settings.service_discovery_enabled:
        return {"services": [], "enabled": False}
    try:
        sd = get_service_discovery()
        return {"services": sd.get_discovered(), "enabled": True}
    except Exception as exc:
        return {"services": [], "error": str(exc)}


# =============================================================================
# SELF-TUNER ROUTES
# =============================================================================

tuner_router = APIRouter(prefix="/api/v1", tags=["SelfTuner"])


@tuner_router.get("/self-tuner/suggestions")
async def self_tuner_suggestions() -> Dict[str, Any]:
    """Get improvement suggestions from the self-tuner."""
    try:
        tuner = get_self_tuner()
        suggestions = await tuner.suggest_improvements()
        return {
            "suggestions": suggestions,
            "stats": tuner.get_stats(),
            "effectiveness": tuner.get_effectiveness_stats(),
        }
    except Exception as exc:
        return {"suggestions": [], "error": str(exc)}


# =============================================================================
# ENTRY POINT
# =============================================================================

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
