from __future__ import annotations

from research_system.runtime.execution.core.action_runner import OrchestratorActionRunner
from research_system.runtime.orchestration.orchestrator_admin import OrchestratorAdminService
from research_system.runtime.orchestration.orchestrator_compat_support import OrchestratorCompatSupport
from research_system.runtime.execution.core.plan_executor import OrchestratorPlanExecutor
from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_run_coordinator import OrchestratorRunCoordinator
from research_system.runtime.orchestration.orchestrator_research_forwarding_support import (
    OrchestratorResearchForwardingSupport,
)
from research_system.runtime.orchestration.orchestrator_research_support import OrchestratorResearchSupport
from research_system.runtime.orchestration.orchestrator_runtime_io_support import (
    OrchestratorRuntimeIoSupport,
)
from research_system.runtime.orchestration.orchestrator_subagent_bridge import OrchestratorSubagentBridge
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.planning import ActionPlan


class OrchestratorAssemblySupport:
    def __init__(self, host) -> None:
        self.host = host

    def build_admin_service(self) -> OrchestratorAdminService:
        run_store = getattr(self.host, "run_store", None)
        if run_store is None:
            run_store = RunStore(self.host.settings.runs_root)
        retention = getattr(self.host, "retention", None)
        if retention is None:
            retention = RunRetentionService(self.host.settings)
        return OrchestratorAdminService(
            settings=self.host.settings,
            run_store=run_store,
            retention=retention,
            retriever=self.host.retriever,
            skill_runtime=getattr(self.host, "skill_runtime", None),
            proposal_store=getattr(self.host, "proposal_store", None),
            subagent_discovery_store=getattr(self.host, "subagent_discovery_store", None),
            research_support=getattr(self.host, "research_support", None),
            orchestrator_error_cls=self.host.orchestrator_error_cls,
        )

    def admin(self) -> OrchestratorAdminService:
        admin = getattr(self.host, "admin", None)
        if admin is None:
            admin = self.build_admin_service()
            self.host.admin = admin
        return admin

    def build_research_support(self) -> OrchestratorResearchSupport:
        return OrchestratorResearchSupport(
            run_store=self.host.run_store,
            comps_service=self.host.comps_service,
            dcf_service=self.host.dcf_service,
            cashflow_resilience_service=self.host.cashflow_resilience_service,
            proposal_store=self.host.proposal_store,
            methodology_evolver=self.host.methodology_evolver,
            proposal_generator=self.host.proposal_generator,
            methodology_manifest_service=self.host.methodology_manifest_service,
            retrieval_policy_service=self.host.retrieval_policy_service,
            memory_service=self.host.memory_service,
            methodology_memory_writer=self.host.methodology_memory_writer,
            orchestrator_error_cls=self.host.orchestrator_error_cls,
        )

    def research_support(self) -> OrchestratorResearchSupport:
        support = getattr(self.host, "research_support", None)
        if support is None:
            support = self.build_research_support()
            self.host.research_support = support
        return support

    def build_execution_support(self) -> OrchestratorExecutionSupport:
        return OrchestratorExecutionSupport(self.host)

    def execution_support(self) -> OrchestratorExecutionSupport:
        support = getattr(self.host, "execution_support", None)
        if support is None:
            support = self.build_execution_support()
            self.host.execution_support = support
        return support

    def build_action_runner(self) -> OrchestratorActionRunner:
        return OrchestratorActionRunner(self.host, self.execution_support())

    def action_runner(self) -> OrchestratorActionRunner:
        runner = getattr(self.host, "action_runner", None)
        if runner is None:
            runner = self.build_action_runner()
            self.host.action_runner = runner
        return runner

    def build_plan_executor(self) -> OrchestratorPlanExecutor:
        return OrchestratorPlanExecutor(self.host)

    def plan_executor(self) -> OrchestratorPlanExecutor:
        executor = getattr(self.host, "plan_executor", None)
        if executor is None:
            executor = self.build_plan_executor()
            self.host.plan_executor = executor
        return executor

    def build_subagent_bridge(self) -> OrchestratorSubagentBridge:
        return OrchestratorSubagentBridge(self.host)

    def subagent_bridge(self) -> OrchestratorSubagentBridge:
        bridge = getattr(self.host, "subagent_bridge", None)
        if bridge is None:
            bridge = self.build_subagent_bridge()
            self.host.subagent_bridge = bridge
        return bridge

    def build_run_coordinator(self) -> OrchestratorRunCoordinator:
        return OrchestratorRunCoordinator(self.host)

    def run_coordinator(self) -> OrchestratorRunCoordinator:
        coordinator = getattr(self.host, "run_coordinator", None)
        if coordinator is None:
            coordinator = self.build_run_coordinator()
            self.host.run_coordinator = coordinator
        return coordinator

    def build_runtime_io_support(self) -> OrchestratorRuntimeIoSupport:
        return OrchestratorRuntimeIoSupport(self.host)

    def runtime_io(self) -> OrchestratorRuntimeIoSupport:
        runtime_io = getattr(self.host, "runtime_io", None)
        if runtime_io is None:
            runtime_io = self.build_runtime_io_support()
            self.host.runtime_io = runtime_io
        return runtime_io

    def build_research_forwarding_support(self) -> OrchestratorResearchForwardingSupport:
        return OrchestratorResearchForwardingSupport(self.host)

    def research_forwarding(self) -> OrchestratorResearchForwardingSupport:
        forwarding = getattr(self.host, "research_forwarding", None)
        if forwarding is None:
            forwarding = self.build_research_forwarding_support()
            self.host.research_forwarding = forwarding
        return forwarding

    def build_compat_support(self) -> OrchestratorCompatSupport:
        return OrchestratorCompatSupport(self.host)

    def compat(self) -> OrchestratorCompatSupport:
        compat = getattr(self.host, "compat", None)
        if compat is None:
            compat = self.build_compat_support()
            self.host.compat = compat
        return compat

    @staticmethod
    def plan_from_payload(payload: dict) -> ActionPlan:
        return ActionPlan.model_validate(payload)
