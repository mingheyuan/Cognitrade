from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.storage.jsonl import append_jsonl, read_json, read_jsonl, write_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.core_runtime import MainAgentRunResult, RequestIntakeDecision
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.events import EventEnvelope
from research_system.schemas.execution_runtime import ExecutionRunResult
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope
from research_system.schemas.state_machine import RUN_TERMINAL_STATUSES, transition_status
from research_system.schemas.turn_runtime import (
    TaskOutcome,
    TurnConfig,
    TurnExecutionMode,
    TurnSnapshot,
)


class TurnStepExecutor(Protocol):
    def execute(self, context: "TurnContext") -> TaskOutcome:
        ...


@dataclass
class TurnContext:
    request: RequestEnvelope
    decision: RequestIntakeDecision
    run_id: str
    thread_id: str
    turn: TurnSnapshot
    config: TurnConfig
    run_store: RunStore
    main_agent: Any
    event_callback: Callable[[dict[str, Any]], None] | None = None
    resume: bool = False
    graph: Any | None = None
    seed_artifacts: list[ArtifactDraft] | None = None
    rerun_from: str | None = None

    @property
    def root(self) -> Path:
        return self.run_store.load_run_paths(self.run_id).root


class CoreLoopExecutor:
    """Adapter for the provider/tool step owned by the Main Agent."""

    def __init__(self, main_agent: Any) -> None:
        self.main_agent = main_agent

    def execute(self, context: TurnContext) -> TaskOutcome:
        core_task = getattr(self.main_agent, "_run_core_task", None)
        if not callable(core_task):
            # Keep custom MainAgent adapters source-compatible during the
            # migration while the concrete MainAgent uses the private task seam.
            core_task = self.main_agent.run
        result: MainAgentRunResult = core_task(
            request=context.request,
            decision=context.decision,
            run_id=context.run_id,
            thread_id=context.thread_id,
            event_callback=context.event_callback,
            resume=context.resume,
            turn_id=context.turn.turn_id,
            # Request routing already emits the route-start event. The
            # TurnRunner owns the enclosing lifecycle and avoids a duplicate.
            emit_started=False,
        )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status=result.status,
            result=result,
        )


class GraphRunnerAdapter(Protocol):
    def step(self, context: TurnContext) -> ExecutionRunResult:
        ...


class GraphExecutor:
    """Optional Graph step executor mounted inside RegularExecutor."""

    def __init__(self, adapter: GraphRunnerAdapter) -> None:
        self.adapter = adapter

    def execute(self, context: TurnContext) -> TaskOutcome:
        result = self.adapter.step(context)
        snapshot = result.snapshot
        return TaskOutcome(
            execution_mode="graph",
            status=snapshot.status,
            result=result,
            graph_state_ref=str(context.root / "execution_snapshot.json"),
        )


class RegularExecutor:
    """The single RegularTask loop that selects an internal step executor."""

    def __init__(
        self,
        *,
        core_executor: TurnStepExecutor,
        graph_executor: TurnStepExecutor,
    ) -> None:
        self.core_executor = core_executor
        self.graph_executor = graph_executor

    def execute(self, context: TurnContext) -> TaskOutcome:
        executor = (
            self.graph_executor
            if context.config.execution_mode == "graph"
            else self.core_executor
        )
        return executor.execute(context)


class RegularTask:
    """Root Turn task; Graph is a configured executor, not a second Task."""

    task_type = "regular"

    def __init__(self, executor: RegularExecutor) -> None:
        self.executor = executor

    def start_or_resume(self, context: TurnContext) -> TaskOutcome:
        return self.executor.execute(context)


class TaskFactory:
    """Select a real Task type and configure RegularExecutor execution."""

    @staticmethod
    def select(*, decision: RequestIntakeDecision, execution_mode: TurnExecutionMode) -> TurnConfig:
        task_type = "regular"
        if execution_mode == "graph" and decision.request_kind != "domain_task":
            raise ValueError("graph execution requires a domain task decision")
        return TurnConfig(
            task_type=task_type,
            execution_mode=execution_mode,
            graph_config=(
                {"domain_id": decision.domain_id}
                if execution_mode == "graph"
                else None
            ),
        )


class TurnRunner:
    """Own the root Turn lifecycle around Core and Graph execution."""

    def __init__(
        self,
        *,
        run_store: RunStore,
        main_agent: Any,
        graph_adapter: GraphRunnerAdapter,
    ) -> None:
        self.run_store = run_store
        self.main_agent = main_agent
        self._execution_lock_guard = RLock()
        self._execution_locks: dict[str, RLock] = {}
        self.task_factory = TaskFactory()
        self.regular_task = RegularTask(
            RegularExecutor(
                core_executor=CoreLoopExecutor(main_agent),
                graph_executor=GraphExecutor(graph_adapter),
            )
        )

    def execute_task(self, **kwargs: Any) -> TaskOutcome:
        """Serialize root execution per Run within this process."""
        run_id = str(kwargs["run_id"])
        with self._lock_for_run(run_id):
            return self._execute_task(**kwargs)

    def _lock_for_run(self, run_id: str) -> RLock:
        with self._execution_lock_guard:
            return self._execution_locks.setdefault(run_id, RLock())

    def _execute_task(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str,
        thread_id: str,
        execution_mode: TurnExecutionMode,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        resume: bool = False,
        graph: Any | None = None,
        seed_artifacts: list[ArtifactDraft] | None = None,
        rerun_from: str | None = None,
    ) -> TaskOutcome:
        config = self.task_factory.select(
            decision=decision,
            execution_mode=execution_mode,
        )
        turn, created = self._load_or_create_turn(
            request=request,
            run_id=run_id,
            thread_id=thread_id,
            config=config,
        )
        config = turn.config
        is_graph_rerun = execution_mode == "graph" and bool(rerun_from)
        if (
            turn.status in RUN_TERMINAL_STATUSES
            and turn.status not in {"gated", "completed_partial"}
            and not is_graph_rerun
        ):
            raise ValueError(f"Turn is already terminal and cannot be resumed: {turn.status}")
        recovered_running = turn.status == "running" and resume
        if turn.status == "running" and not recovered_running:
            raise ValueError(f"Turn is already running: {run_id}")
        if recovered_running:
            # A persisted running state can outlive its process. Explicit
            # recovery reopens it through the legal paused -> running path;
            # the caller must coordinate any live worker lease before doing so.
            turn = turn.model_copy(
                update={
                    "status": "paused",
                    "error": "recovered_after_worker_restart",
                    "updated_at": _utc_now(),
                }
            )
            self._persist_turn(turn)

        context = TurnContext(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=thread_id,
            turn=turn,
            config=config,
            run_store=self.run_store,
            main_agent=self.main_agent,
            event_callback=None,
            resume=resume,
            graph=graph,
            seed_artifacts=list(seed_artifacts or []),
            rerun_from=rerun_from,
        )
        context.event_callback = self._event_observer(context, event_callback)
        if created:
            self._emit(context, "turn_received", task_type="regular")
        if recovered_running:
            self._emit(
                context,
                "turn_recovered_after_worker_restart",
                task_type="regular",
            )
        if is_graph_rerun and context.turn.status in RUN_TERMINAL_STATUSES:
            # Partial rerun is an explicit Graph operation. Preserve the
            # original terminal checkpoint, then reopen this same Turn through
            # the state machine before GraphRunner resumes from the node.
            self._set_status(context, "paused")
        elif context.turn.status in {"gated", "completed_partial"}:
            self._set_status(context, "paused")
        if context.turn.status not in {"paused", "waiting_approval"}:
            self._set_status(context, "ready")
        self._emit(
            context,
            "task_selected",
            task_type="regular",
            execution_mode=execution_mode,
            reason_codes=[
                "graph_configured" if execution_mode == "graph" else "regular_task"
            ],
        )
        self._set_status(context, "running")
        self._emit(
            context,
            "turn_started",
            task_type="regular",
            execution_mode=execution_mode,
        )
        try:
            outcome = self.regular_task.start_or_resume(context)
            self._finish_turn(context, outcome)
            self._sync_event_cursor(context)
            self._emit(
                context,
                self._completion_event(outcome.status),
                status=outcome.status,
                task_type="regular",
                execution_mode=execution_mode,
            )
            return outcome
        except Exception as exc:
            context.turn = context.turn.model_copy(
                update={
                    "status": self._transition_status(context.turn.status, "failed"),
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            )
            self._persist_turn(context.turn)
            self._emit(
                context,
                "turn_failed",
                error=f"{exc.__class__.__name__}: {exc}",
            )
            raise

    def run(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str,
        thread_id: str,
        execution_mode: TurnExecutionMode,
        response_factory: Callable[[Any], ResponseEnvelope],
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        resume: bool = False,
    ) -> ResponseEnvelope:
        if not resume:
            persisted = read_json(self.run_store.load_run_paths(run_id).root / "response.json")
            if isinstance(persisted, dict):
                persisted_response = ResponseEnvelope.model_validate(persisted)
                # An intake clarification is an intermediate response. A
                # follow-up on the same run must continue into the Turn.
                if persisted_response.status != "needs_clarification":
                    return persisted_response
        outcome = self.execute_task(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=thread_id,
            execution_mode=execution_mode,
            event_callback=event_callback,
            resume=resume,
        )
        return self.finalize(outcome=outcome, response_factory=response_factory)

    def finalize(
        self,
        *,
        outcome: TaskOutcome,
        response_factory: Callable[[Any], ResponseEnvelope],
    ) -> ResponseEnvelope:
        """Create and persist the one response owned by the root Turn."""
        response = response_factory(outcome.result)
        self._persist_response(response)
        return response

    def _load_or_create_turn(
        self,
        *,
        request: RequestEnvelope,
        run_id: str,
        thread_id: str,
        config: TurnConfig,
    ) -> tuple[TurnSnapshot, bool]:
        root = self.run_store.load_run_paths(run_id).root
        payload = read_json(root / "turn_state.json")
        config_hash = self._config_hash(config)
        if isinstance(payload, dict):
            turn = TurnSnapshot.model_validate(payload)
            if turn.request_id != request.request_id:
                raise ValueError(f"Turn already belongs to another request: {run_id}")
            if turn.config_hash and turn.config_hash != config_hash:
                raise ValueError(f"Turn configuration changed during recovery: {run_id}")
            if turn.execution_mode != config.execution_mode:
                raise ValueError(f"Turn execution mode changed during recovery: {run_id}")
            turn = turn.model_copy(
                update={
                    "thread_id": turn.thread_id or thread_id,
                    "config_hash": turn.config_hash or config_hash,
                    "updated_at": _utc_now(),
                }
            )
            self._persist_turn(turn)
            return turn, False
        existing_events = read_jsonl(root / "execution_events.jsonl")
        initial_sequence = max(
            (int(event.get("sequence") or 0) for event in existing_events),
            default=0,
        )
        latest_event = max(
            existing_events,
            key=lambda event: int(event.get("sequence") or 0),
            default={},
        )
        turn = TurnSnapshot(
            turn_id=generate_ulid(),
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            task_type="regular",
            execution_mode=config.execution_mode,
            config=config,
            config_hash=config_hash,
            sequence=initial_sequence,
            last_event_id=str(latest_event.get("event_id") or "").strip() or None,
        )
        self._persist_turn(turn)
        return turn, True

    def _finish_turn(self, context: TurnContext, outcome: TaskOutcome) -> None:
        target = self._normalize_status(outcome.status)
        context.turn = context.turn.model_copy(
            update={
                "status": self._transition_status(context.turn.status, target),
                "graph_state_ref": outcome.graph_state_ref,
                "updated_at": _utc_now(),
                "error": outcome.error,
            }
        )
        self._persist_turn(context.turn)

    @staticmethod
    def _transition_status(current: str, target: str) -> str:
        if current == target:
            return target
        return transition_status(scope="run", current=current, target=target)

    def _set_status(self, context: TurnContext, target: str) -> None:
        current = context.turn.status
        if current == target:
            return
        new_status = self._transition_status(current, target)
        context.turn = context.turn.model_copy(
            update={"status": new_status, "updated_at": _utc_now()}
        )
        self._persist_turn(context.turn)

    @staticmethod
    def _normalize_status(status: str) -> str:
        return "paused" if status == "running" else status

    @staticmethod
    def _completion_event(status: str) -> str:
        return {
            "completed": "turn_completed",
            "completed_partial": "turn_completed_partial",
            "gated": "turn_gated",
            "paused": "turn_paused",
            "waiting_approval": "turn_waiting_approval",
            "cancelled": "turn_cancelled",
            "timed_out": "turn_timed_out",
        }.get(status, "turn_finished")

    def _persist_turn(self, turn: TurnSnapshot) -> None:
        root = self.run_store.load_run_paths(turn.run_id).root
        write_json(root / "turn_state.json", turn.model_dump(mode="json"))

    def _sync_event_cursor(self, context: TurnContext) -> None:
        root = self.run_store.load_run_paths(context.run_id).root
        events = read_jsonl(root / "execution_events.jsonl")
        if not events:
            return
        latest = max(events, key=lambda event: int(event.get("sequence") or 0))
        latest_sequence = int(latest.get("sequence") or 0)
        if latest_sequence <= context.turn.sequence:
            return
        context.turn = context.turn.model_copy(
            update={
                "sequence": latest_sequence,
                "last_event_id": str(latest.get("event_id") or "").strip() or None,
                "updated_at": _utc_now(),
            }
        )
        self._persist_turn(context.turn)

    @staticmethod
    def _config_hash(config: TurnConfig) -> str:
        payload = json.dumps(
            config.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _persist_response(self, response: ResponseEnvelope) -> None:
        if response.run_id:
            root = self.run_store.load_run_paths(response.run_id).root
            write_json(root / "response.json", response.model_dump(mode="json"))

    def _event_observer(
        self,
        context: TurnContext,
        external_callback: Callable[[dict[str, Any]], None] | None,
    ) -> Callable[[dict[str, Any]], None]:
        def observe(event: dict[str, Any]) -> None:
            sequence = int(event.get("sequence") or 0)
            next_sequence = (
                sequence
                if sequence > context.turn.sequence
                else context.turn.sequence + 1
                if sequence == 0
                else context.turn.sequence
            )
            context.turn = context.turn.model_copy(
                update={
                    "sequence": next_sequence,
                    "last_event_id": str(event.get("event_id") or "").strip() or None,
                    "updated_at": _utc_now(),
                }
            )
            self._persist_turn(context.turn)
            if external_callback is not None:
                try:
                    external_callback(event)
                except Exception:
                    pass

        return observe

    def _emit(self, context: TurnContext, event_type: str, **payload: Any) -> None:
        event = EventEnvelope(
            event_type=event_type,
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            turn_id=context.turn.turn_id,
            sequence=context.turn.sequence + 1,
            task_type=(str(payload["task_type"]) if payload.get("task_type") else None),
            execution_mode=(
                str(payload["execution_mode"])
                if payload.get("execution_mode")
                else None
            ),
            actor="turn_runner",
            payload=payload,
        ).model_dump(mode="json")
        context.turn = context.turn.model_copy(
            update={
                "sequence": context.turn.sequence + 1,
                "last_event_id": event["event_id"],
                "updated_at": _utc_now(),
            }
        )
        root = self.run_store.load_run_paths(context.run_id).root
        self._persist_turn(context.turn)
        append_jsonl(root / "execution_events.jsonl", event)
        if context.event_callback is not None:
            try:
                context.event_callback(event)
            except Exception:
                # Observers must not be able to corrupt the persisted Turn.
                pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "CoreLoopExecutor",
    "GraphExecutor",
    "GraphRunnerAdapter",
    "RegularExecutor",
    "RegularTask",
    "TaskFactory",
    "TurnContext",
    "TurnRunner",
]
