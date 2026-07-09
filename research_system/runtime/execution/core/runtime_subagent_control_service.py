from __future__ import annotations


class OrchestratorRuntimeSubagentControlService:
    def __init__(self, *, controller) -> None:
        self.controller = controller

    def resolve_subagent_capability_gap_escalation(self, **kwargs):
        return self.controller.protocol_controller.resolve_subagent_capability_gap_escalation(
            **kwargs,
        )

    def resolve_subagent_catalog_entry(self, **kwargs):
        return self.controller.protocol_controller.resolve_subagent_catalog_entry(**kwargs)

    def select_subagent_catalog_entry(self, **kwargs):
        return self.controller.protocol_controller.select_subagent_catalog_entry(**kwargs)

    def execute_subagent_phase_patch_cycle(self, **kwargs):
        return self.controller.protocol_controller.execute_subagent_phase_patch_cycle(**kwargs)


__all__ = ["OrchestratorRuntimeSubagentControlService"]
