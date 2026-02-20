"""
Unified security integration client for Cluster Guardian.

Integrates with Falco (runtime security) and CrowdSec (collaborative IDS/IPS)
to provide security awareness for the Guardian agent.
"""

from datetime import datetime
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

CROWDSEC_DEFAULT_URL = "http://crowdsec-lapi.crowdsec.svc.cluster.local:8080"

_FALCO_SEVERITY_MAP: dict[str, str] = {
    "emergency": "critical",
    "alert": "critical",
    "critical": "critical",
    "error": "warning",
    "warning": "warning",
    "notice": "info",
    "informational": "info",
    "info": "info",
    "debug": "info",
}


class FalcoAlertProcessor:
    """Parses and processes Falco webhook alert payloads."""

    def parse_alert(self, payload: dict) -> dict:
        """Parse a Falco webhook payload into a standardized alert dict.

        Returns a dict with: rule, priority, severity, output, timestamp,
        namespace, pod, container.
        """
        priority_raw = (payload.get("priority") or "").strip().lower()
        output_fields = payload.get("output_fields") or {}

        return {
            "rule": payload.get("rule", ""),
            "priority": payload.get("priority", ""),
            "severity": _FALCO_SEVERITY_MAP.get(priority_raw, "info"),
            "output": payload.get("output", ""),
            "timestamp": payload.get("time", datetime.utcnow().isoformat()),
            "namespace": output_fields.get("k8s.ns.name", ""),
            "pod": output_fields.get("k8s.pod.name", ""),
            "container": output_fields.get("container.name", ""),
        }

    def format_alert_summary(self, alerts: list[dict]) -> str:
        """Format multiple parsed Falco alerts into a readable summary."""
        if not alerts:
            return "No Falco alerts."

        lines = [f"Falco alerts ({len(alerts)}):"]
        for a in alerts:
            sev = a.get("severity", "info").upper()
            rule = a.get("rule", "unknown")
            ns = a.get("namespace") or "n/a"
            pod = a.get("pod") or "n/a"
            output = a.get("output", "")
            lines.append(f"  [{sev}] {rule} | ns={ns} pod={pod} | {output}")

        return "\n".join(lines)


class CrowdSecClient:
    """Async client for CrowdSec Local API (LAPI)."""

    def __init__(
        self, lapi_url: str = CROWDSEC_DEFAULT_URL, api_key: str | None = None
    ):
        self.lapi_url = lapi_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    async def get_decisions(
        self,
        ip: str | None = None,
        scope: str | None = None,
    ) -> list[dict]:
        """Get active ban/captcha decisions from CrowdSec.

        Returns a list of dicts with: id, origin, type, scope, value,
        duration, scenario.
        """
        try:
            params: dict[str, str] = {}
            if ip:
                params["ip"] = ip
            if scope:
                params["scope"] = scope

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.lapi_url}/v1/decisions",
                    headers=self._headers(),
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                if data is None:
                    return []

                return [
                    {
                        "id": d.get("id"),
                        "origin": d.get("origin", ""),
                        "type": d.get("type", ""),
                        "scope": d.get("scope", ""),
                        "value": d.get("value", ""),
                        "duration": d.get("duration", ""),
                        "scenario": d.get("scenario", ""),
                    }
                    for d in data
                ]
        except Exception as exc:
            logger.warning("crowdsec_get_decisions_failed", error=str(exc))
            return []

    async def get_alerts(self, limit: int = 25) -> list[dict]:
        """Get recent CrowdSec alerts.

        Returns a list of dicts with: id, scenario, source_ip,
        source_scope, events_count, created_at.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.lapi_url}/v1/alerts",
                    headers=self._headers(),
                    params={"limit": str(limit)},
                )
                resp.raise_for_status()
                data = resp.json()

                if data is None:
                    return []

                results = []
                for a in data:
                    source = a.get("source") or {}
                    results.append(
                        {
                            "id": a.get("id"),
                            "scenario": a.get("scenario", ""),
                            "source_ip": source.get("ip", ""),
                            "source_scope": source.get("scope", ""),
                            "events_count": a.get("events_count", 0),
                            "created_at": a.get("created_at", ""),
                        }
                    )
                return results
        except Exception as exc:
            logger.warning("crowdsec_get_alerts_failed", error=str(exc))
            return []

    async def get_metrics(self) -> dict:
        """Get CrowdSec metrics/stats.

        Returns a dict with: bouncers, machines, decisions counts.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.lapi_url}/v1/metrics",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json() or {}
        except Exception as exc:
            logger.warning("crowdsec_get_metrics_failed", error=str(exc))
            return {}

    async def health_check(self) -> bool:
        """Check LAPI reachability."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.lapi_url}/v1/decisions",
                    headers=self._headers(),
                )
                return resp.status_code in (200, 403)
        except Exception as exc:
            logger.warning("crowdsec_health_check_failed", error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_falco_processor: Optional[FalcoAlertProcessor] = None
_crowdsec_client: Optional[CrowdSecClient] = None


def get_falco_processor() -> FalcoAlertProcessor:
    """Get or create the FalcoAlertProcessor singleton."""
    global _falco_processor
    if _falco_processor is None:
        _falco_processor = FalcoAlertProcessor()
    return _falco_processor


def get_crowdsec_client(
    lapi_url: str = CROWDSEC_DEFAULT_URL,
    api_key: str | None = None,
) -> CrowdSecClient:
    """Get or create the CrowdSecClient singleton."""
    global _crowdsec_client
    if _crowdsec_client is None:
        _crowdsec_client = CrowdSecClient(lapi_url=lapi_url, api_key=api_key)
    return _crowdsec_client
