from __future__ import annotations

from importlib import import_module

__all__ = [
    "AgentControl",
    "Blackboard",
    "EventStream",
    "RuntimeDegradation",
    "RuntimeExecutionPolicy",
    "GateEvaluator",
    "Mailbox",
    "RunControlPlane",
    "RunControlState",
    "RunLogger",
    "generate_ulid",
    "utc_now",
]

_EXPORTS = {
    "AgentControl": ("research_system.runtime.core.agent_control", "AgentControl"),
    "Blackboard": ("research_system.runtime.core.blackboard", "Blackboard"),
    "EventStream": ("research_system.runtime.core.event_stream", "EventStream"),
    "RuntimeDegradation": ("research_system.runtime.core.execution_policy", "RuntimeDegradation"),
    "RuntimeExecutionPolicy": ("research_system.runtime.core.execution_policy", "RuntimeExecutionPolicy"),
    "GateEvaluator": ("research_system.runtime.core.gates", "GateEvaluator"),
    "Mailbox": ("research_system.runtime.core.mailbox", "Mailbox"),
    "RunControlPlane": ("research_system.runtime.core.control_plane", "RunControlPlane"),
    "RunControlState": ("research_system.runtime.core.control_plane", "RunControlState"),
    "RunLogger": ("research_system.runtime.core.logger", "RunLogger"),
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
