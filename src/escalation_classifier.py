"""
Escalation classifier for Cluster Guardian.

Classifies anomaly signals into QUICK_FIX, LONG_TERM, or OBSERVATION_ONLY
to determine whether the SRE agent should handle them directly, escalate
to the dev controller, or just log them.
"""

from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

QUICK_FIX_SOURCES = frozenset(
    {
        "k8s_crashloop",
        "gatus",
        "daemonset",
    }
)

LONG_TERM_SOURCES = frozenset(
    {
        "node_condition",
    }
)

QUICK_FIX_KEYWORDS = [
    "restart",
    "crashloop",
    "oomkilled",
    "backoff",
    "failed job",
    "rollout stuck",
    "unhealthy endpoint",
]

LONG_TERM_KEYWORDS = [
    "memory limit",
    "resource limit",
    "config change",
    "recurring",
    "disk pressure",
    "pid pressure",
    "node not ready",
]


class EscalationLevel(str, Enum):
    QUICK_FIX = "quick_fix"
    LONG_TERM = "long_term"
    OBSERVATION_ONLY = "observation_only"


class EscalationClassifier:
    """Classifies anomaly signals into escalation levels."""

    def __init__(self, recurring_threshold: int = 3):
        self._recurring_threshold = recurring_threshold
        self._occurrence_counts: dict[str, int] = {}

    def classify(
        self,
        source: str,
        severity: str,
        title: str,
        details: str,
        dedupe_key: str,
        issue_counts: dict[str, int] | None = None,
    ) -> EscalationLevel:
        """Classify a signal into an escalation level.

        Args:
            source: Signal source (k8s_crashloop, prometheus, etc.)
            severity: info, warning, critical
            title: Signal title
            details: Signal details
            dedupe_key: Deduplication key for recurring detection
            issue_counts: Optional external issue count dict (from SelfTuner)
        """
        text = f"{title} {details}".lower()

        # Track occurrences
        self._occurrence_counts[dedupe_key] = (
            self._occurrence_counts.get(dedupe_key, 0) + 1
        )

        # Check external counts too
        external_count = 0
        if issue_counts:
            external_count = max(
                issue_counts.get(dedupe_key, 0),
                issue_counts.get(source, 0),
            )

        total_count = max(self._occurrence_counts[dedupe_key], external_count)

        # Recurring issues beyond threshold -> LONG_TERM
        if total_count >= self._recurring_threshold:
            return EscalationLevel.LONG_TERM

        # Source-based classification
        if source in LONG_TERM_SOURCES:
            return EscalationLevel.LONG_TERM

        if source in QUICK_FIX_SOURCES:
            return EscalationLevel.QUICK_FIX

        # Keyword-based classification
        for kw in LONG_TERM_KEYWORDS:
            if kw in text:
                return EscalationLevel.LONG_TERM

        for kw in QUICK_FIX_KEYWORDS:
            if kw in text:
                return EscalationLevel.QUICK_FIX

        # Severity-based fallback
        if severity == "critical":
            return EscalationLevel.QUICK_FIX
        if severity == "info":
            return EscalationLevel.OBSERVATION_ONLY

        return EscalationLevel.QUICK_FIX

    def get_stats(self) -> dict[str, Any]:
        """Return classifier statistics."""
        return {
            "tracked_keys": len(self._occurrence_counts),
            "recurring_threshold": self._recurring_threshold,
            "occurrence_counts": dict(self._occurrence_counts),
        }
