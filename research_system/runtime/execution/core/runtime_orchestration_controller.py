from __future__ import annotations


class OrchestratorRuntimeOrchestrationController:
    def __init__(self, *, control_plane) -> None:
        self.control_plane = control_plane
        self.host = control_plane.host
        self.protocol_controller = control_plane.protocol_controller
        self.transition_decision_source = control_plane.transition_decision_source
        self.protocol_decision_source = control_plane.protocol_decision_source
        self.subagent_controller = control_plane.subagent_controller
        self.subagent_transition_controller = control_plane.subagent_transition_controller
        self.subagent_protocol_controller = control_plane.subagent_protocol_controller
        self.runtime_binding_controller = control_plane.runtime_binding_controller
        self.action_runtime_control = control_plane.action_runtime_control
        self.subagent_runtime_control = control_plane.subagent_runtime_control
        self.service_binding_controller = control_plane.service_binding_controller

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
        intent: str,
    ) -> None:
        self.runtime_binding_controller.bind_runtime(
            context=context,
            support=support,
            request_id=request_id,
            intent=intent,
        )

    def clear_runtime(self) -> None:
        self.runtime_binding_controller.clear_runtime()

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

    def resolve_supervisor_cycle(self, **kwargs):
        return self.action_runtime_control.resolve_supervisor_cycle(**kwargs)

    def handle_subagent_escalation(self, **kwargs):
        return self.action_runtime_control.handle_subagent_escalation(**kwargs)

    def bind_agent_service_runtime(self, **kwargs) -> None:
        self.service_binding_controller.bind_agent_service_runtime(**kwargs)

    def clear_agent_service_runtime(self, **kwargs) -> None:
        self.service_binding_controller.clear_agent_service_runtime(**kwargs)

    def resolve_subagent_capability_gap_escalation(self, **kwargs):
        return self.subagent_runtime_control.resolve_subagent_capability_gap_escalation(**kwargs)

    def resolve_subagent_catalog_entry(self, **kwargs):
        return self.subagent_runtime_control.resolve_subagent_catalog_entry(**kwargs)

    def select_subagent_catalog_entry(self, **kwargs):
        return self.subagent_runtime_control.select_subagent_catalog_entry(**kwargs)

    def execute_subagent_phase_patch_cycle(self, **kwargs):
        return self.subagent_runtime_control.execute_subagent_phase_patch_cycle(**kwargs)


__all__ = ["OrchestratorRuntimeOrchestrationController"]
