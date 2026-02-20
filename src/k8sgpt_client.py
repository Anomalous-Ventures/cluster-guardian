"""
K8sGPT client for cluster analysis.

Integrates with the K8sGPT service to get AI-powered
analysis of cluster issues.
"""

from typing import Dict, List, Any, Optional
import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)


class K8sGPTClient:
    """Client for K8sGPT API."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.k8sgpt_url
        self.enabled = settings.k8sgpt_enabled

    async def analyze(self, filters: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run K8sGPT analysis on the cluster.

        Args:
            filters: Optional list of analyzers to run (e.g., ["Pod", "Service", "Deployment"])

        Returns:
            Analysis results with issues and explanations
        """
        if not self.enabled:
            return {"enabled": False, "results": []}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                params = {}
                if filters:
                    params["filter"] = ",".join(filters)

                response = await client.get(
                    f"{self.base_url}/v1/analyze",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                logger.info(
                    "K8sGPT analysis complete",
                    result_count=len(data.get("results", [])),
                )
                return data

        except httpx.HTTPError as e:
            logger.error("K8sGPT request failed", error=str(e))
            return {"error": str(e), "results": []}

    async def get_issues(self) -> List[Dict[str, Any]]:
        """
        Get list of current cluster issues from K8sGPT.

        Returns:
            List of issues with kind, name, namespace, and error details
        """
        analysis = await self.analyze()
        issues = []

        for result in analysis.get("results", []):
            issues.append(
                {
                    "kind": result.get("kind", "Unknown"),
                    "name": result.get("name", ""),
                    "namespace": result.get("namespace", "default"),
                    "errors": result.get("error", []),
                    "details": result.get("details", ""),
                    "parent_object": result.get("parentObject", ""),
                }
            )

        return issues

    async def get_issue_summary(self) -> str:
        """
        Get a text summary of current cluster issues.

        Returns:
            Human-readable summary of issues
        """
        issues = await self.get_issues()

        if not issues:
            return "No issues detected by K8sGPT."

        lines = ["Current cluster issues detected by K8sGPT:"]
        for issue in issues:
            lines.append(
                f"- {issue['kind']}/{issue['name']} in {issue['namespace']}: "
                f"{', '.join(issue['errors'][:2]) if issue['errors'] else 'Unknown error'}"
            )

        return "\n".join(lines)

    async def health_check(self) -> bool:
        """Check if K8sGPT service is healthy."""
        if not self.enabled:
            return True  # Consider disabled as "healthy"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/healthz")
                return response.status_code == 200
        except Exception as e:
            logger.warning("K8sGPT health check failed", error=str(e))
            return False


# Global client instance
_k8sgpt_client: Optional[K8sGPTClient] = None


def get_k8sgpt_client() -> K8sGPTClient:
    """Get or create K8sGPT client singleton."""
    global _k8sgpt_client
    if _k8sgpt_client is None:
        _k8sgpt_client = K8sGPTClient()
    return _k8sgpt_client
