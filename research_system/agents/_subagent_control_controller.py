from __future__ import annotations

from research_system.agents._subagent_runtime_control_gateway import (
    SubagentRuntimeControlGateway,
)


class SubagentControlController:
    def __init__(
        self,
        *,
        catalog_controller,
        phase_execution_controller,
        runtime_control_gateway=None,
        runtime_control_resolver=None,
        runtime_binding_getter=None,
    ) -> None:
        self.catalog_controller = catalog_controller
        self.phase_execution_controller = phase_execution_controller
        self.runtime_control_gateway = runtime_control_gateway or SubagentRuntimeControlGateway(
            runtime_control_resolver=runtime_control_resolver,
            runtime_binding_getter=runtime_binding_getter,
        )

    def select_catalog_entry(self, **kwargs):
        return self.runtime_control_gateway.select_catalog_entry(
            catalog_controller=self.catalog_controller,
            **kwargs,
        )

    def resolve_catalog_entry(self, **kwargs):
        return self.runtime_control_gateway.resolve_catalog_entry(
            catalog_controller=self.catalog_controller,
            **kwargs,
        )

    def execute_patch_cycle(self, **kwargs):
        return self.runtime_control_gateway.execute_patch_cycle(
            phase_execution_controller=self.phase_execution_controller,
            **kwargs,
        )


__all__ = ["SubagentControlController"]
