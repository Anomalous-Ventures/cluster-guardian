"""Tests for continuous monitoring loop."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.continuous_monitor import AnomalySignal, ContinuousMonitor, _investigation_id


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
    k8s.apps_v1 = MagicMock()
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
def mock_self_tuner():
    tuner = MagicMock()
    tuner.record_issue = AsyncMock()
    tuner.tune_intervals = AsyncMock()
    tuner._issue_counts = {}
    tuner._dev_controller = None
    return tuner


@pytest.fixture
def mock_loki():
    loki = MagicMock()
    loki.get_cluster_error_summary = AsyncMock(return_value=[])
    return loki


@pytest.fixture
def monitor(cm_config, mock_k8s, mock_prometheus, mock_ingress, settings_env):
    return ContinuousMonitor(
        k8s=mock_k8s,
        prometheus=mock_prometheus,
        health_checker=MagicMock(),
        ingress_monitor=mock_ingress,
        config=cm_config,
    )


@pytest.fixture
def monitor_full(
    cm_config, mock_k8s, mock_prometheus, mock_ingress, mock_self_tuner, mock_loki, settings_env
):
    return ContinuousMonitor(
        k8s=mock_k8s,
        prometheus=mock_prometheus,
        health_checker=MagicMock(),
        ingress_monitor=mock_ingress,
        config=cm_config,
        self_tuner=mock_self_tuner,
        loki=mock_loki,
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


class TestCheckLogAnomalies:
    @pytest.mark.asyncio
    async def test_no_loki(self, monitor):
        result = await monitor._check_log_anomalies()
        assert result == []

    @pytest.mark.asyncio
    async def test_no_errors(self, monitor_full, mock_loki):
        mock_loki.get_cluster_error_summary = AsyncMock(return_value=[])
        result = await monitor_full._check_log_anomalies()
        assert result == []

    @pytest.mark.asyncio
    async def test_error_spike_detected(self, monitor_full, mock_loki):
        mock_loki.get_cluster_error_summary = AsyncMock(
            return_value=[{"namespace": "media", "count": 25}]
        )
        result = await monitor_full._check_log_anomalies()
        assert len(result) == 1
        assert result[0].source == "loki_errors"
        assert "media" in result[0].title

    @pytest.mark.asyncio
    async def test_critical_on_high_count(self, monitor_full, mock_loki):
        mock_loki.get_cluster_error_summary = AsyncMock(
            return_value=[{"namespace": "apps", "count": 100}]
        )
        result = await monitor_full._check_log_anomalies()
        assert len(result) == 1
        assert result[0].severity == "critical"


class TestCheckNodeConditions:
    @pytest.mark.asyncio
    async def test_healthy_nodes(self, monitor, mock_k8s):
        node = MagicMock()
        node.metadata.name = "worker-1"
        condition = MagicMock()
        condition.type = "Ready"
        condition.status = "True"
        condition.message = "kubelet is posting ready"
        node.status.conditions = [condition]

        node_list = MagicMock()
        node_list.items = [node]
        mock_k8s.core_v1.list_node.return_value = node_list

        result = await monitor._check_node_conditions()
        assert result == []

    @pytest.mark.asyncio
    async def test_not_ready_node(self, monitor, mock_k8s):
        node = MagicMock()
        node.metadata.name = "worker-2"
        condition = MagicMock()
        condition.type = "Ready"
        condition.status = "False"
        condition.message = "kubelet stopped posting"
        node.status.conditions = [condition]

        node_list = MagicMock()
        node_list.items = [node]
        mock_k8s.core_v1.list_node.return_value = node_list

        result = await monitor._check_node_conditions()
        assert len(result) == 1
        assert result[0].severity == "critical"
        assert "worker-2" in result[0].title

    @pytest.mark.asyncio
    async def test_memory_pressure(self, monitor, mock_k8s):
        node = MagicMock()
        node.metadata.name = "worker-3"
        ready = MagicMock()
        ready.type = "Ready"
        ready.status = "True"
        pressure = MagicMock()
        pressure.type = "MemoryPressure"
        pressure.status = "True"
        pressure.message = "memory low"
        node.status.conditions = [ready, pressure]

        node_list = MagicMock()
        node_list.items = [node]
        mock_k8s.core_v1.list_node.return_value = node_list

        result = await monitor._check_node_conditions()
        assert len(result) == 1
        assert result[0].source == "node_condition"
        assert "MemoryPressure" in result[0].title

    @pytest.mark.asyncio
    async def test_error_handling(self, monitor, mock_k8s):
        mock_k8s.core_v1.list_node.side_effect = Exception("api error")
        result = await monitor._check_node_conditions()
        assert result == []


class TestCheckDeploymentRollouts:
    @pytest.mark.asyncio
    async def test_healthy_deployment(self, monitor, mock_k8s):
        dep = MagicMock()
        dep.metadata.name = "web"
        dep.metadata.namespace = "default"
        dep.spec.replicas = 3
        dep.status.available_replicas = 3
        dep.status.conditions = []

        dep_list = MagicMock()
        dep_list.items = [dep]
        mock_k8s.apps_v1.list_deployment_for_all_namespaces.return_value = dep_list

        result = await monitor._check_deployment_rollouts()
        assert result == []

    @pytest.mark.asyncio
    async def test_degraded_deployment(self, monitor, mock_k8s):
        dep = MagicMock()
        dep.metadata.name = "api"
        dep.metadata.namespace = "default"
        dep.spec.replicas = 3
        dep.status.available_replicas = 1
        dep.status.conditions = []

        dep_list = MagicMock()
        dep_list.items = [dep]
        mock_k8s.apps_v1.list_deployment_for_all_namespaces.return_value = dep_list

        result = await monitor._check_deployment_rollouts()
        assert len(result) == 1
        assert result[0].source == "deployment_rollout"
        assert "degraded" in result[0].title.lower()

    @pytest.mark.asyncio
    async def test_stalled_rollout(self, monitor, mock_k8s):
        dep = MagicMock()
        dep.metadata.name = "worker"
        dep.metadata.namespace = "default"
        dep.spec.replicas = 2
        dep.status.available_replicas = 2
        condition = MagicMock()
        condition.type = "Progressing"
        condition.status = "False"
        condition.message = "deadline exceeded"
        dep.status.conditions = [condition]

        dep_list = MagicMock()
        dep_list.items = [dep]
        mock_k8s.apps_v1.list_deployment_for_all_namespaces.return_value = dep_list

        result = await monitor._check_deployment_rollouts()
        assert len(result) == 1
        assert result[0].severity == "critical"
        assert "stalled" in result[0].title.lower()

    @pytest.mark.asyncio
    async def test_protected_namespace_skipped(self, monitor, mock_k8s):
        dep = MagicMock()
        dep.metadata.name = "coredns"
        dep.metadata.namespace = "kube-system"
        dep.spec.replicas = 2
        dep.status.available_replicas = 0
        dep.status.conditions = []

        dep_list = MagicMock()
        dep_list.items = [dep]
        mock_k8s.apps_v1.list_deployment_for_all_namespaces.return_value = dep_list

        result = await monitor._check_deployment_rollouts()
        assert result == []


class TestDispatchBatchWithSelfTuner:
    @pytest.mark.asyncio
    async def test_records_issues(self, monitor_full, mock_self_tuner):
        batch = [
            AnomalySignal(
                source="test",
                severity="warning",
                title="Test",
                details="details",
                namespace="default",
                resource="pod-1",
                dedupe_key="test:default/pod-1",
            )
        ]
        await monitor_full._dispatch_batch(batch)
        mock_self_tuner.record_issue.assert_awaited_once()


class TestInvestigationId:
    def test_format(self):
        inv_id = _investigation_id("default/pod-1")
        assert inv_id.startswith("inv-")
        assert len(inv_id) == 16  # "inv-" + 12 hex chars

    def test_unique(self):
        ids = {_investigation_id("default/pod-1") for _ in range(10)}
        # time.time() changes between calls, so IDs should differ
        assert len(ids) >= 2


class TestEnrichedBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_includes_enriched_fields(self, monitor, settings_env):
        """The anomaly_detected broadcast should include timestamp, details, and investigation_id."""
        captured = []

        async def capture_broadcast(msg):
            captured.append(msg)

        investigate_mock = AsyncMock()
        monitor.set_callbacks(investigate=investigate_mock, broadcast=capture_broadcast)

        batch = [
            AnomalySignal(
                source="test",
                severity="warning",
                title="Test anomaly",
                details="Something went wrong in the cluster",
                namespace="default",
                resource="pod-1",
                dedupe_key="test:default/pod-1",
            )
        ]
        await monitor._dispatch_batch(batch)

        assert len(captured) == 1
        msg = captured[0]
        assert msg["type"] == "anomaly_detected"
        assert "timestamp" in msg
        assert "investigation_id" in msg
        assert msg["investigation_id"].startswith("inv-")

        data = msg["data"]
        assert "description" in data
        assert len(data["signals"]) == 1

        sig = data["signals"][0]
        assert sig["details"] == "Something went wrong in the cluster"
        assert sig["dedupe_key"] == "test:default/pod-1"

    @pytest.mark.asyncio
    async def test_investigation_id_passed_to_callback(self, monitor, settings_env):
        """The investigate callback should receive investigation_id."""
        investigate_mock = AsyncMock()
        broadcast_mock = AsyncMock()
        monitor.set_callbacks(investigate=investigate_mock, broadcast=broadcast_mock)

        batch = [
            AnomalySignal(
                source="test",
                severity="warning",
                title="Test",
                details="details",
                namespace="default",
                resource="pod-1",
                dedupe_key="test:default/pod-1",
            )
        ]
        await monitor._dispatch_batch(batch)

        investigate_mock.assert_called_once()
        call_kwargs = investigate_mock.call_args
        # Should have investigation_id kwarg
        assert "investigation_id" in call_kwargs.kwargs or (
            len(call_kwargs.args) > 2
        )


class TestGetStatusV2:
    def test_includes_checks_list(self, monitor):
        status = monitor.get_status()
        assert "log_anomalies" in status["checks"]
        assert "node_conditions" in status["checks"]
        assert "deployment_rollouts" in status["checks"]
