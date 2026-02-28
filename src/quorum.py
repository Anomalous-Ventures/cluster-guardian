"""LLM quorum mechanism for safe remediation decisions.

When enabled, destructive actions (pod restarts, scaling, node operations)
require agreement from multiple independent LLM evaluations before proceeding.
"""

import asyncio
import json
from dataclasses import dataclass, field

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from .llm_factory import create_llm

logger = structlog.get_logger(__name__)

# Tools that require quorum approval before execution
QUORUM_REQUIRED_TOOLS = {
    "restart_pod",
    "rollout_restart_deployment",
    "rollout_restart_statefulset",
    "scale_deployment",
    "cordon_node",
    "drain_node",
    "delete_failed_job",
    "rollback_deployment",
}

# Perspective prompts for diverse evaluation
_AGENT_PERSPECTIVES = [
    (
        "You are a cautious SRE focused on stability. "
        "Err on the side of not taking action. Only approve if the evidence "
        "clearly shows the action will resolve the issue without side effects."
    ),
    (
        "You are a pragmatic SRE focused on resolving issues quickly. "
        "Approve actions when the evidence supports them. Weigh the cost of "
        "inaction (prolonged outage) against the risk of action."
    ),
    (
        "You are a senior SRE focused on root cause analysis. "
        "Only approve actions that address the root cause, not symptoms. "
        "Reject if the proposed action is a band-aid that will mask deeper issues."
    ),
]


@dataclass
class QuorumVote:
    """A single agent's vote on a proposed action."""

    agent_id: str
    action: str
    target: str
    approved: bool
    reasoning: str
    confidence: float


@dataclass
class QuorumResult:
    """Aggregated result from all quorum agents."""

    action: str
    target: str
    approved: bool
    votes: list[QuorumVote] = field(default_factory=list)
    consensus_ratio: float = 0.0
    dissenting_reasons: list[str] = field(default_factory=list)


class QuorumEvaluator:
    """Evaluates proposed actions by consulting multiple independent LLM agents."""

    def __init__(
        self,
        num_agents: int = 3,
        threshold: float = 0.5,
        timeout: float = 30.0,
    ):
        self.num_agents = min(num_agents, len(_AGENT_PERSPECTIVES))
        self.threshold = threshold
        self.timeout = timeout

    async def evaluate_action(
        self,
        action: str,
        target: str,
        context: str,
        agent_reasoning: str,
    ) -> QuorumResult:
        """Fan out to N independent LLM evaluations and aggregate votes.

        Args:
            action: The proposed action name (e.g., "restart_pod")
            target: The target resource (e.g., "default/nginx-abc123")
            context: Investigation context and cluster state
            agent_reasoning: The original agent's reasoning for the action
        """
        prompt = (
            f"A Kubernetes SRE agent proposes the following action:\n\n"
            f"Action: {action}\n"
            f"Target: {target}\n\n"
            f"Context:\n{context}\n\n"
            f"Agent's reasoning:\n{agent_reasoning}\n\n"
            f"Should this action be taken? Respond with ONLY a JSON object:\n"
            f'{{"approved": true/false, "reasoning": "your reasoning", "confidence": 0.0-1.0}}'
        )

        tasks = []
        for i in range(self.num_agents):
            tasks.append(
                self._evaluate_single(
                    agent_id=f"agent-{i + 1}",
                    perspective=_AGENT_PERSPECTIVES[i],
                    prompt=prompt,
                    action=action,
                    target=target,
                )
            )

        votes = await asyncio.gather(*tasks, return_exceptions=True)

        valid_votes: list[QuorumVote] = []
        for v in votes:
            if isinstance(v, QuorumVote):
                valid_votes.append(v)
            else:
                logger.warning("Quorum agent failed", error=str(v))

        if not valid_votes:
            return QuorumResult(
                action=action,
                target=target,
                approved=False,
                votes=[],
                consensus_ratio=0.0,
                dissenting_reasons=["All quorum agents failed to respond"],
            )

        approve_count = sum(1 for v in valid_votes if v.approved)
        consensus_ratio = approve_count / len(valid_votes)
        approved = consensus_ratio > self.threshold

        dissenting = [v.reasoning for v in valid_votes if v.approved != approved]

        return QuorumResult(
            action=action,
            target=target,
            approved=approved,
            votes=valid_votes,
            consensus_ratio=consensus_ratio,
            dissenting_reasons=dissenting,
        )

    async def _evaluate_single(
        self,
        agent_id: str,
        perspective: str,
        prompt: str,
        action: str,
        target: str,
    ) -> QuorumVote:
        """Get a single agent's vote."""
        llm = create_llm()

        messages = [
            SystemMessage(content=perspective),
            HumanMessage(content=prompt),
        ]

        try:
            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=self.timeout,
            )

            content = response.content.strip()
            # Try to extract JSON from the response
            parsed = _parse_vote_json(content)

            return QuorumVote(
                agent_id=agent_id,
                action=action,
                target=target,
                approved=parsed.get("approved", False),
                reasoning=parsed.get("reasoning", content),
                confidence=float(parsed.get("confidence", 0.5)),
            )
        except asyncio.TimeoutError:
            return QuorumVote(
                agent_id=agent_id,
                action=action,
                target=target,
                approved=False,
                reasoning="Agent timed out",
                confidence=0.0,
            )
        except Exception as exc:
            return QuorumVote(
                agent_id=agent_id,
                action=action,
                target=target,
                approved=False,
                reasoning=f"Agent error: {exc}",
                confidence=0.0,
            )


def _parse_vote_json(content: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Try direct parse first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    if "```" in content:
        parts = content.split("```")
        for part in parts:
            text = part.strip()
            if text.startswith("json"):
                text = text[4:].strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue

    # Try finding JSON object in text
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            pass

    return {}
