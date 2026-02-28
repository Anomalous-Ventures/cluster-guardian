"""
Ingress and infrastructure monitoring for Cluster Guardian.

Validates Traefik IngressRoutes, checks Service endpoints,
monitors DaemonSet health, and queries PVC usage.
"""

import asyncio
from typing import Any, Optional

import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)


class IngressMonitor:
    """Monitors Traefik IngressRoutes and validates routing."""

    def __init__(self, k8s, prometheus=None):
        self._k8s = k8s
        self._prometheus = prometheus
        self._timeout = httpx.Timeout(10.0)

    async def check_all_ingress_routes(self) -> list[dict[str, Any]]:
        """List all Traefik IngressRoute CRDs, validate each concurrently."""
        try:
            routes = await self._list_ingress_routes()
            sem = asyncio.Semaphore(10)

            async def _check_with_limit(route):
                async with sem:
                    return await self.check_ingress_route(
                        route["namespace"], route["name"]
                    )

            results = await asyncio.gather(
                *[_check_with_limit(r) for r in routes],
                return_exceptions=True,
            )
            return [r for r in results if isinstance(r, dict)]
        except Exception as exc:
            logger.error("check_all_ingress_routes failed", error=str(exc))
            return []

    async def check_ingress_route(self, namespace: str, name: str) -> dict[str, Any]:
        """Deep check a specific IngressRoute."""
        result: dict[str, Any] = {
            "name": name,
            "namespace": namespace,
            "healthy": True,
            "checks": [],
        }

        try:
            route = await asyncio.to_thread(
                self._k8s.custom_objects.get_namespaced_custom_object,
                group="traefik.io",
                version="v1alpha1",
                namespace=namespace,
                plural="ingressroutes",
                name=name,
            )

            spec = route.get("spec", {})
            routes = spec.get("routes", [])

            # Extract hosts from match rules
            hosts = self._extract_hosts(routes)
            result["hosts"] = hosts

            # Check backend services
            for route_entry in routes:
                for svc in route_entry.get("services", []):
                    svc_name = svc.get("name", "")
                    svc_ns = svc.get("namespace", namespace)
                    ep_check = await self.check_service_endpoints(svc_ns, svc_name)
                    result["checks"].append(
                        {"type": "service_endpoints", "service": svc_name, **ep_check}
                    )
                    if ep_check.get("ready", 0) == 0:
                        result["healthy"] = False
                        result["error"] = f"Service {svc_name} has no ready endpoints"

            # HTTP check on first host
            if hosts:
                host = hosts[0]
                tls = spec.get("tls", {})
                scheme = "https" if tls else "http"
                http_check = await self._http_check(f"{scheme}://{host}/")
                result["checks"].append(
                    {"type": "http_check", "host": host, **http_check}
                )
                if not http_check.get("success"):
                    result["healthy"] = False
                    result["status_code"] = http_check.get("status_code")
                    result["error"] = http_check.get("error", "HTTP check failed")

        except Exception as exc:
            result["healthy"] = False
            result["error"] = str(exc)

        return result

    async def check_service_endpoints(
        self, namespace: str, service_name: str
    ) -> dict[str, Any]:
        """Verify a Service has healthy endpoints."""
        try:
            endpoints = await asyncio.to_thread(
                self._k8s.core_v1.read_namespaced_endpoints,
                service_name,
                namespace,
            )
            ready = 0
            not_ready = 0
            for subset in endpoints.subsets or []:
                ready += len(subset.addresses or [])
                not_ready += len(subset.not_ready_addresses or [])
            return {
                "ready": ready,
                "not_ready": not_ready,
                "healthy": ready > 0,
            }
        except Exception as exc:
            return {"ready": 0, "not_ready": 0, "healthy": False, "error": str(exc)}

    async def check_daemonset_health(self) -> list[dict[str, Any]]:
        """Check all DaemonSets have desired==ready pods."""
        results = []
        try:
            ds_list = await asyncio.to_thread(
                self._k8s.apps_v1.list_daemon_set_for_all_namespaces
            )
            for ds in ds_list.items:
                ns = ds.metadata.namespace
                if ns in settings.protected_namespaces:
                    continue
                status = ds.status
                results.append(
                    {
                        "name": ds.metadata.name,
                        "namespace": ns,
                        "desired": status.desired_number_scheduled or 0,
                        "ready": status.number_ready or 0,
                        "unavailable": status.number_unavailable or 0,
                    }
                )
        except Exception as exc:
            logger.error("check_daemonset_health failed", error=str(exc))
        return results

    async def check_pvc_usage(self, threshold: float = 0.85) -> list[dict[str, Any]]:
        """Query Prometheus for PVC usage above threshold."""
        if not self._prometheus:
            return []
        try:
            query = (
                "kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes"
            )
            data = await self._prometheus.query(query)
            if "error" in data:
                return []

            results = []
            for item in data.get("result", []):
                usage = float(item["value"][1])
                if usage >= threshold:
                    metric = item.get("metric", {})
                    results.append(
                        {
                            "namespace": metric.get("namespace", "unknown"),
                            "pvc": metric.get("persistentvolumeclaim", "unknown"),
                            "usage_percent": round(usage * 100, 1),
                        }
                    )
            return results
        except Exception as exc:
            logger.error("check_pvc_usage failed", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _list_ingress_routes(
        self, namespace: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """List Traefik IngressRoute CRDs."""
        try:
            if namespace:
                resp = await asyncio.to_thread(
                    self._k8s.custom_objects.list_namespaced_custom_object,
                    group="traefik.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="ingressroutes",
                )
            else:
                resp = await asyncio.to_thread(
                    self._k8s.custom_objects.list_cluster_custom_object,
                    group="traefik.io",
                    version="v1alpha1",
                    plural="ingressroutes",
                )
            return [
                {
                    "name": item["metadata"]["name"],
                    "namespace": item["metadata"]["namespace"],
                }
                for item in resp.get("items", [])
            ]
        except Exception as exc:
            logger.error("list_ingress_routes failed", error=str(exc))
            return []

    def _extract_hosts(self, routes: list[dict]) -> list[str]:
        """Extract hostnames from IngressRoute match rules."""
        hosts = []
        for route in routes:
            match = route.get("match", "")
            # Parse Host(`example.com`) patterns
            import re

            found = re.findall(r"Host\(`([^`]+)`\)", match)
            hosts.extend(found)
        return hosts

    async def _http_check(self, url: str) -> dict[str, Any]:
        """Perform an HTTP GET and return result."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, verify=False, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                error_indicators = [
                    "502 Bad Gateway",
                    "503 Service Unavailable",
                    "504 Gateway Timeout",
                    "Application Error",
                ]
                content_error = None
                for indicator in error_indicators:
                    if indicator in resp.text[:2000]:
                        content_error = f"Error page detected: {indicator}"
                        break

                # Suspicious small response body on 200
                suspicious_small_body = (
                    resp.status_code == 200 and len(resp.content) < 100
                )

                return {
                    "status_code": resp.status_code,
                    "response_time_ms": resp.elapsed.total_seconds() * 1000,
                    "success": resp.status_code < 500
                    and content_error is None
                    and not suspicious_small_body,
                    "content_error": content_error,
                    "suspicious_small_body": suspicious_small_body,
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_ingress_monitor: Optional[IngressMonitor] = None


def get_ingress_monitor(k8s=None, prometheus=None) -> IngressMonitor:
    """Get or create IngressMonitor singleton."""
    global _ingress_monitor
    if _ingress_monitor is None:
        if k8s is None:
            from .k8s_client import get_k8s_client

            k8s = get_k8s_client()
        if prometheus is None:
            from .prometheus_client import get_prometheus_client

            prometheus = get_prometheus_client()
        _ingress_monitor = IngressMonitor(k8s=k8s, prometheus=prometheus)
    return _ingress_monitor
