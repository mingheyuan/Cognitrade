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
    "OrchestratorRuntimeActionControlService",
    "OrchestratorRuntimeBindingController",
    "OrchestratorRuntimeControlPlaneController",
    "OrchestratorRuntimeOrchestrationController",
    "OrchestratorRuntimeProtocolController",
    "OrchestratorRuntimeServiceBindingController",
    "OrchestratorRuntimeSubagentControlService",
    "core",
    "handlers",
]

_EXPORTS = {
    "OrchestratorActionRunner": ("research_system.runtime.execution.core.action_runner", "OrchestratorActionRunner"),
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
    "OrchestratorExecutionSupport": ("research_system.runtime.execution.core.support", "OrchestratorExecutionSupport"),
    "OrchestratorExecutionTraceSupport": (
        "research_system.runtime.execution.core.trace_support",
        "OrchestratorExecutionTraceSupport",
    ),
    "OrchestratorPlanExecutor": ("research_system.runtime.execution.core.plan_executor", "OrchestratorPlanExecutor"),
    "OrchestratorRuntimeActionControlService": (
        "research_system.runtime.execution.core.runtime_action_control_service",
        "OrchestratorRuntimeActionControlService",
    ),
    "OrchestratorRuntimeBindingController": (
        "research_system.runtime.execution.core.runtime_binding_controller",
        "OrchestratorRuntimeBindingController",
    ),
    "OrchestratorRuntimeControlPlaneController": (
        "research_system.runtime.execution.core.runtime_control_plane_controller",
        "OrchestratorRuntimeControlPlaneController",
    ),
    "OrchestratorRuntimeOrchestrationController": (
        "research_system.runtime.execution.core.runtime_orchestration_controller",
        "OrchestratorRuntimeOrchestrationController",
    ),
    "OrchestratorRuntimeProtocolController": (
        "research_system.runtime.execution.core.runtime_protocol_controller",
        "OrchestratorRuntimeProtocolController",
    ),
    "OrchestratorRuntimeServiceBindingController": (
        "research_system.runtime.execution.core.runtime_service_binding_controller",
        "OrchestratorRuntimeServiceBindingController",
    ),
    "OrchestratorRuntimeSubagentControlService": (
        "research_system.runtime.execution.core.runtime_subagent_control_service",
        "OrchestratorRuntimeSubagentControlService",
    ),
    "core": "research_system.runtime.execution.core",
    "handlers": "research_system.runtime.execution.handlers",
}


def __getattr__(name: str):
    try:
        target = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    if isinstance(target, str):
        value = import_module(target)
    else:
        module_name, attr_name = target
        value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
