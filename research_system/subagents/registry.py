from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterable

from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation
from research_system.subagents.base import SupportsSubagent


class SubagentRegistry:
    def __init__(self, subagents: Iterable[SupportsSubagent] | None = None) -> None:
        self._subagents: dict[str, SupportsSubagent] = {}
        self._observers: list[Callable[..., None]] = []
        for subagent in subagents or []:
            self.register(subagent)

    def register(self, subagent: SupportsSubagent) -> None:
        self._subagents[subagent.subagent_type] = subagent

    def add_observer(self, observer: Callable[..., None]) -> None:
        self._observers.append(observer)

    def invoke(self, invocation: SubagentInvocation) -> tuple[dict[str, Any], SubagentExecutionRecord]:
        if invocation.subagent_type not in self._subagents:
            raise KeyError(f"unknown subagent: {invocation.subagent_type}")

        subagent = self._subagents[invocation.subagent_type]
        self._notify(
            stage="started",
            invocation=invocation,
            record=None,
            outputs=None,
            error=None,
        )
        try:
            outputs = subagent.run(**invocation.inputs)
        except Exception as exc:
            record = SubagentExecutionRecord(
                subagent_type=invocation.subagent_type,
                status="failed",
                objective=invocation.objective,
                inputs=invocation.inputs,
                output_keys=[],
                notes=[f"{exc.__class__.__name__}: {exc}"],
            )
            self._notify(
                stage="failed",
                invocation=invocation,
                record=record,
                outputs=None,
                error=exc,
            )
            raise
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
        self._notify(
            stage="completed",
            invocation=invocation,
            record=record,
            outputs=outputs,
            error=None,
        )
        return outputs, record

    def _notify(
        self,
        *,
        stage: str,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord | None,
        outputs: dict[str, Any] | None,
        error: Exception | None,
    ) -> None:
        for observer in self._observers:
            try:
                observer(
                    stage=stage,
                    invocation=invocation,
                    record=record,
                    outputs=outputs,
                    error=error,
                )
            except Exception:
                continue
