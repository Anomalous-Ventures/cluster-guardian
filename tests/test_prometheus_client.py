"""Tests for src.prometheus_client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.prometheus_client import PrometheusClient, _extract_value


# ---------------------------------------------------------------------------
# _extract_value
# ---------------------------------------------------------------------------


class TestExtractValue:
    def test_extract_value_valid(self):
        result = {"result": [{"value": [1234, "0.5"]}]}
        assert _extract_value(result) == 0.5

    def test_extract_value_empty(self):
        result = {"result": []}
        assert _extract_value(result) == 0.0

    def test_extract_value_missing_key(self):
        assert _extract_value({}) == 0.0


# ---------------------------------------------------------------------------
# PrometheusClient.query
# ---------------------------------------------------------------------------


class TestQuery:
    @pytest.mark.asyncio
    async def test_query_success(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "success",
            "data": {"resultType": "vector", "result": [{"value": [1234, "0.75"]}]},
        }

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.prometheus_client.httpx.AsyncClient", return_value=mock_client_instance
        ):
            pc = PrometheusClient(base_url="http://fake:9090")
            data = await pc.query("up")

        assert data == {"resultType": "vector", "result": [{"value": [1234, "0.75"]}]}
        mock_client_instance.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_error(self):
        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = Exception("connection refused")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.prometheus_client.httpx.AsyncClient", return_value=mock_client_instance
        ):
            pc = PrometheusClient(base_url="http://fake:9090")
            data = await pc.query("up")

        assert "error" in data
        assert "connection refused" in data["error"]


# ---------------------------------------------------------------------------
# PrometheusClient.get_pod_cpu_usage
# ---------------------------------------------------------------------------


class TestGetPodCpuUsage:
    @pytest.mark.asyncio
    async def test_get_pod_cpu_usage(self):
        pc = PrometheusClient(base_url="http://fake:9090")

        call_count = 0

        async def fake_query(promql: str) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # CPU usage: 0.25 cores
                return {"result": [{"value": [1234, "0.25"]}]}
            else:
                # CPU request: 0.5 cores
                return {"result": [{"value": [1234, "0.5"]}]}

        pc.query = fake_query

        result = await pc.get_pod_cpu_usage("default", "my-pod")

        assert result["pod"] == "my-pod"
        assert result["namespace"] == "default"
        assert result["cpu_cores"] == 0.25
        assert result["cpu_request_cores"] == 0.5
        assert result["cpu_percent_of_request"] == 50.0
