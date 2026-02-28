"""Quorum-gated tool wrappers.

Wraps destructive Kubernetes tools with quorum evaluation so that
multiple independent LLM agents must agree before the action proceeds.
"""

from typing import Any, Callable, Coroutine, Optional

import structlog

from .quorum import QuorumEvaluator, QUORUM_REQUIRED_TOOLS

logger = structlog.get_logger(__name__)


def quorum_gate(
    tool,
    evaluator: QuorumEvaluator,
    broadcast_callback: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None,
):
    """Wrap a tool with quorum evaluation before execution.

    Returns a new tool with the same name and schema but gated by quorum.
    If the quorum rejects, the tool returns a BLOCKED message instead of
    executing.
    """
    original_coroutine = tool.coroutine

    async def gated_func(*args, **kwargs):
        action = tool.name
        target = _extract_target(kwargs)
        context = _build_context(kwargs)

        result = await evaluator.evaluate_action(
            action=action,
            target=target,
            context=context,
            agent_reasoning="",
        )

        # Broadcast the vote result
        if broadcast_callback:
            try:
                from datetime import datetime, timezone

                await broadcast_callback(
                    {
                        "type": "quorum_vote",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "data": {
                            "action": action,
                            "target": target,
                            "approved": result.approved,
                            "consensus_ratio": result.consensus_ratio,
                            "votes": [
                                {
                                    "agent_id": v.agent_id,
                                    "approved": v.approved,
                                    "reasoning": v.reasoning,
                                    "confidence": v.confidence,
                                }
                                for v in result.votes
                            ],
                        },
                    }
                )
            except Exception:
                pass

        if not result.approved:
            reasons = (
                "; ".join(result.dissenting_reasons)
                if result.dissenting_reasons
                else "majority voted against"
            )
            return (
                f"BLOCKED by quorum ({result.consensus_ratio:.0%} approved, "
                f"threshold >50% required). Reasons: {reasons}"
            )

        return await original_coroutine(*args, **kwargs)

    # Replace the tool's coroutine
    tool.coroutine = gated_func
    return tool


def apply_quorum_gates(
    tools: list,
    evaluator: QuorumEvaluator,
    broadcast_callback: Optional[Callable[..., Coroutine[Any, Any, Any]]] = None,
) -> list:
    """Apply quorum gates to all destructive tools in the list.

    Non-destructive tools pass through unchanged.
    """
    result = []
    for t in tools:
        if t.name in QUORUM_REQUIRED_TOOLS:
            result.append(quorum_gate(t, evaluator, broadcast_callback))
        else:
            result.append(t)
    return result


def _extract_target(kwargs: dict) -> str:
    """Build a target identifier from tool kwargs."""
    namespace = kwargs.get("namespace", "")
    name = (
        kwargs.get("pod_name", "")
        or kwargs.get("deployment_name", "")
        or kwargs.get("statefulset_name", "")
        or kwargs.get("node_name", "")
        or kwargs.get("job_name", "")
        or ""
    )
    if namespace and name:
        return f"{namespace}/{name}"
    return name or namespace or "unknown"


def _build_context(kwargs: dict) -> str:
    """Build a context string from tool kwargs."""
    parts = []
    if "reason" in kwargs:
        parts.append(f"Reason: {kwargs['reason']}")
    if "namespace" in kwargs:
        parts.append(f"Namespace: {kwargs['namespace']}")
    if "replicas" in kwargs:
        parts.append(f"Target replicas: {kwargs['replicas']}")
    return "\n".join(parts) if parts else "No additional context"
