from __future__ import annotations

from typing import Any, Iterable

from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation
from research_system.subagents.base import SupportsSubagent


class SubagentRegistry:
    def __init__(self, subagents: Iterable[SupportsSubagent] | None = None) -> None:
        self._subagents: dict[str, SupportsSubagent] = {}
        for subagent in subagents or []:
            self.register(subagent)

    def register(self, subagent: SupportsSubagent) -> None:
        self._subagents[subagent.subagent_type] = subagent

    def invoke(self, invocation: SubagentInvocation) -> tuple[dict[str, Any], SubagentExecutionRecord]:
        if invocation.subagent_type not in self._subagents:
            raise KeyError(f"unknown subagent: {invocation.subagent_type}")

        subagent = self._subagents[invocation.subagent_type]
        outputs = subagent.run(**invocation.inputs)
        notes = []
        if "notes" in outputs and isinstance(outputs["notes"], list):
            notes = [str(item) for item in outputs["notes"]]
        record = SubagentExecutionRecord(
            subagent_type=invocation.subagent_type,
            objective=invocation.objective,
            inputs=invocation.inputs,
            output_keys=sorted(outputs.keys()),
            notes=notes,
        )
        return outputs, record
