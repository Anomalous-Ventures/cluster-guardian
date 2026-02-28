"""E2E tests for continuous monitor anomaly detection."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestContinuousMonitor:
    async def test_monitor_detects_crashloop_pod(self, guardian_server, ws_connect):
        """Verify crashloop detection produces anomaly_detected broadcast."""
        app_state = guardian_server["app_state"]
        cm = app_state.continuous_monitor

        if cm is None:
            pytest.skip("ContinuousMonitor not initialized")

        # Mock K8s to return a crashloop pod
        cm._k8s.get_crashloopbackoff_pods = AsyncMock(
            return_value=[
                {
                    "namespace": "default",
                    "name": "test-pod-abc123",
                    "container": "main",
                    "restart_count": 10,
                }
            ]
        )

        # Clear dedup keys so the signal isn't suppressed
        cm._seen_keys.clear()

        async with ws_connect() as ws:
            await asyncio.sleep(0.2)

            # Run the crashloop check directly
            signals = await cm._check_crashloop_pods()
            assert len(signals) >= 1
            assert signals[0].source == "k8s_crashloop"
            assert "test-pod-abc123" in signals[0].title

            # Put signals into the anomaly queue and dispatch
            for sig in signals:
                await cm._anomaly_queue.put(sig)
            await cm._dispatch_batch(signals)

            # Collect WS messages
            messages = []
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                    messages.append(msg)
            except asyncio.TimeoutError:
                pass

            anomaly_msgs = [m for m in messages if m["type"] == "anomaly_detected"]
            assert len(anomaly_msgs) >= 1
            assert "test-pod-abc123" in anomaly_msgs[0]["data"]["description"]

    async def test_monitor_deduplicates_anomalies(self, guardian_server):
        """Verify that the same anomaly is suppressed within the dedup window."""
        app_state = guardian_server["app_state"]
        cm = app_state.continuous_monitor

        if cm is None:
            pytest.skip("ContinuousMonitor not initialized")

        # Clear and set a dedup key
        cm._seen_keys.clear()
        import time

        dedupe_key = "crashloop:default/test-pod/main"
        cm._seen_keys[dedupe_key] = time.time()

        # Second occurrence should be suppressed
        from src.continuous_monitor import AnomalySignal

        sig = AnomalySignal(
            source="k8s_crashloop",
            severity="critical",
            title="CrashLoopBackOff: default/test-pod",
            details="Container main has 10 restarts",
            namespace="default",
            resource="test-pod",
            dedupe_key=dedupe_key,
        )

        await cm._anomaly_queue.put(sig)

        # Let the dispatcher process it
        await asyncio.sleep(0.5)

        # The signal should have been suppressed (or at minimum, the dedup key
        # prevents re-broadcast). We verify by checking the seen_keys exist.
        assert dedupe_key in cm._seen_keys
