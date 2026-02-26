"""Tests for ingress and infrastructure monitoring."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ingress_monitor import IngressMonitor


@pytest.fixture
def mock_k8s():
    k8s = MagicMock()
    k8s.core_v1 = MagicMock()
    k8s.apps_v1 = MagicMock()
    k8s.custom_objects = MagicMock()
    return k8s


@pytest.fixture
def mock_prometheus():
    prom = MagicMock()
    prom.query = AsyncMock(return_value={"result": []})
    return prom


@pytest.fixture
def ingress(mock_k8s, mock_prometheus, settings_env):
    return IngressMonitor(k8s=mock_k8s, prometheus=mock_prometheus)


class TestCheckServiceEndpoints:
    @pytest.mark.asyncio
    async def test_healthy_endpoints(self, ingress, mock_k8s):
        mock_endpoints = MagicMock()
        mock_subset = MagicMock()
        mock_addr = MagicMock()
        mock_addr.ip = "10.0.0.1"
        mock_subset.addresses = [mock_addr]
        mock_subset.not_ready_addresses = []
        mock_endpoints.subsets = [mock_subset]

        mock_k8s.core_v1.read_namespaced_endpoints.return_value = mock_endpoints

        result = await ingress.check_service_endpoints("default", "web-svc")
        assert result["healthy"] is True
        assert result["ready"] == 1
        assert result["not_ready"] == 0

    @pytest.mark.asyncio
    async def test_no_endpoints(self, ingress, mock_k8s):
        mock_endpoints = MagicMock()
        mock_endpoints.subsets = []
        mock_k8s.core_v1.read_namespaced_endpoints.return_value = mock_endpoints

        result = await ingress.check_service_endpoints("default", "web-svc")
        assert result["healthy"] is False
        assert result["ready"] == 0

    @pytest.mark.asyncio
    async def test_endpoint_error(self, ingress, mock_k8s):
        mock_k8s.core_v1.read_namespaced_endpoints.side_effect = Exception("not found")

        result = await ingress.check_service_endpoints("default", "web-svc")
        assert result["healthy"] is False
        assert "error" in result


class TestCheckDaemonsetHealth:
    @pytest.mark.asyncio
    async def test_all_healthy(self, ingress, mock_k8s):
        ds = MagicMock()
        ds.metadata.name = "node-exporter"
        ds.metadata.namespace = "monitoring"
        ds.status.desired_number_scheduled = 3
        ds.status.number_ready = 3
        ds.status.number_unavailable = 0

        ds_list = MagicMock()
        ds_list.items = [ds]
        mock_k8s.apps_v1.list_daemon_set_for_all_namespaces.return_value = ds_list

        result = await ingress.check_daemonset_health()
        assert len(result) == 1
        assert result[0]["unavailable"] == 0

    @pytest.mark.asyncio
    async def test_unavailable_pods(self, ingress, mock_k8s):
        ds = MagicMock()
        ds.metadata.name = "fluentd"
        ds.metadata.namespace = "logging"
        ds.status.desired_number_scheduled = 3
        ds.status.number_ready = 2
        ds.status.number_unavailable = 1

        ds_list = MagicMock()
        ds_list.items = [ds]
        mock_k8s.apps_v1.list_daemon_set_for_all_namespaces.return_value = ds_list

        result = await ingress.check_daemonset_health()
        assert len(result) == 1
        assert result[0]["unavailable"] == 1

    @pytest.mark.asyncio
    async def test_protected_namespace_skipped(self, ingress, mock_k8s):
        ds = MagicMock()
        ds.metadata.name = "kube-proxy"
        ds.metadata.namespace = "kube-system"  # protected
        ds.status.desired_number_scheduled = 3
        ds.status.number_ready = 2
        ds.status.number_unavailable = 1

        ds_list = MagicMock()
        ds_list.items = [ds]
        mock_k8s.apps_v1.list_daemon_set_for_all_namespaces.return_value = ds_list

        result = await ingress.check_daemonset_health()
        assert len(result) == 0


class TestCheckPvcUsage:
    @pytest.mark.asyncio
    async def test_no_high_usage(self, ingress, mock_prometheus):
        mock_prometheus.query = AsyncMock(
            return_value={
                "result": [
                    {
                        "metric": {
                            "namespace": "default",
                            "persistentvolumeclaim": "data-pvc",
                        },
                        "value": [1234567890, "0.50"],
                    }
                ]
            }
        )
        result = await ingress.check_pvc_usage()
        assert result == []

    @pytest.mark.asyncio
    async def test_high_usage_detected(self, ingress, mock_prometheus):
        mock_prometheus.query = AsyncMock(
            return_value={
                "result": [
                    {
                        "metric": {
                            "namespace": "media",
                            "persistentvolumeclaim": "plex-data",
                        },
                        "value": [1234567890, "0.92"],
                    }
                ]
            }
        )
        result = await ingress.check_pvc_usage()
        assert len(result) == 1
        assert result[0]["pvc"] == "plex-data"
        assert result[0]["usage_percent"] == 92.0

    @pytest.mark.asyncio
    async def test_no_prometheus(self, mock_k8s, settings_env):
        ingress = IngressMonitor(k8s=mock_k8s, prometheus=None)
        result = await ingress.check_pvc_usage()
        assert result == []


class TestExtractHosts:
    def test_single_host(self, ingress):
        routes = [{"match": "Host(`grafana.spooty.io`)"}]
        hosts = ingress._extract_hosts(routes)
        assert hosts == ["grafana.spooty.io"]

    def test_multiple_hosts(self, ingress):
        routes = [
            {"match": "Host(`a.spooty.io`) || Host(`b.spooty.io`)"},
        ]
        hosts = ingress._extract_hosts(routes)
        assert hosts == ["a.spooty.io", "b.spooty.io"]

    def test_no_hosts(self, ingress):
        routes = [{"match": "PathPrefix(`/api`)"}]
        hosts = ingress._extract_hosts(routes)
        assert hosts == []


class TestSmallResponseBody:
    """Test that _http_check detects suspiciously small responses."""

    @pytest.mark.asyncio
    async def test_small_body_flagged(self, ingress):
        from unittest.mock import patch

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_response.content = b"ok"
        mock_response.elapsed = MagicMock()
        mock_response.elapsed.total_seconds.return_value = 0.05

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.ingress_monitor.httpx.AsyncClient", return_value=mock_client):
            result = await ingress._http_check("https://test.example.com/")

        assert result["suspicious_small_body"] is True
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_normal_body_passes(self, ingress):
        from unittest.mock import patch

        body = "x" * 200
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = body
        mock_response.content = body.encode()
        mock_response.elapsed = MagicMock()
        mock_response.elapsed.total_seconds.return_value = 0.1

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.ingress_monitor.httpx.AsyncClient", return_value=mock_client):
            result = await ingress._http_check("https://test.example.com/")

        assert result.get("suspicious_small_body") is False
        assert result["success"] is True
