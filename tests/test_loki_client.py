"""Tests for src.loki_client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.loki_client import LokiClient, _parse_duration_ns


# ---------------------------------------------------------------------------
# _parse_duration_ns
# ---------------------------------------------------------------------------


class TestParseDurationNs:
    def test_parse_duration_seconds(self):
        assert _parse_duration_ns("5s") == 5_000_000_000

    def test_parse_duration_minutes(self):
        assert _parse_duration_ns("30m") == 30 * 60 * 1_000_000_000

    def test_parse_duration_hours(self):
        assert _parse_duration_ns("1h") == 3600 * 1_000_000_000

    def test_parse_duration_days(self):
        assert _parse_duration_ns("2d") == 2 * 86400 * 1_000_000_000

    def test_parse_duration_invalid(self):
        with pytest.raises(ValueError, match="Invalid duration format"):
            _parse_duration_ns("abc")


# ---------------------------------------------------------------------------
# LokiClient.query_logs
# ---------------------------------------------------------------------------


class TestQueryLogs:
    @pytest.mark.asyncio
    async def test_query_logs_returns_entries(self):
        loki_response = {
            "data": {
                "result": [
                    {
                        "stream": {"namespace": "default", "pod": "my-pod"},
                        "values": [
                            ["1700000000000000000", "log line one"],
                            ["1700000001000000000", "log line two"],
                        ],
                    }
                ]
            }
        }

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = loki_response

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.loki_client.httpx.AsyncClient", return_value=mock_client_instance
        ):
            lc = LokiClient(base_url="http://fake:3100")
            entries = await lc.query_logs('{namespace="default"}', limit=50, since="1h")

        assert len(entries) == 2
        assert entries[0]["line"] == "log line one"
        assert entries[0]["labels"]["namespace"] == "default"
        assert entries[0]["labels"]["pod"] == "my-pod"
        assert "timestamp" in entries[0]
        assert entries[1]["line"] == "log line two"


# ---------------------------------------------------------------------------
# LokiClient.query_instant
# ---------------------------------------------------------------------------


class TestQueryInstant:
    @pytest.mark.asyncio
    async def test_returns_data(self):
        loki_response = {
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"namespace": "default"}, "value": [1700000000, "42"]}
                ],
            }
        }

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = loki_response

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.loki_client.httpx.AsyncClient", return_value=mock_client_instance
        ):
            lc = LokiClient(base_url="http://fake:3100")
            data = await lc.query_instant('count_over_time({job=~".+"}[5m])')

        assert len(data["result"]) == 1
        assert data["result"][0]["metric"]["namespace"] == "default"

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = Exception("connection refused")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.loki_client.httpx.AsyncClient", return_value=mock_client_instance
        ):
            lc = LokiClient(base_url="http://fake:3100")
            data = await lc.query_instant("bad query")

        assert data == {}


# ---------------------------------------------------------------------------
# LokiClient.get_cluster_error_summary
# ---------------------------------------------------------------------------


class TestGetClusterErrorSummary:
    @pytest.mark.asyncio
    async def test_filters_by_min_count(self):
        loki_response = {
            "data": {
                "result": [
                    {"metric": {"namespace": "media"}, "value": [1700000000, "25"]},
                    {"metric": {"namespace": "quiet"}, "value": [1700000000, "3"]},
                ]
            }
        }

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = loki_response

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.loki_client.httpx.AsyncClient", return_value=mock_client_instance
        ):
            lc = LokiClient(base_url="http://fake:3100")
            results = await lc.get_cluster_error_summary(since="5m", min_count=10)

        assert len(results) == 1
        assert results[0]["namespace"] == "media"
        assert results[0]["count"] == 25

    @pytest.mark.asyncio
    async def test_sorted_by_count(self):
        loki_response = {
            "data": {
                "result": [
                    {"metric": {"namespace": "ns-a"}, "value": [1700000000, "15"]},
                    {"metric": {"namespace": "ns-b"}, "value": [1700000000, "50"]},
                ]
            }
        }

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = loki_response

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.loki_client.httpx.AsyncClient", return_value=mock_client_instance
        ):
            lc = LokiClient(base_url="http://fake:3100")
            results = await lc.get_cluster_error_summary(since="5m", min_count=10)

        assert results[0]["namespace"] == "ns-b"
        assert results[1]["namespace"] == "ns-a"
