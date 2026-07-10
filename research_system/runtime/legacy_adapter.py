from __future__ import annotations

from typing import Any

class LegacyRuntimeAdapter:
    """Owns the compatibility-only Supervisor and ActionPlan execution path."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def activate(self) -> None:
        from research_system.runtime.supervision.supervisor import RuntimeSupervisor

        if self.host.supervisor is not None:
            self.host.legacy_runtime_used = True
            return
        self.host.supervisor = RuntimeSupervisor(
            llm_provider=self.host.llm_provider,
            settings=self.host.settings,
        )
        self.host.action_runner = self.host._build_action_runner()
        self.host.runtime_control = self.host._build_runtime_control()
        self.host.runtime_orchestration = self.host._build_runtime_orchestration()
        self.host.plan_executor = self.host._build_plan_executor()
        self.host._attach_subagent_observers()
        self.host.legacy_runtime_used = True


__all__ = ["LegacyRuntimeAdapter"]
