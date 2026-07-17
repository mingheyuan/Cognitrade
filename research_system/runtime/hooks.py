from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.turn_runtime import HookPolicy


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PromptFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source: Literal["hook", "system"] = "hook"
    operation_id: str = Field(default_factory=generate_ulid)


class StopHookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str
    thread_id: str
    turn_id: str
    task_type: str
    target: Literal["stop", "subagent_stop"]
    cwd: str | None = None
    transcript_ref: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    stop_hook_active: bool = False
    last_assistant_message: str | None = None
    parent_turn_id: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    agent_transcript_ref: str | None = None
    hook_iteration: int = Field(default=0, ge=0)


class HookRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook_run_id: str = Field(default_factory=generate_ulid)
    hook_name: str
    hook_event: str
    source: str
    status: Literal["started", "completed", "blocked", "stopped", "failed", "timed_out"]
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    exit_code: int | None = None
    output_summary: str | None = None
    decision: str | None = None
    reason: str | None = None


class StopHookOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook_events: list[HookRunRecord] = Field(default_factory=list)
    should_stop: bool = False
    stop_reason: str | None = None
    should_block: bool = False
    block_reason: str | None = None
    continuation_fragments: list[PromptFragment] = Field(default_factory=list)


class LifecycleInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: Literal[
        "SessionStart",
        "UserPromptSubmit",
        "TurnStart",
        "TurnStop",
        "TurnAbort",
        "TurnError",
    ]
    run_id: str
    thread_id: str
    turn_id: str
    task_type: str
    status: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class HookDefinition:
    event: str
    name: str
    callback: Callable[[Any], Any]
    source: str = "runtime"
    failure_mode: Literal["fail_open", "fail_closed"] | None = None


class HookRuntime:
    """Governed, replayable hook dispatcher owned by the current Turn."""

    def __init__(
        self,
        *,
        policy: HookPolicy | None = None,
        emitter: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.policy = policy or HookPolicy()
        self._emitter = emitter
        self._hooks: list[HookDefinition] = []

    def register(
        self,
        event: str,
        callback: Callable[[Any], Any],
        *,
        name: str | None = None,
        source: str = "runtime",
        failure_mode: Literal["fail_open", "fail_closed"] | None = None,
    ) -> None:
        self._hooks.append(
            HookDefinition(
                event=event,
                name=name or getattr(callback, "__name__", "hook"),
                callback=callback,
                source=source,
                failure_mode=failure_mode,
            )
        )

    def clear(self, event: str | None = None) -> None:
        if event is None:
            self._hooks.clear()
        else:
            self._hooks = [item for item in self._hooks if item.event != event]

    def clone(
        self,
        *,
        policy: HookPolicy | None = None,
        emitter: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> "HookRuntime":
        runtime = HookRuntime(policy=policy or self.policy, emitter=emitter)
        runtime._hooks = list(self._hooks)
        return runtime

    def preview_stop(self, *, target: str = "stop") -> bool:
        return any(item.event == target for item in self._hooks)

    def run_stop(
        self,
        hook_input: StopHookInput,
        *,
        policy: HookPolicy | None = None,
    ) -> StopHookOutcome:
        effective_policy = policy or self.policy
        definitions = [item for item in self._hooks if item.event == hook_input.target]
        if not definitions:
            return StopHookOutcome(should_stop=True)

        records: list[HookRunRecord] = []
        block_reasons: list[str] = []
        fragments: list[PromptFragment] = []
        stop_reasons: list[str] = []
        should_stop = False
        for definition in definitions:
            started = HookRunRecord(
                hook_name=definition.name,
                hook_event=definition.event,
                source=definition.source,
                status="started",
            )
            records.append(started)
            self._emit(
                "hook_started",
                hook_input=hook_input,
                hook=started,
            )
            try:
                raw = definition.callback(hook_input)
                decision = self._normalize_decision(raw)
                if decision.should_stop:
                    should_stop = True
                    if decision.stop_reason:
                        stop_reasons.append(decision.stop_reason)
                if decision.should_block and decision.block_reason:
                    block_reasons.append(decision.block_reason)
                    fragments.extend(decision.continuation_fragments)
                elif decision.should_block:
                    block_reasons.append(
                        f"hook {definition.name} returned a block without a reason"
                    )
                record_status = "blocked" if decision.should_block else "stopped" if decision.should_stop else "completed"
                completed = started.model_copy(
                    update={
                        "status": record_status,
                        "completed_at": _utc_now(),
                        "decision": "block" if decision.should_block else "stop" if decision.should_stop else "continue",
                        "reason": decision.block_reason or decision.stop_reason,
                        "output_summary": self._summary(decision),
                    }
                )
            except Exception as exc:
                fail_closed = (
                    definition.failure_mode == "fail_closed"
                    if definition.failure_mode is not None
                    else effective_policy.failure_mode == "fail_closed"
                )
                reason = f"hook {definition.name} failed: {exc.__class__.__name__}: {exc}"
                if fail_closed:
                    block_reasons.append(reason)
                else:
                    should_stop = True
                completed = started.model_copy(
                    update={
                        "status": "failed",
                        "completed_at": _utc_now(),
                        "decision": "block" if fail_closed else "stop",
                        "reason": reason,
                    }
                )
            records[-1] = completed
            self._emit(
                "hook_completed",
                hook_input=hook_input,
                hook=completed,
            )

        # An explicit stop wins over a block from another hook in the same batch.
        if should_stop:
            return StopHookOutcome(
                hook_events=records,
                should_stop=True,
                stop_reason="; ".join(stop_reasons) or "hook_allowed_stop",
            )
        if block_reasons:
            return StopHookOutcome(
                hook_events=records,
                should_stop=False,
                should_block=True,
                block_reason="; ".join(block_reasons),
                continuation_fragments=fragments,
            )
        return StopHookOutcome(hook_events=records, should_stop=True)

    def run_lifecycle(self, input_value: LifecycleInput) -> list[HookRunRecord]:
        records: list[HookRunRecord] = []
        for definition in [item for item in self._hooks if item.event == input_value.event]:
            started = HookRunRecord(
                hook_name=definition.name,
                hook_event=definition.event,
                source=definition.source,
                status="started",
            )
            records.append(started)
            self._emit("hook_started", hook_input=input_value, hook=started)
            try:
                definition.callback(input_value)
                completed = started.model_copy(
                    update={"status": "completed", "completed_at": _utc_now()}
                )
            except Exception as exc:
                completed = started.model_copy(
                    update={
                        "status": "failed",
                        "completed_at": _utc_now(),
                        "reason": f"{exc.__class__.__name__}: {exc}",
                    }
                )
            records[-1] = completed
            self._emit("hook_completed", hook_input=input_value, hook=completed)
        return records

    def run_event(self, event: str, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Run non-stop hooks and return (allowed, possibly rewritten payload)."""
        allowed = True
        current = dict(payload)
        for definition in [item for item in self._hooks if item.event == event]:
            started = HookRunRecord(
                hook_name=definition.name,
                hook_event=event,
                source=definition.source,
                status="started",
            )
            self._emit("hook_started", hook_input=payload, hook=started)
            hook_allowed = True
            hook_reason: str | None = None
            try:
                raw = definition.callback(current)
                if isinstance(raw, dict):
                    if raw.get("allowed") is False or raw.get("should_block") is True:
                        allowed = False
                        hook_allowed = False
                        hook_reason = str(
                            raw.get("reason") or raw.get("block_reason") or ""
                        ).strip() or None
                    updates = raw.get("arguments") if event == "PreToolUse" else raw.get("payload")
                    if updates is None and event == "UserPromptSubmit":
                        updates = raw.get("request")
                    if isinstance(updates, dict):
                        if event == "PreToolUse":
                            current["arguments"] = updates
                        else:
                            current.update(updates)
                    for key in ("user_query", "text"):
                        if key in raw:
                            current[key] = raw[key]
                elif raw is False:
                    allowed = False
                    hook_allowed = False
                    hook_reason = "hook returned false"
            except Exception as exc:
                hook_reason = f"hook {definition.name} failed: {exc.__class__.__name__}: {exc}"
                if (definition.failure_mode or self.policy.failure_mode) == "fail_closed":
                    allowed = False
                    hook_allowed = False
            self._emit(
                "hook_completed",
                hook_input=payload,
                hook=HookRunRecord(
                    hook_run_id=started.hook_run_id,
                    hook_name=definition.name,
                    hook_event=event,
                    source=definition.source,
                    status="completed" if hook_allowed else "blocked",
                    decision="allow" if hook_allowed else "block",
                    reason=hook_reason,
                ),
            )
            if hook_reason and not current.get("_hook_block_reason"):
                current["_hook_block_reason"] = hook_reason
        return allowed, current

    @staticmethod
    def _normalize_decision(value: Any) -> StopHookOutcome:
        if value is None:
            return StopHookOutcome(should_stop=True)
        if isinstance(value, StopHookOutcome):
            return value
        if isinstance(value, bool):
            return StopHookOutcome(should_stop=value, should_block=not value)
        if isinstance(value, str):
            return StopHookOutcome(
                should_stop=False,
                should_block=True,
                block_reason=value.strip() or "hook requested continuation",
                continuation_fragments=[PromptFragment(text=value.strip())] if value.strip() else [],
            )
        if isinstance(value, dict):
            payload = dict(value)
            fragments = payload.get("continuation_fragments") or []
            payload["continuation_fragments"] = [
                item if isinstance(item, PromptFragment) else PromptFragment.model_validate(item)
                for item in fragments
            ]
            return StopHookOutcome.model_validate(payload)
        raise TypeError(f"unsupported stop hook result: {type(value).__name__}")

    @staticmethod
    def _summary(outcome: StopHookOutcome) -> str:
        if outcome.block_reason:
            return outcome.block_reason[:500]
        if outcome.stop_reason:
            return outcome.stop_reason[:500]
        return "hook completed"

    def _emit(self, event_type: str, *, hook_input: Any, hook: HookRunRecord) -> None:
        if self._emitter is None:
            return
        payload = {
            "hook_run_id": hook.hook_run_id,
            "hook_name": hook.hook_name,
            "hook_event": hook.hook_event,
            "source": hook.source,
            "status": hook.status,
            "decision": hook.decision,
            "reason": hook.reason,
            "run_id": getattr(hook_input, "run_id", None),
            "thread_id": getattr(hook_input, "thread_id", None),
            "turn_id": getattr(hook_input, "turn_id", None),
        }
        self._emitter(event_type, payload)


__all__ = [
    "HookDefinition",
    "HookRunRecord",
    "HookRuntime",
    "LifecycleInput",
    "PromptFragment",
    "StopHookInput",
    "StopHookOutcome",
]
