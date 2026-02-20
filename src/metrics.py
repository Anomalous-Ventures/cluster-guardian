"""
Prometheus metrics for Cluster Guardian.
"""

import time
from typing import Callable

from prometheus_client import Counter, Gauge, Histogram, Info, generate_latest, CONTENT_TYPE_LATEST
from starlette.requests import Request
from starlette.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

# =============================================================================
# METRICS DEFINITIONS
# =============================================================================

guardian_scans_total = Counter(
    "guardian_scans_total",
    "Total cluster scans executed",
    ["result"],
)

guardian_scan_duration_seconds = Histogram(
    "guardian_scan_duration_seconds",
    "Duration of cluster scans in seconds",
)

guardian_remediations_total = Counter(
    "guardian_remediations_total",
    "Total remediation actions taken",
    ["action", "result"],
)

guardian_health_check_status = Gauge(
    "guardian_health_check_status",
    "Health check status per service (1=healthy, 0=unhealthy)",
    ["service"],
)

guardian_agent_iterations_total = Counter(
    "guardian_agent_iterations_total",
    "Total LLM reasoning iterations",
)

guardian_rate_limit_remaining = Gauge(
    "guardian_rate_limit_remaining",
    "Remaining remediation actions allowed in current rate-limit window",
)

guardian_active_websockets = Gauge(
    "guardian_active_websockets",
    "Number of active WebSocket connections",
)

guardian_issues_detected_total = Counter(
    "guardian_issues_detected_total",
    "Total issues detected by source",
    ["source"],
)

guardian_info = Info(
    "guardian",
    "Cluster Guardian instance metadata",
)

# HTTP request metrics for middleware
http_requests_total = Counter(
    "guardian_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
    "guardian_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)


# =============================================================================
# MIDDLEWARE
# =============================================================================

class MetricsMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that tracks request count and duration."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        path = request.url.path

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        http_requests_total.labels(method=method, path=path, status=response.status_code).inc()
        http_request_duration_seconds.labels(method=method, path=path).observe(duration)

        return response


def metrics_middleware(app):
    """Add Prometheus metrics middleware to a FastAPI app."""
    app.add_middleware(MetricsMiddleware)


# =============================================================================
# RESPONSE HELPER
# =============================================================================

def get_metrics_response() -> Response:
    """Return Prometheus metrics in text exposition format."""
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
