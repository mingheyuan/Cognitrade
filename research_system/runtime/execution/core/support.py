from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.research_facade import (
    OrchestratorExecutionResearchFacade,
)
from research_system.runtime.execution.core.runtime_support import (
    OrchestratorExecutionRuntimeSupport,
)


class OrchestratorExecutionSupport:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.runtime_support = OrchestratorExecutionRuntimeSupport(host)
        self.research_facade = OrchestratorExecutionResearchFacade(host)

    def _research_support(self):
        return self.research_facade._research_support()

    def begin_action(self, **kwargs) -> None:
        self.runtime_support.begin_action(**kwargs)

    def complete_action(self, **kwargs) -> None:
        self.runtime_support.complete_action(**kwargs)

    def fail_action(self, **kwargs) -> None:
        self.runtime_support.fail_action(**kwargs)

    def add_entry(self, *args, **kwargs) -> None:
        self.runtime_support.add_entry(*args, **kwargs)

    def emit_event(self, *args, **kwargs) -> None:
        self.runtime_support.emit_event(*args, **kwargs)

    def transition(self, *args, **kwargs) -> None:
        self.runtime_support.transition(*args, **kwargs)

    def update_state(self, *args, **kwargs) -> None:
        self.runtime_support.update_state(*args, **kwargs)

    def extend_subagent_trace(self, *args, **kwargs) -> None:
        self.runtime_support.extend_subagent_trace(*args, **kwargs)

    def trace_task_name(self, payload: dict) -> str:
        return self.runtime_support.trace_task_name(payload)

    def trace_agent_path(self, payload: dict) -> str:
        return self.runtime_support.trace_agent_path(payload)

    def trace_agent_label(self, payload: dict) -> str:
        return self.runtime_support.trace_agent_label(payload)

    def enforce_runtime_controls(self, *args, **kwargs) -> None:
        self.runtime_support.enforce_runtime_controls(*args, **kwargs)

    def wait_for_external_dependency(self, *args, **kwargs) -> None:
        self.runtime_support.wait_for_external_dependency(*args, **kwargs)

    def reserve_llm_slot(self, *args, **kwargs) -> bool:
        return self.runtime_support.reserve_llm_slot(*args, **kwargs)

    def resolve_llm_execution(self, *args, **kwargs):
        return self.runtime_support.resolve_llm_execution(*args, **kwargs)

    def record_degradation(self, *args, **kwargs) -> None:
        self.runtime_support.record_degradation(*args, **kwargs)

    def record_payload_degradation(self, *args, **kwargs) -> bool:
        return self.runtime_support.record_payload_degradation(*args, **kwargs)

    def run_methodology_evolution_collect(self, **kwargs):
        return self.research_facade.run_methodology_evolution_collect(**kwargs)

    def build_candidate_methodology_entries(self, **kwargs):
        return self.research_facade.build_candidate_methodology_entries(**kwargs)

    def build_stock_methodology_entries(self, **kwargs):
        return self.research_facade.build_stock_methodology_entries(**kwargs)

    def build_review_methodology_entries(self, **kwargs):
        return self.research_facade.build_review_methodology_entries(**kwargs)

    def build_debug_payload(self, **kwargs) -> dict:
        return self.research_facade.build_debug_payload(**kwargs)
