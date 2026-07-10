from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.control_support import (
    OrchestratorExecutionControlSupport,
)
from research_system.runtime.execution.core.lifecycle_support import (
    OrchestratorExecutionLifecycleSupport,
)
from research_system.runtime.execution.core.trace_support import (
    OrchestratorExecutionTraceSupport,
)


class OrchestratorExecutionRuntimeSupport:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.lifecycle_support = OrchestratorExecutionLifecycleSupport(host)
        self.control_support = OrchestratorExecutionControlSupport(
            host,
            lifecycle_support=self.lifecycle_support,
        )
        self.trace_support = OrchestratorExecutionTraceSupport(
            lifecycle_support=self.lifecycle_support,
        )

    def emit_event(self, *args, **kwargs) -> None:
        self.lifecycle_support.emit_event(*args, **kwargs)

    def transition(self, *args, **kwargs) -> None:
        self.lifecycle_support.transition(*args, **kwargs)

    def update_state(self, *args, **kwargs) -> None:
        self.lifecycle_support.update_state(*args, **kwargs)

    def extend_subagent_trace(self, *args, **kwargs) -> None:
        self.trace_support.extend_subagent_trace(*args, **kwargs)

    def trace_task_name(self, payload: dict) -> str:
        return self.trace_support.trace_task_name(payload)

    def trace_agent_path(self, payload: dict) -> str:
        return self.trace_support.trace_agent_path(payload)

    def trace_agent_label(self, payload: dict) -> str:
        return self.trace_support.trace_agent_label(payload)

    def enforce_run_controls(self, *args, **kwargs) -> None:
        self.control_support.enforce_run_controls(*args, **kwargs)

    def wait_for_external_dependency(self, *args, **kwargs) -> None:
        self.control_support.wait_for_external_dependency(*args, **kwargs)

    def reserve_llm_slot(self, *args, **kwargs) -> bool:
        return self.control_support.reserve_llm_slot(*args, **kwargs)

    def resolve_llm_execution(self, *args, **kwargs):
        return self.control_support.resolve_llm_execution(*args, **kwargs)

    def record_degradation(self, *args, **kwargs) -> None:
        self.control_support.record_degradation(*args, **kwargs)

    def record_payload_degradation(self, *args, **kwargs) -> bool:
        return self.control_support.record_payload_degradation(*args, **kwargs)
