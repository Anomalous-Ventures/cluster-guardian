"""Tests for continuous monitoring loop."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.continuous_monitor import AnomalySignal, ContinuousMonitor


@pytest.fixture
def cm_config():
    return {
        "fast_loop_interval_seconds": 1,
        "event_watch_enabled": False,
        "anomaly_suppression_window": 5,
        "anomaly_batch_window": 1,
    }


@pytest.fixture
def mock_k8s():
    k8s = MagicMock()
    k8s.get_crashloopbackoff_pods = AsyncMock(return_value=[])
    k8s.core_v1 = MagicMock()
    return k8s


@pytest.fixture
def mock_prometheus():
    prom = MagicMock()
    prom.get_alerts = AsyncMock(return_value=[])
    return prom


@pytest.fixture
def mock_ingress():
    ing = MagicMock()
    ing.check_all_ingress_routes = AsyncMock(return_value=[])
    ing.check_daemonset_health = AsyncMock(return_value=[])
    ing.check_pvc_usage = AsyncMock(return_value=[])
    return ing


@pytest.fixture
def monitor(cm_config, mock_k8s, mock_prometheus, mock_ingress, settings_env):
    return ContinuousMonitor(
        k8s=mock_k8s,
        prometheus=mock_prometheus,
        health_checker=MagicMock(),
        ingress_monitor=mock_ingress,
        config=cm_config,
    )


class TestAnomalySignal:
    def test_creation(self):
        sig = AnomalySignal(
            source="test",
            severity="warning",
            title="Test anomaly",
            details="Something happened",
            namespace="default",
            resource="pod-1",
            dedupe_key="test:default/pod-1",
        )
        assert sig.source == "test"
        assert sig.severity == "warning"
        assert sig.dedupe_key == "test:default/pod-1"


class TestContinuousMonitor:
    def test_initial_state(self, monitor):
        assert not monitor._running
        assert monitor._anomaly_queue.qsize() == 0
        assert monitor._total_anomalies == 0

    def test_get_status(self, monitor):
        status = monitor.get_status()
        assert status["running"] is False
        assert status["anomaly_queue_depth"] == 0
        assert status["total_anomalies"] == 0

    def test_get_recent_anomalies_empty(self, monitor):
        anomalies = monitor.get_recent_anomalies()
        assert anomalies == []

    def test_cleanup_stale_keys(self, monitor):
        # Add stale and fresh keys
        now = time.time()
        monitor._seen_keys["stale:key"] = now - 1000
        monitor._seen_keys["fresh:key"] = now
        monitor._suppression_window = 5

        monitor.cleanup_stale_keys()

        assert "stale:key" not in monitor._seen_keys
        assert "fresh:key" in monitor._seen_keys


class TestCheckCrashloopPods:
    @pytest.mark.asyncio
    async def test_no_pods(self, monitor, mock_k8s):
        mock_k8s.get_crashloopbackoff_pods = AsyncMock(return_value=[])
        result = await monitor._check_crashloop_pods()
        assert result == []

    @pytest.mark.asyncio
    async def test_crashing_pods(self, monitor, mock_k8s):
        mock_k8s.get_crashloopbackoff_pods = AsyncMock(
            return_value=[
                {
                    "name": "web-abc",
                    "namespace": "default",
                    "container": "web",
                    "restart_count": 5,
                    "message": "CrashLoopBackOff",
                }
            ]
        )
        result = await monitor._check_crashloop_pods()
        assert len(result) == 1
        assert result[0].source == "k8s_crashloop"
        assert result[0].severity == "critical"
        assert "web-abc" in result[0].title


class TestCheckPrometheusAlerts:
    @pytest.mark.asyncio
    async def test_no_alerts(self, monitor, mock_prometheus):
        mock_prometheus.get_alerts = AsyncMock(return_value=[])
        result = await monitor._check_prometheus_alerts()
        assert result == []

    @pytest.mark.asyncio
    async def test_firing_alerts(self, monitor, mock_prometheus):
        mock_prometheus.get_alerts = AsyncMock(
            return_value=[
                {
                    "name": "HighCPU",
                    "severity": "warning",
                    "summary": "CPU high",
                    "labels": {"namespace": "prod", "pod": "web-1"},
                }
            ]
        )
        result = await monitor._check_prometheus_alerts()
        assert len(result) == 1
        assert result[0].source == "prometheus"
        assert "HighCPU" in result[0].title

    @pytest.mark.asyncio
    async def test_error_response(self, monitor, mock_prometheus):
        mock_prometheus.get_alerts = AsyncMock(return_value=[{"error": "timeout"}])
        result = await monitor._check_prometheus_alerts()
        assert result == []


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_same_signal_suppressed(self, monitor):
        """Signals with the same dedupe_key within suppression window are suppressed."""
        sig = AnomalySignal(
            source="test",
            severity="warning",
            title="Test",
            details="",
            namespace="default",
            resource="pod-1",
            dedupe_key="test:dedup",
        )

        # Mark as seen
        monitor._seen_keys["test:dedup"] = time.time()

        # Put into queue
        await monitor._anomaly_queue.put(sig)
        assert monitor._anomaly_queue.qsize() == 1

    def test_get_recent_anomalies_with_data(self, monitor):
        now = time.time()
        monitor._seen_keys["key1"] = now - 10
        monitor._seen_keys["key2"] = now - 1

        anomalies = monitor.get_recent_anomalies()
        assert len(anomalies) == 2
        # Most recent first
        assert anomalies[0]["dedupe_key"] == "key2"
        assert anomalies[1]["dedupe_key"] == "key1"


class TestSetCallbacks:
    def test_set_callbacks(self, monitor):
        investigate = AsyncMock()
        broadcast = AsyncMock()
        monitor.set_callbacks(investigate=investigate, broadcast=broadcast)
        assert monitor._investigate_callback is investigate
        assert monitor._broadcast_callback is broadcast
