from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.runtime_control_controller import (
    OrchestratorRuntimeControlController,
)
from research_system.runtime.execution.core.runtime_control_protocol_controller import (
    OrchestratorRuntimeControlProtocolController,
    RuntimeControlProtocolResolution,
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
        self.transition_controller: OrchestratorRuntimeControlController | None = None
        self.protocol_controller: OrchestratorRuntimeControlProtocolController | None = None

    def register_execution_callbacks(
        self,
        *,
        apply_plan_patch,
        request_action_plan_replan,
    ) -> None:
        self.transition_controller = OrchestratorRuntimeControlController(
            self.host,
            apply_plan_patch=apply_plan_patch,
            request_action_plan_replan=request_action_plan_replan,
        )
        self.protocol_controller = OrchestratorRuntimeControlProtocolController(
            self.host,
            supervisor=self.supervisor,
            transition_controller=self.transition_controller,
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
        if self.protocol_controller is None:
            raise RuntimeError("runtime control execution callbacks not registered")
        return self.protocol_controller.resolve_supervisor_cycle(
            context=context,
            request=request,
            plan=plan,
            ready_actions=ready_actions,
            pending_actions=pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped_node_ids,
        )

    def handle_subagent_escalation(self, **kwargs) -> list[dict[str, Any]]:
        if self.protocol_controller is None:
            raise RuntimeError("runtime control execution callbacks not registered")
        return self.protocol_controller.handle_subagent_escalation(**kwargs)


__all__ = ["OrchestratorRuntimeControlPlaneController"]
