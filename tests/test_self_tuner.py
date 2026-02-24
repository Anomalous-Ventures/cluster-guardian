"""Tests for the self-tuner module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.self_tuner import SelfTuner


@pytest.fixture
def mock_redis(settings_env):
    redis = MagicMock()
    redis.available = True
    redis.increment_issue_pattern = AsyncMock(return_value=1)
    redis.get_issue_pattern_count = AsyncMock(return_value=0)
    redis.record_escalation = AsyncMock()
    redis.was_recently_escalated = AsyncMock(return_value=False)
    return redis


@pytest.fixture
def mock_dev_controller():
    dc = MagicMock()
    dc.submit_goal = AsyncMock(return_value={"status": "injected", "new_tasks": 1})
    return dc


@pytest.fixture
def tuner(mock_redis, mock_dev_controller, settings_env):
    return SelfTuner(redis=mock_redis, dev_controller=mock_dev_controller)


class TestRecordIssue:
    @pytest.mark.asyncio
    async def test_first_occurrence(self, tuner, mock_redis):
        await tuner.record_issue("default/web/crashloop", "restarted pod", True)
        assert tuner._issue_counts["default/web/crashloop"] == 1
        mock_redis.increment_issue_pattern.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_increment_count(self, tuner, mock_redis):
        await tuner.record_issue("default/web/oom", "restarted", True)
        await tuner.record_issue("default/web/oom", "restarted", True)
        assert tuner._issue_counts["default/web/oom"] == 2

    @pytest.mark.asyncio
    async def test_escalation_triggered(self, tuner, mock_redis, mock_dev_controller):
        tuner._escalation_threshold = 3
        for i in range(3):
            await tuner.record_issue("ns/pod/type", "restart", True)

        # Should have triggered auto_escalate -> submit_goal
        mock_dev_controller.submit_goal.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_escalation_on_failure(
        self, tuner, mock_redis, mock_dev_controller
    ):
        tuner._escalation_threshold = 3
        for i in range(3):
            await tuner.record_issue("ns/pod/type", "failed", False)

        # Failed resolutions don't trigger escalation
        mock_dev_controller.submit_goal.assert_not_awaited()


class TestCheckEscalationNeeded:
    @pytest.mark.asyncio
    async def test_below_threshold(self, tuner):
        tuner._issue_counts["test/key"] = 1
        assert await tuner.check_escalation_needed("test/key") is False

    @pytest.mark.asyncio
    async def test_at_threshold(self, tuner):
        tuner._escalation_threshold = 3
        tuner._issue_counts["test/key"] = 3
        assert await tuner.check_escalation_needed("test/key") is True

    @pytest.mark.asyncio
    async def test_redis_count_used(self, tuner, mock_redis):
        tuner._escalation_threshold = 3
        tuner._issue_counts["test/key"] = 1
        mock_redis.get_issue_pattern_count = AsyncMock(return_value=5)
        assert await tuner.check_escalation_needed("test/key") is True


class TestAutoEscalate:
    @pytest.mark.asyncio
    async def test_escalation_submitted(self, tuner, mock_dev_controller, mock_redis):
        tuner._issue_counts["ns/pod/type"] = 5
        await tuner.auto_escalate("ns/pod/type", "Pod keeps OOMing")

        mock_dev_controller.submit_goal.assert_awaited_once()
        call_args = mock_dev_controller.submit_goal.call_args
        assert "Recurring issue detected" in call_args.kwargs["description"]
        assert len(call_args.kwargs["acceptance_criteria"]) == 3
        mock_redis.record_escalation.assert_awaited_once_with("ns/pod/type")

    @pytest.mark.asyncio
    async def test_cooldown_prevents_re_escalation(
        self, tuner, mock_dev_controller, mock_redis
    ):
        mock_redis.was_recently_escalated = AsyncMock(return_value=True)
        await tuner.auto_escalate("ns/pod/type", "Pod keeps OOMing")

        mock_dev_controller.submit_goal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dev_controller(self, mock_redis, settings_env):
        tuner = SelfTuner(redis=mock_redis, dev_controller=None)
        tuner._issue_counts["ns/pod/type"] = 5
        # Should not raise
        await tuner.auto_escalate("ns/pod/type", "Issue")


class TestDerivePatternKey:
    def test_key_format(self, tuner):
        key = tuner.derive_pattern_key("default", "web-pod", "crashloop")
        assert key == "default/web-pod/crashloop"


class TestGetStats:
    def test_empty_stats(self, tuner):
        stats = tuner.get_stats()
        assert stats["total_tracked_patterns"] == 0
        assert stats["escalation_threshold"] == 3

    def test_with_data(self, tuner):
        tuner._issue_counts["a"] = 1
        tuner._issue_counts["b"] = 5
        stats = tuner.get_stats()
        assert stats["total_tracked_patterns"] == 2
        assert stats["issue_counts"]["b"] == 5


class TestTuneIntervals:
    @pytest.mark.asyncio
    async def test_stable_cluster_relaxes(self, tuner):
        """With zero issues, interval should increase toward 60s."""
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=30)
        mock_store.set = AsyncMock()

        with patch("src.config_store.get_config_store", return_value=mock_store):
            await tuner.tune_intervals()

        mock_store.set.assert_awaited_once()
        new_val = mock_store.set.call_args[0][1]
        assert new_val > 30

    @pytest.mark.asyncio
    async def test_active_issues_tightens(self, tuner):
        """With many issues, interval should decrease toward 15s."""
        tuner._issue_counts = {f"issue-{i}": 1 for i in range(10)}

        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=30)
        mock_store.set = AsyncMock()

        with patch("src.config_store.get_config_store", return_value=mock_store):
            await tuner.tune_intervals()

        mock_store.set.assert_awaited_once()
        new_val = mock_store.set.call_args[0][1]
        assert new_val < 30
