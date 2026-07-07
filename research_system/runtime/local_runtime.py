from __future__ import annotations

import inspect
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from research_system.runtime.execution.core.runtime_support import OrchestratorExecutionRuntimeSupport
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext, build_runtime_context
from research_system.runtime.storage.jsonl import write_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.protocol import RequestEnvelope


@dataclass
class _LocalRuntimeHost:
    event_listener: object = None
    run_cancelled_error_cls: type[Exception] = RuntimeError
    run_timed_out_error_cls: type[Exception] = TimeoutError


@dataclass
class _LocalRuntimeObserver:
    runtime: "LocalRuntimeRun"

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
class LocalRuntimeRun:
    context: RuntimeContext
    support: OrchestratorExecutionRuntimeSupport
    request: RequestEnvelope
    intent: str
    phase: str
    _temp_dir: TemporaryDirectory[str]

    @property
    def run_root(self) -> Path:
        return Path(self.context.paths.root)

    def replay(self) -> dict[str, Any]:
        return SessionHistory(self.run_root).replay()

    def trace(self) -> dict[str, Any]:
        return SessionHistory(self.run_root).trace()

    def events(self) -> dict[str, Any]:
        return SessionHistory(self.run_root).events()

    def metrics(self) -> dict[str, Any]:
        return SessionHistory(self.run_root).metrics()

    def close(self) -> None:
        self._temp_dir.cleanup()


def start_local_runtime(
    *,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    runs_root: Path | None = None,
    run_timeout_seconds: int = 120,
    max_llm_calls_per_turn: int = 1,
    max_subagent_calls_per_turn: int = 32,
    max_child_threads_per_turn: int = 8,
) -> LocalRuntimeRun:
    normalized_request = _normalize_request(request)
    temp_dir = TemporaryDirectory(prefix="cognitrade-local-runtime-")
    effective_runs_root = runs_root or (Path(temp_dir.name) / "runs")
    run_store = RunStore(effective_runs_root)
    run_id, paths, snapshot = run_store.create_run(normalized_request, intent)
    context = build_runtime_context(
        run_id=run_id,
        paths=paths,
        state=snapshot,
        run_timeout_seconds=run_timeout_seconds,
        max_llm_calls_per_turn=max_llm_calls_per_turn,
        max_subagent_calls_per_turn=max_subagent_calls_per_turn,
        max_child_threads_per_turn=max_child_threads_per_turn,
    )
    support = OrchestratorExecutionRuntimeSupport(_LocalRuntimeHost())
    runtime = LocalRuntimeRun(
        context=context,
        support=support,
        request=normalized_request,
        intent=intent,
        phase=phase,
        _temp_dir=temp_dir,
    )
    context.agent_control.add_observer(_LocalRuntimeObserver(runtime))
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="request_received",
        phase=phase,
        agent=None,
        message=f"{phase} request received",
    )
    support.transition(
        context,
        "running",
        request_id=request.request_id,
        intent=intent,
        message=f"{phase} running",
    )
    context.thread.status = "running"
    context.turn.status = "running"
    support.update_state(context, current_phase=phase, active_agent=None)
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="run_accepted",
        phase=phase,
        agent=None,
        message=f"{phase} accepted",
    )
    return runtime


@contextmanager
def bind_local_runtime(
    target,
    *,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    runs_root: Path | None = None,
    run_timeout_seconds: int = 120,
    max_llm_calls_per_turn: int = 1,
    max_subagent_calls_per_turn: int = 32,
    max_child_threads_per_turn: int = 8,
):
    runtime = start_local_runtime(
        request=request,
        intent=intent,
        phase=phase,
        runs_root=runs_root,
        run_timeout_seconds=run_timeout_seconds,
        max_llm_calls_per_turn=max_llm_calls_per_turn,
        max_subagent_calls_per_turn=max_subagent_calls_per_turn,
        max_child_threads_per_turn=max_child_threads_per_turn,
    )
    bind_runtime = getattr(target, "bind_runtime", None)
    clear_runtime = getattr(target, "clear_runtime", None)
    if callable(bind_runtime):
        bind_kwargs = {
            "context": runtime.context,
            "support": runtime.support,
            "request_id": request.request_id,
        }
        parameters = inspect.signature(bind_runtime).parameters
        bind_with_intent = "intent" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        try:
            if bind_with_intent:
                bind_runtime(**(bind_kwargs | {"intent": intent}))
            else:
                bind_runtime(**bind_kwargs)
        except TypeError as exc:
            if not bind_with_intent or "intent" not in str(exc):
                _finalize_local_runtime(
                    runtime,
                    status="failed",
                    message=f"{phase} failed",
                    error_summary=f"{exc.__class__.__name__}: {exc}",
                )
                runtime.close()
                raise
            bind_runtime(**bind_kwargs)
    try:
        yield runtime
    except Exception as exc:
        _finalize_local_runtime(
            runtime,
            status="failed",
            message=f"{phase} failed",
            error_summary=f"{exc.__class__.__name__}: {exc}",
        )
        raise
    else:
        _finalize_local_runtime(
            runtime,
            status="completed",
            message=f"{phase} completed",
        )
    finally:
        if callable(clear_runtime):
            clear_runtime()
        runtime.close()


def _finalize_local_runtime(
    runtime: LocalRuntimeRun,
    *,
    status: str,
    message: str,
    error_summary: str | None = None,
) -> None:
    thread_status = "completed" if status == "completed" else "failed"
    turn_status = "completed" if status == "completed" else "failed"
    runtime.context.thread.status = thread_status  # type: ignore[assignment]
    runtime.context.turn.status = turn_status  # type: ignore[assignment]
    runtime.support.update_state(
        runtime.context,
        current_phase=runtime.phase,
        active_agent=None,
    )
    runtime.support.transition(
        runtime.context,
        status,
        request_id=runtime.request.request_id,
        intent=runtime.intent,
        message=message,
        error_summary=error_summary,
    )
    write_json(runtime.context.paths.thread_state, runtime.context.thread.model_dump(mode="json"))
    write_json(runtime.context.paths.turn_state, runtime.context.turn.model_dump(mode="json"))


def _normalize_request(request: RequestEnvelope | Any) -> RequestEnvelope:
    if isinstance(request, RequestEnvelope):
        return request
    if hasattr(request, "model_dump"):
        return RequestEnvelope.model_validate(request.model_dump(mode="python"))
    payload: dict[str, Any] = {
        "user_query": str(getattr(request, "user_query", "") or ""),
        "conversation_context": list(getattr(request, "conversation_context", None) or []),
        "slots": dict(getattr(request, "slots", None) or {}),
        "constraints": dict(getattr(request, "constraints", None) or {}),
        "clarification_status": str(getattr(request, "clarification_status", "not_needed") or "not_needed"),
    }
    request_id = str(getattr(request, "request_id", "") or "").strip()
    if request_id:
        payload["request_id"] = request_id
    intent = getattr(request, "intent", None)
    if intent is not None:
        payload["intent"] = intent
    created_at = getattr(request, "created_at", None)
    if created_at is not None:
        payload["created_at"] = created_at
    return RequestEnvelope(**payload)


def inject_runtime_degradation(
    runtime: LocalRuntimeRun,
    *,
    exhaust_llm_budget: bool = False,
    exhaust_subagent_budget: bool = False,
    exhaust_child_thread_budget: bool = False,
) -> dict[str, bool]:
    policy = runtime.context.execution_policy
    turn_id = runtime.context.turn.turn_id
    injected = {
        "llm_budget_exhausted": False,
        "subagent_budget_exhausted": False,
        "child_thread_budget_exhausted": False,
    }
    if exhaust_llm_budget:
        reserve_llm_call = getattr(policy, "reserve_llm_call", None)
        while callable(reserve_llm_call) and bool(reserve_llm_call(turn_id=turn_id)):
            injected["llm_budget_exhausted"] = True
    if exhaust_subagent_budget:
        reserve_subagent_call = getattr(policy, "reserve_subagent_call", None)
        while callable(reserve_subagent_call) and bool(reserve_subagent_call(turn_id=turn_id)):
            injected["subagent_budget_exhausted"] = True
    if exhaust_child_thread_budget:
        reserve_child_thread = getattr(policy, "reserve_child_thread", None)
        while callable(reserve_child_thread) and bool(reserve_child_thread(turn_id=turn_id)):
            injected["child_thread_budget_exhausted"] = True
    if any(injected.values()):
        runtime.support.emit_event(
            runtime.context,
            request_id=runtime.request.request_id,
            intent=runtime.intent,
            event_type="runtime_degradation_injected",
            phase=runtime.phase,
            agent=None,
            message="Injected runtime degradation for local runtime",
            payload=injected,
        )
    return injected


__all__ = [
    "LocalRuntimeRun",
    "bind_local_runtime",
    "inject_runtime_degradation",
    "start_local_runtime",
]
