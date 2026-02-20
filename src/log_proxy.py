"""
Loki log streaming proxy and K8s events API for the frontend log viewer.

Translates frontend filter parameters into LogQL queries against Loki,
and exposes Kubernetes events for the unified log/events viewer.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query
from kubernetes import client as k8s_client_lib
from kubernetes.client.rest import ApiException

from .config import settings

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 15.0

log_router = APIRouter(prefix="/api/v1", tags=["Logs"])


# =============================================================================
# HELPERS
# =============================================================================

def _build_logql(
    query: Optional[str],
    namespace: Optional[str],
    pod: Optional[str],
    container: Optional[str],
    severity: Optional[str],
) -> str:
    """Build a LogQL query from frontend filter parameters."""
    selectors: List[str] = []
    if namespace:
        selectors.append(f'namespace="{namespace}"')
    if pod:
        selectors.append(f'pod=~"{pod}.*"')
    if container:
        selectors.append(f'container="{container}"')

    stream_selector = "{" + ", ".join(selectors) + "}" if selectors else '{job=~".+"}'

    pipeline: List[str] = []
    if severity:
        severity_pattern = "|".join(s.strip() for s in severity.split(","))
        pipeline.append(f'|~ "(?i)({severity_pattern})"')
    if query:
        pipeline.append(f'|~ "{query}"')

    return stream_selector + (" " + " ".join(pipeline) if pipeline else "")


def _parse_duration_ns(since: str) -> int:
    """Convert a human duration string (e.g. '1h', '30m') to nanoseconds."""
    import re

    pattern = re.compile(r"^(\d+)([smhd])$")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    match = pattern.match(since)
    if not match:
        raise ValueError(f"Invalid duration format: {since!r}. Use e.g. '1h', '30m', '5s'.")
    value, unit = int(match.group(1)), match.group(2)
    return value * multipliers[unit] * 1_000_000_000


def _parse_loki_streams(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Loki query_range response into structured log entries."""
    entries: List[Dict[str, Any]] = []
    for stream in data.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            entries.append({
                "timestamp": datetime.fromtimestamp(
                    int(ts_ns) / 1_000_000_000, tz=timezone.utc
                ).isoformat(),
                "line": line,
                "labels": labels,
            })
    return entries


# =============================================================================
# LOG ENDPOINTS
# =============================================================================

@log_router.get("/logs")
async def query_logs(
    query: Optional[str] = Query(None, description="Free-text search pattern"),
    namespace: Optional[str] = Query(None, description="Filter by namespace"),
    pod: Optional[str] = Query(None, description="Filter by pod name"),
    container: Optional[str] = Query(None, description="Filter by container name"),
    severity: Optional[str] = Query(None, description="Comma-separated severity levels (e.g. error,warning)"),
    since: str = Query("1h", description="Time range, e.g. '1h', '30m', '5m'"),
    limit: int = Query(100, ge=1, le=5000, description="Max entries to return"),
) -> Dict[str, Any]:
    """Query Loki logs with optional filters.

    Translates frontend filter parameters into a LogQL query and returns
    structured log entries for the log viewer.
    """
    logql = _build_logql(query, namespace, pod, container, severity)

    try:
        now_ns = int(time.time() * 1_000_000_000)
        start_ns = now_ns - _parse_duration_ns(since)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    params = {
        "query": logql,
        "limit": str(limit),
        "start": str(start_ns),
        "end": str(now_ns),
        "direction": "backward",
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.get(
                f"{settings.loki_url}/loki/api/v1/query_range",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("loki_query_failed", logql=logql, status=exc.response.status_code)
        raise HTTPException(
            status_code=502,
            detail=f"Loki returned {exc.response.status_code}",
        )
    except httpx.HTTPError as exc:
        logger.error("loki_query_failed", logql=logql, error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to reach Loki")

    entries = _parse_loki_streams(data)
    return {"entries": entries, "total": len(entries)}


@log_router.get("/logs/labels")
async def get_log_labels() -> Dict[str, List[str]]:
    """Get available label values for frontend filter dropdowns.

    Queries Loki for namespace, pod, and container label values.
    """
    label_names = ["namespace", "pod", "container"]
    results: Dict[str, List[str]] = {
        "namespaces": [],
        "pods": [],
        "containers": [],
    }
    key_map = {
        "namespace": "namespaces",
        "pod": "pods",
        "container": "containers",
    }

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        for label in label_names:
            try:
                response = await client.get(
                    f"{settings.loki_url}/loki/api/v1/label/{label}/values",
                )
                response.raise_for_status()
                data = response.json()
                values = data.get("data", [])
                results[key_map[label]] = sorted(values) if values else []
            except httpx.HTTPError as exc:
                logger.warning(
                    "loki_label_query_failed",
                    label=label,
                    error=str(exc),
                )

    return results


# =============================================================================
# K8S EVENTS ENDPOINT
# =============================================================================

@log_router.get("/events")
async def get_events(
    namespace: Optional[str] = Query(None, description="Filter events by namespace"),
) -> Dict[str, Any]:
    """List Kubernetes events, optionally filtered by namespace.

    Returns recent cluster events for the frontend events viewer.
    """
    try:
        core_v1 = k8s_client_lib.CoreV1Api()

        if namespace:
            event_list = core_v1.list_namespaced_event(namespace)
        else:
            event_list = core_v1.list_event_for_all_namespaces()

        events: List[Dict[str, Any]] = []
        for event in sorted(
            event_list.items,
            key=lambda e: e.last_timestamp or e.event_time or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        ):
            involved = event.involved_object
            events.append({
                "timestamp": str(
                    event.last_timestamp or event.event_time or ""
                ),
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "object": f"{involved.kind}/{involved.name}" if involved else "",
                "namespace": event.metadata.namespace,
            })

        return {"events": events}

    except ApiException as exc:
        logger.error("k8s_events_query_failed", status=exc.status, error=exc.reason)
        raise HTTPException(
            status_code=502,
            detail=f"Kubernetes API error: {exc.reason}",
        )
