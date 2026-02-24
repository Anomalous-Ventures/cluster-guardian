"""
Self-tuning module for Cluster Guardian.

Tracks recurring issue patterns, auto-escalates to the AI Dev Controller
when issues exceed a threshold, and adjusts monitoring intervals based
on cluster stability.
"""

from typing import Any, Optional

import structlog

from .config import settings

logger = structlog.get_logger(__name__)

KEY_ISSUE_PATTERNS = "guardian:issue_patterns"
KEY_ESCALATION_PREFIX = "guardian:escalated:"
ESCALATION_COOLDOWN = 86400  # 24 hours


class SelfTuner:
    """Tracks patterns and auto-tunes monitoring behavior."""

    def __init__(self, redis, dev_controller):
        self._redis = redis
        self._dev_controller = dev_controller
        self._issue_counts: dict[str, int] = {}
        self._escalation_threshold = getattr(settings, "escalation_threshold", 3)

    async def record_issue(self, pattern_key: str, resolution: str, success: bool):
        """Record an issue occurrence and its resolution outcome."""
        self._issue_counts[pattern_key] = self._issue_counts.get(pattern_key, 0) + 1

        # Persist to Redis
        if self._redis and self._redis.available:
            try:
                await self._redis.increment_issue_pattern(pattern_key)
            except Exception as exc:
                logger.debug("record_issue redis failed", error=str(exc))

        # Check if escalation is needed
        if self._issue_counts[pattern_key] >= self._escalation_threshold:
            if success:
                # Issue was fixed but keeps recurring - needs permanent fix
                await self.auto_escalate(
                    pattern_key,
                    f"Recurring issue ({self._issue_counts[pattern_key]} occurrences): "
                    f"resolution='{resolution}' keeps being applied. Needs permanent fix.",
                )

    async def check_escalation_needed(self, pattern_key: str) -> bool:
        """Check if a recurring issue should be escalated for permanent fix."""
        count = self._issue_counts.get(pattern_key, 0)

        # Also check Redis for distributed count
        if self._redis and self._redis.available:
            try:
                redis_count = await self._redis.get_issue_pattern_count(pattern_key)
                count = max(count, redis_count)
            except Exception:
                pass

        return count >= self._escalation_threshold

    async def auto_escalate(self, pattern_key: str, issue_summary: str):
        """Submit recurring issue to dev controller as improvement goal."""
        # Check 24h cooldown
        if self._redis and self._redis.available:
            try:
                if await self._redis.was_recently_escalated(pattern_key):
                    logger.debug(
                        "Skipping escalation (cooldown)",
                        pattern_key=pattern_key,
                    )
                    return
            except Exception:
                pass

        if not self._dev_controller:
            logger.info(
                "Dev controller not available, skipping escalation",
                pattern_key=pattern_key,
            )
            return

        description = (
            f"Recurring issue detected ({self._issue_counts.get(pattern_key, '?')} "
            f"occurrences): {issue_summary}. Implement permanent fix."
        )
        acceptance_criteria = [
            f"Issue pattern '{pattern_key}' no longer recurs",
            "Root cause is addressed in infrastructure or application code",
            "Monitoring is updated if needed",
        ]

        result = await self._dev_controller.submit_goal(
            description=description,
            acceptance_criteria=acceptance_criteria,
        )

        if "error" not in result:
            logger.info(
                "Auto-escalated recurring issue to dev controller",
                pattern_key=pattern_key,
                result=result,
            )
            # Record escalation in Redis with TTL
            if self._redis and self._redis.available:
                try:
                    await self._redis.record_escalation(pattern_key)
                except Exception:
                    pass
        else:
            logger.warning(
                "Failed to escalate to dev controller",
                pattern_key=pattern_key,
                error=result["error"],
            )

    async def tune_intervals(self):
        """Adjust scan intervals based on cluster stability."""
        try:
            from .config_store import get_config_store

            store = get_config_store()

            # Count recent anomalies (from in-memory counts)
            total_recent = sum(self._issue_counts.values())

            current_interval = await store.get("fast_loop_interval_seconds")
            if not isinstance(current_interval, (int, float)):
                current_interval = 30

            if total_recent == 0:
                # Stable cluster: relax to 60s
                new_interval = min(int(current_interval) + 10, 60)
            elif total_recent > 5:
                # Active issues: tighten to 15s
                new_interval = max(int(current_interval) - 5, 15)
            else:
                new_interval = 30

            if new_interval != int(current_interval):
                await store.set("fast_loop_interval_seconds", new_interval)
                logger.info(
                    "Tuned fast loop interval",
                    old=current_interval,
                    new=new_interval,
                    recent_issues=total_recent,
                )
        except Exception as exc:
            logger.debug("tune_intervals failed", error=str(exc))

    def derive_pattern_key(self, namespace: str, resource: str, issue_type: str) -> str:
        """Create a stable key for deduplicating recurring issues."""
        return f"{namespace}/{resource}/{issue_type}"

    def get_stats(self) -> dict[str, Any]:
        """Return current issue pattern counts."""
        return {
            "issue_counts": dict(self._issue_counts),
            "escalation_threshold": self._escalation_threshold,
            "total_tracked_patterns": len(self._issue_counts),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_self_tuner: Optional[SelfTuner] = None


def get_self_tuner(redis=None, dev_controller=None) -> SelfTuner:
    """Get or create SelfTuner singleton."""
    global _self_tuner
    if _self_tuner is None:
        if redis is None:
            from .redis_client import get_redis_client

            redis = get_redis_client()
        if dev_controller is None:
            from .dev_controller_client import get_dev_controller

            dev_controller = get_dev_controller()
        _self_tuner = SelfTuner(redis=redis, dev_controller=dev_controller)
    return _self_tuner
