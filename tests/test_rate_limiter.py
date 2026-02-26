"""Tests for ActionRateLimiter and AuditLog from k8s_client."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.k8s_client import ActionRateLimiter, AuditLog


# ---------------------------------------------------------------------------
# ActionRateLimiter
# ---------------------------------------------------------------------------


class TestActionRateLimiter:
    """Unit tests for ActionRateLimiter."""

    @pytest.mark.asyncio
    async def test_can_act_under_limit(self, settings_env):
        """Fresh limiter with no recorded actions allows acting."""
        limiter = ActionRateLimiter(max_actions=5)
        with patch.object(limiter, "_refresh_max_actions", new_callable=AsyncMock):
            assert await limiter.can_act() is True

    @pytest.mark.asyncio
    async def test_can_act_at_limit(self, settings_env):
        """Returns False once in-memory deque reaches max_actions."""
        limiter = ActionRateLimiter(max_actions=2)
        now = datetime.now(timezone.utc)
        limiter.actions.append((now, "a1"))
        limiter.actions.append((now, "a2"))

        with patch.object(limiter, "_refresh_max_actions", new_callable=AsyncMock):
            assert await limiter.can_act() is False

    @pytest.mark.asyncio
    async def test_record_action_appends(self, settings_env):
        """record_action appends a (datetime, str) tuple to the deque."""
        limiter = ActionRateLimiter(max_actions=10)
        await limiter.record_action("restart_pod:default/nginx")

        assert len(limiter.actions) == 1
        ts, action = limiter.actions[0]
        assert action == "restart_pod:default/nginx"
        assert isinstance(ts, datetime)

    def test_cleanup_old_removes_expired(self, settings_env):
        """Entries older than the window are pruned by _cleanup_old."""
        limiter = ActionRateLimiter(max_actions=10, window_seconds=3600)
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        recent = datetime.now(timezone.utc)
        limiter.actions.append((old, "old_action"))
        limiter.actions.append((recent, "recent_action"))

        limiter._cleanup_old()

        assert len(limiter.actions) == 1
        assert limiter.actions[0][1] == "recent_action"

    def test_get_remaining_accurate(self, settings_env):
        """get_remaining returns max_actions minus current valid count."""
        limiter = ActionRateLimiter(max_actions=5, window_seconds=3600)
        now = datetime.now(timezone.utc)
        limiter.actions.append((now, "a1"))
        limiter.actions.append((now, "a2"))

        assert limiter.get_remaining() == 3

    @pytest.mark.asyncio
    async def test_can_act_uses_redis_when_available(
        self, settings_env, mock_redis_client
    ):
        """When redis_client is available, can_act delegates to redis count."""
        limiter = ActionRateLimiter(max_actions=5, redis_client=mock_redis_client)

        # Record 3 actions so redis sorted set has 3 members
        for i in range(3):
            await limiter.record_action(f"action_{i}")

        with patch.object(limiter, "_refresh_max_actions", new_callable=AsyncMock):
            assert await limiter.can_act() is True

        # Fill up to the limit via redis
        for i in range(3, 5):
            await limiter.record_action(f"action_{i}")

        with patch.object(limiter, "_refresh_max_actions", new_callable=AsyncMock):
            assert await limiter.can_act() is False

    @pytest.mark.asyncio
    async def test_can_act_fallback_without_redis(
        self, settings_env, disconnected_redis_client
    ):
        """When redis is unavailable, can_act falls back to in-memory deque."""
        limiter = ActionRateLimiter(
            max_actions=2, redis_client=disconnected_redis_client
        )
        now = datetime.now(timezone.utc)
        limiter.actions.append((now, "a1"))

        with patch.object(limiter, "_refresh_max_actions", new_callable=AsyncMock):
            assert await limiter.can_act() is True

        limiter.actions.append((now, "a2"))

        with patch.object(limiter, "_refresh_max_actions", new_callable=AsyncMock):
            assert await limiter.can_act() is False


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Unit tests for AuditLog."""

    @pytest.mark.asyncio
    async def test_log_appends_entry(self, settings_env):
        """log() appends a dict with expected keys to entries list."""
        audit = AuditLog()
        await audit.log(
            action="restart_pod",
            target="nginx",
            namespace="default",
            reason="CrashLoopBackOff",
            result="success",
        )

        assert len(audit.entries) == 1
        entry = audit.entries[0]
        assert entry["action"] == "restart_pod"
        assert entry["target"] == "nginx"
        assert entry["namespace"] == "default"
        assert entry["reason"] == "CrashLoopBackOff"
        assert entry["result"] == "success"
        assert "timestamp" in entry

    @pytest.mark.asyncio
    async def test_log_persists_to_redis(self, settings_env, mock_redis_client):
        """When redis is available, log() also writes to redis audit list."""
        audit = AuditLog(redis_client=mock_redis_client)
        await audit.log(
            action="scale_deployment",
            target="web",
            namespace="prod",
            reason="high latency",
            result="success",
        )

        # Verify the entry landed in both in-memory and redis
        assert len(audit.entries) == 1
        redis_entries = await mock_redis_client.get_audit_entries(10)
        assert len(redis_entries) == 1
        assert redis_entries[0]["action"] == "scale_deployment"

    @pytest.mark.asyncio
    async def test_get_recent_from_redis(self, settings_env, mock_redis_client):
        """When redis has data, get_recent returns redis entries."""
        audit = AuditLog(redis_client=mock_redis_client)

        # Write several entries through the normal path
        for i in range(3):
            await audit.log(
                action=f"action_{i}",
                target=f"target_{i}",
                namespace="default",
                reason="test",
                result="success",
            )

        recent = await audit.get_recent(count=10)
        assert len(recent) == 3
        # Redis lpush inserts at head, so most recent is first
        assert recent[0]["action"] == "action_2"

    @pytest.mark.asyncio
    async def test_get_recent_fallback_memory(
        self, settings_env, disconnected_redis_client
    ):
        """When redis is unavailable, get_recent returns in-memory entries."""
        audit = AuditLog(redis_client=disconnected_redis_client)
        await audit.log(
            action="restart_pod",
            target="nginx",
            namespace="default",
            reason="OOMKilled",
            result="success",
        )

        recent = await audit.get_recent(count=50)
        assert len(recent) == 1
        assert recent[0]["action"] == "restart_pod"
