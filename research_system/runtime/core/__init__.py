from __future__ import annotations

from importlib import import_module

__all__ = [
    "AgentControl",
    "ArtifactBoard",
    "Blackboard",
    "HumanApprovalStore",
    "combine_degrade_reasons",
    "derive_llm_error_reason",
    "EventStream",
    "normalize_degrade_reason",
    "RuntimeDegradation",
    "RuntimeDegradeReason",
    "RuntimeExecutionPolicy",
    "GateEvaluator",
    "Mailbox",
    "RunControlPlane",
    "RunControlState",
    "RunLogger",
    "split_degrade_reasons",
    "generate_ulid",
    "utc_now",
]

_EXPORTS = {
    "AgentControl": ("research_system.runtime.core.agent_control", "AgentControl"),
    "ArtifactBoard": ("research_system.runtime.core.artifact_board", "ArtifactBoard"),
    "Blackboard": ("research_system.runtime.core.blackboard", "Blackboard"),
    "HumanApprovalStore": ("research_system.runtime.core.approvals", "HumanApprovalStore"),
    "EventStream": ("research_system.runtime.core.event_stream", "EventStream"),
    "combine_degrade_reasons": ("research_system.runtime.core.execution_policy", "combine_degrade_reasons"),
    "derive_llm_error_reason": ("research_system.runtime.core.execution_policy", "derive_llm_error_reason"),
    "normalize_degrade_reason": ("research_system.runtime.core.execution_policy", "normalize_degrade_reason"),
    "RuntimeDegradation": ("research_system.runtime.core.execution_policy", "RuntimeDegradation"),
    "RuntimeDegradeReason": ("research_system.runtime.core.execution_policy", "RuntimeDegradeReason"),
    "RuntimeExecutionPolicy": ("research_system.runtime.core.execution_policy", "RuntimeExecutionPolicy"),
    "GateEvaluator": ("research_system.runtime.core.gates", "GateEvaluator"),
    "Mailbox": ("research_system.runtime.core.mailbox", "Mailbox"),
    "RunControlPlane": ("research_system.runtime.core.control_plane", "RunControlPlane"),
    "RunControlState": ("research_system.runtime.core.control_plane", "RunControlState"),
    "RunLogger": ("research_system.runtime.core.logger", "RunLogger"),
    "split_degrade_reasons": ("research_system.runtime.core.execution_policy", "split_degrade_reasons"),
    "generate_ulid": ("research_system.runtime.core.id_utils", "generate_ulid"),
    "utc_now": ("research_system.runtime.core.control_plane", "utc_now"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
