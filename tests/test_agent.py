"""Tests for pure logic in src.agent (no LLM calls)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent import _is_quiet_hours, create_tools, ClusterGuardian
from src.config import settings


class TestIsQuietHours:
    """Tests for the _is_quiet_hours helper."""

    def test_quiet_hours_disabled(self, settings_env, monkeypatch):
        monkeypatch.setattr(settings, "quiet_hours_start", None)
        monkeypatch.setattr(settings, "quiet_hours_end", None)
        assert _is_quiet_hours() is False

    def test_quiet_hours_same_day_inside(self, settings_env, monkeypatch):
        monkeypatch.setattr(settings, "quiet_hours_start", "02:00")
        monkeypatch.setattr(settings, "quiet_hours_end", "06:00")
        monkeypatch.setattr(settings, "quiet_hours_tz", "UTC")

        with patch("src.agent.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 3, 0, tzinfo=timezone.utc)
            assert _is_quiet_hours() is True

    def test_quiet_hours_same_day_outside(self, settings_env, monkeypatch):
        monkeypatch.setattr(settings, "quiet_hours_start", "02:00")
        monkeypatch.setattr(settings, "quiet_hours_end", "06:00")
        monkeypatch.setattr(settings, "quiet_hours_tz", "UTC")

        with patch("src.agent.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 7, 0, tzinfo=timezone.utc)
            assert _is_quiet_hours() is False

    def test_quiet_hours_overnight_inside_late(self, settings_env, monkeypatch):
        monkeypatch.setattr(settings, "quiet_hours_start", "22:00")
        monkeypatch.setattr(settings, "quiet_hours_end", "06:00")
        monkeypatch.setattr(settings, "quiet_hours_tz", "UTC")

        with patch("src.agent.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 23, 0, tzinfo=timezone.utc)
            assert _is_quiet_hours() is True

    def test_quiet_hours_overnight_inside_early(self, settings_env, monkeypatch):
        monkeypatch.setattr(settings, "quiet_hours_start", "22:00")
        monkeypatch.setattr(settings, "quiet_hours_end", "06:00")
        monkeypatch.setattr(settings, "quiet_hours_tz", "UTC")

        with patch("src.agent.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 3, 0, tzinfo=timezone.utc)
            assert _is_quiet_hours() is True

    def test_quiet_hours_overnight_outside(self, settings_env, monkeypatch):
        monkeypatch.setattr(settings, "quiet_hours_start", "22:00")
        monkeypatch.setattr(settings, "quiet_hours_end", "06:00")
        monkeypatch.setattr(settings, "quiet_hours_tz", "UTC")

        with patch("src.agent.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
            assert _is_quiet_hours() is False

    def test_quiet_hours_bad_timezone(self, settings_env, monkeypatch):
        monkeypatch.setattr(settings, "quiet_hours_start", "02:00")
        monkeypatch.setattr(settings, "quiet_hours_end", "06:00")
        monkeypatch.setattr(settings, "quiet_hours_tz", "Not/A/Timezone")

        with patch("src.agent.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 1, 3, 0, tzinfo=timezone.utc)
            result = _is_quiet_hours()
            assert isinstance(result, bool)


class TestCreateTools:
    """Tests for the create_tools factory."""

    def test_create_tools_returns_expected_count(self, settings_env):
        tools = create_tools(
            k8s=MagicMock(),
            k8sgpt=MagicMock(),
            health_checker=MagicMock(),
            prometheus=MagicMock(),
            loki=MagicMock(),
            cert_monitor=MagicMock(),
            storage_monitor=MagicMock(),
            crowdsec=MagicMock(),
            gatus=MagicMock(),
        )
        assert len(tools) >= 42


class TestBroadcastCallback:
    """Tests for set_broadcast_callback and investigation lifecycle events."""

    def _make_guardian(self, settings_env):
        """Create a ClusterGuardian with mocked dependencies."""
        with (
            patch("src.agent.get_k8s_client"),
            patch("src.agent.get_k8sgpt_client"),
            patch("src.agent.get_health_checker"),
            patch("src.agent.get_prometheus_client"),
            patch("src.agent.get_loki_client"),
            patch("src.agent.get_cert_monitor"),
            patch("src.agent.get_storage_monitor"),
            patch("src.agent.get_crowdsec_client"),
            patch("src.agent.get_gatus_client"),
            patch("src.agent.get_ingress_monitor"),
            patch("src.agent.get_dev_controller"),
            patch("src.agent.get_self_tuner"),
            patch("src.agent.create_llm") as mllm,
        ):
            mock_llm = MagicMock()
            mock_llm.bind_tools = MagicMock(return_value=MagicMock())
            mllm.return_value = mock_llm
            guardian = ClusterGuardian()
        return guardian

    def test_set_broadcast_callback(self, settings_env):
        guardian = self._make_guardian(settings_env)
        assert guardian._broadcast_callback is None

        callback = AsyncMock()
        guardian.set_broadcast_callback(callback)
        assert guardian._broadcast_callback is callback

    @pytest.mark.asyncio
    async def test_broadcast_event_calls_callback(self, settings_env):
        guardian = self._make_guardian(settings_env)
        callback = AsyncMock()
        guardian.set_broadcast_callback(callback)

        event = {"type": "test", "data": {}}
        await guardian._broadcast_event(event)
        callback.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_broadcast_event_no_callback(self, settings_env):
        guardian = self._make_guardian(settings_env)
        # Should not raise when no callback is set
        await guardian._broadcast_event({"type": "test", "data": {}})

    @pytest.mark.asyncio
    async def test_investigate_broadcasts_lifecycle(self, settings_env):
        """investigate_issue should broadcast started/completed events."""
        guardian = self._make_guardian(settings_env)
        captured = []

        async def capture(msg):
            captured.append(msg)

        guardian.set_broadcast_callback(capture)

        # Mock graph.astream to yield no states (empty investigation)
        async def empty_astream(*args, **kwargs):
            return
            yield  # Make it an async generator

        guardian.graph = MagicMock()
        guardian.graph.astream = empty_astream
        guardian.k8s.get_audit_log = AsyncMock(return_value=[])

        result = await guardian.investigate_issue(
            description="test issue",
            investigation_id="inv-test123456",
        )

        assert result["success"] is True
        assert result["investigation_id"] == "inv-test123456"

        # Should have started + completed events
        types = [e["type"] for e in captured]
        assert "investigation_started" in types
        assert "investigation_completed" in types

        # Verify started event
        started = next(e for e in captured if e["type"] == "investigation_started")
        assert started["investigation_id"] == "inv-test123456"

        # Verify completed event
        completed = next(e for e in captured if e["type"] == "investigation_completed")
        assert completed["data"]["status"] == "completed"
        assert "duration_seconds" in completed["data"]

    @pytest.mark.asyncio
    async def test_investigate_auto_generates_id(self, settings_env):
        """investigate_issue should auto-generate an investigation_id if not provided."""
        guardian = self._make_guardian(settings_env)

        async def empty_astream(*args, **kwargs):
            return
            yield

        guardian.graph = MagicMock()
        guardian.graph.astream = empty_astream
        guardian.k8s.get_audit_log = AsyncMock(return_value=[])

        result = await guardian.investigate_issue(description="test issue")
        assert result["investigation_id"].startswith("inv-")

    @pytest.mark.asyncio
    async def test_investigate_failure_broadcasts_failed(self, settings_env):
        """On failure, investigate_issue should broadcast status=failed."""
        guardian = self._make_guardian(settings_env)
        captured = []

        async def capture(msg):
            captured.append(msg)

        guardian.set_broadcast_callback(capture)

        async def failing_astream(*args, **kwargs):
            raise RuntimeError("LLM unavailable")
            yield  # noqa: F841

        guardian.graph = MagicMock()
        guardian.graph.astream = failing_astream
        guardian.k8s.get_audit_log = AsyncMock(return_value=[])

        result = await guardian.investigate_issue(description="test failure")
        assert result["success"] is False

        completed = next(
            (e for e in captured if e["type"] == "investigation_completed"), None
        )
        assert completed is not None
        assert completed["data"]["status"] == "failed"
