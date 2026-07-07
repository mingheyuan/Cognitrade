from __future__ import annotations

import inspect
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from research_system.runtime.core.execution_policy import RuntimeDegradeReason
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
        item_payload = dict(item.payload or {})
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
                "payload": item_payload,
                "observer_payload": dict(payload or {}),
            }
            | self._derived_payload_fields(item_type=item.item_type, item=item, item_payload=item_payload),
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

    @staticmethod
    def _derived_payload_fields(*, item_type: str, item, item_payload: dict[str, Any]) -> dict[str, Any]:
        derived: dict[str, Any] = {
            "invocation_id": item_payload.get("invocation_id"),
            "subagent_type": item_payload.get("subagent_type"),
            "action_id": item_payload.get("action_id"),
            "node_id": item_payload.get("node_id"),
            "task_name": item_payload.get("task_name"),
            "agent_path": item_payload.get("agent_path"),
            "agent_label": item_payload.get("agent_label"),
            "child_thread_id": item_payload.get("child_thread_id"),
            "child_turn_id": item_payload.get("child_turn_id"),
            "notes": list(item_payload.get("notes") or []),
            "llm_used": item_payload.get("llm_used"),
        }
        if item_type == "subagent":
            derived["subagent_type"] = derived.get("subagent_type") or item.title
            if not derived.get("task_name"):
                phase = str(item.phase or "").strip()
                derived["task_name"] = f"{phase}:{item.title}" if phase else item.title
            if not derived.get("agent_path"):
                agent = str(item.agent or "").strip()
                derived["agent_path"] = f"{agent}/{item.title}" if agent else item.title
            if not derived.get("agent_label"):
                derived["agent_label"] = " ".join(
                    segment.capitalize() for segment in str(item.title or "").split("_") if segment
                ).strip() or item.title
        return {key: value for key, value in derived.items() if value not in (None, "", [])}


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

    def steps(self) -> dict[str, Any]:
        return SessionHistory(self.run_root).steps()

    def subagent(self, invocation_id: str) -> dict[str, Any]:
        return SessionHistory(self.run_root).subagent(invocation_id)

    def thread(self, thread_id: str) -> dict[str, Any]:
        return SessionHistory(self.run_root).thread(thread_id)

    def thread_replay(self, thread_id: str, *, include_descendants: bool = True) -> dict[str, Any]:
        return SessionHistory(self.run_root).thread_replay(
            thread_id,
            include_descendants=include_descendants,
        )

    def thread_metrics(self, thread_id: str, *, include_descendants: bool = True) -> dict[str, Any]:
        return SessionHistory(self.run_root).thread_metrics(
            thread_id,
            include_descendants=include_descendants,
        )

    def thread_tree(self) -> list[dict[str, Any]]:
        return SessionHistory(self.run_root).thread_tree()

    def inject_degradation(self, **kwargs) -> dict[str, bool]:
        return inject_runtime_degradation(self, **kwargs)

    def close(self) -> None:
        self._temp_dir.cleanup()


def start_local_runtime(
    *,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    runs_root: Path | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    id_factory: Callable[[], str] | None = None,
    run_timeout_seconds: int = 120,
    max_llm_calls_per_turn: int = 1,
    max_subagent_calls_per_turn: int = 32,
    max_child_threads_per_turn: int = 8,
) -> LocalRuntimeRun:
    normalized_request = _normalize_request(request)
    temp_dir = TemporaryDirectory(prefix="cognitrade-local-runtime-")
    effective_runs_root = runs_root or (Path(temp_dir.name) / "runs")
    run_store = RunStore(effective_runs_root)
    run_id, paths, snapshot = run_store.create_run(normalized_request, intent, run_id=run_id)
    context = build_runtime_context(
        run_id=run_id,
        paths=paths,
        state=snapshot,
        run_timeout_seconds=run_timeout_seconds,
        max_llm_calls_per_turn=max_llm_calls_per_turn,
        max_subagent_calls_per_turn=max_subagent_calls_per_turn,
        max_child_threads_per_turn=max_child_threads_per_turn,
        thread_id=thread_id,
        turn_id=turn_id,
        id_factory=id_factory,
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
    run_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    id_factory: Callable[[], str] | None = None,
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
        run_id=run_id,
        thread_id=thread_id,
        turn_id=turn_id,
        id_factory=id_factory,
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
    llm_unavailable: bool = False,
    qdrant_unavailable: bool = False,
    memory_unavailable: bool = False,
) -> dict[str, bool]:
    policy = runtime.context.execution_policy
    turn_id = runtime.context.turn.turn_id
    injected = {
        "llm_budget_exhausted": False,
        "subagent_budget_exhausted": False,
        "child_thread_budget_exhausted": False,
        "llm_unavailable": False,
        "qdrant_unavailable": False,
        "memory_unavailable": False,
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
    inject_degradation = getattr(policy, "inject_degradation", None)
    if llm_unavailable and callable(inject_degradation):
        inject_degradation(RuntimeDegradeReason.LLM_UNAVAILABLE.value)
        injected["llm_unavailable"] = True
    if qdrant_unavailable and callable(inject_degradation):
        inject_degradation(RuntimeDegradeReason.QDRANT_UNAVAILABLE.value)
        injected["qdrant_unavailable"] = True
    if memory_unavailable and callable(inject_degradation):
        inject_degradation(RuntimeDegradeReason.MEMORY_SERVICE_UNAVAILABLE.value)
        injected["memory_unavailable"] = True
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


@dataclass
class LocalRuntimeHarness:
    runs_root: Path
    run_id: str = "local-runtime-run"
    request_id: str = "local-runtime-request"
    thread_id: str = "local-runtime-thread"
    turn_id: str = "local-runtime-turn"
    id_prefix: str = "local-runtime"
    _id_counter: int = 0

    def build_request(
        self,
        *,
        user_query: str,
        intent: str | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
        slots: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        clarification_status: str = "not_needed",
    ) -> RequestEnvelope:
        return RequestEnvelope(
            request_id=self.request_id,
            intent=intent,
            user_query=user_query,
            conversation_context=list(conversation_context or []),
            slots=dict(slots or {}),
            constraints=dict(constraints or {}),
            clarification_status=clarification_status,
        )

    def start(
        self,
        *,
        intent: str,
        phase: str,
        user_query: str | None = None,
        request: RequestEnvelope | None = None,
        run_timeout_seconds: int = 120,
        max_llm_calls_per_turn: int = 1,
        max_subagent_calls_per_turn: int = 32,
        max_child_threads_per_turn: int = 8,
    ) -> LocalRuntimeRun:
        effective_request = _normalize_request(
            request
            or self.build_request(
                user_query=user_query or f"{intent}:{phase}",
                intent=intent,
            )
        )
        return start_local_runtime(
            request=effective_request,
            intent=intent,
            phase=phase,
            runs_root=self.runs_root,
            run_id=self.run_id,
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            id_factory=self._next_id,
            run_timeout_seconds=run_timeout_seconds,
            max_llm_calls_per_turn=max_llm_calls_per_turn,
            max_subagent_calls_per_turn=max_subagent_calls_per_turn,
            max_child_threads_per_turn=max_child_threads_per_turn,
        )

    def replay(self) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).replay()

    def trace(self) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).trace()

    def events(self) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).events()

    def metrics(self) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).metrics()

    def steps(self) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).steps()

    def subagent(self, invocation_id: str) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).subagent(invocation_id)

    def thread(self, thread_id: str) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).thread(thread_id)

    def thread_replay(self, thread_id: str, *, include_descendants: bool = True) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).thread_replay(
            thread_id,
            include_descendants=include_descendants,
        )

    def thread_metrics(self, thread_id: str, *, include_descendants: bool = True) -> dict[str, Any]:
        return SessionHistory(self.runs_root / self.run_id).thread_metrics(
            thread_id,
            include_descendants=include_descendants,
        )

    def thread_tree(self) -> list[dict[str, Any]]:
        return SessionHistory(self.runs_root / self.run_id).thread_tree()

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"{self.id_prefix}-{self._id_counter:04d}"


__all__ = [
    "LocalRuntimeRun",
    "LocalRuntimeHarness",
    "bind_local_runtime",
    "inject_runtime_degradation",
    "start_local_runtime",
]
