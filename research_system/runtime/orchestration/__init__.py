from __future__ import annotations

from importlib import import_module

__all__ = [
    "OrchestratorAdminService",
    "OrchestratorAssemblySupport",
    "OrchestratorCompatSupport",
    "OrchestratorDependencies",
    "OrchestratorError",
    "OrchestratorQdrantAdminSupport",
    "OrchestratorResearchForwardingSupport",
    "OrchestratorResearchSupport",
    "OrchestratorRunAdminSupport",
    "OrchestratorRunCoordinator",
    "OrchestratorRunFailureSupport",
    "OrchestratorRunResponseSupport",
    "OrchestratorRunStartupSupport",
    "OrchestratorRunSuccessSupport",
    "OrchestratorRunTerminalSupport",
    "OrchestratorRuntimeIoSupport",
    "OrchestratorSubagentDiscoveryAdminSupport",
    "SubagentDiscoveryApprovalPolicyService",
    "SubagentDiscoveryActivationPolicyService",
    "SubagentDiscoveryCandidateSupport",
    "SubagentRuntimeMaterializationSupport",
    "SubagentRuntimePackagingSupport",
    "SubagentRuntimeRegistrationBuilderSupport",
    "SubagentRuntimeTemplateSupport",
    "SubagentRuntimeSourceResolutionSupport",
    "SubagentRuntimeValidationSupport",
    "SubagentDiscoveryStore",
    "RuntimeRoleExecutorAdapter",
    "RuntimeRoleOverlay",
    "SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES",
    "SUPPORTED_RUNTIME_MATERIALIZATION_TYPES",
    "SUPPORTED_RUNTIME_REGISTRATION_TYPES",
    "SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES",
    "SUPPORTED_RUNTIME_TEMPLATE_TYPES",
    "SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES",
    "SUPPORTED_RUNTIME_VALIDATION_TYPES",
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
    "OrchestratorDependencies": ("research_system.runtime.orchestration.orchestrator_dependencies", "OrchestratorDependencies"),
    "OrchestratorError": ("research_system.runtime.orchestration.orchestrator", "OrchestratorError"),
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
    "SubagentDiscoveryApprovalPolicyService": (
        "research_system.runtime.orchestration._subagent_discovery_approval_policy",
        "SubagentDiscoveryApprovalPolicyService",
    ),
    "SubagentDiscoveryActivationPolicyService": (
        "research_system.runtime.orchestration._subagent_discovery_activation_policy",
        "SubagentDiscoveryActivationPolicyService",
    ),
    "SubagentDiscoveryCandidateSupport": (
        "research_system.runtime.orchestration.subagent_discovery_candidate_sources",
        "SubagentDiscoveryCandidateSupport",
    ),
    "SubagentRuntimeMaterializationSupport": (
        "research_system.runtime.orchestration.subagent_runtime_materialization",
        "SubagentRuntimeMaterializationSupport",
    ),
    "SubagentRuntimePackagingSupport": (
        "research_system.runtime.orchestration.subagent_runtime_packaging",
        "SubagentRuntimePackagingSupport",
    ),
    "SubagentRuntimeRegistrationBuilderSupport": (
        "research_system.runtime.orchestration.subagent_runtime_registration_builder",
        "SubagentRuntimeRegistrationBuilderSupport",
    ),
    "SubagentRuntimeTemplateSupport": (
        "research_system.runtime.orchestration.subagent_runtime_templates",
        "SubagentRuntimeTemplateSupport",
    ),
    "SubagentRuntimeSourceResolutionSupport": (
        "research_system.runtime.orchestration.subagent_runtime_source_resolution",
        "SubagentRuntimeSourceResolutionSupport",
    ),
    "SubagentRuntimeValidationSupport": (
        "research_system.runtime.orchestration.subagent_runtime_validation",
        "SubagentRuntimeValidationSupport",
    ),
    "SubagentDiscoveryStore": ("research_system.runtime.orchestration.subagent_discovery_store", "SubagentDiscoveryStore"),
    "RuntimeRoleExecutorAdapter": (
        "research_system.runtime.orchestration.runtime_role_overlay",
        "RuntimeRoleExecutorAdapter",
    ),
    "RuntimeRoleOverlay": (
        "research_system.runtime.orchestration.runtime_role_overlay",
        "RuntimeRoleOverlay",
    ),
    "SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES": (
        "research_system.runtime.orchestration.subagent_discovery_candidate_sources",
        "SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES",
    ),
    "SUPPORTED_RUNTIME_REGISTRATION_TYPES": (
        "research_system.runtime.orchestration.subagent_runtime_packaging",
        "SUPPORTED_RUNTIME_REGISTRATION_TYPES",
    ),
    "SUPPORTED_RUNTIME_MATERIALIZATION_TYPES": (
        "research_system.runtime.orchestration.subagent_runtime_materialization",
        "SUPPORTED_RUNTIME_MATERIALIZATION_TYPES",
    ),
    "SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES": (
        "research_system.runtime.orchestration.subagent_runtime_registration_builder",
        "SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES",
    ),
    "SUPPORTED_RUNTIME_TEMPLATE_TYPES": (
        "research_system.runtime.orchestration.subagent_runtime_templates",
        "SUPPORTED_RUNTIME_TEMPLATE_TYPES",
    ),
    "SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES": (
        "research_system.runtime.orchestration.subagent_runtime_source_resolution",
        "SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES",
    ),
    "SUPPORTED_RUNTIME_VALIDATION_TYPES": (
        "research_system.runtime.orchestration.subagent_runtime_validation",
        "SUPPORTED_RUNTIME_VALIDATION_TYPES",
    ),
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
