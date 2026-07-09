from __future__ import annotations


class OrchestratorRuntimeActionControlService:
    def __init__(self, *, controller) -> None:
        self.controller = controller

    def register_execution_callbacks(
        self,
        *,
        apply_plan_patch,
        request_action_plan_replan,
    ) -> None:
        self.controller.protocol_controller.register_execution_callbacks(
            apply_plan_patch=apply_plan_patch,
            request_action_plan_replan=request_action_plan_replan,
        )

    def resolve_supervisor_cycle(self, **kwargs):
        return self.controller.protocol_controller.resolve_supervisor_cycle(**kwargs)

    def handle_subagent_escalation(self, **kwargs):
        return self.controller.protocol_controller.handle_subagent_escalation(**kwargs)


__all__ = ["OrchestratorRuntimeActionControlService"]
