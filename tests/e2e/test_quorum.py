"""E2E tests for quorum mechanism integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class TestQuorumE2E:
    async def test_investigation_without_quorum(self, http_client):
        """Default: quorum is disabled, investigation runs normally."""
        resp = await http_client.post(
            "/api/v1/investigate",
            json={"description": "Quorum disabled test"},
            timeout=60.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    async def test_quorum_config_defaults(self, guardian_server):
        """Verify quorum config fields exist with expected defaults."""
        from src.config import Settings

        s = Settings()
        assert hasattr(s, "quorum_enabled")
        assert hasattr(s, "quorum_agents")
        assert hasattr(s, "quorum_threshold")
        assert s.quorum_enabled is False
        assert s.quorum_agents == 3
        assert s.quorum_threshold == 0.5

    async def test_quorum_evaluator_importable(self):
        """Verify the quorum module can be imported."""
        from src.quorum import (
            QuorumEvaluator,
            QUORUM_REQUIRED_TOOLS,
        )

        assert QuorumEvaluator is not None
        assert len(QUORUM_REQUIRED_TOOLS) > 0
        assert "restart_pod" in QUORUM_REQUIRED_TOOLS

    async def test_quorum_tools_importable(self):
        """Verify the quorum_tools module can be imported."""
        from src.quorum_tools import apply_quorum_gates, quorum_gate

        assert apply_quorum_gates is not None
        assert quorum_gate is not None

    @patch("src.quorum.create_llm")
    async def test_quorum_evaluator_standalone(self, mock_create_llm):
        """Test the QuorumEvaluator directly outside of the server."""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = (
            '{"approved": true, "reasoning": "safe action", "confidence": 0.9}'
        )
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_create_llm.return_value = mock_llm

        from src.quorum import QuorumEvaluator

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5)
        result = await evaluator.evaluate_action(
            action="restart_pod",
            target="default/test-pod",
            context="Pod is crashing",
            agent_reasoning="Need to restart",
        )

        assert result.approved is True
        assert result.consensus_ratio == 1.0
        assert len(result.votes) == 3
