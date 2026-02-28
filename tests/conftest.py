"""Shared test fixtures for cluster-guardian."""

import os
import sys
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Stub third-party modules broken on Python 3.14 BEFORE any src imports.
# E2E tests set CLUSTER_GUARDIAN_E2E=1 and handle their own stubs/mocks.
# ---------------------------------------------------------------------------
if os.environ.get("CLUSTER_GUARDIAN_E2E") != "1":
    _STUB_MODULES = [
        # langfuse: pydantic v1 broken on 3.14
        "langfuse",
        "langfuse.callback",
        "langfuse.batch_evaluation",
        "langfuse.api",
        "langfuse.api.resources",
        # grpc / protobuf: version mismatch
        "grpc",
        "grpc.aio",
        "grpc._channel",
        "google.protobuf.runtime_version",
        # kubernetes: depends on 'six' (removed) and 'dateutil' (missing on 3.14)
        "six",
        "six.moves",
        "six.moves.http_client",
        "six.moves.urllib",
        "six.moves.urllib.parse",
        "dateutil",
        "dateutil.parser",
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.config",
        "kubernetes.watch",
        # qdrant_client: metaclass conflict on 3.14
        "qdrant_client",
        "qdrant_client.http",
        "qdrant_client.http.models",
    ]

    for _mod in _STUB_MODULES:
        sys.modules.setdefault(_mod, MagicMock())

    # Also stub the proto modules that fail on protobuf version mismatch
    _proto_mock = MagicMock()
    sys.modules.setdefault("src.proto", _proto_mock)
    sys.modules.setdefault("src.proto.k8sgpt_pb2", _proto_mock)
    sys.modules.setdefault("src.proto.k8sgpt_pb2_grpc", _proto_mock)

    # Provide realistic qdrant_client stubs so VectorMemory can be imported
    _qdrant_mock = sys.modules["qdrant_client"]
    _qdrant_http_models = sys.modules["qdrant_client.http.models"]
    _qdrant_http_models.Distance = MagicMock()
    _qdrant_http_models.Distance.COSINE = "Cosine"
    _qdrant_http_models.PointStruct = MagicMock()
    _qdrant_http_models.VectorParams = MagicMock()
    _qdrant_mock.AsyncQdrantClient = MagicMock()


# ---------------------------------------------------------------------------
# Environment fixture (needed by any test that instantiates Settings)
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_env(monkeypatch):
    """Set minimal environment for Settings to load."""
    monkeypatch.setenv("CLUSTER_GUARDIAN_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CLUSTER_GUARDIAN_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("CLUSTER_GUARDIAN_QDRANT_URL", "http://localhost:6333")


# ---------------------------------------------------------------------------
# Singleton reset fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset all module-level singletons between tests.

    Uses getattr to gracefully handle modules that failed to import.
    """
    modules_and_attrs = [
        ("src.k8s_client", "_k8s_client"),
        ("src.redis_client", "_redis_client"),
        ("src.config_store", "_config_store"),
        ("src.memory", "_memory"),
        ("src.prometheus_client", "_prometheus_client"),
        ("src.loki_client", "_loki_client"),
        ("src.security_client", "_falco_processor"),
        ("src.security_client", "_crowdsec_client"),
        ("src.ingress_monitor", "_ingress_monitor"),
        ("src.dev_controller_client", "_dev_controller"),
        ("src.self_tuner", "_self_tuner"),
        ("src.incident_correlator", "_correlator"),
        ("src.service_discovery", "_service_discovery"),
        ("src.health_checks", "_health_checker"),
    ]

    # Also try agent but don't fail if it can't import
    try:
        import src.agent  # noqa: F401

        modules_and_attrs.append(("src.agent", "_guardian"))
    except Exception:
        pass

    yield

    for mod_path, attr in modules_and_attrs:
        try:
            mod = sys.modules.get(mod_path)
            if mod is not None:
                setattr(mod, attr, None)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Mock Redis client
# ---------------------------------------------------------------------------


class FakeRedis:
    """Dict-backed async Redis stand-in for tests."""

    def __init__(self):
        self._store: dict[str, Any] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self._sorted_sets: dict[str, dict[str, float]] = {}
        self._expiry: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    async def set(self, key: str, value: str, **kwargs) -> None:
        self._store[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self._store.pop(k, None)

    async def hget(self, name: str, key: str) -> Optional[str]:
        return self._hashes.get(name, {}).get(key)

    async def hset(self, name: str, key: str, value: str) -> None:
        self._hashes.setdefault(name, {})[key] = value

    async def hincrby(self, name: str, key: str, amount: int = 1) -> int:
        h = self._hashes.setdefault(name, {})
        current = int(h.get(key, 0))
        h[key] = str(current + amount)
        return current + amount

    async def hdel(self, name: str, *keys: str) -> None:
        h = self._hashes.get(name, {})
        for k in keys:
            h.pop(k, None)

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))

    async def lpush(self, name: str, *values: str) -> None:
        lst = self._lists.setdefault(name, [])
        for v in values:
            lst.insert(0, v)

    async def ltrim(self, name: str, start: int, stop: int) -> None:
        lst = self._lists.get(name, [])
        self._lists[name] = lst[start : stop + 1]

    async def lrange(self, name: str, start: int, stop: int) -> list[str]:
        lst = self._lists.get(name, [])
        return lst[start : stop + 1] if stop >= 0 else lst[start:]

    async def zadd(self, name: str, mapping: dict[str, float]) -> None:
        ss = self._sorted_sets.setdefault(name, {})
        ss.update(mapping)

    async def zrangebyscore(self, name: str, min_score, max_score) -> list[str]:
        ss = self._sorted_sets.get(name, {})
        lo = float("-inf") if min_score == "-inf" else float(min_score)
        hi = float("inf") if max_score == "+inf" else float(max_score)
        return [m for m, s in ss.items() if lo <= s <= hi]

    async def zremrangebyscore(self, name: str, min_score, max_score) -> None:
        ss = self._sorted_sets.get(name, {})
        lo = float("-inf") if min_score == "-inf" else float(min_score)
        hi = float("inf") if max_score == "+inf" else float(max_score)
        to_del = [m for m, s in ss.items() if lo <= s <= hi]
        for m in to_del:
            del ss[m]

    async def expire(self, name: str, seconds: int) -> None:
        self._expiry[name] = seconds

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_redis():
    """Return a FakeRedis instance."""
    return FakeRedis()


@pytest.fixture
def mock_redis_client(fake_redis, settings_env):
    """Return a RedisClient wired to a FakeRedis backend."""
    from src.redis_client import RedisClient

    rc = RedisClient.__new__(RedisClient)
    rc.url = "redis://fake:6379"
    rc.available = True
    rc._redis = fake_redis
    return rc


@pytest.fixture
def disconnected_redis_client(settings_env):
    """Return a RedisClient that reports unavailable."""
    from src.redis_client import RedisClient

    rc = RedisClient.__new__(RedisClient)
    rc.url = "redis://fake:6379"
    rc.available = False
    rc._redis = None
    return rc


# ---------------------------------------------------------------------------
# Mock K8s client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_k8s_client(mock_redis_client, settings_env):
    """Return a K8sClient with all K8s API objects mocked."""
    with patch("src.k8s_client.config"):
        with patch("src.k8s_client.client") as mock_client:
            mock_client.CoreV1Api.return_value = MagicMock()
            mock_client.AppsV1Api.return_value = MagicMock()
            mock_client.BatchV1Api.return_value = MagicMock()
            mock_client.AutoscalingV2Api.return_value = MagicMock()
            mock_client.PolicyV1Api.return_value = MagicMock()
            mock_client.CustomObjectsApi.return_value = MagicMock()

            with patch(
                "src.k8s_client.get_redis_client", return_value=mock_redis_client
            ):
                from src.k8s_client import K8sClient

                k = K8sClient()
                yield k


# ---------------------------------------------------------------------------
# httpx / FastAPI test client
# ---------------------------------------------------------------------------


@pytest.fixture
def app_no_lifespan(settings_env):
    """Create a FastAPI app instance without running lifespan (no real services).

    Routes are registered but background tasks / Redis / Qdrant are stubbed.
    """
    from src.api import (
        create_app,
        app_state,
    )

    # Stub guardian
    guardian = MagicMock()
    guardian.run_scan = AsyncMock(
        return_value={
            "success": True,
            "summary": "test",
            "audit_log": [],
            "rate_limit": {"remaining_actions": 30, "max_actions_per_hour": 30},
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
    )
    guardian.investigate_issue = AsyncMock(
        return_value={
            "success": True,
            "summary": "investigated",
            "audit_log": [],
            "timestamp": "2025-01-01T00:00:00+00:00",
        }
    )
    app_state.guardian = guardian
    app_state.last_scan_result = None
    app_state.websocket_connections = []
    app_state.pending_approvals = []

    app = create_app()
    # Remove the lifespan so httpx can call routes directly
    app.router.lifespan_context = None
    return app


@pytest_asyncio.fixture
async def async_client(app_no_lifespan):
    """Async httpx test client for FastAPI endpoint tests."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app_no_lifespan)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
