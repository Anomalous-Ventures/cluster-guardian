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
