"""
K8sGPT gRPC client for cluster analysis.

Integrates with the K8sGPT service to get AI-powered
analysis of cluster issues via gRPC.
"""

from typing import Dict, List, Any, Optional
from urllib.parse import urlparse

import grpc
import grpc.aio
import structlog

from .config import settings
from .proto import k8sgpt_pb2, k8sgpt_pb2_grpc

logger = structlog.get_logger(__name__)


def _parse_grpc_target(url: str) -> str:
    """Extract host:port from a URL for gRPC channel creation."""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8080
    return f"{host}:{port}"


class K8sGPTClient:
    """Client for K8sGPT gRPC API."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.k8sgpt_url
        self.enabled = settings.k8sgpt_enabled
        self._target = _parse_grpc_target(self.base_url)

    async def analyze(self, filters: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run K8sGPT analysis on the cluster.

        Args:
            filters: Optional list of analyzers to run
                     (e.g., ["Pod", "Service", "Deployment"])

        Returns:
            Analysis results with issues and explanations
        """
        if not self.enabled:
            return {"enabled": False, "results": []}

        try:
            async with grpc.aio.insecure_channel(self._target) as channel:
                stub = k8sgpt_pb2_grpc.ServerAnalyzerServiceStub(channel)

                request = k8sgpt_pb2.AnalyzeRequest(
                    explain=False,
                )
                if filters:
                    request.filters.extend(filters)

                response = await stub.Analyze(request, timeout=60.0)

                results = []
                for result in response.results:
                    errors = [err.text for err in result.error]
                    results.append({
                        "kind": result.kind,
                        "name": result.name,
                        "error": errors,
                        "details": result.details,
                        "parentObject": result.parent_object,
                    })

                logger.info(
                    "K8sGPT analysis complete",
                    result_count=len(results),
                )
                return {
                    "status": response.status,
                    "problems": response.problems,
                    "results": results,
                }

        except grpc.aio.AioRpcError as e:
            logger.error(
                "K8sGPT request failed",
                error=str(e),
                code=e.code().name,
            )
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
            # k8sgpt encodes namespace in name as "namespace/name"
            raw_name = result.get("name", "")
            if "/" in raw_name:
                namespace, name = raw_name.split("/", 1)
            else:
                namespace = "default"
                name = raw_name

            issues.append({
                "kind": result.get("kind", "Unknown"),
                "name": name,
                "namespace": namespace,
                "errors": result.get("error", []),
                "details": result.get("details", ""),
                "parent_object": result.get("parentObject", ""),
            })

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
        """Check if K8sGPT service is healthy by checking gRPC channel connectivity."""
        if not self.enabled:
            return True

        try:
            async with grpc.aio.insecure_channel(self._target) as channel:
                # Use a lightweight Analyze call with no results expected
                # to verify the service is responsive
                stub = k8sgpt_pb2_grpc.ServerAnalyzerServiceStub(channel)
                request = k8sgpt_pb2.AnalyzeRequest(
                    explain=False,
                    filters=["Nonexistent"],
                )
                await stub.Analyze(request, timeout=5.0)
                return True
        except Exception as e:
            logger.warning("K8sGPT health check failed", error=str(e))
            return False


_k8sgpt_client: Optional[K8sGPTClient] = None


def get_k8sgpt_client() -> K8sGPTClient:
    """Get or create K8sGPT client singleton."""
    global _k8sgpt_client
    if _k8sgpt_client is None:
        _k8sgpt_client = K8sGPTClient()
    return _k8sgpt_client
