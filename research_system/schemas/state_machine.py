from __future__ import annotations

from typing import Literal, TypeAlias


RunStatus: TypeAlias = Literal[
    "received",
    "ready",
    "running",
    "paused",
    "waiting_approval",
    "completed",
    "completed_partial",
    "gated",
    "failed",
    "cancelled",
    "timed_out",
]

NodeStatus: TypeAlias = Literal[
    "pending",
    "waiting",
    "waiting_approval",
    "running",
    "completed",
    "skipped",
    "degraded",
    "failed",
    "cancelled",
    "timed_out",
    "gated",
]

ToolCallStatus: TypeAlias = Literal[
    "pending",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "rejected",
    "cancelled",
    "timed_out",
]

IntakeStatus: TypeAlias = Literal["accepted", "needs_clarification"]

RUN_TERMINAL_STATUSES = frozenset({
    "completed",
    "completed_partial",
    "gated",
    "failed",
    "cancelled",
    "timed_out",
})

_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "received": frozenset({"ready", "running", "paused", "waiting_approval", "gated", "failed", "cancelled"}),
    "ready": frozenset({"running", "paused", "waiting_approval", "gated", "failed", "cancelled"}),
    "running": frozenset({"paused", "waiting_approval", "completed", "completed_partial", "gated", "failed", "cancelled", "timed_out"}),
    "paused": frozenset({"running", "cancelled", "failed", "timed_out"}),
    "waiting_approval": frozenset({"running", "paused", "gated", "failed", "cancelled", "timed_out"}),
    "completed": frozenset({"paused", "completed_partial"}),
    "completed_partial": frozenset({"paused"}),
    "gated": frozenset({"paused"}),
    "failed": frozenset({"paused"}),
    "cancelled": frozenset({"paused"}),
    "timed_out": frozenset({"paused"}),
}

_NODE_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "waiting", "waiting_approval", "skipped", "cancelled"}),
    "waiting": frozenset({"running", "waiting_approval", "skipped", "failed", "cancelled"}),
    "waiting_approval": frozenset({"running", "skipped", "failed", "cancelled", "timed_out"}),
    "running": frozenset({"waiting", "waiting_approval", "completed", "skipped", "degraded", "failed", "cancelled", "timed_out", "gated"}),
    "completed": frozenset({"pending"}),
    "skipped": frozenset({"pending"}),
    "degraded": frozenset({"pending"}),
    "failed": frozenset({"pending"}),
    "cancelled": frozenset({"pending"}),
    "timed_out": frozenset({"pending"}),
    "gated": frozenset({"pending"}),
}

_TOOL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "waiting_approval", "rejected", "cancelled"}),
    "running": frozenset({"completed", "failed", "rejected", "cancelled", "timed_out", "waiting_approval"}),
    "waiting_approval": frozenset({"running", "rejected", "cancelled", "timed_out"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
    "timed_out": frozenset(),
}


class InvalidStatusTransition(ValueError):
    """Raised when a persisted runtime state skips a protocol transition."""


def validate_status_transition(
    *,
    scope: Literal["run", "node", "tool"],
    current: str,
    target: str,
) -> None:
    if current == target:
        return
    transitions = {
        "run": _RUN_TRANSITIONS,
        "node": _NODE_TRANSITIONS,
        "tool": _TOOL_TRANSITIONS,
    }[scope]
    if target not in transitions.get(current, frozenset()):
        raise InvalidStatusTransition(
            f"invalid {scope} status transition: {current}->{target}"
        )


def transition_status(
    *,
    scope: Literal["run", "node", "tool"],
    current: str,
    target: str,
) -> str:
    validate_status_transition(scope=scope, current=current, target=target)
    return target


__all__ = [
    "IntakeStatus",
    "InvalidStatusTransition",
    "NodeStatus",
    "RUN_TERMINAL_STATUSES",
    "RunStatus",
    "ToolCallStatus",
    "transition_status",
    "validate_status_transition",
]
