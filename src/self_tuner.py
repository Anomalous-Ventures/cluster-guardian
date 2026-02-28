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
        self._effectiveness: dict[str, dict[str, int]] = {}
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

    async def suggest_improvements(self) -> list[dict[str, Any]]:
        """Analyze accumulated issue patterns and suggest improvements.

        Returns list of suggestion dicts with type, description, and priority.
        """
        suggestions: list[dict[str, Any]] = []

        # Find recurring patterns that haven't been escalated
        for pattern_key, count in self._issue_counts.items():
            if count >= self._escalation_threshold:
                suggestions.append(
                    {
                        "type": "new_playbook",
                        "description": f"Create playbook for recurring issue: {pattern_key} ({count} occurrences)",
                        "pattern_key": pattern_key,
                        "occurrences": count,
                        "priority": "high"
                        if count >= self._escalation_threshold * 2
                        else "medium",
                    }
                )

        # Check for patterns that suggest missing health checks
        namespaces_with_issues = set()
        for pattern_key in self._issue_counts:
            parts = pattern_key.split("/")
            if len(parts) >= 1:
                namespaces_with_issues.add(parts[0])

        for ns in namespaces_with_issues:
            ns_total = sum(
                c for k, c in self._issue_counts.items() if k.startswith(f"{ns}/")
            )
            if ns_total >= 5:
                suggestions.append(
                    {
                        "type": "enhanced_monitoring",
                        "description": f"Namespace '{ns}' has {ns_total} total issues - consider adding dedicated health checks",
                        "namespace": ns,
                        "total_issues": ns_total,
                        "priority": "medium",
                    }
                )

        # Detect high false positive rates
        for pattern_key, stats in self._effectiveness.items():
            total = stats.get("true_positive", 0) + stats.get("false_positive", 0)
            if total >= 5 and stats.get("false_positive", 0) / total > 0.5:
                suggestions.append(
                    {
                        "type": "tune_threshold",
                        "description": f"Check '{pattern_key}' has >50% false positive rate - tune sensitivity",
                        "pattern_key": pattern_key,
                        "false_positive_rate": round(
                            stats["false_positive"] / total, 2
                        ),
                        "priority": "high",
                    }
                )

        return suggestions

    def track_check_effectiveness(self, check_key: str, true_positive: bool):
        """Track whether a check result was a true or false positive.

        Args:
            check_key: Identifier for the check type
            true_positive: Whether the detected issue was a real problem
        """
        if check_key not in self._effectiveness:
            self._effectiveness[check_key] = {"true_positive": 0, "false_positive": 0}

        if true_positive:
            self._effectiveness[check_key]["true_positive"] += 1
        else:
            self._effectiveness[check_key]["false_positive"] += 1

    def get_effectiveness_stats(self) -> dict[str, Any]:
        """Return effectiveness tracking stats."""
        return dict(self._effectiveness)

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
            from .config import settings

            if settings.dev_controller_enabled:
                from .dev_controller_client import get_dev_controller

                dev_controller = get_dev_controller()
        _self_tuner = SelfTuner(redis=redis, dev_controller=dev_controller)
    return _self_tuner
