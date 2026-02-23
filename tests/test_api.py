"""Tests for FastAPI endpoints in src.api."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api import app_state


# ---------------------------------------------------------------------------
# Liveness / Readiness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness(async_client):
    resp = await async_client.get("/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_readiness_when_ready(async_client):
    resp = await async_client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readiness_when_not_ready(async_client):
    original = app_state.guardian
    app_state.guardian = None
    try:
        resp = await async_client.get("/ready")
        assert resp.status_code == 503
    finally:
        app_state.guardian = original


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    mock_k8sgpt = MagicMock()
    mock_k8sgpt.health_check = AsyncMock(return_value=True)

    with patch("src.api.get_k8sgpt_client", return_value=mock_k8sgpt):
        resp = await async_client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "0.9.0"
    assert body["status"] == "healthy"
    assert "components" in body
    assert body["components"]["k8sgpt"] == "healthy"
    assert body["components"]["guardian"] == "ready"


# ---------------------------------------------------------------------------
# AlertManager webhook
# ---------------------------------------------------------------------------

ALERTMANAGER_FIRING = {
    "version": "4",
    "groupKey": "test-group",
    "status": "firing",
    "receiver": "cluster-guardian",
    "groupLabels": {"alertname": "KubePodCrashLooping"},
    "commonLabels": {"alertname": "KubePodCrashLooping", "severity": "critical"},
    "commonAnnotations": {"description": "Pod crashing"},
    "externalURL": "http://alertmanager:9093",
    "alerts": [
        {
            "labels": {
                "alertname": "KubePodCrashLooping",
                "namespace": "default",
                "severity": "critical",
            },
            "annotations": {"description": "Pod is crash-looping"},
        }
    ],
}

ALERTMANAGER_RESOLVED = {
    **ALERTMANAGER_FIRING,
    "status": "resolved",
}


@pytest.mark.asyncio
async def test_alertmanager_webhook_firing(async_client):
    resp = await async_client.post("/webhook/alertmanager", json=ALERTMANAGER_FIRING)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["alerts_received"] == 1


@pytest.mark.asyncio
async def test_alertmanager_webhook_resolved(async_client):
    resp = await async_client.post("/webhook/alertmanager", json=ALERTMANAGER_RESOLVED)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ignored"


# ---------------------------------------------------------------------------
# Falco webhook
# ---------------------------------------------------------------------------

FALCO_ALERT = {
    "uuid": "test-uuid",
    "output": "Sensitive file opened",
    "priority": "Warning",
    "rule": "Read sensitive file",
    "time": "2025-01-01T00:00:00Z",
    "output_fields": {"k8s.ns.name": "default", "k8s.pod.name": "test-pod"},
}


@pytest.mark.asyncio
async def test_falco_webhook(async_client):
    falco_proc = MagicMock()
    falco_proc.parse_alert = MagicMock(
        return_value={
            "rule": "Read sensitive file",
            "priority": "Warning",
            "severity": "warning",
            "output": "Sensitive file opened",
            "timestamp": "2025-01-01T00:00:00Z",
            "namespace": "default",
            "pod": "test-pod",
            "container": "",
        }
    )

    with patch("src.api.get_falco_processor", return_value=falco_proc):
        resp = await async_client.post("/webhook/falco", json=FALCO_ALERT)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["rule"] == "Read sensitive file"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_config(async_client):
    mock_store = MagicMock()
    mock_store.get_all = AsyncMock(return_value={"host": "0.0.0.0", "port": 8900})
    mock_store.set = AsyncMock()
    mock_store.reset = AsyncMock()

    with patch("src.api.get_config_store", return_value=mock_store):
        resp = await async_client.get("/api/v1/config")

    assert resp.status_code == 200
    assert resp.json() == {"host": "0.0.0.0", "port": 8900}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint(async_client):
    resp = await async_client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

SAMPLE_APPROVAL = {
    "id": "abc-123",
    "action": "restart_pod",
    "description": "Restart crashing pod",
    "namespace": "default",
    "timestamp": "2025-01-01T00:00:00Z",
    "status": "pending",
}


@pytest.mark.asyncio
async def test_approve_action(async_client):
    app_state.pending_approvals.append(dict(SAMPLE_APPROVAL))

    mock_redis = MagicMock()
    mock_redis.update_pending_approval = AsyncMock()

    with patch("src.api.get_redis_client", return_value=mock_redis):
        resp = await async_client.post("/api/v1/approvals/abc-123/approve")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["id"] == "abc-123"
    mock_redis.update_pending_approval.assert_awaited_once_with("abc-123", "approved")


@pytest.mark.asyncio
async def test_reject_action(async_client):
    app_state.pending_approvals.append(dict(SAMPLE_APPROVAL))

    mock_redis = MagicMock()
    mock_redis.update_pending_approval = AsyncMock()

    with patch("src.api.get_redis_client", return_value=mock_redis):
        resp = await async_client.post("/api/v1/approvals/abc-123/reject")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["id"] == "abc-123"
    mock_redis.update_pending_approval.assert_awaited_once_with("abc-123", "rejected")


@pytest.mark.asyncio
async def test_approve_not_found(async_client):
    mock_redis = MagicMock()
    mock_redis.update_pending_approval = AsyncMock()

    with patch("src.api.get_redis_client", return_value=mock_redis):
        resp = await async_client.post("/api/v1/approvals/nonexistent/approve")

    assert resp.status_code == 404
