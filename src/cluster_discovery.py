"""Kubernetes service auto-discovery for Cluster Guardian.

Scans the cluster for well-known services and populates settings overrides.
"""

import asyncio

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Well-known service patterns: (service_name_substring, config_key, default_port)
WELL_KNOWN_SERVICES = [
    ("prometheus", "prometheus_url", 9090),
    ("loki", "loki_url", 3100),
    ("alertmanager", "alertmanager_url", 9093),
    ("redis", "redis_url", 6379),
    ("qdrant", "qdrant_url", 6333),
    ("longhorn-frontend", "longhorn_url", 8000),
    ("gatus", "gatus_url", 80),
    ("k8sgpt", "k8sgpt_url", 8080),
    ("crowdsec-lapi", "crowdsec_lapi_url", 8080),
]


class ClusterDiscovery:
    """Discovers services in the cluster at startup."""

    def __init__(self, k8s_client=None):
        self._k8s = k8s_client
        self._discovered: dict[str, str] = {}

    async def discover(self) -> dict[str, str]:
        """Scan the cluster for well-known services.

        Returns a dict of config_key -> discovered_url for each found service.
        """
        if not self._k8s:
            logger.warning("No K8s client available for auto-discovery")
            return {}

        try:
            services = await asyncio.to_thread(
                self._k8s.core_v1.list_service_for_all_namespaces
            )
        except Exception as exc:
            logger.warning("Failed to list services for auto-discovery", error=str(exc))
            return {}

        discovered = {}
        for svc in services.items:
            name = svc.metadata.name
            namespace = svc.metadata.namespace
            ports = svc.spec.ports or []

            for svc_pattern, config_key, default_port in WELL_KNOWN_SERVICES:
                if svc_pattern in name.lower():
                    # Find the best port
                    port = default_port
                    for p in ports:
                        if p.port == default_port:
                            port = p.port
                            break
                    else:
                        if ports:
                            port = ports[0].port

                    # Construct URL
                    if config_key == "redis_url":
                        url = f"redis://{name}.{namespace}.svc.cluster.local:{port}"
                    else:
                        url = f"http://{name}.{namespace}.svc.cluster.local:{port}"

                    # Quick probe (non-blocking, 2s timeout)
                    reachable = await self._probe(url, config_key)

                    discovered[config_key] = url
                    logger.info(
                        "Discovered service",
                        service=name,
                        namespace=namespace,
                        config_key=config_key,
                        url=url,
                        reachable=reachable,
                    )
                    break  # Don't match same service to multiple patterns

        self._discovered = discovered

        # Log summary
        found = [k.replace("_url", "") for k in discovered]
        not_found = [p[0] for p in WELL_KNOWN_SERVICES if p[1] not in discovered]
        logger.info(
            "Auto-discovery complete",
            found=found,
            not_found=not_found,
        )

        return discovered

    async def _probe(self, url: str, config_key: str) -> bool:
        """Quick HTTP probe to check if a service is reachable."""
        if config_key == "redis_url":
            return True  # Can't HTTP probe Redis
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                return resp.status_code < 500
        except Exception:
            return False

    def get_discovered(self) -> dict[str, str]:
        """Return previously discovered services."""
        return dict(self._discovered)
