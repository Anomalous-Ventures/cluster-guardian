"""Tests for pure logic in src.agent (no LLM calls)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.agent import _is_quiet_hours, create_tools
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
