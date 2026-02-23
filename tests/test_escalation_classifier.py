"""Tests for the escalation classifier."""

import pytest

from src.escalation_classifier import EscalationClassifier, EscalationLevel


@pytest.fixture
def classifier():
    return EscalationClassifier(recurring_threshold=3)


class TestClassify:
    def test_crashloop_is_quick_fix(self, classifier):
        level = classifier.classify(
            source="k8s_crashloop",
            severity="critical",
            title="CrashLoopBackOff: default/web",
            details="Container web has 5 restarts",
            dedupe_key="crashloop:default/web/web",
        )
        assert level == EscalationLevel.QUICK_FIX

    def test_node_condition_is_long_term(self, classifier):
        level = classifier.classify(
            source="node_condition",
            severity="critical",
            title="Node not ready: worker-1",
            details="Ready=False",
            dedupe_key="node:not_ready:worker-1",
        )
        assert level == EscalationLevel.LONG_TERM

    def test_recurring_issue_escalates(self, classifier):
        for _ in range(3):
            level = classifier.classify(
                source="prometheus",
                severity="warning",
                title="Alert firing: HighCPU",
                details="CPU high",
                dedupe_key="prom:high_cpu",
            )
        assert level == EscalationLevel.LONG_TERM

    def test_info_severity_is_observation(self, classifier):
        level = classifier.classify(
            source="some_source",
            severity="info",
            title="Something informational",
            details="no keywords here",
            dedupe_key="info:something",
        )
        assert level == EscalationLevel.OBSERVATION_ONLY

    def test_memory_limit_keyword_is_long_term(self, classifier):
        level = classifier.classify(
            source="prometheus",
            severity="warning",
            title="Memory limit exceeded",
            details="Container approaching memory limit",
            dedupe_key="mem:default/web",
        )
        assert level == EscalationLevel.LONG_TERM

    def test_restart_keyword_is_quick_fix(self, classifier):
        level = classifier.classify(
            source="some_source",
            severity="warning",
            title="Pod needs restart",
            details="Container restarted",
            dedupe_key="restart:default/web",
        )
        assert level == EscalationLevel.QUICK_FIX

    def test_gatus_is_quick_fix(self, classifier):
        level = classifier.classify(
            source="gatus",
            severity="warning",
            title="Status page unhealthy",
            details="uptime_7d=95%",
            dedupe_key="gatus:web",
        )
        assert level == EscalationLevel.QUICK_FIX

    def test_critical_fallback_is_quick_fix(self, classifier):
        level = classifier.classify(
            source="unknown_source",
            severity="critical",
            title="Something critical",
            details="no matching keywords",
            dedupe_key="unknown:critical",
        )
        assert level == EscalationLevel.QUICK_FIX

    def test_external_issue_counts(self, classifier):
        external = {"prom:cpu": 5}
        level = classifier.classify(
            source="prometheus",
            severity="warning",
            title="CPU alert",
            details="high",
            dedupe_key="prom:cpu",
            issue_counts=external,
        )
        assert level == EscalationLevel.LONG_TERM


class TestGetStats:
    def test_empty_stats(self, classifier):
        stats = classifier.get_stats()
        assert stats["tracked_keys"] == 0

    def test_stats_after_classify(self, classifier):
        classifier.classify(
            source="test", severity="warning",
            title="t", details="d", dedupe_key="key1",
        )
        stats = classifier.get_stats()
        assert stats["tracked_keys"] == 1
        assert stats["occurrence_counts"]["key1"] == 1
