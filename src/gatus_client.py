"""
Gatus status page client.

Fetches endpoint health status from the Gatus API for use by the
Guardian agent and dashboard status widget.
"""

from typing import Any, Dict, List, Optional

import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 10.0


class GatusClient:
    """Client for the Gatus status page API."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.gatus_url).rstrip("/")

    async def get_endpoint_statuses(self) -> List[Dict[str, Any]]:
        """Fetch all endpoint statuses from Gatus."""
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/endpoints/statuses",
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("gatus_query_failed", error=str(exc))
            return []

        results: List[Dict[str, Any]] = []
        for ep in data:
            name = ep.get("name", "")
            group = ep.get("group", "")
            results_list = ep.get("results", [])
            last_result = results_list[-1] if results_list else {}

            healthy = last_result.get("success", False)
            hostname = last_result.get("hostname", "")
            timestamp = last_result.get("timestamp", "")

            # Calculate 7-day uptime from available results
            total = len(results_list)
            successes = sum(1 for r in results_list if r.get("success", False))
            uptime_7d = (successes / total * 100) if total > 0 else 0.0

            results.append({
                "name": name,
                "group": group,
                "healthy": healthy,
                "hostname": hostname,
                "last_check": timestamp,
                "uptime_7d": round(uptime_7d, 2),
            })

        return results

    async def get_unhealthy(self) -> List[Dict[str, Any]]:
        """Return only unhealthy endpoints."""
        all_statuses = await self.get_endpoint_statuses()
        return [ep for ep in all_statuses if not ep["healthy"]]


_gatus_client: Optional[GatusClient] = None


def get_gatus_client() -> GatusClient:
    """Get or create Gatus client singleton."""
    global _gatus_client
    if _gatus_client is None:
        _gatus_client = GatusClient()
    return _gatus_client
