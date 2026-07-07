from __future__ import annotations

from collections.abc import Callable
from typing import Iterable

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

    def get(self, subagent_type: str) -> SupportsSubagent:
        try:
            return self._subagents[subagent_type]
        except KeyError as exc:
            raise KeyError(f"unknown subagent: {subagent_type}") from exc

    def notify_started(self, *, invocation: SubagentInvocation) -> None:
        self._notify(
            stage="started",
            invocation=invocation,
            record=None,
            outputs=None,
            error=None,
        )

    def notify_completed(
        self,
        *,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord,
        outputs: dict[str, Any],
    ) -> None:
        self._notify(
            stage="completed",
            invocation=invocation,
            record=record,
            outputs=outputs,
            error=None,
        )

    def notify_failed(
        self,
        *,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord,
        error: Exception,
    ) -> None:
        self._notify(
            stage="failed",
            invocation=invocation,
            record=record,
            outputs=None,
            error=error,
        )

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
