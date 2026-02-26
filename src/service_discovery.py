"""
Dynamic service discovery for Cluster Guardian.

Queries IngressRoute CRDs to discover services that don't have explicit
health checks configured, and creates generic checks for them.
"""

import re
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)


class ServiceDiscovery:
    """Discovers services from IngressRoute CRDs and creates generic health checks."""

    def __init__(self, k8s, health_checker):
        self._k8s = k8s
        self._health_checker = health_checker
        self._discovered: dict[str, dict[str, Any]] = {}
        self._loop_counter = 0

    async def refresh(self) -> list[dict[str, Any]]:
        """Query IngressRoute CRDs and discover unknown services."""
        new_services = []
        try:
            routes = self._k8s.custom_objects.list_cluster_custom_object(
                group="traefik.io",
                version="v1alpha1",
                plural="ingressroutes",
            )
            known_services = set(self._health_checker.service_checks.keys())

            for item in routes.get("items", []):
                metadata = item.get("metadata", {})
                name = metadata.get("name", "")
                namespace = metadata.get("namespace", "")
                spec = item.get("spec", {})

                hosts = self._extract_hosts(spec.get("routes", []))
                if not hosts:
                    continue

                # Derive a service name from the IngressRoute name
                svc_name = name.lower().replace("-ingressroute", "").replace("-ingress", "")

                if svc_name in known_services or svc_name in self._discovered:
                    continue

                svc_info = {
                    "name": svc_name,
                    "namespace": namespace,
                    "hosts": hosts,
                    "ingress_route": name,
                    "tls": bool(spec.get("tls")),
                }
                self._discovered[svc_name] = svc_info
                new_services.append(svc_info)

                logger.info(
                    "Discovered new service",
                    service=svc_name,
                    hosts=hosts,
                    namespace=namespace,
                )

        except Exception as exc:
            logger.warning("Service discovery refresh failed", error=str(exc))

        return new_services

    async def check_discovered_services(self) -> list[dict[str, Any]]:
        """Run generic health checks on discovered services."""
        results = []
        for svc_name, svc_info in self._discovered.items():
            host = svc_info["hosts"][0]
            scheme = "https" if svc_info["tls"] else "http"
            url = f"{scheme}://{host}/"

            check_result = {
                "service": svc_name,
                "namespace": svc_info["namespace"],
                "url": url,
                "healthy": True,
                "checks": [],
            }

            # HTTP reachability check
            http_result = await self._http_check(url)
            check_result["checks"].append({"type": "http", **http_result})
            if not http_result.get("success"):
                check_result["healthy"] = False

            results.append(check_result)
        return results

    async def _http_check(self, url: str) -> dict[str, Any]:
        """Perform an HTTP GET with error page detection."""
        error_indicators = [
            "502 Bad Gateway",
            "503 Service Unavailable",
            "504 Gateway Timeout",
        ]
        try:
            async with httpx.AsyncClient(
                timeout=10.0, verify=False, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                body = resp.text[:2000]

                content_error = None
                for indicator in error_indicators:
                    if indicator in body:
                        content_error = f"Error page detected: {indicator}"
                        break

                # Small response body check
                suspicious_body = (
                    resp.status_code == 200 and len(resp.content) < 100
                )

                return {
                    "status_code": resp.status_code,
                    "response_time_ms": resp.elapsed.total_seconds() * 1000,
                    "success": resp.status_code < 500
                    and content_error is None
                    and not suspicious_body,
                    "content_error": content_error,
                    "suspicious_small_body": suspicious_body,
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _extract_hosts(self, routes: list[dict]) -> list[str]:
        """Extract hostnames from IngressRoute match rules."""
        hosts = []
        for route in routes:
            match = route.get("match", "")
            found = re.findall(r"Host\(`([^`]+)`\)", match)
            hosts.extend(found)
        return hosts

    def get_discovered(self) -> list[dict[str, Any]]:
        """Return all discovered services."""
        return list(self._discovered.values())

    def should_refresh(self, interval_loops: int = 10) -> bool:
        """Check if it's time to refresh based on loop count."""
        self._loop_counter += 1
        if self._loop_counter >= interval_loops:
            self._loop_counter = 0
            return True
        return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service_discovery: Optional[ServiceDiscovery] = None


def get_service_discovery(k8s=None, health_checker=None) -> ServiceDiscovery:
    """Get or create ServiceDiscovery singleton."""
    global _service_discovery
    if _service_discovery is None:
        if k8s is None:
            from .k8s_client import get_k8s_client
            k8s = get_k8s_client()
        if health_checker is None:
            from .health_checks import get_health_checker
            health_checker = get_health_checker()
        _service_discovery = ServiceDiscovery(k8s=k8s, health_checker=health_checker)
    return _service_discovery
