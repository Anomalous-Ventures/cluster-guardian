"""E2E tests for investigation endpoint."""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestInvestigation:
    async def test_investigate_returns_success(self, http_client):
        resp = await http_client.post(
            "/api/v1/investigate",
            json={"description": "Check pod health in default namespace"},
            timeout=60.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["summary"]  # non-empty
        assert data["investigation_id"].startswith("inv-")

    async def test_investigate_with_custom_thread_id(self, http_client):
        resp = await http_client.post(
            "/api/v1/investigate",
            json={
                "description": "Check pod health",
                "thread_id": "custom-thread-42",
            },
            timeout=60.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    async def test_investigate_has_timestamp(self, http_client):
        resp = await http_client.post(
            "/api/v1/investigate",
            json={"description": "timestamp test"},
            timeout=60.0,
        )
        data = resp.json()
        assert "timestamp" in data
        # ISO 8601 format
        assert "T" in data["timestamp"]

    @pytest.mark.llm_real
    async def test_investigate_with_real_llm(self, http_client):
        """Run with a real LLM - only when OPENAI_API_KEY is set."""
        import os

        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set")

        resp = await http_client.post(
            "/api/v1/investigate",
            json={
                "description": "Check if there are any pods in CrashLoopBackOff state"
            },
            timeout=120.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["summary"]) > 20  # Real LLM should give substantial response
