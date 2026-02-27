"""Tests for cluster auto-discovery (src.cluster_discovery)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cluster_discovery import ClusterDiscovery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(name: str, namespace: str, ports: list[tuple[int, str]]):
    """Build a mock K8s Service object."""
    svc = MagicMock()
    svc.metadata.name = name
    svc.metadata.namespace = namespace
    mock_ports = []
    for port_num, port_name in ports:
        p = MagicMock()
        p.port = port_num
        p.name = port_name
        mock_ports.append(p)
    svc.spec.ports = mock_ports
    return svc


def _make_svc_list(services):
    """Wrap a list of mock services in a mock ServiceList."""
    svc_list = MagicMock()
    svc_list.items = services
    return svc_list


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_no_k8s_client():
    """discover() returns empty when no K8s client is provided."""
    cd = ClusterDiscovery(k8s_client=None)
    result = await cd.discover()
    assert result == {}


@pytest.mark.asyncio
async def test_discover_list_services_fails():
    """discover() returns empty when listing services throws."""
    k8s = MagicMock()
    k8s.core_v1.list_service_for_all_namespaces.side_effect = RuntimeError("fail")
    cd = ClusterDiscovery(k8s_client=k8s)

    with patch("src.cluster_discovery.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        mock_thread.side_effect = RuntimeError("fail")
        result = await cd.discover()

    assert result == {}


@pytest.mark.asyncio
async def test_discover_finds_prometheus():
    """discover() correctly identifies a Prometheus service."""
    svc = _make_service("prometheus-kube-prometheus-prometheus", "prometheus", [(9090, "http")])
    svc_list = _make_svc_list([svc])

    k8s = MagicMock()

    cd = ClusterDiscovery(k8s_client=k8s)

    with (
        patch("src.cluster_discovery.asyncio.to_thread", new_callable=AsyncMock, return_value=svc_list),
        patch.object(cd, "_probe", new_callable=AsyncMock, return_value=True),
    ):
        result = await cd.discover()

    assert "prometheus_url" in result
    assert "prometheus" in result["prometheus_url"]
    assert ":9090" in result["prometheus_url"]


@pytest.mark.asyncio
async def test_discover_finds_redis_with_redis_scheme():
    """discover() uses redis:// scheme for Redis services."""
    svc = _make_service("redis-ai-master", "llm", [(6379, "redis")])
    svc_list = _make_svc_list([svc])

    k8s = MagicMock()
    cd = ClusterDiscovery(k8s_client=k8s)

    with (
        patch("src.cluster_discovery.asyncio.to_thread", new_callable=AsyncMock, return_value=svc_list),
        patch.object(cd, "_probe", new_callable=AsyncMock, return_value=True),
    ):
        result = await cd.discover()

    assert "redis_url" in result
    assert result["redis_url"].startswith("redis://")


@pytest.mark.asyncio
async def test_discover_uses_default_port_when_matching():
    """discover() uses default port when the service has that port."""
    svc = _make_service("loki-gateway", "loki", [(3100, "http"), (9095, "grpc")])
    svc_list = _make_svc_list([svc])

    k8s = MagicMock()
    cd = ClusterDiscovery(k8s_client=k8s)

    with (
        patch("src.cluster_discovery.asyncio.to_thread", new_callable=AsyncMock, return_value=svc_list),
        patch.object(cd, "_probe", new_callable=AsyncMock, return_value=True),
    ):
        result = await cd.discover()

    assert "loki_url" in result
    assert ":3100" in result["loki_url"]


@pytest.mark.asyncio
async def test_discover_falls_back_to_first_port():
    """discover() falls back to first port when default port not present."""
    svc = _make_service("gatus", "observability", [(8080, "http")])
    svc_list = _make_svc_list([svc])

    k8s = MagicMock()
    cd = ClusterDiscovery(k8s_client=k8s)

    with (
        patch("src.cluster_discovery.asyncio.to_thread", new_callable=AsyncMock, return_value=svc_list),
        patch.object(cd, "_probe", new_callable=AsyncMock, return_value=True),
    ):
        result = await cd.discover()

    assert "gatus_url" in result
    # Default port for gatus is 80, but the service only has 8080
    assert ":8080" in result["gatus_url"]


@pytest.mark.asyncio
async def test_discover_multiple_services():
    """discover() finds multiple well-known services in a single scan."""
    services = [
        _make_service("prometheus-server", "monitoring", [(9090, "http")]),
        _make_service("alertmanager", "monitoring", [(9093, "http")]),
        _make_service("qdrant", "llm", [(6333, "http")]),
        _make_service("my-app", "default", [(8080, "http")]),  # not well-known
    ]
    svc_list = _make_svc_list(services)

    k8s = MagicMock()
    cd = ClusterDiscovery(k8s_client=k8s)

    with (
        patch("src.cluster_discovery.asyncio.to_thread", new_callable=AsyncMock, return_value=svc_list),
        patch.object(cd, "_probe", new_callable=AsyncMock, return_value=True),
    ):
        result = await cd.discover()

    assert "prometheus_url" in result
    assert "alertmanager_url" in result
    assert "qdrant_url" in result
    assert len(result) == 3  # my-app should not be discovered


@pytest.mark.asyncio
async def test_get_discovered_returns_copy():
    """get_discovered() returns a copy of the discovered dict."""
    cd = ClusterDiscovery()
    cd._discovered = {"prometheus_url": "http://prom:9090"}
    result = cd.get_discovered()
    assert result == {"prometheus_url": "http://prom:9090"}
    # Mutating the copy should not affect internal state
    result["foo"] = "bar"
    assert "foo" not in cd._discovered


@pytest.mark.asyncio
async def test_probe_skips_redis():
    """_probe() returns True immediately for Redis URLs."""
    cd = ClusterDiscovery()
    result = await cd._probe("redis://localhost:6379", "redis_url")
    assert result is True


@pytest.mark.asyncio
async def test_probe_http_success():
    """_probe() returns True for successful HTTP responses."""
    cd = ClusterDiscovery()

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("src.cluster_discovery.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await cd._probe("http://localhost:9090", "prometheus_url")

    assert result is True


@pytest.mark.asyncio
async def test_probe_http_failure():
    """_probe() returns False when HTTP request fails."""
    cd = ClusterDiscovery()

    with patch("src.cluster_discovery.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await cd._probe("http://localhost:9090", "prometheus_url")

    assert result is False
