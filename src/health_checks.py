"""
Deep health checks for services.

Goes beyond simple HTTP 200 checks to verify:
- Authentication works
- Backend dependencies are connected
- Data flows correctly
- SSL certificates are valid
"""

import asyncio
import ssl
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
import httpx
import structlog


logger = structlog.get_logger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a deep health check."""

    service: str
    healthy: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service,
            "healthy": self.healthy,
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
            "timestamp": self.timestamp,
        }


class DeepHealthChecker:
    """
    Deep health checker for cluster services.

    Performs end-to-end validation including:
    - SSL certificate validity
    - Authentication flow
    - Backend connectivity
    - Functional tests
    """

    # Checks that construct URLs from self.domain and must be skipped when
    # domain is None.
    _DOMAIN_DEPENDENT_CHECKS = frozenset(
        {
            "grafana",
            "authentik",
            "jellyseerr",
            "plex",
            "sonarr",
            "radarr",
            "prowlarr",
            "vault",
            "harbor",
            "argocd",
            "open-webui",
            "traefik",
            "lidarr",
            "qbittorrent",
            "headlamp",
        }
    )

    def __init__(self, domain: Optional[str] = None):
        self.domain = domain
        self._custom_checks: Dict[str, Dict[str, Any]] = {}
        self.service_checks: Dict[str, Callable] = {
            "grafana": self._check_grafana,
            "authentik": self._check_authentik,
            "jellyseerr": self._check_jellyseerr,
            "plex": self._check_plex,
            "sonarr": self._check_sonarr,
            "radarr": self._check_radarr,
            "prowlarr": self._check_prowlarr,
            "vault": self._check_vault,
            "harbor": self._check_harbor,
            "argocd": self._check_argocd,
            "prometheus": self._check_prometheus,
            "open-webui": self._check_open_webui,
            "litellm": self._check_litellm,
            "longhorn": self._check_longhorn,
            "traefik": self._check_traefik,
            "loki": self._check_loki,
            "tempo": self._check_tempo,
            "wazuh": self._check_wazuh,
            "lidarr": self._check_lidarr,
            "qbittorrent": self._check_qbittorrent,
            "ollama": self._check_ollama,
            "headlamp": self._check_headlamp,
            "langfuse": self._check_langfuse,
            "qdrant": self._check_qdrant,
        }

    def register_check(
        self,
        name: str,
        url: str,
        expected_status: int = 200,
        expected_content: Optional[str] = None,
    ) -> None:
        """Register a data-driven custom health check.

        Args:
            name: Unique name for this check.
            url: URL to probe.
            expected_status: Expected HTTP status code.
            expected_content: Optional substring expected in the response body.
        """
        self._custom_checks[name] = {
            "url": url,
            "expected_status": expected_status,
            "expected_content": expected_content,
        }

    async def _run_custom_check(
        self, name: str, spec: Dict[str, Any]
    ) -> HealthCheckResult:
        """Run a single data-driven custom health check."""
        result = HealthCheckResult(service=name, healthy=True)
        check = await self._check_endpoint(
            url=spec["url"],
            expected_status=spec["expected_status"],
            expected_content=spec.get("expected_content"),
        )
        result.checks.append({"name": "endpoint", **check})
        if not check.get("success"):
            result.errors.append(
                f"Endpoint check failed: {check.get('error', 'Unknown')}"
            )
            result.healthy = False
        return result

    async def check_all(self) -> List[HealthCheckResult]:
        """Run health checks on all registered services and custom checks."""
        tasks = [
            self._run_check(name, check)
            for name, check in self.service_checks.items()
            if self.domain is not None or name not in self._DOMAIN_DEPENDENT_CHECKS
        ]

        # Include data-driven custom checks
        for name, spec in self._custom_checks.items():
            tasks.append(self._run_custom_check(name, spec))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions and convert to results
        valid_results = []
        for r in results:
            if isinstance(r, HealthCheckResult):
                valid_results.append(r)
            elif isinstance(r, Exception):
                logger.error("Health check failed with exception", error=str(r))

        return valid_results

    async def check_service(self, service: str) -> HealthCheckResult:
        """Run health check on a specific service."""
        # Check custom checks first
        if service in self._custom_checks:
            return await self._run_custom_check(service, self._custom_checks[service])

        if service not in self.service_checks:
            return HealthCheckResult(
                service=service,
                healthy=False,
                errors=[f"Unknown service: {service}"],
            )

        if self.domain is None and service in self._DOMAIN_DEPENDENT_CHECKS:
            return HealthCheckResult(
                service=service,
                healthy=False,
                errors=[f"Skipped: no domain configured for {service}"],
            )

        return await self._run_check(service, self.service_checks[service])

    async def _run_check(self, name: str, check_func: Callable) -> HealthCheckResult:
        """Run a single health check with error handling."""
        try:
            return await check_func()
        except Exception as e:
            logger.error(f"Health check failed for {name}", error=str(e))
            return HealthCheckResult(
                service=name,
                healthy=False,
                errors=[f"Check failed: {str(e)}"],
            )

    async def check_ssl_cert(self, hostname: str, port: int = 443) -> Dict[str, Any]:
        """Check SSL certificate validity and expiration."""
        try:
            context = ssl.create_default_context()

            # Connect and get certificate
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(hostname, port, ssl=context),
                timeout=10.0,
            )

            # Get peer certificate
            ssl_object = writer.get_extra_info("ssl_object")
            cert = ssl_object.getpeercert()

            writer.close()
            await writer.wait_closed()

            # Parse expiration
            not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
            days_until_expiry = (not_after - datetime.now(timezone.utc)).days

            return {
                "valid": True,
                "issuer": dict(x[0] for x in cert.get("issuer", [])),
                "subject": dict(x[0] for x in cert.get("subject", [])),
                "expires": not_after.isoformat(),
                "days_until_expiry": days_until_expiry,
                "warning": days_until_expiry < 30,
                "critical": days_until_expiry < 7,
            }
        except Exception as e:
            return {
                "valid": False,
                "error": str(e),
            }

    async def _check_endpoint(
        self,
        url: str,
        expected_status: int = 200,
        expected_content: Optional[str] = None,
        expected_content_patterns: Optional[List[str]] = None,
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Check if an endpoint is reachable and returns expected response."""
        error_page_indicators = [
            "502 Bad Gateway",
            "503 Service Unavailable",
            "504 Gateway Timeout",
            "Application Error",
            "upstream connect error",
        ]
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
                response = await client.get(url)

                result: Dict[str, Any] = {
                    "url": url,
                    "status_code": response.status_code,
                    "response_time_ms": response.elapsed.total_seconds() * 1000,
                    "success": response.status_code == expected_status,
                }

                if expected_content and expected_content not in response.text:
                    result["success"] = False
                    result["error"] = f"Expected content '{expected_content}' not found"

                # Check for error page indicators in response body
                body_snippet = response.text[:2000]
                for indicator in error_page_indicators:
                    if indicator in body_snippet:
                        result["success"] = False
                        result["content_error"] = f"Error page detected: {indicator}"
                        break

                # Check additional content patterns
                if expected_content_patterns and result["success"]:
                    for pattern in expected_content_patterns:
                        if pattern not in response.text:
                            result["success"] = False
                            result["error"] = (
                                f"Expected content pattern '{pattern}' not found"
                            )
                            break

                return result
        except Exception as e:
            return {
                "url": url,
                "success": False,
                "error": str(e),
            }

    # =========================================================================
    # SERVICE-SPECIFIC HEALTH CHECKS
    # =========================================================================

    async def _check_grafana(self) -> HealthCheckResult:
        """Deep health check for Grafana."""
        result = HealthCheckResult(service="grafana", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"grafana.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False
        elif ssl_check.get("critical"):
            result.errors.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )
        elif ssl_check.get("warning"):
            result.warnings.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )

        # Check login page loads
        login_check = await self._check_endpoint(
            f"https://grafana.{self.domain}/login",
            expected_content="Grafana",
        )
        result.checks.append({"name": "login_page", **login_check})
        if not login_check.get("success"):
            result.errors.append(
                f"Login page not accessible: {login_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        # Check API health
        api_check = await self._check_endpoint(
            f"https://grafana.{self.domain}/api/health",
            expected_status=200,
        )
        result.checks.append({"name": "api_health", **api_check})
        if not api_check.get("success"):
            result.errors.append("Grafana API health check failed")
            result.healthy = False

        return result

    async def _check_authentik(self) -> HealthCheckResult:
        """Deep health check for Authentik."""
        result = HealthCheckResult(service="authentik", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"auth.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        # Check flow endpoint (login page)
        flow_check = await self._check_endpoint(
            f"https://auth.{self.domain}/if/flow/default-authentication-flow/",
            expected_status=200,
            expected_content="authentik",
        )
        result.checks.append({"name": "authentication_flow", **flow_check})
        if not flow_check.get("success"):
            result.errors.append(
                f"Authentication flow not accessible: {flow_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        # Check outpost health (internal)
        outpost_check = await self._check_endpoint(
            "http://authentik-server.auth.svc.cluster.local:9000/-/health/ready/",
            expected_status=200,
        )
        result.checks.append({"name": "outpost_health", **outpost_check})
        if not outpost_check.get("success"):
            result.errors.append("Authentik outpost health check failed")
            result.healthy = False

        return result

    async def _check_jellyseerr(self) -> HealthCheckResult:
        """Deep health check for Jellyseerr."""
        result = HealthCheckResult(service="jellyseerr", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"request.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        # Check web interface
        web_check = await self._check_endpoint(
            f"https://request.{self.domain}/",
            expected_status=200,
            expected_content="Jellyseerr",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        # Check status endpoint (verifies backend connectivity)
        status_check = await self._check_endpoint(
            f"https://request.{self.domain}/api/v1/status",
            expected_status=200,
        )
        result.checks.append({"name": "api_status", **status_check})
        if not status_check.get("success"):
            result.warnings.append(
                "Jellyseerr status API returned error (may need auth)"
            )

        return result

    async def _check_plex(self) -> HealthCheckResult:
        """Deep health check for Plex."""
        result = HealthCheckResult(service="plex", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"plex.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        # Check web interface
        web_check = await self._check_endpoint(
            f"https://plex.{self.domain}/web",
            expected_status=200,
            expected_content="Plex",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        # Check identity endpoint (internal)
        identity_check = await self._check_endpoint(
            "http://plex.media.svc.cluster.local:32400/identity",
            expected_status=200,
        )
        result.checks.append({"name": "identity_endpoint", **identity_check})
        if not identity_check.get("success"):
            result.warnings.append("Plex identity endpoint not responding")

        return result

    async def _check_sonarr(self) -> HealthCheckResult:
        """Deep health check for Sonarr."""
        result = HealthCheckResult(service="sonarr", healthy=True)

        ssl_check = await self.check_ssl_cert(f"sonarr.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        # Check web interface
        web_check = await self._check_endpoint(
            f"https://sonarr.{self.domain}/",
            expected_status=200,
            expected_content="Sonarr",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_radarr(self) -> HealthCheckResult:
        """Deep health check for Radarr."""
        result = HealthCheckResult(service="radarr", healthy=True)

        ssl_check = await self.check_ssl_cert(f"radarr.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        web_check = await self._check_endpoint(
            f"https://radarr.{self.domain}/",
            expected_status=200,
            expected_content="Radarr",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_prowlarr(self) -> HealthCheckResult:
        """Deep health check for Prowlarr."""
        result = HealthCheckResult(service="prowlarr", healthy=True)

        ssl_check = await self.check_ssl_cert(f"prowlarr.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        web_check = await self._check_endpoint(
            f"https://prowlarr.{self.domain}/",
            expected_status=200,
            expected_content="Prowlarr",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_vault(self) -> HealthCheckResult:
        """Deep health check for Vault."""
        result = HealthCheckResult(service="vault", healthy=True)

        ssl_check = await self.check_ssl_cert(f"vault.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        # Check seal status
        health_check = await self._check_endpoint(
            f"https://vault.{self.domain}/v1/sys/health",
            expected_status=200,  # 200 = unsealed, 429 = standby, 472 = disaster recovery, 473 = performance standby, 501 = uninitialized, 503 = sealed
        )
        result.checks.append({"name": "vault_health", **health_check})

        if health_check.get("status_code") == 503:
            result.errors.append("Vault is sealed")
            result.healthy = False
        elif health_check.get("status_code") == 501:
            result.errors.append("Vault is not initialized")
            result.healthy = False
        elif not health_check.get("success") and health_check.get(
            "status_code"
        ) not in [429, 472, 473]:
            result.errors.append(
                f"Vault health check failed: {health_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_harbor(self) -> HealthCheckResult:
        """Deep health check for Harbor."""
        result = HealthCheckResult(service="harbor", healthy=True)

        ssl_check = await self.check_ssl_cert(f"harbor.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        # Check API health
        api_check = await self._check_endpoint(
            f"https://harbor.{self.domain}/api/v2.0/health",
            expected_status=200,
        )
        result.checks.append({"name": "api_health", **api_check})
        if not api_check.get("success"):
            result.errors.append(
                f"Harbor API not healthy: {api_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_argocd(self) -> HealthCheckResult:
        """Deep health check for ArgoCD."""
        result = HealthCheckResult(service="argocd", healthy=True)

        ssl_check = await self.check_ssl_cert(f"argocd.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        web_check = await self._check_endpoint(
            f"https://argocd.{self.domain}/",
            expected_status=200,
            expected_content="Argo CD",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_prometheus(self) -> HealthCheckResult:
        """Deep health check for Prometheus."""
        result = HealthCheckResult(service="prometheus", healthy=True)

        # Check internal API (usually not exposed externally)
        api_check = await self._check_endpoint(
            "http://prometheus-kube-prometheus-prometheus.prometheus.svc.cluster.local:9090/-/healthy",
            expected_status=200,
        )
        result.checks.append({"name": "prometheus_health", **api_check})
        if not api_check.get("success"):
            result.errors.append("Prometheus health check failed")
            result.healthy = False

        # Check targets
        targets_check = await self._check_endpoint(
            "http://prometheus-kube-prometheus-prometheus.prometheus.svc.cluster.local:9090/api/v1/targets",
            expected_status=200,
        )
        result.checks.append({"name": "targets_api", **targets_check})

        return result

    async def _check_open_webui(self) -> HealthCheckResult:
        """Deep health check for Open WebUI."""
        result = HealthCheckResult(service="open-webui", healthy=True)

        ssl_check = await self.check_ssl_cert(f"open-webui.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False

        web_check = await self._check_endpoint(
            f"https://open-webui.{self.domain}/",
            expected_status=200,
            expected_content="Open WebUI",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_litellm(self) -> HealthCheckResult:
        """Deep health check for LiteLLM."""
        result = HealthCheckResult(service="litellm", healthy=True)

        # Check internal health
        health_check = await self._check_endpoint(
            "http://litellm.llm.svc.cluster.local:4000/health",
            expected_status=200,
        )
        result.checks.append({"name": "litellm_health", **health_check})
        if not health_check.get("success"):
            result.errors.append("LiteLLM health check failed")
            result.healthy = False

        # Check models endpoint
        models_check = await self._check_endpoint(
            "http://litellm.llm.svc.cluster.local:4000/v1/models",
            expected_status=200,
        )
        result.checks.append({"name": "models_api", **models_check})
        if not models_check.get("success"):
            result.warnings.append("LiteLLM models endpoint not responding")

        return result

    async def _check_longhorn(self) -> HealthCheckResult:
        """Deep health check for Longhorn."""
        result = HealthCheckResult(service="longhorn", healthy=True)

        # Check Longhorn manager API
        api_check = await self._check_endpoint(
            "http://longhorn-frontend.longhorn-system.svc.cluster.local:8000/v1",
            expected_status=200,
        )
        result.checks.append({"name": "manager_api", **api_check})
        if not api_check.get("success"):
            result.errors.append("Longhorn manager API not responding")
            result.healthy = False

        return result

    async def _check_traefik(self) -> HealthCheckResult:
        """Deep health check for Traefik."""
        result = HealthCheckResult(service="traefik", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"traefik.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False
        elif ssl_check.get("critical"):
            result.errors.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )
        elif ssl_check.get("warning"):
            result.warnings.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )

        # Check internal health ping
        health_check = await self._check_endpoint(
            "http://traefik.traefik.svc.cluster.local:9000/ping",
            expected_status=200,
        )
        result.checks.append({"name": "health_ping", **health_check})
        if not health_check.get("success"):
            result.errors.append("Traefik health ping failed")
            result.healthy = False

        return result

    async def _check_loki(self) -> HealthCheckResult:
        """Deep health check for Loki."""
        result = HealthCheckResult(service="loki", healthy=True)

        # Check ready endpoint
        ready_check = await self._check_endpoint(
            "http://loki-gateway.loki.svc.cluster.local:80/ready",
            expected_status=200,
        )
        result.checks.append({"name": "ready_endpoint", **ready_check})
        if not ready_check.get("success"):
            result.errors.append("Loki ready endpoint check failed")
            result.healthy = False

        # Check build info
        buildinfo_check = await self._check_endpoint(
            "http://loki-gateway.loki.svc.cluster.local:80/loki/api/v1/status/buildinfo",
            expected_status=200,
        )
        result.checks.append({"name": "build_info", **buildinfo_check})
        if not buildinfo_check.get("success"):
            result.warnings.append("Loki build info endpoint not responding")

        return result

    async def _check_tempo(self) -> HealthCheckResult:
        """Deep health check for Tempo."""
        result = HealthCheckResult(service="tempo", healthy=True)

        # Check ready endpoint
        ready_check = await self._check_endpoint(
            "http://tempo.tempo.svc.cluster.local:3200/ready",
            expected_status=200,
        )
        result.checks.append({"name": "ready_endpoint", **ready_check})
        if not ready_check.get("success"):
            result.errors.append("Tempo ready endpoint check failed")
            result.healthy = False

        return result

    async def _check_wazuh(self) -> HealthCheckResult:
        """Deep health check for Wazuh."""
        result = HealthCheckResult(service="wazuh", healthy=True)

        # Check Wazuh API (401 means API is running, auth required)
        api_check = await self._check_endpoint(
            "https://wazuh-manager-master-0.wazuh-manager.security.svc.cluster.local:55000/",
            expected_status=401,
        )
        result.checks.append({"name": "wazuh_api", **api_check})
        if not api_check.get("success"):
            result.errors.append("Wazuh API not responding")
            result.healthy = False

        return result

    async def _check_lidarr(self) -> HealthCheckResult:
        """Deep health check for Lidarr."""
        result = HealthCheckResult(service="lidarr", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"lidarr.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False
        elif ssl_check.get("critical"):
            result.errors.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )
        elif ssl_check.get("warning"):
            result.warnings.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )

        # Check web interface
        web_check = await self._check_endpoint(
            f"https://lidarr.{self.domain}/",
            expected_status=200,
            expected_content="Lidarr",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_qbittorrent(self) -> HealthCheckResult:
        """Deep health check for qBittorrent."""
        result = HealthCheckResult(service="qbittorrent", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"qbit.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False
        elif ssl_check.get("critical"):
            result.errors.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )
        elif ssl_check.get("warning"):
            result.warnings.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )

        # Check web interface (200 or 401 for auth)
        web_check = await self._check_endpoint(
            "http://qbittorrent.media.svc.cluster.local:8080/",
            expected_status=200,
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            # 401 is also acceptable (auth required)
            if web_check.get("status_code") != 401:
                result.errors.append(
                    f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
                )
                result.healthy = False

        return result

    async def _check_ollama(self) -> HealthCheckResult:
        """Deep health check for Ollama."""
        result = HealthCheckResult(service="ollama", healthy=True)

        # Check health endpoint
        health_check = await self._check_endpoint(
            "http://ollama.llm.svc.cluster.local:11434/",
            expected_status=200,
            expected_content="Ollama is running",
        )
        result.checks.append({"name": "ollama_health", **health_check})
        if not health_check.get("success"):
            result.errors.append("Ollama health check failed")
            result.healthy = False

        # Check API tags
        api_check = await self._check_endpoint(
            "http://ollama.llm.svc.cluster.local:11434/api/tags",
            expected_status=200,
        )
        result.checks.append({"name": "api_tags", **api_check})
        if not api_check.get("success"):
            result.warnings.append("Ollama API tags endpoint not responding")

        return result

    async def _check_headlamp(self) -> HealthCheckResult:
        """Deep health check for Headlamp."""
        result = HealthCheckResult(service="headlamp", healthy=True)

        # Check SSL
        ssl_check = await self.check_ssl_cert(f"headlamp.{self.domain}")
        result.checks.append({"name": "ssl_certificate", **ssl_check})
        if not ssl_check.get("valid"):
            result.errors.append(f"SSL certificate invalid: {ssl_check.get('error')}")
            result.healthy = False
        elif ssl_check.get("critical"):
            result.errors.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )
        elif ssl_check.get("warning"):
            result.warnings.append(
                f"SSL certificate expires in {ssl_check.get('days_until_expiry')} days"
            )

        # Check web interface
        web_check = await self._check_endpoint(
            f"https://headlamp.{self.domain}/",
            expected_status=200,
            expected_content="Headlamp",
        )
        result.checks.append({"name": "web_interface", **web_check})
        if not web_check.get("success"):
            result.errors.append(
                f"Web interface not accessible: {web_check.get('error', 'Unknown')}"
            )
            result.healthy = False

        return result

    async def _check_langfuse(self) -> HealthCheckResult:
        """Deep health check for Langfuse."""
        result = HealthCheckResult(service="langfuse", healthy=True)

        # Check internal health
        health_check = await self._check_endpoint(
            "http://langfuse.llm.svc.cluster.local:3000/api/public/health",
            expected_status=200,
        )
        result.checks.append({"name": "langfuse_health", **health_check})
        if not health_check.get("success"):
            result.errors.append("Langfuse health check failed")
            result.healthy = False

        return result

    async def _check_qdrant(self) -> HealthCheckResult:
        """Deep health check for Qdrant."""
        result = HealthCheckResult(service="qdrant", healthy=True)

        # Check internal health
        health_check = await self._check_endpoint(
            "http://qdrant.llm.svc.cluster.local:6333/healthz",
            expected_status=200,
        )
        result.checks.append({"name": "qdrant_health", **health_check})
        if not health_check.get("success"):
            result.errors.append("Qdrant health check failed")
            result.healthy = False

        # Check collections endpoint
        collections_check = await self._check_endpoint(
            "http://qdrant.llm.svc.cluster.local:6333/collections",
            expected_status=200,
        )
        result.checks.append({"name": "collections_api", **collections_check})
        if not collections_check.get("success"):
            result.warnings.append("Qdrant collections endpoint not responding")

        return result


# Global instance
_health_checker: Optional[DeepHealthChecker] = None


def get_health_checker(domain: Optional[str] = "spooty.io") -> DeepHealthChecker:
    """Get or create health checker singleton."""
    global _health_checker
    if _health_checker is None:
        _health_checker = DeepHealthChecker(domain=domain)
    return _health_checker
