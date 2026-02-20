"""
Loki log query client for Cluster Guardian.

Provides LogQL query capabilities for root cause analysis
when pods crash or services degrade.
"""

import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)

LOKI_GATEWAY_URL = "http://loki-gateway.loki.svc.cluster.local:80"
MAX_LINE_LENGTH = 500
DEFAULT_TIMEOUT = 15.0

DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")
DURATION_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_duration_ns(since: str) -> int:
    """Convert a human duration string (e.g. '1h', '30m') to nanoseconds."""
    match = DURATION_PATTERN.match(since)
    if not match:
        raise ValueError(f"Invalid duration format: {since!r}. Use e.g. '1h', '30m', '5s'.")
    value, unit = int(match.group(1)), match.group(2)
    return value * DURATION_MULTIPLIERS[unit] * 1_000_000_000


def _truncate(line: str) -> str:
    if len(line) <= MAX_LINE_LENGTH:
        return line
    return line[:MAX_LINE_LENGTH] + "..."


class LokiClient:
    """Async Loki log query client using the v1 HTTP API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def query_logs(self, logql: str, limit: int = 100, since: str = "1h") -> list[dict]:
        """Execute a LogQL query against /loki/api/v1/query_range.

        Returns list of log entries with keys: timestamp, labels, line.
        """
        now_ns = int(time.time() * 1_000_000_000)
        start_ns = now_ns - _parse_duration_ns(since)

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
                    f"{self.base_url}/loki/api/v1/query_range",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning("Loki query failed", query=logql, error=str(exc))
            return []

        entries: list[dict] = []
        for stream in data.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                entries.append({
                    "timestamp": datetime.fromtimestamp(
                        int(ts_ns) / 1_000_000_000, tz=timezone.utc
                    ).isoformat(),
                    "labels": labels,
                    "line": _truncate(line),
                })
        return entries

    async def get_pod_logs(
        self, namespace: str, pod_name: str, limit: int = 50, since: str = "1h"
    ) -> str:
        """Get recent logs for a specific pod as formatted text."""
        query = f'{{namespace="{namespace}", pod="{pod_name}"}}'
        entries = await self.query_logs(query, limit=limit, since=since)
        if not entries:
            return f"No logs found for pod {namespace}/{pod_name} in the last {since}."
        return self._format_entries(entries)

    async def get_namespace_errors(
        self, namespace: str, limit: int = 50, since: str = "30m"
    ) -> str:
        """Get error-level logs from a namespace."""
        query = f'{{namespace="{namespace}"}} |~ "(?i)(error|exception|fatal|panic|crash)"'
        entries = await self.query_logs(query, limit=limit, since=since)
        if not entries:
            return f"No error logs found in namespace {namespace} in the last {since}."
        return self._format_entries(entries)

    async def get_container_logs(
        self,
        namespace: str,
        pod_name: str,
        container: str,
        limit: int = 50,
        since: str = "1h",
    ) -> str:
        """Get logs for a specific container in a pod."""
        query = f'{{namespace="{namespace}", pod="{pod_name}", container="{container}"}}'
        entries = await self.query_logs(query, limit=limit, since=since)
        if not entries:
            return (
                f"No logs found for container {container} in pod "
                f"{namespace}/{pod_name} in the last {since}."
            )
        return self._format_entries(entries)

    async def search_logs(
        self,
        query_text: str,
        namespace: Optional[str] = None,
        limit: int = 50,
        since: str = "1h",
    ) -> str:
        """Search logs across the cluster (or a namespace) for a text pattern."""
        selector = f'{{namespace="{namespace}"}}' if namespace else '{job=~".+"}'
        query = f'{selector} |~ "{query_text}"'
        entries = await self.query_logs(query, limit=limit, since=since)
        scope = f"namespace {namespace}" if namespace else "cluster"
        if not entries:
            return f'No logs matching "{query_text}" found in {scope} in the last {since}.'
        return self._format_entries(entries)

    async def get_log_volume(self, namespace: str, since: str = "1h") -> dict:
        """Get log volume (lines/sec) for a namespace to detect log storms.

        Returns dict with total_lines, lines_per_second, and per-pod breakdown.
        """
        query = f'sum(count_over_time({{namespace="{namespace}"}} [1m])) by (pod)'
        now_ns = int(time.time() * 1_000_000_000)
        start_ns = now_ns - _parse_duration_ns(since)

        params = {
            "query": query,
            "start": str(start_ns),
            "end": str(now_ns),
            "step": "60",
        }

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    f"{self.base_url}/loki/api/v1/query_range",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning("Loki log volume query failed", namespace=namespace, error=str(exc))
            return {"total_lines": 0, "lines_per_second": 0.0, "pods": {}}

        duration_s = _parse_duration_ns(since) / 1_000_000_000
        pods: dict[str, float] = {}
        total = 0.0

        for stream in data.get("data", {}).get("result", []):
            pod = stream.get("metric", {}).get("pod", "unknown")
            values = stream.get("values", [])
            pod_total = sum(float(v) for _, v in values)
            pods[pod] = round(pod_total / duration_s, 2)
            total += pod_total

        return {
            "total_lines": int(total),
            "lines_per_second": round(total / duration_s, 2),
            "pods": pods,
        }

    async def health_check(self) -> bool:
        """Check Loki reachability via /ready endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/ready")
                return response.status_code == 200
        except Exception as exc:
            logger.warning("Loki health check failed", error=str(exc))
            return False

    @staticmethod
    def _format_entries(entries: list[dict]) -> str:
        """Format log entries into human-readable text."""
        lines = []
        for entry in entries:
            ts = entry["timestamp"]
            pod = entry["labels"].get("pod", "")
            container = entry["labels"].get("container", "")
            prefix = f"[{ts}] {pod}"
            if container:
                prefix += f"/{container}"
            lines.append(f"{prefix}: {entry['line']}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_loki_client: Optional[LokiClient] = None


def get_loki_client() -> LokiClient:
    """Get or create the LokiClient singleton."""
    global _loki_client
    if _loki_client is None:
        _loki_client = LokiClient(base_url=settings.loki_url)
    return _loki_client
