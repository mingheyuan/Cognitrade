from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_system.runtime.execution.core.runtime_support import OrchestratorExecutionRuntimeSupport
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext, build_runtime_context
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.runtime.storage.jsonl import write_json
from research_system.runtime.storage.runs.run_store import RunPaths, RunStore
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.routing.router.task_router import ChatDispatchDecision

_CHAT_AGENT = "chat"
_CHAT_RUNS_DIRNAME = "chat_runs"


class ChatLLMRuntimeError(Exception):
    pass


class ChatLLMRunCancelledError(ChatLLMRuntimeError):
    pass


class ChatLLMRunTimedOutError(ChatLLMRuntimeError):
    pass


@dataclass
class _ChatLLMRuntimeHost:
    event_listener: object = None
    run_cancelled_error_cls: type[Exception] = ChatLLMRunCancelledError
    run_timed_out_error_cls: type[Exception] = ChatLLMRunTimedOutError


@dataclass
class _ChatItemObserver:
    runtime: "ChatLLMRun"

    def __call__(self, *, stage: str, item, payload: dict[str, Any]) -> None:
        if item.item_type not in {"agent", "subagent", "thread", "degradation"}:
            return
        event_type = self._event_type(item_type=item.item_type, stage=stage)
        self.runtime.support.emit_event(
            self.runtime.context,
            request_id=self.runtime.request.request_id,
            intent=self.runtime.intent,
            event_type=event_type,
            phase=item.phase,
            agent=item.agent,
            message=f"{item.title} {stage}",
            payload={
                "item_id": item.item_id,
                "thread_id": item.thread_id,
                "turn_id": item.turn_id,
                "status": item.status,
                "title": item.title,
                "objective": item.objective,
                "degrade_reason": item.degrade_reason,
                "payload": item.payload,
                "observer_payload": dict(payload or {}),
            },
        )

    @staticmethod
    def _event_type(*, item_type: str, stage: str) -> str:
        if item_type == "degradation":
            return "runtime_degraded"
        if item_type == "agent":
            return {
                "started": "agent_started",
                "completed": "agent_completed",
                "failed": "agent_failed",
                "updated": "agent_updated",
            }.get(stage, "agent_trace")
        if item_type == "thread":
            return {
                "started": "thread_started",
                "completed": "thread_completed",
                "failed": "thread_failed",
                "updated": "thread_updated",
            }.get(stage, "thread_trace")
        return {
            "started": "subagent_started",
            "completed": "subagent_completed",
            "failed": "subagent_failed",
            "updated": "subagent_updated",
        }.get(stage, "subagent_trace")


@dataclass
class ChatLLMRun:
    context: RuntimeContext
    request: RequestEnvelope
    support: OrchestratorExecutionRuntimeSupport
    intent: str
    phase: str
    operation: str
    paths: RunPaths
    _finalized: bool = False

    def resolve_llm_execution(self, *, provider, strategy: str) -> bool:
        decision = self.support.resolve_llm_execution(
            self.context,
            request_id=self.request.request_id,
            agent=_CHAT_AGENT,
            phase=self.phase,
            strategy=strategy,
            provider=provider,
            reserve=True,
        )
        return decision.can_use_llm

    def start_item(self, *, title: str, objective: str, payload: dict[str, Any] | None = None):
        item = self.context.agent_control.spawn(
            thread_id=self.context.thread.thread_id,
            turn_id=self.context.turn.turn_id,
            request_id=self.request.request_id,
            agent=_CHAT_AGENT,
            phase=self.phase,
            title=title,
            objective=objective,
            payload=dict(payload or {}),
            item_type="subagent",
        )
        self.context.agent_control.send(item, payload=dict(payload or {}))
        return item

    def complete_item(self, item, *, payload: dict[str, Any] | None = None, degrade_reason: str | None = None) -> None:
        self.context.agent_control.wait(
            item,
            status="degraded" if degrade_reason else "completed",
            payload=dict(payload or {}),
            degrade_reason=degrade_reason,
        )

    def fail_item(self, item, *, error: Exception) -> None:
        self.context.agent_control.wait(
            item,
            status="failed",
            payload={"error": f"{error.__class__.__name__}: {error}"},
        )

    def record_llm_error(self, *, component: str, error: Exception, payload: dict[str, Any] | None = None) -> str:
        reason = _derive_llm_error_reason(error)
        self.support.record_degradation(
            self.context,
            request_id=self.request.request_id,
            component=component,
            reason=reason,
            phase=self.phase,
            agent=_CHAT_AGENT,
            payload=dict(payload or {}),
        )
        return reason

    def complete_run(self, *, status: str, message: str, result: dict[str, Any] | None = None) -> None:
        if self._finalized:
            return
        thread_status = "completed" if status == "completed" else "failed" if status in {"failed", "timed_out", "cancelled"} else "running"
        turn_status = "completed" if status == "completed" else "failed" if status in {"failed", "timed_out", "cancelled"} else "running"
        self.context.thread.status = thread_status  # type: ignore[assignment]
        self.context.turn.status = turn_status  # type: ignore[assignment]
        self.support.update_state(
            self.context,
            current_phase=self.phase,
            active_agent=None,
        )
        self.support.transition(
            self.context,
            status,
            request_id=self.request.request_id,
            intent=self.intent,
            message=message,
            error_summary=message if status in {"failed", "timed_out", "cancelled"} else None,
        )
        event_type = {
            "completed": "run_completed",
            "failed": "run_failed",
            "timed_out": "run_timed_out",
            "cancelled": "run_cancelled",
        }.get(status, "run_updated")
        self.support.emit_event(
            self.context,
            request_id=self.request.request_id,
            intent=self.intent,
            event_type=event_type,
            phase=self.phase,
            agent=_CHAT_AGENT,
            message=message,
            payload=dict(result or {}),
        )
        result_payload = dict(result or {})
        write_json(self.paths.result, result_payload)
        write_json(
            self.paths.summary,
            {
                "run_id": self.context.run_id,
                "intent": self.intent,
                "status": status,
                "message": message,
                "display_summary": _display_summary(result_payload),
            },
        )
        self._finalized = True


def complete_text_with_runtime(
    *,
    settings: Settings,
    state,
    llm_provider,
    query: str,
    conversation_context: list[dict] | None,
    intent: str,
    phase: str,
    operation: str,
    strategy: str,
    title: str,
    objective: str,
    system_prompt: str,
    user_prompt: str,
    source_run_id: str | None = None,
    temperature: float = 0.2,
    reasoning_effort: str = "low",
) -> str | None:
    run = _start_chat_llm_run(
        settings=settings,
        state=state,
        query=query,
        conversation_context=conversation_context,
        intent=intent,
        phase=phase,
        operation=operation,
        source_run_id=source_run_id,
    )
    if not run.resolve_llm_execution(provider=llm_provider, strategy=strategy):
        run.complete_run(
            status="completed",
            message=f"{operation} completed with fallback",
            result={"degraded": True, "degrade_reason": "runtime_fallback", "mode": "fallback"},
        )
        return None
    item = run.start_item(
        title=title,
        objective=objective,
        payload={"strategy": strategy, "source_run_id": source_run_id},
    )
    try:
        text = llm_provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        ).strip()
    except LLMProviderError as exc:
        reason = run.record_llm_error(
            component=f"{operation}_llm_request",
            error=exc,
            payload={"strategy": strategy, "source_run_id": source_run_id},
        )
        run.complete_item(
            item,
            payload={"strategy": strategy, "source_run_id": source_run_id},
            degrade_reason=reason,
        )
        run.complete_run(
            status="completed",
            message=f"{operation} completed with fallback",
            result={"degraded": True, "degrade_reason": reason, "mode": "fallback"},
        )
        return None
    except Exception as exc:
        run.fail_item(item, error=exc)
        run.complete_run(status="failed", message=f"{operation} failed", result={"error": str(exc)})
        raise
    if not text:
        reason = "llm_empty_response"
        run.support.record_degradation(
            run.context,
            request_id=run.request.request_id,
            component=f"{operation}_llm_response",
            reason=reason,
            phase=run.phase,
            agent=_CHAT_AGENT,
            payload={"strategy": strategy, "source_run_id": source_run_id},
        )
        run.complete_item(
            item,
            payload={"strategy": strategy, "source_run_id": source_run_id, "output_length": 0},
            degrade_reason=reason,
        )
        run.complete_run(
            status="completed",
            message=f"{operation} completed with fallback",
            result={"degraded": True, "degrade_reason": reason, "mode": "fallback"},
        )
        return None
    run.complete_item(
        item,
        payload={
            "strategy": strategy,
            "source_run_id": source_run_id,
            "output_length": len(text),
        },
    )
    run.complete_run(
        status="completed",
        message=f"{operation} completed",
        result={"text": text, "mode": "complete"},
    )
    return text


def stream_text_with_runtime(
    *,
    settings: Settings,
    state,
    llm_provider,
    query: str,
    conversation_context: list[dict] | None,
    intent: str,
    phase: str,
    operation: str,
    strategy: str,
    title: str,
    objective: str,
    system_prompt: str,
    user_prompt: str,
    on_text_delta,
    on_reasoning_summary,
    source_run_id: str | None = None,
    temperature: float = 0.2,
    reasoning_effort: str = "low",
) -> str | None:
    run = _start_chat_llm_run(
        settings=settings,
        state=state,
        query=query,
        conversation_context=conversation_context,
        intent=intent,
        phase=phase,
        operation=operation,
        source_run_id=source_run_id,
    )
    if not run.resolve_llm_execution(provider=llm_provider, strategy=strategy):
        run.complete_run(
            status="completed",
            message=f"{operation} completed with fallback",
            result={"degraded": True, "degrade_reason": "runtime_fallback", "mode": "fallback"},
        )
        return None
    item = run.start_item(
        title=title,
        objective=objective,
        payload={"strategy": strategy, "source_run_id": source_run_id},
    )
    chunks: list[str] = []
    try:
        llm_provider.stream_complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_text_delta=lambda delta: _append_chunk(chunks, delta, on_text_delta),
            on_reasoning_summary=on_reasoning_summary,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
    except LLMProviderError as exc:
        reason = run.record_llm_error(
            component=f"{operation}_llm_request",
            error=exc,
            payload={"strategy": strategy, "source_run_id": source_run_id},
        )
        run.complete_item(
            item,
            payload={
                "strategy": strategy,
                "source_run_id": source_run_id,
                "output_length": len("".join(chunks)),
            },
            degrade_reason=reason,
        )
        run.complete_run(
            status="completed",
            message=f"{operation} completed with fallback",
            result={"degraded": True, "degrade_reason": reason, "mode": "fallback"},
        )
        return None
    except Exception as exc:
        run.fail_item(item, error=exc)
        run.complete_run(status="failed", message=f"{operation} failed", result={"error": str(exc)})
        raise
    text = "".join(chunks).strip()
    if not text:
        reason = "llm_empty_response"
        run.support.record_degradation(
            run.context,
            request_id=run.request.request_id,
            component=f"{operation}_llm_response",
            reason=reason,
            phase=run.phase,
            agent=_CHAT_AGENT,
            payload={"strategy": strategy, "source_run_id": source_run_id},
        )
        run.complete_item(
            item,
            payload={"strategy": strategy, "source_run_id": source_run_id, "output_length": 0},
            degrade_reason=reason,
        )
        run.complete_run(
            status="completed",
            message=f"{operation} completed with fallback",
            result={"degraded": True, "degrade_reason": reason, "mode": "fallback"},
        )
        return None
    run.complete_item(
        item,
        payload={
            "strategy": strategy,
            "source_run_id": source_run_id,
            "output_length": len(text),
        },
    )
    run.complete_run(
        status="completed",
        message=f"{operation} completed",
        result={"text": text, "mode": "stream"},
    )
    return text


def decide_chat_dispatch_with_runtime(
    *,
    settings: Settings,
    state,
    task_router,
    query: str,
) -> ChatDispatchDecision:
    run = _start_chat_llm_run(
        settings=settings,
        state=state,
        query=query,
        conversation_context=None,
        intent="chat_dispatch",
        phase="chat_dispatch",
        operation="chat_dispatch_decision",
        source_run_id=None,
    )
    bind_runtime = getattr(task_router, "bind_runtime", None)
    clear_runtime = getattr(task_router, "clear_runtime", None)
    if callable(bind_runtime):
        bind_runtime(
            context=run.context,
            support=run.support,
            request_id=run.request.request_id,
        )
    try:
        decision = task_router.decide_chat_dispatch(query)
    except Exception as exc:
        run.complete_run(
            status="failed",
            message="chat_dispatch_decision failed",
            result={"error": f"{exc.__class__.__name__}: {exc}"},
        )
        raise
    finally:
        if callable(clear_runtime):
            clear_runtime()
    run.complete_run(
        status="completed",
        message="chat_dispatch_decision completed",
        result={
            "mode": decision.mode,
            "intent": decision.intent,
            "reason": decision.reason,
        },
    )
    return decision


def _start_chat_llm_run(
    *,
    settings: Settings,
    state,
    query: str,
    conversation_context: list[dict] | None,
    intent: str,
    phase: str,
    operation: str,
    source_run_id: str | None,
) -> ChatLLMRun:
    request = RequestEnvelope(
        intent=intent,
        user_query=query,
        conversation_context=list(conversation_context or []),
        slots={"operation": operation},
        constraints={
            "chat_thread_id": str(getattr(state, "thread_id", None) or "").strip() or None,
            "source_run_id": str(source_run_id or "").strip() or None,
        },
    )
    run_store = RunStore(_chat_runs_root(settings))
    run_id, paths, snapshot = run_store.create_run(request, intent)
    context = build_runtime_context(
        run_id=run_id,
        paths=paths,
        state=snapshot,
        run_timeout_seconds=settings.run_timeout_seconds,
        max_llm_calls_per_turn=settings.max_llm_calls_per_turn,
        max_subagent_calls_per_turn=settings.max_subagent_calls_per_turn,
        max_child_threads_per_turn=settings.max_child_threads_per_turn,
    )
    host = _ChatLLMRuntimeHost()
    support = OrchestratorExecutionRuntimeSupport(host)
    run = ChatLLMRun(
        context=context,
        request=request,
        support=support,
        intent=intent,
        phase=phase,
        operation=operation,
        paths=paths,
    )
    context.agent_control.add_observer(_ChatItemObserver(run))
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="request_received",
        phase=phase,
        agent=_CHAT_AGENT,
        message=f"{operation} request received",
    )
    support.transition(
        context,
        "running",
        request_id=request.request_id,
        intent=intent,
        message=f"{operation} running",
    )
    context.thread.status = "running"
    context.turn.status = "running"
    support.update_state(context, current_phase=phase, active_agent=_CHAT_AGENT)
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="run_accepted",
        phase=phase,
        agent=_CHAT_AGENT,
        message=f"{operation} accepted",
    )
    return run


def _chat_runs_root(settings: Settings) -> Path:
    return settings.workspace_root / _CHAT_RUNS_DIRNAME


def _derive_llm_error_reason(error: Exception) -> str:
    error_text = str(error)
    if "llm_request_timeout" in error_text:
        return "llm_request_timeout"
    return f"llm_error:{error.__class__.__name__}"


def _display_summary(result: dict[str, Any]) -> str | None:
    text = str(result.get("text") or "").strip()
    if text:
        return text[:240]
    if result.get("degraded"):
        return str(result.get("degrade_reason") or "degraded")
    return None


def _append_chunk(chunks: list[str], delta: str, callback) -> None:
    chunks.append(delta)
    callback(delta)
