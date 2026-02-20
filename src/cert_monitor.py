"""
cert-manager Certificate CRD monitor for Cluster Guardian.

Watches Certificate, CertificateRequest, Issuer, and ClusterIssuer
resources for renewal failures and approaching expiration.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog
from kubernetes import client, config

from .config import settings

logger = structlog.get_logger(__name__)

CERT_GROUP = "cert-manager.io"
CERT_VERSION = "v1"
EXPIRY_WARNING_DAYS = 7


class CertMonitor:
    """Monitor cert-manager Certificate CRDs for health and expiration."""

    def __init__(self):
        try:
            if settings.kubeconfig_path:
                config.load_kube_config(settings.kubeconfig_path)
            else:
                config.load_incluster_config()
        except config.ConfigException:
            logger.warning("Failed to load in-cluster config, trying kubeconfig")
            config.load_kube_config()

        self.custom_api = client.CustomObjectsApi()

    def _parse_ready_condition(self, status: dict) -> tuple[bool, str]:
        """Extract Ready state and message from status conditions."""
        for cond in status.get("conditions", []):
            if cond.get("type") == "Ready":
                return cond.get("status") == "True", cond.get("message", "")
        return False, "No Ready condition found"

    def _parse_not_after(self, status: dict) -> Optional[datetime]:
        """Parse status.notAfter into a datetime."""
        raw = status.get("notAfter")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def _days_until(self, dt: Optional[datetime]) -> Optional[float]:
        if dt is None:
            return None
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86400

    async def get_certificates(self, namespace: str | None = None) -> list[dict]:
        """List all Certificate resources with their status.

        CRD: certificates.cert-manager.io/v1
        """
        try:
            if namespace:
                resp = self.custom_api.list_namespaced_custom_object(
                    CERT_GROUP, CERT_VERSION, namespace, "certificates",
                )
            else:
                resp = self.custom_api.list_cluster_custom_object(
                    CERT_GROUP, CERT_VERSION, "certificates",
                )
        except Exception as exc:
            logger.error("Failed to list certificates", error=str(exc))
            return []

        results = []
        for item in resp.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            ready, message = self._parse_ready_condition(status)
            not_after = self._parse_not_after(status)

            results.append({
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "dns_names": spec.get("dnsNames", []),
                "ready": ready,
                "not_after": not_after.isoformat() if not_after else None,
                "days_until_expiry": self._days_until(not_after),
                "renewal_time": status.get("renewalTime"),
                "issuer": spec.get("issuerRef", {}).get("name"),
                "message": message,
            })

        return results

    async def get_failing_certificates(self) -> list[dict]:
        """Get certificates that are not Ready or expiring within 7 days."""
        certs = await self.get_certificates()
        cutoff = EXPIRY_WARNING_DAYS
        failing = []
        for cert in certs:
            not_ready = not cert["ready"]
            expiring_soon = (
                cert["days_until_expiry"] is not None
                and cert["days_until_expiry"] <= cutoff
            )
            if not_ready or expiring_soon:
                failing.append(cert)
        return failing

    async def get_certificate_requests(self, namespace: str | None = None) -> list[dict]:
        """List CertificateRequest resources to check for failed issuance.

        CRD: certificaterequests.cert-manager.io/v1
        """
        try:
            if namespace:
                resp = self.custom_api.list_namespaced_custom_object(
                    CERT_GROUP, CERT_VERSION, namespace, "certificaterequests",
                )
            else:
                resp = self.custom_api.list_cluster_custom_object(
                    CERT_GROUP, CERT_VERSION, "certificaterequests",
                )
        except Exception as exc:
            logger.error("Failed to list certificate requests", error=str(exc))
            return []

        results = []
        for item in resp.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            ready, message = self._parse_ready_condition(status)

            results.append({
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "issuer": spec.get("issuerRef", {}).get("name"),
                "ready": ready,
                "message": message,
                "conditions": status.get("conditions", []),
            })

        return results

    async def get_issuers(self, namespace: str | None = None) -> list[dict]:
        """List Issuer and ClusterIssuer resources and their ready status.

        CRDs: issuers.cert-manager.io/v1, clusterissuers.cert-manager.io/v1
        """
        results = []

        # Namespaced Issuers
        try:
            if namespace:
                resp = self.custom_api.list_namespaced_custom_object(
                    CERT_GROUP, CERT_VERSION, namespace, "issuers",
                )
            else:
                resp = self.custom_api.list_cluster_custom_object(
                    CERT_GROUP, CERT_VERSION, "issuers",
                )
            for item in resp.get("items", []):
                meta = item.get("metadata", {})
                status = item.get("status", {})
                ready, message = self._parse_ready_condition(status)
                results.append({
                    "name": meta.get("name"),
                    "namespace": meta.get("namespace"),
                    "kind": "Issuer",
                    "ready": ready,
                    "message": message,
                })
        except Exception as exc:
            logger.error("Failed to list issuers", error=str(exc))

        # ClusterIssuers (always cluster-scoped)
        try:
            resp = self.custom_api.list_cluster_custom_object(
                CERT_GROUP, CERT_VERSION, "clusterissuers",
            )
            for item in resp.get("items", []):
                meta = item.get("metadata", {})
                status = item.get("status", {})
                ready, message = self._parse_ready_condition(status)
                results.append({
                    "name": meta.get("name"),
                    "namespace": None,
                    "kind": "ClusterIssuer",
                    "ready": ready,
                    "message": message,
                })
        except Exception as exc:
            logger.error("Failed to list cluster issuers", error=str(exc))

        return results

    async def health_check(self) -> bool:
        """Check if cert-manager CRDs are available."""
        try:
            self.custom_api.list_cluster_custom_object(
                CERT_GROUP, CERT_VERSION, "certificates",
            )
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_cert_monitor: Optional[CertMonitor] = None


def get_cert_monitor() -> CertMonitor:
    """Get or create the CertMonitor singleton."""
    global _cert_monitor
    if _cert_monitor is None:
        _cert_monitor = CertMonitor()
    return _cert_monitor
