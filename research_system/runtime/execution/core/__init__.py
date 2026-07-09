from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorActionRunner",
    "OrchestratorExecutionControlSupport",
    "OrchestratorExecutionLifecycleSupport",
    "OrchestratorExecutionResearchFacade",
    "OrchestratorExecutionRuntimeSupport",
    "OrchestratorExecutionSupport",
    "OrchestratorExecutionTraceSupport",
    "OrchestratorPlanExecutor",
    "OrchestratorRuntimeControlPlaneController",
    "OrchestratorRuntimeProtocolController",
]

_EXPORTS = {
    "OrchestratorActionRunner": (
        "research_system.runtime.execution.core.action_runner",
        "OrchestratorActionRunner",
    ),
    "OrchestratorExecutionControlSupport": (
        "research_system.runtime.execution.core.control_support",
        "OrchestratorExecutionControlSupport",
    ),
    "OrchestratorExecutionLifecycleSupport": (
        "research_system.runtime.execution.core.lifecycle_support",
        "OrchestratorExecutionLifecycleSupport",
    ),
    "OrchestratorExecutionResearchFacade": (
        "research_system.runtime.execution.core.research_facade",
        "OrchestratorExecutionResearchFacade",
    ),
    "OrchestratorExecutionRuntimeSupport": (
        "research_system.runtime.execution.core.runtime_support",
        "OrchestratorExecutionRuntimeSupport",
    ),
    "OrchestratorExecutionSupport": (
        "research_system.runtime.execution.core.support",
        "OrchestratorExecutionSupport",
    ),
    "OrchestratorExecutionTraceSupport": (
        "research_system.runtime.execution.core.trace_support",
        "OrchestratorExecutionTraceSupport",
    ),
    "OrchestratorPlanExecutor": (
        "research_system.runtime.execution.core.plan_executor",
        "OrchestratorPlanExecutor",
    ),
    "OrchestratorRuntimeControlPlaneController": (
        "research_system.runtime.execution.core.runtime_control_plane_controller",
        "OrchestratorRuntimeControlPlaneController",
    ),
    "OrchestratorRuntimeProtocolController": (
        "research_system.runtime.execution.core.runtime_protocol_controller",
        "OrchestratorRuntimeProtocolController",
    ),
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
