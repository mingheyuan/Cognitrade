from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.runtime_control_protocol_controller import (
    RuntimeControlProtocolResolution,
)
from research_system.runtime.execution.core.runtime_action_control_service import (
    OrchestratorRuntimeActionControlService,
)
from research_system.runtime.execution.core.runtime_protocol_controller import (
    OrchestratorRuntimeProtocolController,
)
from research_system.runtime.execution.core.runtime_service_binding_controller import (
    OrchestratorRuntimeServiceBindingController,
)
from research_system.runtime.execution.core.runtime_subagent_control_service import (
    OrchestratorRuntimeSubagentControlService,
)
from research_system.schemas.planning import ActionPlan, ActionSpec


class OrchestratorRuntimeControlPlaneController:
    def __init__(
        self,
        host: Any,
        *,
        supervisor,
    ) -> None:
        self.host = host
        self.supervisor = supervisor
        self.protocol_controller = OrchestratorRuntimeProtocolController(
            host,
            supervisor=supervisor,
        )
        self.transition_decision_source = self.protocol_controller.transition_decision_source
        self.protocol_decision_source = self.protocol_controller.decision_source
        self.subagent_controller = self.protocol_controller.subagent_controller
        self.subagent_transition_controller = self.protocol_controller.subagent_transition_controller
        self.subagent_protocol_controller = self.protocol_controller.subagent_protocol_controller
        self.action_runtime_control = OrchestratorRuntimeActionControlService(
            controller=self,
        )
        self.subagent_runtime_control = OrchestratorRuntimeSubagentControlService(
            controller=self,
        )
        self.service_binding_controller = OrchestratorRuntimeServiceBindingController(
            host=host,
            subagent_runtime_control=self.subagent_runtime_control,
        )

    def register_execution_callbacks(
        self,
        *,
        apply_plan_patch,
        request_action_plan_replan,
    ) -> None:
        self.action_runtime_control.register_execution_callbacks(
            apply_plan_patch=apply_plan_patch,
            request_action_plan_replan=request_action_plan_replan,
        )

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
        intent: str,
    ) -> None:
        bind_runtime = getattr(self.supervisor, "bind_runtime", None)
        if callable(bind_runtime):
            bind_runtime(
                context=context,
                support=support,
                request_id=request_id,
                intent=intent,
            )

    def clear_runtime(self) -> None:
        clear_runtime = getattr(self.supervisor, "clear_runtime", None)
        if callable(clear_runtime):
            clear_runtime()

    def bind_agent_service_runtime(
        self,
        *,
        service,
        context,
        support,
        request_id: str,
        intent: str,
        agent: str,
        phase: str,
    ) -> None:
        self.service_binding_controller.bind_agent_service_runtime(
            service=service,
            context=context,
            support=support,
            request_id=request_id,
            intent=intent,
            agent=agent,
            phase=phase,
        )

    def clear_agent_service_runtime(self, *, service) -> None:
        self.service_binding_controller.clear_agent_service_runtime(service=service)

    def resolve_supervisor_cycle(
        self,
        *,
        context,
        request,
        plan: ActionPlan,
        ready_actions: list[ActionSpec],
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
    ) -> RuntimeControlProtocolResolution:
        return self.action_runtime_control.resolve_supervisor_cycle(
            context=context,
            request=request,
            plan=plan,
            ready_actions=ready_actions,
            pending_actions=pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped_node_ids,
        )

    def handle_subagent_escalation(self, **kwargs) -> list[dict[str, Any]]:
        return self.action_runtime_control.handle_subagent_escalation(**kwargs)

    def resolve_subagent_capability_gap_escalation(self, **kwargs):
        return self.subagent_runtime_control.resolve_subagent_capability_gap_escalation(**kwargs)

    def resolve_subagent_catalog_entry(self, **kwargs):
        return self.subagent_runtime_control.resolve_subagent_catalog_entry(**kwargs)

    def select_subagent_catalog_entry(self, **kwargs):
        return self.subagent_runtime_control.select_subagent_catalog_entry(**kwargs)

    def execute_subagent_phase_patch_cycle(self, **kwargs):
        return self.subagent_runtime_control.execute_subagent_phase_patch_cycle(**kwargs)


__all__ = ["OrchestratorRuntimeControlPlaneController"]
