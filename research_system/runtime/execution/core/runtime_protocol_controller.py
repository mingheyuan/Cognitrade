from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.runtime_control_controller import (
    OrchestratorRuntimeControlController,
)
from research_system.runtime.execution.core.runtime_control_protocol_controller import (
    OrchestratorRuntimeControlProtocolController,
    RuntimeControlProtocolResolution,
)
from research_system.runtime.execution.core.runtime_protocol_decision_source import (
    OrchestratorRuntimeProtocolDecisionSource,
)
from research_system.runtime.execution.core.runtime_subagent_control_controller import (
    OrchestratorRuntimeSubagentControlController,
)
from research_system.runtime.execution.core.runtime_subagent_control_protocol_controller import (
    OrchestratorRuntimeSubagentControlProtocolController,
)
from research_system.runtime.execution.core.runtime_subagent_transition_controller import (
    OrchestratorRuntimeSubagentTransitionController,
)
from research_system.runtime.execution.core.runtime_transition_decision_source import (
    OrchestratorRuntimeTransitionDecisionSource,
)
from research_system.schemas.planning import ActionPlan, ActionSpec


class OrchestratorRuntimeProtocolController:
    def __init__(
        self,
        host: Any,
        *,
        supervisor,
        transition_decision_source=None,
        decision_source=None,
        subagent_controller=None,
        subagent_transition_controller=None,
    ) -> None:
        self.host = host
        self.supervisor = supervisor
        self.transition_decision_source = (
            transition_decision_source or OrchestratorRuntimeTransitionDecisionSource()
        )
        self.decision_source = decision_source or OrchestratorRuntimeProtocolDecisionSource(
            transition_decision_source=self.transition_decision_source,
        )
        self.action_transition_controller: OrchestratorRuntimeControlController | None = None
        self.action_protocol_controller: OrchestratorRuntimeControlProtocolController | None = None
        self.subagent_controller = subagent_controller or OrchestratorRuntimeSubagentControlController(host)
        self.subagent_transition_controller = subagent_transition_controller or (
            OrchestratorRuntimeSubagentTransitionController(
                host,
                decision_source=self.transition_decision_source,
            )
        )
        self.subagent_protocol_controller = OrchestratorRuntimeSubagentControlProtocolController(
            host,
            subagent_controller=self.subagent_controller,
            transition_controller=self.subagent_transition_controller,
            decision_source=self.decision_source,
        )

    def register_execution_callbacks(
        self,
        *,
        apply_plan_patch,
        request_action_plan_replan,
    ) -> None:
        self.action_transition_controller = OrchestratorRuntimeControlController(
            self.host,
            apply_plan_patch=apply_plan_patch,
            request_action_plan_replan=request_action_plan_replan,
            decision_source=self.transition_decision_source,
        )
        self.action_protocol_controller = OrchestratorRuntimeControlProtocolController(
            self.host,
            supervisor=self.supervisor,
            transition_controller=self.action_transition_controller,
            decision_source=self.decision_source,
        )

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
        if self.action_protocol_controller is None:
            raise RuntimeError("runtime control execution callbacks not registered")
        return self.action_protocol_controller.resolve_supervisor_cycle(
            context=context,
            request=request,
            plan=plan,
            ready_actions=ready_actions,
            pending_actions=pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped_node_ids,
        )

    def handle_subagent_escalation(self, **kwargs) -> list[dict[str, Any]]:
        if self.action_protocol_controller is None:
            raise RuntimeError("runtime control execution callbacks not registered")
        return self.action_protocol_controller.handle_subagent_escalation(**kwargs)

    def resolve_subagent_capability_gap_escalation(self, **kwargs):
        return self.subagent_protocol_controller.resolve_capability_gap_escalation(**kwargs)

    def resolve_subagent_catalog_entry(self, **kwargs):
        return self.subagent_protocol_controller.resolve_catalog_entry(**kwargs)

    def select_subagent_catalog_entry(self, **kwargs):
        return self.subagent_protocol_controller.select_catalog_entry(**kwargs)

    def execute_subagent_phase_patch_cycle(self, **kwargs):
        return self.subagent_protocol_controller.execute_patch_cycle(**kwargs)


__all__ = ["OrchestratorRuntimeProtocolController"]
