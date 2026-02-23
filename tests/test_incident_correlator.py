"""Tests for the incident correlation engine."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from src.incident_correlator import (
    IncidentCorrelator,
    Incident,
    _correlation_key,
    _alerts_related,
)


def _make_alert(alertname="KubePodCrashLooping", namespace="default", pod="web-abc"):
    return {
        "labels": {
            "alertname": alertname,
            "namespace": namespace,
            "pod": pod,
        },
        "annotations": {
            "description": f"{alertname} for {pod}",
        },
    }


# ---------------------------------------------------------------------------
# Correlation key
# ---------------------------------------------------------------------------


class TestCorrelationKey:
    def test_workload_key(self):
        key = _correlation_key(_make_alert(pod="web-abc"))
        assert key == "default/web-abc"

    def test_node_key(self):
        alert = {"labels": {"alertname": "KubeNodeNotReady", "node": "worker-1"}}
        key = _correlation_key(alert)
        assert key == "node/worker-1"

    def test_fallback_key(self):
        alert = {"labels": {"alertname": "Watchdog"}}
        key = _correlation_key(alert)
        assert key == "/Watchdog"


# ---------------------------------------------------------------------------
# Alert relation
# ---------------------------------------------------------------------------


class TestAlertRelation:
    def test_related_crash_and_not_ready(self):
        assert _alerts_related("KubePodCrashLooping", "KubePodNotReady") is True

    def test_unrelated_alerts(self):
        assert _alerts_related("KubePodCrashLooping", "Watchdog") is False

    def test_same_alert(self):
        assert _alerts_related("KubeNodeNotReady", "KubeNodeNotReady") is True


# ---------------------------------------------------------------------------
# Incident model
# ---------------------------------------------------------------------------


class TestIncident:
    def test_add_alert_updates_last_alert_at(self):
        inc = Incident(id="inc-1", correlation_key="default/web")
        before = inc.last_alert_at
        time.sleep(0.01)
        inc.add_alert(_make_alert())
        assert inc.last_alert_at >= before

    def test_description_includes_all_alerts(self):
        inc = Incident(id="inc-1", correlation_key="default/web")
        inc.add_alert(_make_alert(alertname="KubePodCrashLooping"))
        inc.add_alert(_make_alert(alertname="KubePodNotReady"))
        desc = inc.description()
        assert "KubePodCrashLooping" in desc
        assert "KubePodNotReady" in desc

    def test_to_dict(self):
        inc = Incident(id="inc-1", correlation_key="default/web")
        inc.add_alert(_make_alert())
        d = inc.to_dict()
        assert d["id"] == "inc-1"
        assert d["alert_count"] == 1
        assert "KubePodCrashLooping" in d["alert_names"]


# ---------------------------------------------------------------------------
# IncidentCorrelator
# ---------------------------------------------------------------------------


class TestCorrelator:
    def test_new_alert_creates_incident(self):
        c = IncidentCorrelator()
        alert = _make_alert()
        inc = c.correlate(alert)
        assert len(inc.alerts) == 1
        assert len(c.get_active_incidents()) == 1

    def test_same_workload_correlated(self):
        c = IncidentCorrelator(window_seconds=300)
        a1 = _make_alert(alertname="KubePodCrashLooping")
        a2 = _make_alert(alertname="KubePodNotReady")
        inc1 = c.correlate(a1)
        inc2 = c.correlate(a2)
        # Same correlation key (default/web-abc) -> same incident
        assert inc1.id == inc2.id
        assert len(inc1.alerts) == 2
        assert len(c.get_active_incidents()) == 1

    def test_different_workload_separate(self):
        c = IncidentCorrelator()
        a1 = _make_alert(pod="web-1")
        a2 = _make_alert(pod="web-2")
        inc1 = c.correlate(a1)
        inc2 = c.correlate(a2)
        assert inc1.id != inc2.id
        assert len(c.get_active_incidents()) == 2

    def test_related_alerts_grouped(self):
        c = IncidentCorrelator(window_seconds=300)
        # These are related per RELATED_ALERT_GROUPS
        a1 = _make_alert(alertname="KubePodCrashLooping", pod="web-1")
        a2 = _make_alert(alertname="KubePodNotReady", pod="web-2")
        inc1 = c.correlate(a1)
        inc2 = c.correlate(a2)
        # Related alertnames should group even with different pods
        assert inc1.id == inc2.id

    def test_expire_old_removes_stale(self):
        c = IncidentCorrelator(expiry_seconds=0)
        c.correlate(_make_alert())
        assert len(c.get_active_incidents()) == 0
        removed = c.expire_old()
        assert removed == 1

    def test_get_incident_by_id(self):
        c = IncidentCorrelator()
        inc = c.correlate(_make_alert())
        found = c.get_incident(inc.id)
        assert found is not None
        assert found.id == inc.id

    def test_get_incident_not_found(self):
        c = IncidentCorrelator()
        assert c.get_incident("nonexistent") is None


# ---------------------------------------------------------------------------
# Debounced investigation
# ---------------------------------------------------------------------------


class TestDebouncedInvestigation:
    @pytest.mark.asyncio
    async def test_schedule_investigation_fires(self):
        callback = AsyncMock()
        c = IncidentCorrelator(debounce_seconds=0.05)
        c.set_investigation_callback(callback)
        inc = c.correlate(_make_alert())
        await c.schedule_investigation(inc)
        await asyncio.sleep(0.1)
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_debounce_resets_on_new_alert(self):
        callback = AsyncMock()
        c = IncidentCorrelator(debounce_seconds=0.1)
        c.set_investigation_callback(callback)
        inc = c.correlate(_make_alert())
        await c.schedule_investigation(inc)
        await asyncio.sleep(0.05)
        # Add another alert, which should reset the timer
        inc.add_alert(_make_alert(alertname="KubePodNotReady"))
        await c.schedule_investigation(inc)
        await asyncio.sleep(0.05)
        # Should NOT have fired yet (timer reset)
        callback.assert_not_awaited()
        # Wait for the debounce to complete
        await asyncio.sleep(0.1)
        callback.assert_awaited_once()
