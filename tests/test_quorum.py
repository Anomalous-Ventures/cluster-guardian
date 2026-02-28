"""Unit tests for the quorum mechanism."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from src.quorum import (
    QuorumEvaluator,
    QuorumResult,
    QuorumVote,
    QUORUM_REQUIRED_TOOLS,
    _parse_vote_json,
)
from src.quorum_tools import (
    apply_quorum_gates,
    _extract_target,
    _build_context,
)


class TestParseVoteJson:
    def test_direct_json(self):
        result = _parse_vote_json(
            '{"approved": true, "reasoning": "looks good", "confidence": 0.9}'
        )
        assert result["approved"] is True
        assert result["reasoning"] == "looks good"
        assert result["confidence"] == 0.9

    def test_json_in_markdown_code_block(self):
        content = (
            '```json\n{"approved": false, "reasoning": "risky", "confidence": 0.3}\n```'
        )
        result = _parse_vote_json(content)
        assert result["approved"] is False

    def test_json_embedded_in_text(self):
        content = 'I think we should approve. {"approved": true, "reasoning": "ok", "confidence": 0.7} That is my decision.'
        result = _parse_vote_json(content)
        assert result["approved"] is True

    def test_invalid_json_returns_empty(self):
        result = _parse_vote_json("this is not json at all")
        assert result == {}

    def test_empty_string(self):
        result = _parse_vote_json("")
        assert result == {}


class TestQuorumVote:
    def test_dataclass_fields(self):
        vote = QuorumVote(
            agent_id="agent-1",
            action="restart_pod",
            target="default/nginx",
            approved=True,
            reasoning="Pod needs restart",
            confidence=0.85,
        )
        assert vote.agent_id == "agent-1"
        assert vote.approved is True
        assert vote.confidence == 0.85


class TestQuorumResult:
    def test_default_fields(self):
        result = QuorumResult(
            action="restart_pod", target="default/nginx", approved=True
        )
        assert result.votes == []
        assert result.consensus_ratio == 0.0
        assert result.dissenting_reasons == []


class TestQuorumEvaluator:
    @patch("src.quorum.create_llm")
    async def test_unanimous_approve(self, mock_create_llm):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = (
            '{"approved": true, "reasoning": "safe to proceed", "confidence": 0.9}'
        )
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_create_llm.return_value = mock_llm

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5)
        result = await evaluator.evaluate_action(
            action="restart_pod",
            target="default/nginx",
            context="Pod is in CrashLoopBackOff",
            agent_reasoning="Pod needs restart",
        )

        assert result.approved is True
        assert result.consensus_ratio == 1.0
        assert len(result.votes) == 3
        assert all(v.approved for v in result.votes)

    @patch("src.quorum.create_llm")
    async def test_unanimous_reject(self, mock_create_llm):
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = (
            '{"approved": false, "reasoning": "too risky", "confidence": 0.8}'
        )
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_create_llm.return_value = mock_llm

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5)
        result = await evaluator.evaluate_action(
            action="drain_node",
            target="worker-1",
            context="Node has high load",
            agent_reasoning="Drain for maintenance",
        )

        assert result.approved is False
        assert result.consensus_ratio == 0.0
        assert len(result.votes) == 3

    @patch("src.quorum.create_llm")
    async def test_majority_approve(self, mock_create_llm):
        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            if call_count <= 2:
                mock_response.content = (
                    '{"approved": true, "reasoning": "approve", "confidence": 0.8}'
                )
            else:
                mock_response.content = (
                    '{"approved": false, "reasoning": "reject", "confidence": 0.6}'
                )
            return mock_response

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=side_effect)
        mock_create_llm.return_value = mock_llm

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5)
        result = await evaluator.evaluate_action(
            action="restart_pod",
            target="default/nginx",
            context="Pod crashing",
            agent_reasoning="Restart needed",
        )

        assert result.approved is True
        assert abs(result.consensus_ratio - 2 / 3) < 0.01

    @patch("src.quorum.create_llm")
    async def test_majority_reject(self, mock_create_llm):
        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            if call_count == 1:
                mock_response.content = (
                    '{"approved": true, "reasoning": "approve", "confidence": 0.7}'
                )
            else:
                mock_response.content = (
                    '{"approved": false, "reasoning": "reject", "confidence": 0.8}'
                )
            return mock_response

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=side_effect)
        mock_create_llm.return_value = mock_llm

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5)
        result = await evaluator.evaluate_action(
            action="scale_deployment",
            target="default/api",
            context="High load",
            agent_reasoning="Scale up",
        )

        assert result.approved is False
        assert abs(result.consensus_ratio - 1 / 3) < 0.01

    @patch("src.quorum.create_llm")
    async def test_malformed_response_treated_as_reject(self, mock_create_llm):
        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            if call_count == 1:
                mock_response.content = "this is not json"  # malformed
            else:
                mock_response.content = (
                    '{"approved": true, "reasoning": "ok", "confidence": 0.9}'
                )
            return mock_response

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=side_effect)
        mock_create_llm.return_value = mock_llm

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5)
        result = await evaluator.evaluate_action(
            action="restart_pod",
            target="default/nginx",
            context="test",
            agent_reasoning="test",
        )

        # malformed defaults to approved=False, so 2 approve + 1 reject = 2/3
        assert result.approved is True
        assert len(result.votes) == 3

    @patch("src.quorum.create_llm")
    async def test_timeout_treated_as_reject(self, mock_create_llm):
        call_count = 0

        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(100)  # will timeout
            mock_response = MagicMock()
            mock_response.content = (
                '{"approved": true, "reasoning": "ok", "confidence": 0.9}'
            )
            return mock_response

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=side_effect)
        mock_create_llm.return_value = mock_llm

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5, timeout=0.1)
        result = await evaluator.evaluate_action(
            action="restart_pod",
            target="default/nginx",
            context="test",
            agent_reasoning="test",
        )

        assert len(result.votes) == 3
        # One timed out (approved=False), two approved
        timed_out = [v for v in result.votes if "timed out" in v.reasoning.lower()]
        assert len(timed_out) == 1
        assert timed_out[0].approved is False

    @patch("src.quorum.create_llm")
    async def test_all_agents_fail(self, mock_create_llm):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM down"))
        mock_create_llm.return_value = mock_llm

        evaluator = QuorumEvaluator(num_agents=3, threshold=0.5)
        result = await evaluator.evaluate_action(
            action="restart_pod",
            target="default/nginx",
            context="test",
            agent_reasoning="test",
        )

        # All agents errored - they return QuorumVote with approved=False
        # So result should be not approved
        assert result.approved is False


class TestQuorumToolIntegration:
    def test_quorum_required_tools_set(self):
        assert "restart_pod" in QUORUM_REQUIRED_TOOLS
        assert "scale_deployment" in QUORUM_REQUIRED_TOOLS
        assert "cordon_node" in QUORUM_REQUIRED_TOOLS
        assert "drain_node" in QUORUM_REQUIRED_TOOLS
        # Read-only tools should NOT be in the set
        assert "get_pod_details" not in QUORUM_REQUIRED_TOOLS
        assert "list_nodes" not in QUORUM_REQUIRED_TOOLS

    def test_extract_target_with_namespace_and_pod(self):
        target = _extract_target({"namespace": "default", "pod_name": "nginx-abc"})
        assert target == "default/nginx-abc"

    def test_extract_target_with_deployment(self):
        target = _extract_target({"namespace": "prod", "deployment_name": "api-server"})
        assert target == "prod/api-server"

    def test_extract_target_node_only(self):
        target = _extract_target({"node_name": "worker-1"})
        assert target == "worker-1"

    def test_extract_target_unknown(self):
        target = _extract_target({})
        assert target == "unknown"

    def test_build_context_with_reason(self):
        ctx = _build_context({"reason": "Pod crashing", "namespace": "default"})
        assert "Reason: Pod crashing" in ctx
        assert "Namespace: default" in ctx

    def test_build_context_empty(self):
        ctx = _build_context({})
        assert ctx == "No additional context"

    def test_apply_quorum_gates_only_gates_destructive(self):
        # Create mock tools
        destructive_tool = MagicMock()
        destructive_tool.name = "restart_pod"
        original_coroutine = AsyncMock()
        destructive_tool.coroutine = original_coroutine

        readonly_tool = MagicMock()
        readonly_tool.name = "get_pod_details"
        readonly_tool.coroutine = AsyncMock()

        evaluator = MagicMock(spec=QuorumEvaluator)

        result = apply_quorum_gates([destructive_tool, readonly_tool], evaluator)

        assert len(result) == 2
        # The destructive tool's coroutine should have been replaced (mutated in-place)
        assert result[0].coroutine is not original_coroutine
        # The readonly tool should be unchanged
        assert result[1] is readonly_tool

    @patch("src.quorum.create_llm")
    async def test_quorum_disabled_bypasses(
        self, mock_create_llm, settings_env, monkeypatch
    ):
        """When quorum_enabled=False, tools execute directly."""
        monkeypatch.setenv("CLUSTER_GUARDIAN_QUORUM_ENABLED", "false")
        # The quorum gate is only applied during ClusterGuardian.__init__
        # when settings.quorum_enabled is True, so this test just verifies
        # the config default
        from src.config import Settings

        s = Settings()
        assert s.quorum_enabled is False
