from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorAdminService",
    "OrchestratorAssemblySupport",
    "OrchestratorCompatSupport",
    "OrchestratorDebugSupport",
    "OrchestratorDependencies",
    "OrchestratorError",
    "OrchestratorMethodologyEvolutionInputSupport",
    "OrchestratorQdrantAdminSupport",
    "OrchestratorResearchForwardingSupport",
    "OrchestratorResearchSupport",
    "OrchestratorRunAdminSupport",
    "OrchestratorRunCoordinator",
    "OrchestratorRunFailureSupport",
    "OrchestratorRunGateSupport",
    "OrchestratorRunResponseSupport",
    "OrchestratorRunStartupSupport",
    "OrchestratorRunSuccessSupport",
    "OrchestratorRunTerminalSupport",
    "OrchestratorRuntimeIoSupport",
    "OrchestratorSubagentDiscoveryAdminSupport",
    "SubagentDiscoveryStore",
    "OrchestratorSubagentBridge",
    "ProgressService",
    "ResearchOrchestrator",
    "RunCancelledError",
    "RunTimedOutError",
    "RuntimeContext",
    "build_orchestrator_dependencies",
    "build_runtime_context",
]

_EXPORTS = {
    "OrchestratorAdminService": ("research_system.runtime.orchestration.orchestrator_admin", "OrchestratorAdminService"),
    "OrchestratorAssemblySupport": ("research_system.runtime.orchestration.orchestrator_assembly_support", "OrchestratorAssemblySupport"),
    "OrchestratorCompatSupport": ("research_system.runtime.orchestration.orchestrator_compat_support", "OrchestratorCompatSupport"),
    "OrchestratorDebugSupport": ("research_system.runtime.orchestration.orchestrator_research_debug", "OrchestratorDebugSupport"),
    "OrchestratorDependencies": ("research_system.runtime.orchestration.orchestrator_dependencies", "OrchestratorDependencies"),
    "OrchestratorError": ("research_system.runtime.orchestration.orchestrator", "OrchestratorError"),
    "OrchestratorMethodologyEvolutionInputSupport": (
        "research_system.runtime.orchestration.orchestrator_methodology_evolution_input_support",
        "OrchestratorMethodologyEvolutionInputSupport",
    ),
    "OrchestratorQdrantAdminSupport": ("research_system.runtime.orchestration.orchestrator_qdrant_admin_support", "OrchestratorQdrantAdminSupport"),
    "OrchestratorResearchForwardingSupport": (
        "research_system.runtime.orchestration.orchestrator_research_forwarding_support",
        "OrchestratorResearchForwardingSupport",
    ),
    "OrchestratorResearchSupport": ("research_system.runtime.orchestration.orchestrator_research_support", "OrchestratorResearchSupport"),
    "OrchestratorRunAdminSupport": ("research_system.runtime.orchestration.orchestrator_run_admin_support", "OrchestratorRunAdminSupport"),
    "OrchestratorRunCoordinator": ("research_system.runtime.orchestration.orchestrator_run_coordinator", "OrchestratorRunCoordinator"),
    "OrchestratorRunFailureSupport": (
        "research_system.runtime.orchestration.orchestrator_run_failure_support",
        "OrchestratorRunFailureSupport",
    ),
    "OrchestratorRunGateSupport": ("research_system.runtime.orchestration.orchestrator_run_gate_support", "OrchestratorRunGateSupport"),
    "OrchestratorRunResponseSupport": (
        "research_system.runtime.orchestration.orchestrator_run_response_support",
        "OrchestratorRunResponseSupport",
    ),
    "OrchestratorRunStartupSupport": (
        "research_system.runtime.orchestration.orchestrator_run_startup_support",
        "OrchestratorRunStartupSupport",
    ),
    "OrchestratorRunSuccessSupport": (
        "research_system.runtime.orchestration.orchestrator_run_success_support",
        "OrchestratorRunSuccessSupport",
    ),
    "OrchestratorRunTerminalSupport": (
        "research_system.runtime.orchestration.orchestrator_run_terminal_support",
        "OrchestratorRunTerminalSupport",
    ),
    "OrchestratorRuntimeIoSupport": ("research_system.runtime.orchestration.orchestrator_runtime_io_support", "OrchestratorRuntimeIoSupport"),
    "OrchestratorSubagentDiscoveryAdminSupport": (
        "research_system.runtime.orchestration.subagent_discovery_admin_support",
        "OrchestratorSubagentDiscoveryAdminSupport",
    ),
    "SubagentDiscoveryStore": ("research_system.runtime.orchestration.subagent_discovery_store", "SubagentDiscoveryStore"),
    "OrchestratorSubagentBridge": ("research_system.runtime.orchestration.orchestrator_subagent_bridge", "OrchestratorSubagentBridge"),
    "ProgressService": ("research_system.runtime.orchestration.progress_service", "ProgressService"),
    "ResearchOrchestrator": ("research_system.runtime.orchestration.orchestrator", "ResearchOrchestrator"),
    "RunCancelledError": ("research_system.runtime.orchestration.orchestrator", "RunCancelledError"),
    "RunTimedOutError": ("research_system.runtime.orchestration.orchestrator", "RunTimedOutError"),
    "RuntimeContext": ("research_system.runtime.orchestration.orchestrator_context", "RuntimeContext"),
    "build_orchestrator_dependencies": (
        "research_system.runtime.orchestration.orchestrator_dependencies",
        "build_orchestrator_dependencies",
    ),
    "build_runtime_context": ("research_system.runtime.orchestration.orchestrator_context", "build_runtime_context"),
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
