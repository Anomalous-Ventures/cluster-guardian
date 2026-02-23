"""
HTTP client for the AI Dev Controller.

Submits long-term goals and tracks their status for issues that
require code or infrastructure changes beyond quick SRE fixes.
"""

from typing import Any, Optional

import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)


class DevControllerClient:
    """HTTP client for submitting long-term goals to AI Dev Controller."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(15.0)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout)

    async def submit_goal(
        self, description: str, acceptance_criteria: list[str]
    ) -> dict[str, Any]:
        """POST /dev-loop/goals with a Goal object."""
        payload = {
            "description": description,
            "acceptance_criteria": acceptance_criteria,
            "source": "cluster-guardian",
        }
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self.base_url}/dev-loop/goals",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("dev_controller submit_goal failed", error=str(exc))
            return {"error": str(exc)}

    async def get_loop_status(self) -> dict[str, Any]:
        """GET /dev-loop/status - check if dev loop is running."""
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/dev-loop/status")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("dev_controller get_loop_status failed", error=str(exc))
            return {"error": str(exc)}

    async def get_task_status(self, goal_description: str) -> dict[str, Any]:
        """GET /dev-loop/tasks - find tasks matching a submitted goal."""
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self.base_url}/dev-loop/tasks",
                    params={"query": goal_description[:200]},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("dev_controller get_task_status failed", error=str(exc))
            return {"error": str(exc)}

    async def health_check(self) -> bool:
        """GET /health on dev controller."""
        try:
            async with self._client() as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_dev_controller: Optional[DevControllerClient] = None


def get_dev_controller() -> DevControllerClient:
    """Get or create DevControllerClient singleton."""
    global _dev_controller
    if _dev_controller is None:
        _dev_controller = DevControllerClient(base_url=settings.dev_controller_url)
    return _dev_controller
