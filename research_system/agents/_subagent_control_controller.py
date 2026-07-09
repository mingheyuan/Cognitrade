from __future__ import annotations


class SubagentControlController:
    def __init__(
        self,
        *,
        catalog_controller,
        phase_execution_controller,
        runtime_control_resolver=None,
        runtime_binding_getter=None,
    ) -> None:
        self.catalog_controller = catalog_controller
        self.phase_execution_controller = phase_execution_controller
        self.runtime_control_resolver = runtime_control_resolver
        self.runtime_binding_getter = runtime_binding_getter

    def select_catalog_entry(self, **kwargs):
        runtime_control = self._runtime_control()
        select_catalog_entry = getattr(runtime_control, "select_subagent_catalog_entry", None)
        if callable(select_catalog_entry):
            return select_catalog_entry(
                catalog_controller=self.catalog_controller,
                runtime_binding=self._runtime_binding(),
                **kwargs,
            )
        return self.catalog_controller.select_catalog_entry(**kwargs)

    def resolve_catalog_entry(self, **kwargs):
        runtime_control = self._runtime_control()
        resolve_catalog_entry = getattr(runtime_control, "resolve_subagent_catalog_entry", None)
        if callable(resolve_catalog_entry):
            return resolve_catalog_entry(
                catalog_controller=self.catalog_controller,
                runtime_binding=self._runtime_binding(),
                **kwargs,
            )
        return self.catalog_controller.resolve_catalog_entry(**kwargs)

    def execute_patch_cycle(self, **kwargs):
        runtime_control = self._runtime_control()
        execute_patch_cycle = getattr(runtime_control, "execute_subagent_phase_patch_cycle", None)
        if callable(execute_patch_cycle):
            return execute_patch_cycle(
                phase_execution_controller=self.phase_execution_controller,
                runtime_binding=self._runtime_binding(),
                **kwargs,
            )
        return self.phase_execution_controller.execute_patch_cycle(**kwargs)

    def _runtime_control(self):
        if callable(self.runtime_control_resolver):
            try:
                return self.runtime_control_resolver()
            except Exception:
                return None
        return None

    def _runtime_binding(self) -> dict:
        if callable(self.runtime_binding_getter):
            try:
                return dict(self.runtime_binding_getter() or {})
            except Exception:
                return {}
        return {}


__all__ = ["SubagentControlController"]
