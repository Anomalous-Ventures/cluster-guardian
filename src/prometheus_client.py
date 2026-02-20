"""
Prometheus query client for Cluster Guardian.

Enables data-driven decisions by querying Prometheus for pod/node
metrics, error rates, latency percentiles, and active alerts.
"""

from typing import Optional

import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)

PROMETHEUS_URL = "http://prometheus-kube-prometheus-prometheus.prometheus.svc.cluster.local:9090"


class PrometheusClient:
    """Async Prometheus query client with graceful error handling."""

    def __init__(self, base_url: str = PROMETHEUS_URL):
        self.base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(15.0)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, verify=False)

    async def query(self, promql: str) -> dict:
        """Execute an instant PromQL query. Returns the parsed JSON result."""
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/query",
                    params={"query": promql},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    return {"error": data.get("error", "unknown error"), "result": []}
                return data["data"]
        except Exception as e:
            logger.error("prometheus_query_failed", query=promql, error=str(e))
            return {"error": str(e), "result": []}

    async def query_range(self, promql: str, start: str, end: str, step: str = "1m") -> dict:
        """Execute a range PromQL query. start/end are RFC3339 or relative like 'now-1h'."""
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/query_range",
                    params={"query": promql, "start": start, "end": end, "step": step},
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    return {"error": data.get("error", "unknown error"), "result": []}
                return data["data"]
        except Exception as e:
            logger.error("prometheus_query_range_failed", query=promql, error=str(e))
            return {"error": str(e), "result": []}

    async def get_pod_cpu_usage(self, namespace: str, pod_name: str) -> dict:
        """Get current CPU usage for a pod. Returns cores used and percentage of request."""
        usage_query = (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",'
            f'pod="{pod_name}",container!="",container!="POD"}}[5m]))'
        )
        request_query = (
            f'sum(kube_pod_container_resource_requests{{namespace="{namespace}",'
            f'pod="{pod_name}",resource="cpu"}})'
        )

        usage = await self.query(usage_query)
        requests = await self.query(request_query)

        if "error" in usage or "error" in requests:
            return {"error": usage.get("error") or requests.get("error")}

        cores = _extract_value(usage)
        request_cores = _extract_value(requests)

        return {
            "pod": pod_name,
            "namespace": namespace,
            "cpu_cores": cores,
            "cpu_request_cores": request_cores,
            "cpu_percent_of_request": round((cores / request_cores) * 100, 2) if request_cores else None,
        }

    async def get_pod_memory_usage(self, namespace: str, pod_name: str) -> dict:
        """Get current memory usage for a pod. Returns bytes used and percentage of limit."""
        usage_query = (
            f'sum(container_memory_working_set_bytes{{namespace="{namespace}",'
            f'pod="{pod_name}",container!="",container!="POD"}})'
        )
        limit_query = (
            f'sum(kube_pod_container_resource_limits{{namespace="{namespace}",'
            f'pod="{pod_name}",resource="memory"}})'
        )

        usage = await self.query(usage_query)
        limits = await self.query(limit_query)

        if "error" in usage or "error" in limits:
            return {"error": usage.get("error") or limits.get("error")}

        mem_bytes = _extract_value(usage)
        limit_bytes = _extract_value(limits)

        return {
            "pod": pod_name,
            "namespace": namespace,
            "memory_bytes": mem_bytes,
            "memory_limit_bytes": limit_bytes,
            "memory_percent_of_limit": round((mem_bytes / limit_bytes) * 100, 2) if limit_bytes else None,
        }

    async def get_namespace_resource_usage(self, namespace: str) -> dict:
        """Get aggregate CPU/memory for all pods in a namespace."""
        cpu_query = (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",'
            f'container!="",container!="POD"}}[5m]))'
        )
        mem_query = (
            f'sum(container_memory_working_set_bytes{{namespace="{namespace}",'
            f'container!="",container!="POD"}})'
        )
        pod_count_query = f'count(kube_pod_info{{namespace="{namespace}"}})'

        cpu = await self.query(cpu_query)
        mem = await self.query(mem_query)
        pods = await self.query(pod_count_query)

        if "error" in cpu:
            return {"error": cpu["error"]}

        return {
            "namespace": namespace,
            "total_cpu_cores": _extract_value(cpu),
            "total_memory_bytes": _extract_value(mem),
            "pod_count": int(_extract_value(pods)),
        }

    async def get_error_rate(self, namespace: str, service: str, window: str = "5m") -> dict:
        """Get HTTP error rate (5xx/total) for a service over window."""
        errors_query = (
            f'sum(rate(traefik_service_requests_total{{service=~".*{service}.*",'
            f'code=~"5.."}}[{window}]))'
        )
        total_query = (
            f'sum(rate(traefik_service_requests_total{{service=~".*{service}.*"}}[{window}]))'
        )

        errors = await self.query(errors_query)
        total = await self.query(total_query)

        if "error" in errors or "error" in total:
            return {"error": errors.get("error") or total.get("error")}

        error_rps = _extract_value(errors)
        total_rps = _extract_value(total)

        return {
            "service": service,
            "namespace": namespace,
            "window": window,
            "error_rate": round((error_rps / total_rps) * 100, 4) if total_rps else 0.0,
            "error_rps": error_rps,
            "total_rps": total_rps,
        }

    async def get_request_latency(self, namespace: str, service: str, window: str = "5m") -> dict:
        """Get p50/p95/p99 request latency for a service."""
        percentiles = {}
        for label, quantile in [("p50", "0.5"), ("p95", "0.95"), ("p99", "0.99")]:
            q = (
                f'histogram_quantile({quantile},'
                f'sum(rate(traefik_service_request_duration_seconds_bucket'
                f'{{service=~".*{service}.*"}}[{window}])) by (le))'
            )
            result = await self.query(q)
            if "error" in result:
                return {"error": result["error"]}
            percentiles[label] = round(_extract_value(result), 6)

        return {
            "service": service,
            "namespace": namespace,
            "window": window,
            **percentiles,
        }

    async def get_node_resource_usage(self, node_name: str) -> dict:
        """Get CPU/memory usage and pressure for a specific node."""
        cpu_query = (
            f'1 - avg(rate(node_cpu_seconds_total{{instance=~"{node_name}.*",'
            f'mode="idle"}}[5m]))'
        )
        mem_avail_query = (
            f'node_memory_MemAvailable_bytes{{instance=~"{node_name}.*"}}'
        )
        mem_total_query = (
            f'node_memory_MemTotal_bytes{{instance=~"{node_name}.*"}}'
        )

        cpu = await self.query(cpu_query)
        mem_avail = await self.query(mem_avail_query)
        mem_total = await self.query(mem_total_query)

        if "error" in cpu:
            return {"error": cpu["error"]}

        cpu_pct = _extract_value(cpu)
        avail = _extract_value(mem_avail)
        total = _extract_value(mem_total)

        return {
            "node": node_name,
            "cpu_usage_percent": round(cpu_pct * 100, 2),
            "memory_available_bytes": avail,
            "memory_total_bytes": total,
            "memory_usage_percent": round(((total - avail) / total) * 100, 2) if total else None,
        }

    async def get_alerts(self, state: str = "firing") -> list[dict]:
        """Get current alerts from Prometheus rules. State: firing, pending, inactive."""
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/api/v1/rules")
                resp.raise_for_status()
                data = resp.json()

            if data.get("status") != "success":
                return [{"error": data.get("error", "unknown error")}]

            alerts = []
            for group in data.get("data", {}).get("groups", []):
                for rule in group.get("rules", []):
                    if rule.get("type") != "alerting":
                        continue
                    for alert in rule.get("alerts", []):
                        if alert.get("state") == state:
                            alerts.append({
                                "name": rule.get("name"),
                                "state": alert.get("state"),
                                "severity": alert.get("labels", {}).get("severity", "unknown"),
                                "summary": alert.get("annotations", {}).get("summary", ""),
                                "description": alert.get("annotations", {}).get("description", ""),
                                "labels": alert.get("labels", {}),
                                "active_at": alert.get("activeAt"),
                            })
            return alerts
        except Exception as e:
            logger.error("prometheus_get_alerts_failed", error=str(e))
            return [{"error": str(e)}]

    async def health_check(self) -> bool:
        """Check Prometheus reachability."""
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/-/healthy")
                return resp.status_code == 200
        except Exception as e:
            logger.warning("prometheus_health_check_failed", error=str(e))
            return False


def _extract_value(result: dict) -> float:
    """Extract a scalar float from a Prometheus instant query result."""
    try:
        vector = result.get("result", [])
        if not vector:
            return 0.0
        # Instant vector: [{"metric": {}, "value": [timestamp, "value"]}]
        return float(vector[0]["value"][1])
    except (IndexError, KeyError, TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_prometheus_client: Optional[PrometheusClient] = None


def get_prometheus_client() -> PrometheusClient:
    """Get or create the PrometheusClient singleton."""
    global _prometheus_client
    if _prometheus_client is None:
        _prometheus_client = PrometheusClient(base_url=settings.prometheus_url)
    return _prometheus_client
