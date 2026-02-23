"""Tests for the AI Dev Controller HTTP client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.dev_controller_client import DevControllerClient


@pytest.fixture
def client(settings_env):
    return DevControllerClient(base_url="http://dev-controller:8096")


class _FakeHTTPClient:
    """Fake httpx.AsyncClient that works as an async context manager."""

    def __init__(self, *, post=None, get=None):
        self.post = post or AsyncMock()
        self.get = get or AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _ok_response(**json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


class TestSubmitGoal:
    @pytest.mark.asyncio
    async def test_successful_submission(self, client):
        fake = _FakeHTTPClient(
            post=AsyncMock(return_value=_ok_response(status="injected", new_tasks=2))
        )

        with patch.object(client, "_client", return_value=fake):
            result = await client.submit_goal(
                description="Fix recurring OOM in sonarr",
                acceptance_criteria=["Memory limit increased", "No more OOM kills"],
            )

        assert result["status"] == "injected"
        assert result["new_tasks"] == 2

    @pytest.mark.asyncio
    async def test_submission_error(self, client):
        fake = _FakeHTTPClient(
            post=AsyncMock(side_effect=Exception("connection refused"))
        )

        with patch.object(client, "_client", return_value=fake):
            result = await client.submit_goal(
                description="Fix issue",
                acceptance_criteria=["It works"],
            )

        assert "error" in result
        assert "connection refused" in result["error"]


class TestGetLoopStatus:
    @pytest.mark.asyncio
    async def test_success(self, client):
        fake = _FakeHTTPClient(
            get=AsyncMock(return_value=_ok_response(running=True, phase="working"))
        )

        with patch.object(client, "_client", return_value=fake):
            result = await client.get_loop_status()

        assert result["running"] is True

    @pytest.mark.asyncio
    async def test_error(self, client):
        fake = _FakeHTTPClient(
            get=AsyncMock(side_effect=Exception("timeout"))
        )

        with patch.object(client, "_client", return_value=fake):
            result = await client.get_loop_status()

        assert "error" in result


class TestGetTaskStatus:
    @pytest.mark.asyncio
    async def test_success(self, client):
        fake = _FakeHTTPClient(
            get=AsyncMock(
                return_value=_ok_response(tasks=[{"id": 1, "status": "done"}])
            )
        )

        with patch.object(client, "_client", return_value=fake):
            result = await client.get_task_status("Fix OOM")

        assert "tasks" in result


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self, client):
        resp = MagicMock()
        resp.status_code = 200
        fake = _FakeHTTPClient(get=AsyncMock(return_value=resp))

        with patch.object(client, "_client", return_value=fake):
            result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy(self, client):
        fake = _FakeHTTPClient(
            get=AsyncMock(side_effect=Exception("connection refused"))
        )

        with patch.object(client, "_client", return_value=fake):
            result = await client.health_check()

        assert result is False
