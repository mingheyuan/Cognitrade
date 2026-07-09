from __future__ import annotations

from typing import Any


class SubagentRuntimeControlGateway:
    def __init__(
        self,
        *,
        runtime_control_resolver=None,
        runtime_binding_getter=None,
    ) -> None:
        self.runtime_control_resolver = runtime_control_resolver
        self.runtime_binding_getter = runtime_binding_getter

    def select_catalog_entry(self, *, catalog_controller, **kwargs):
        runtime_control = self._runtime_control()
        select_catalog_entry = getattr(runtime_control, "select_subagent_catalog_entry", None)
        if callable(select_catalog_entry):
            return select_catalog_entry(
                catalog_controller=catalog_controller,
                runtime_binding=self.runtime_binding(),
                **kwargs,
            )
        return catalog_controller.select_catalog_entry(**kwargs)

    def resolve_catalog_entry(self, *, catalog_controller, **kwargs):
        runtime_control = self._runtime_control()
        resolve_catalog_entry = getattr(runtime_control, "resolve_subagent_catalog_entry", None)
        if callable(resolve_catalog_entry):
            return resolve_catalog_entry(
                catalog_controller=catalog_controller,
                runtime_binding=self.runtime_binding(),
                **kwargs,
            )
        return catalog_controller.resolve_catalog_entry(**kwargs)

    def execute_patch_cycle(self, *, phase_execution_controller, **kwargs):
        runtime_control = self._runtime_control()
        execute_patch_cycle = getattr(runtime_control, "execute_subagent_phase_patch_cycle", None)
        if callable(execute_patch_cycle):
            return execute_patch_cycle(
                phase_execution_controller=phase_execution_controller,
                runtime_binding=self.runtime_binding(),
                **kwargs,
            )
        return phase_execution_controller.execute_patch_cycle(**kwargs)

    def resolve_capability_gap_escalation(self, *, escalation_controller, **kwargs):
        runtime_control = self._runtime_control()
        resolve_capability_gap_escalation = getattr(
            runtime_control,
            "resolve_subagent_capability_gap_escalation",
            None,
        )
        if callable(resolve_capability_gap_escalation):
            return resolve_capability_gap_escalation(
                escalation_controller=escalation_controller,
                runtime_binding=self.runtime_binding(),
                **kwargs,
            )
        return escalation_controller.resolve_local(**kwargs)

    def runtime_binding(self) -> dict[str, Any]:
        if callable(self.runtime_binding_getter):
            try:
                return dict(self.runtime_binding_getter() or {})
            except Exception:
                return {}
        return {}

    def _runtime_control(self):
        if callable(self.runtime_control_resolver):
            try:
                return self.runtime_control_resolver()
            except Exception:
                return None
        binding = self.runtime_binding()
        return binding.get("runtime_control")


__all__ = ["SubagentRuntimeControlGateway"]
