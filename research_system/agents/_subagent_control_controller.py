from __future__ import annotations


class SubagentControlController:
    def __init__(
        self,
        *,
        catalog_controller,
        phase_execution_controller,
    ) -> None:
        self.catalog_controller = catalog_controller
        self.phase_execution_controller = phase_execution_controller

    def select_catalog_entry(self, **kwargs):
        return self.catalog_controller.select_catalog_entry(**kwargs)

    def resolve_catalog_entry(self, **kwargs):
        return self.catalog_controller.resolve_catalog_entry(**kwargs)

    def execute_patch_cycle(self, **kwargs):
        return self.phase_execution_controller.execute_patch_cycle(**kwargs)


__all__ = ["SubagentControlController"]
