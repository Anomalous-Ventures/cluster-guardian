"""Tests for dynamic service discovery."""

from unittest.mock import MagicMock

import pytest

from src.service_discovery import ServiceDiscovery


@pytest.fixture
def mock_k8s():
    k8s = MagicMock()
    k8s.custom_objects = MagicMock()
    return k8s


@pytest.fixture
def mock_health_checker():
    hc = MagicMock()
    hc.service_checks = {"grafana": MagicMock(), "authentik": MagicMock()}
    return hc


@pytest.fixture
def discovery(mock_k8s, mock_health_checker, settings_env):
    return ServiceDiscovery(k8s=mock_k8s, health_checker=mock_health_checker)


class TestRefresh:
    @pytest.mark.asyncio
    async def test_discovers_new_service(self, discovery, mock_k8s):
        mock_k8s.custom_objects.list_cluster_custom_object.return_value = {
            "items": [
                {
                    "metadata": {"name": "myapp-ingressroute", "namespace": "apps"},
                    "spec": {
                        "routes": [{"match": "Host(`myapp.spooty.io`)"}],
                        "tls": {"certResolver": "le"},
                    },
                }
            ]
        }
        new = await discovery.refresh()
        assert len(new) == 1
        assert new[0]["name"] == "myapp"
        assert new[0]["hosts"] == ["myapp.spooty.io"]
        assert new[0]["tls"] is True

    @pytest.mark.asyncio
    async def test_skips_known_services(self, discovery, mock_k8s):
        mock_k8s.custom_objects.list_cluster_custom_object.return_value = {
            "items": [
                {
                    "metadata": {
                        "name": "grafana-ingressroute",
                        "namespace": "monitoring",
                    },
                    "spec": {
                        "routes": [{"match": "Host(`grafana.spooty.io`)"}],
                    },
                }
            ]
        }
        new = await discovery.refresh()
        assert len(new) == 0

    @pytest.mark.asyncio
    async def test_skips_no_hosts(self, discovery, mock_k8s):
        mock_k8s.custom_objects.list_cluster_custom_object.return_value = {
            "items": [
                {
                    "metadata": {"name": "internal", "namespace": "ops"},
                    "spec": {"routes": [{"match": "PathPrefix(`/api`)"}]},
                }
            ]
        }
        new = await discovery.refresh()
        assert len(new) == 0

    @pytest.mark.asyncio
    async def test_no_duplicates_on_second_refresh(self, discovery, mock_k8s):
        items = {
            "items": [
                {
                    "metadata": {"name": "myapp-ingressroute", "namespace": "apps"},
                    "spec": {
                        "routes": [{"match": "Host(`myapp.spooty.io`)"}],
                    },
                }
            ]
        }
        mock_k8s.custom_objects.list_cluster_custom_object.return_value = items
        first = await discovery.refresh()
        second = await discovery.refresh()
        assert len(first) == 1
        assert len(second) == 0

    @pytest.mark.asyncio
    async def test_error_handling(self, discovery, mock_k8s):
        mock_k8s.custom_objects.list_cluster_custom_object.side_effect = Exception(
            "fail"
        )
        new = await discovery.refresh()
        assert new == []


class TestGetDiscovered:
    @pytest.mark.asyncio
    async def test_returns_all(self, discovery, mock_k8s):
        mock_k8s.custom_objects.list_cluster_custom_object.return_value = {
            "items": [
                {
                    "metadata": {"name": "svc1-ingressroute", "namespace": "ns1"},
                    "spec": {"routes": [{"match": "Host(`svc1.spooty.io`)"}]},
                },
                {
                    "metadata": {"name": "svc2-ingressroute", "namespace": "ns2"},
                    "spec": {"routes": [{"match": "Host(`svc2.spooty.io`)"}]},
                },
            ]
        }
        await discovery.refresh()
        discovered = discovery.get_discovered()
        assert len(discovered) == 2


class TestShouldRefresh:
    def test_first_n_minus_1_loops_false(self, discovery):
        for _ in range(9):
            assert discovery.should_refresh(10) is False

    def test_nth_loop_true(self, discovery):
        for _ in range(9):
            discovery.should_refresh(10)
        assert discovery.should_refresh(10) is True

    def test_resets_after_refresh(self, discovery):
        for _ in range(10):
            discovery.should_refresh(10)
        assert discovery.should_refresh(10) is False
