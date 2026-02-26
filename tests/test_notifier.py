"""Tests for src.notifier notification dispatch functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.notifier import (
    notify_all,
    send_discord,
    send_pagerduty,
    send_slack,
)


def _mock_httpx_client(*, raise_on_post=False, exception=None):
    """Build a mock httpx.AsyncClient context manager.

    Args:
        raise_on_post: If True, ``post()`` raises an exception.
        exception: Custom exception instance (defaults to ``RuntimeError``).
    """
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    if raise_on_post:
        mock_client.post = AsyncMock(side_effect=exception or RuntimeError("http boom"))
    else:
        mock_client.post = AsyncMock(return_value=mock_response)

    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# send_slack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_slack_not_configured(settings_env, monkeypatch):
    """Returns False when slack_webhook_url is None."""
    monkeypatch.setattr(settings, "slack_webhook_url", None)
    result = await send_slack("test message", "info")
    assert result is False


@pytest.mark.asyncio
async def test_send_slack_success(settings_env, monkeypatch):
    """Returns True when httpx POST succeeds."""
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/test")
    mock_client = _mock_httpx_client()
    with patch("src.notifier.httpx.AsyncClient", return_value=mock_client):
        result = await send_slack("deploy complete", "info")

    assert result is True
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_slack_http_error(settings_env, monkeypatch):
    """Returns False when httpx raises an exception."""
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/test")
    mock_client = _mock_httpx_client(raise_on_post=True)
    with patch("src.notifier.httpx.AsyncClient", return_value=mock_client):
        result = await send_slack("deploy complete", "info")

    assert result is False


# ---------------------------------------------------------------------------
# send_discord
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_discord_not_configured(settings_env, monkeypatch):
    """Returns False when discord_webhook_url is None."""
    monkeypatch.setattr(settings, "discord_webhook_url", None)
    result = await send_discord("test message", "info")
    assert result is False


@pytest.mark.asyncio
async def test_send_discord_success(settings_env, monkeypatch):
    """Returns True when httpx POST succeeds."""
    monkeypatch.setattr(
        settings, "discord_webhook_url", "https://discord.com/api/webhooks/test"
    )
    mock_client = _mock_httpx_client()
    with patch("src.notifier.httpx.AsyncClient", return_value=mock_client):
        result = await send_discord("node rebooted", "warning")

    assert result is True
    mock_client.post.assert_awaited_once()


# ---------------------------------------------------------------------------
# send_pagerduty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_pagerduty_not_configured(settings_env, monkeypatch):
    """Returns False when pagerduty_integration_key is None."""
    monkeypatch.setattr(settings, "pagerduty_integration_key", None)
    result = await send_pagerduty("incident", "critical")
    assert result is False


# ---------------------------------------------------------------------------
# notify_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_all_no_channels(settings_env, monkeypatch):
    """Returns empty dict when no channels are configured."""
    monkeypatch.setattr(settings, "slack_webhook_url", None)
    monkeypatch.setattr(settings, "discord_webhook_url", None)
    monkeypatch.setattr(settings, "teams_webhook_url", None)
    monkeypatch.setattr(settings, "pagerduty_integration_key", None)
    monkeypatch.setattr(settings, "custom_webhook_url", None)
    monkeypatch.setattr(settings, "email_smtp_host", None)

    result = await notify_all("hello", "info")
    assert result == {}


@pytest.mark.asyncio
async def test_notify_all_slack_only(settings_env, monkeypatch):
    """Only slack appears in results when only slack is configured."""
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.com/test")
    monkeypatch.setattr(settings, "discord_webhook_url", None)
    monkeypatch.setattr(settings, "teams_webhook_url", None)
    monkeypatch.setattr(settings, "pagerduty_integration_key", None)
    monkeypatch.setattr(settings, "custom_webhook_url", None)
    monkeypatch.setattr(settings, "email_smtp_host", None)

    mock_client = _mock_httpx_client()
    with patch("src.notifier.httpx.AsyncClient", return_value=mock_client):
        result = await notify_all("slack only test", "info")

    assert list(result.keys()) == ["slack"]
    assert result["slack"] is True


@pytest.mark.asyncio
async def test_notify_all_pagerduty_skipped_for_info(settings_env, monkeypatch):
    """PagerDuty is not called when severity is info, even if configured."""
    monkeypatch.setattr(settings, "slack_webhook_url", None)
    monkeypatch.setattr(settings, "discord_webhook_url", None)
    monkeypatch.setattr(settings, "teams_webhook_url", None)
    monkeypatch.setattr(settings, "pagerduty_integration_key", "test-key-123")
    monkeypatch.setattr(settings, "custom_webhook_url", None)
    monkeypatch.setattr(settings, "email_smtp_host", None)

    result = await notify_all("low priority event", "info")

    assert "pagerduty" not in result
