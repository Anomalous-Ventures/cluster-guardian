"""E2E tests for health endpoints."""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestHealth:
    async def test_health_returns_healthy(self, http_client):
        resp = await http_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "timestamp" in data

    async def test_health_reports_components(self, http_client):
        resp = await http_client.get("/health")
        data = resp.json()
        assert "components" in data
        assert isinstance(data["components"], dict)

    async def test_live_endpoint(self, http_client):
        resp = await http_client.get("/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    async def test_ready_endpoint(self, http_client):
        resp = await http_client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    async def test_health_after_investigation(self, http_client):
        # Trigger an investigation first
        await http_client.post(
            "/api/v1/investigate",
            json={"description": "health check stability test"},
            timeout=60.0,
        )
        # Health should still be healthy
        resp = await http_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
