"""Tests for src.memory.VectorMemory."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.memory import VectorMemory


@pytest.fixture
def unavailable_vm(settings_env):
    """Return a VectorMemory instance with available=False."""
    vm = VectorMemory(
        qdrant_url="http://fake:6333",
        litellm_url="http://fake:4000",
        litellm_api_key="test",
    )
    vm.available = False
    return vm


@pytest.fixture
def available_vm(settings_env):
    """Return a VectorMemory instance with available=True and mocked Qdrant client."""
    vm = VectorMemory(
        qdrant_url="http://fake:6333",
        litellm_url="http://fake:4000",
        litellm_api_key="test",
    )
    vm.available = True
    vm._client = AsyncMock()
    return vm


# ---------------------------------------------------------------------------
# Unavailable short-circuits
# ---------------------------------------------------------------------------


class TestUnavailable:
    @pytest.mark.asyncio
    async def test_store_issue_when_unavailable(self, unavailable_vm):
        await unavailable_vm.store_issue("pod crash", "restarted pod")
        # No error raised, no Qdrant call attempted

    @pytest.mark.asyncio
    async def test_recall_similar_when_unavailable(self, unavailable_vm):
        result = await unavailable_vm.recall_similar_issues("pod crash")
        assert result == []


# ---------------------------------------------------------------------------
# Available -- store_issue
# ---------------------------------------------------------------------------


class TestStoreIssue:
    @pytest.mark.asyncio
    async def test_store_issue_calls_qdrant(self, available_vm):
        fake_embedding = [0.1] * 1536

        with patch.object(
            available_vm, "_get_embedding", new_callable=AsyncMock
        ) as mock_embed:
            mock_embed.return_value = fake_embedding
            await available_vm.store_issue(
                "OOMKilled in prod", "Increased memory limit"
            )

        mock_embed.assert_awaited_once_with("OOMKilled in prod")
        available_vm._client.upsert.assert_awaited_once()

        call_kwargs = available_vm._client.upsert.call_args
        assert call_kwargs.kwargs["collection_name"] == available_vm.collection
        points = call_kwargs.kwargs["points"]
        assert len(points) == 1


# ---------------------------------------------------------------------------
# Available -- recall_similar_issues
# ---------------------------------------------------------------------------


class TestRecallSimilar:
    @pytest.mark.asyncio
    async def test_recall_similar_returns_results(self, available_vm):
        fake_embedding = [0.1] * 1536

        mock_point = MagicMock()
        mock_point.payload = {
            "issue": "OOMKilled in prod",
            "resolution": "Increased memory limit",
            "timestamp": "2025-01-15T10:00:00+00:00",
        }
        mock_point.score = 0.95

        mock_query_result = MagicMock()
        mock_query_result.points = [mock_point]
        available_vm._client.query_points = AsyncMock(return_value=mock_query_result)

        with patch.object(
            available_vm, "_get_embedding", new_callable=AsyncMock
        ) as mock_embed:
            mock_embed.return_value = fake_embedding
            results = await available_vm.recall_similar_issues(
                "pod keeps crashing", top_k=3
            )

        mock_embed.assert_awaited_once_with("pod keeps crashing")
        available_vm._client.query_points.assert_awaited_once()

        assert len(results) == 1
        assert results[0]["issue"] == "OOMKilled in prod"
        assert results[0]["resolution"] == "Increased memory limit"
        assert results[0]["score"] == 0.95
        assert results[0]["timestamp"] == "2025-01-15T10:00:00+00:00"
