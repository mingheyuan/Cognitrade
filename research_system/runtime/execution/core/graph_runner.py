from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import Event, RLock, Semaphore
from time import monotonic, perf_counter
from typing import Any, Callable, Protocol

from typing import TYPE_CHECKING

import jsonschema

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.approvals import (
    HumanApprovalStore,
    approval_policy_fingerprint,
)
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.core.tool_registry import CoreToolRegistry
from research_system.runtime.event_store import append_canonical_event
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.execution_graph import (
    ExecutionGraphScheduler,
    affected_node_ids_for_patch,
    apply_execution_graph_patch,
)
from research_system.runtime.execution.core.execution_context import (
    ExecutionNodeContext,
    GateEvaluationContext,
    GraphCancelledError,
    GraphExecutionError,
    GraphGovernanceError,
    GraphNodeTimeoutError,
    GraphTimedOutError,
)
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json
from research_system.schemas.artifacts import ArtifactDraft, ArtifactProducer, ArtifactRecord
from research_system.schemas.core_runtime import PlanDecision
from research_system.schemas.execution_graph import (
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionGraphPatchOperation,
    ExecutionNode,
)
from research_system.schemas.execution_runtime import (
    ExecutionRunResult,
    ExecutionRunSnapshot,
    ExecutionUsage,
    GraphStepOutcome,
    GateEvaluation,
    GraphPatchImpact,
    NodeExecutionOutput,
    NodeExecutionState,
)
from research_system.schemas.events import EventEnvelope
from research_system.schemas.governance import GateEvaluationResult
from research_system.schemas.state_machine import RUN_TERMINAL_STATUSES, transition_status

if TYPE_CHECKING:
    from research_system.services.memory.layers import LayeredMemoryStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DynamicGraphPlanner(Protocol):
    def expand_compound(
        self,
        *,
        graph: ExecutionGraph,
        compound: ExecutionNode,
        artifacts: list[ArtifactRecord],
    ) -> ExecutionGraphPatch: ...

    def evaluate_frontier(
        self,
        *,
        graph: ExecutionGraph,
        frontier: list[ExecutionNode],
        artifacts: list[ArtifactRecord],
    ) -> ExecutionGraphPatch | None: ...

    def decide_expand_compound(
        self,
        *,
        graph: ExecutionGraph,
        compound: ExecutionNode,
        artifacts: list[ArtifactRecord],
    ) -> PlanDecision: ...

    def decide_frontier(
        self,
        *,
        graph: ExecutionGraph,
        frontier: list[ExecutionNode],
        artifacts: list[ArtifactRecord],
    ) -> PlanDecision: ...


@dataclass(frozen=True)
class _NodeOutcome:
    node: ExecutionNode
    state: NodeExecutionState
    output: NodeExecutionOutput | None = None
    error: Exception | None = None


class ExecutionGraphRunner:
    """Domain-neutral, artifact-only executor for the canonical graph protocol."""

    def __init__(
        self,
        *,
        registry: DomainPackRegistry,
        run_root: Path,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        dynamic_planner: DynamicGraphPlanner | None = None,
        memory_store: "LayeredMemoryStore | None" = None,
        tool_registry: CoreToolRegistry | None = None,
        skill_runtime_factory: Callable[[str, str | None], Any] | None = None,
        settings: Any | None = None,
        llm_provider: Any | None = None,
        cache_root: Path | None = None,
        failure_injections: list[dict[str, Any]] | None = None,
        progress_observer: Callable[[dict[str, Any]], dict[str, str] | None] | None = None,
        turn_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        task_type: str = "regular",
        execution_mode: str = "graph",
        deadline_at: float | None = None,
    ) -> None:
        self.registry = registry
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.board = ArtifactBoard(self.run_root / "artifact_board.jsonl")
        self.approvals = HumanApprovalStore(self.run_root / "approvals.jsonl")
        self.snapshot_path = self.run_root / "execution_snapshot.json"
        self.graph_path = self.run_root / "execution_graph.json"
        self.events_path = self.run_root / "execution_events.jsonl"
        self.control_path = self.run_root / "execution_control.json"
        self.event_callback = event_callback
        self.dynamic_planner = dynamic_planner
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.skill_runtime_factory = skill_runtime_factory
        self.settings = settings
        self.llm_provider = llm_provider
        self.progress_observer = progress_observer
        self.cache_root = Path(cache_root) if cache_root is not None else self.run_root / "cache"
        self.scheduler = ExecutionGraphScheduler()
        self._pause_requested = Event()
        self._cancel_requested = Event()
        self._node_cancellations: dict[str, Event] = {}
        self._patches: list[ExecutionGraphPatch] = []
        self._lock = RLock()
        self._reserved_tokens = 0
        self._reserved_cost = 0.0
        self._run_id: str | None = self.run_root.name
        self._thread_id: str | None = None
        self._request_id: str | None = None
        self._user_id = user_id
        self._tenant_id = tenant_id
        self._turn_id = turn_id
        self._task_type = task_type
        self._execution_mode = execution_mode
        self._deadline_at = deadline_at
        self._graph_revision: int | None = None
        self._tool_semaphore: Semaphore | None = None
        self._progress_policy: dict[str, Any] = {}
        self._progress_model_updates = 0
        self._progress_last_model_at = 0.0
        self._failure_injections = [
            {
                "kind": str(row.get("kind") or "").strip(),
                "target": str(row.get("target") or "").strip(),
                "count": max(int(row.get("count") or 1), 0),
                "reason": str(row.get("reason") or "评估失败注入").strip(),
            }
            for row in list(failure_injections or [])
            if isinstance(row, dict)
        ]

    def request_pause(self) -> None:
        self._pause_requested.set()
        self._write_control(pause_requested=True)

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        self._write_control(cancel_requested=True)
        with self._lock:
            for cancellation in self._node_cancellations.values():
                cancellation.set()

    @staticmethod
    def _set_run_status(snapshot: ExecutionRunSnapshot, status: str) -> None:
        snapshot.status = transition_status(
            scope="run",
            current=snapshot.status,
            target=status,
        )

    @staticmethod
    def _set_node_status(state: NodeExecutionState, status: str) -> None:
        state.status = transition_status(
            scope="node",
            current=state.status,
            target=status,
        )

    def submit_patch(self, patch: ExecutionGraphPatch) -> None:
        if patch.actor != "main_agent.plan_agent":
            raise GraphGovernanceError(
                "only the Main Agent PlanAgent role may submit a graph patch"
            )
        snapshot = self._load_snapshot()
        with self._lock:
            if snapshot is not None and patch.patch_id in snapshot.applied_patch_ids:
                self._emit(
                    "graph_patch_duplicate_ignored",
                    patch_id=patch.patch_id,
                    revision=snapshot.graph.revision,
                )
                return
            if snapshot is not None and patch.base_revision != snapshot.graph.revision:
                self._emit(
                    "graph_patch_rejected",
                    patch_id=patch.patch_id,
                    base_revision=patch.base_revision,
                    current_revision=snapshot.graph.revision,
                    reason="ExecutionGraph 版本已过期。",
                )
                raise GraphExecutionError(
                    f"stale graph patch: {patch.base_revision}!={snapshot.graph.revision}"
                )
            if snapshot is not None:
                try:
                    apply_execution_graph_patch(snapshot.graph, patch)
                except Exception as exc:
                    self._emit(
                        "graph_patch_rejected",
                        patch_id=patch.patch_id,
                        base_revision=patch.base_revision,
                        reason=f"{exc.__class__.__name__}: {exc}",
                    )
                    raise GraphExecutionError(
                        f"graph patch preflight failed: {exc}"
                    ) from exc
            missing_cancellations = [
                node_id
                for node_id in patch.cancel_in_flight_node_ids
                if node_id not in self._node_cancellations
            ]
            if missing_cancellations:
                self._emit(
                    "graph_patch_rejected",
                    patch_id=patch.patch_id,
                    base_revision=patch.base_revision,
                    reason="取消目标当前未在运行。",
                    node_ids=missing_cancellations,
                )
                raise GraphGovernanceError(
                    "in-flight cancellation target is not running: "
                    + ",".join(missing_cancellations)
                )
            self._patches.append(patch)
            for node_id in patch.cancel_in_flight_node_ids:
                cancellation = self._node_cancellations.get(node_id)
                if cancellation is not None:
                    cancellation.set()
        self._emit(
            "graph_patch_requested",
            patch_id=patch.patch_id,
            base_revision=patch.base_revision,
            actor=patch.actor,
            cancel_in_flight_node_ids=patch.cancel_in_flight_node_ids,
            patch=patch.model_dump(mode="json"),
        )

    def run(
        self,
        *,
        graph: ExecutionGraph,
        run_id: str,
        thread_id: str,
        request_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        seed_artifacts: list[ArtifactDraft] | None = None,
        resume: bool = False,
        max_steps: int | None = None,
    ) -> ExecutionRunResult:
        self._run_id = run_id
        self._thread_id = thread_id
        self._request_id = request_id
        self._user_id = user_id
        self._tenant_id = tenant_id
        self._graph_revision = graph.revision
        self._progress_policy = dict(graph.metadata.get("progress_policy") or {})
        self._progress_model_updates = 0
        self._progress_last_model_at = 0.0
        self.board.bind_thread(thread_id)
        snapshot = self._load_snapshot() if resume else None
        if snapshot is None:
            snapshot = self._new_snapshot(
                graph=graph,
                run_id=run_id,
                thread_id=thread_id,
                request_id=request_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            self._seed_artifacts(snapshot, seed_artifacts or [])
            if self._rejected_approval_ids(snapshot):
                self._mark_approval_rejection(snapshot)
                self._persist(snapshot)
                return self._result(snapshot)
            control_payload = read_json(self.control_path) or {}
            cancel_requested = self._cancel_requested.is_set() or (
                isinstance(control_payload, dict)
                and bool(control_payload.get("cancel_requested"))
            )
            if snapshot.status == "waiting_approval" and cancel_requested:
                self._set_run_status(snapshot, "cancelled")
                self._persist(snapshot)
                self._emit("graph_run_cancelled", reason="已请求取消运行。")
                return self._result(snapshot)
            if snapshot.status == "waiting_approval":
                self._persist(snapshot)
                self._emit(
                    "graph_run_waiting_approval",
                    approval_ids=self._pending_approval_ids(snapshot),
                )
                return self._result(snapshot)
        else:
            self._user_id = snapshot.user_id
            self._tenant_id = snapshot.tenant_id
            self._validate_resume_identity(
                snapshot=snapshot,
                graph=graph,
                run_id=run_id,
                thread_id=thread_id,
                request_id=request_id,
            )
            graph = snapshot.graph
            if snapshot.status in {"completed", "completed_partial", "gated", "failed", "cancelled", "timed_out"}:
                return self._result(snapshot)
            if self._rejected_approval_ids(snapshot):
                self._mark_approval_rejection(snapshot)
                self._persist(snapshot)
                return self._result(snapshot)
            control_payload = read_json(self.control_path) or {}
            cancel_requested = self._cancel_requested.is_set() or (
                isinstance(control_payload, dict)
                and bool(control_payload.get("cancel_requested"))
            )
            if snapshot.status == "waiting_approval" and cancel_requested:
                self._set_run_status(snapshot, "cancelled")
                self._persist(snapshot)
                self._emit("graph_run_cancelled", reason="已请求取消运行。")
                return self._result(snapshot)
            if snapshot.status == "waiting_approval" and self._pending_approval_ids(snapshot):
                self._persist(snapshot)
                self._emit(
                    "graph_run_waiting_approval",
                    approval_ids=self._pending_approval_ids(snapshot),
                )
                return self._result(snapshot)
            self._set_run_status(snapshot, "running")
            snapshot.updated_at = utc_now()
        control = read_json(self.control_path) or {}
        if (
            self._cancel_requested.is_set()
            or (isinstance(control, dict) and control.get("cancel_requested"))
        ):
            if snapshot.status not in RUN_TERMINAL_STATUSES:
                self._set_run_status(snapshot, "cancelled")
                snapshot.updated_at = utc_now()
                self._persist(snapshot)
                self._emit("graph_run_cancelled", reason="已请求取消运行。")
            return self._result(snapshot)
        self.board.bind_invalidated_artifacts(snapshot.invalidated_artifact_ids)
        self._set_run_status(snapshot, "running")
        if not self.control_path.exists():
            self._write_control(pause_requested=False, cancel_requested=False)
        self._reserved_tokens = snapshot.usage.tokens
        self._reserved_cost = snapshot.usage.cost
        self._tool_semaphore = Semaphore(snapshot.graph.governance.max_concurrent_tool_calls)
        self._persist(snapshot)
        started = perf_counter()
        step_count = 0
        self._emit(
            "main_agent_plan_started",
            actor="main_agent.plan_agent",
            graph_id=graph.graph_id,
            revision=graph.revision,
            message=(
                "Main Agent is materializing the initial ExecutionGraph "
                "from the accepted request and installed capabilities."
            ),
        )
        self._emit(
            "graph_run_started",
            graph_id=graph.graph_id,
            revision=graph.revision,
            actor="main_agent",
            message="Main Agent started the governed ExecutionGraph.",
        )
        self._emit_main_agent_progress(
            phase="graph_start",
            current_action=f"ExecutionGraph {graph.graph_id} 已在第 {graph.revision} 个修订版本启动",
            next_action="展开延迟 compound 节点并计算可执行前沿",
        )

        try:
            while True:
                self._enforce_run_controls(snapshot, started)
                graph = self._expand_ready_compounds(snapshot)
                graph = self._apply_pending_patches(snapshot, graph)
                snapshot.graph = graph
                if self._rejected_approval_ids(snapshot):
                    self._mark_approval_rejection(snapshot)
                    self._persist(snapshot)
                    return self._result(snapshot)
                if snapshot.status == "waiting_approval":
                    self._persist(snapshot)
                    self._emit(
                        "graph_run_waiting_approval",
                        approval_ids=self._pending_approval_ids(snapshot),
                    )
                    return self._result(snapshot)
                if snapshot.status == "paused":
                    self._persist(snapshot)
                    return self._result(snapshot)

                completed = self._node_ids(snapshot, "completed")
                skipped = self._node_ids(snapshot, "skipped")
                failed = self._node_ids(snapshot, "failed")
                pending = self._node_ids(snapshot, "pending") | self._node_ids(snapshot, "waiting")

                blocked = self.scheduler.blocked_leaf_nodes(
                    graph,
                    failed_node_ids=failed,
                    active_node_ids=pending,
                )
                for node in blocked:
                    state = snapshot.node_states[node.node_id]
                    self._set_node_status(state, "skipped")
                    state.error = "blocked_by_failed_dependency"
                    state.completed_at = utc_now()
                    self._emit("graph_node_blocked", node_id=node.node_id)
                self._refresh_compound_statuses(snapshot)

                pending = self._node_ids(snapshot, "pending") | self._node_ids(snapshot, "waiting")
                if not pending:
                    break

                ready = self.scheduler.ready_leaf_nodes(
                    graph,
                    completed_node_ids=completed,
                    skipped_node_ids=skipped,
                    failed_node_ids=failed,
                    active_node_ids=pending,
                )
                if not ready:
                    raise GraphExecutionError(
                        f"execution graph stalled: {','.join(sorted(pending))}"
                    )
                frontier = self._governed_frontier(graph, ready)
                self._emit(
                    "graph_frontier_ready",
                    node_ids=[node.node_id for node in frontier],
                    revision=graph.revision,
                )
                self._emit_main_agent_progress(
                    phase="frontier_ready",
                    current_action=(
                        "当前可执行前沿："
                        + ", ".join(node.node_id for node in frontier)
                    ),
                    next_action=(
                        "执行可执行节点："
                        + ", ".join(node.node_id for node in frontier)
                    ),
                )
                frontier_outcomes = self._execute_frontier(snapshot, frontier)
                for outcome_index, outcome in enumerate(frontier_outcomes):
                    snapshot.node_states[outcome.node.node_id] = outcome.state
                    if outcome.output is not None:
                        snapshot.usage = self._add_usage(snapshot.usage, outcome.output.usage)
                        if outcome.output.control_requests:
                            snapshot.pending_control_requests.extend(
                                outcome.output.control_requests
                            )
                            for request in outcome.output.control_requests:
                                self._emit(
                                    "subagent_control_request",
                                    node_id=outcome.node.node_id,
                                    request=request.model_dump(mode="json"),
                                )
                        self._enforce_total_budget(snapshot)
                    if (
                        outcome.error is not None
                        and not isinstance(outcome.error, GraphCancelledError)
                        and outcome.node.failure_mode == "halt"
                    ):
                        self._set_run_status(snapshot, "failed")
                        self._persist(snapshot)
                        raise GraphExecutionError(
                            f"execution node failed: {outcome.node.node_id}: {outcome.state.error}"
                        ) from outcome.error
                    remaining = len(frontier_outcomes) - outcome_index - 1
                    fallback_current = (
                        f"Subagent {outcome.node.node_id} 已返回 "
                        f"{outcome.state.status}"
                        + (
                            f"；产物：{', '.join(outcome.state.output_artifact_ids)}"
                            if outcome.state.output_artifact_ids
                            else ""
                        )
                    )
                    fallback_next = (
                        "处理剩余可执行节点的结果"
                        if remaining
                        else "评估 Board 并决定下一步图操作"
                    )
                    observed = self._observe_main_agent_progress(
                        {
                            "event": "subagent_completed",
                            "run_id": snapshot.run_id,
                            "thread_id": snapshot.thread_id,
                            "request_id": snapshot.request_id,
                            "graph_revision": graph.revision,
                            "objective": graph.objective,
                            "domain_id": graph.domain_id,
                            "node_id": outcome.node.node_id,
                            "capability": outcome.node.capability,
                            "status": outcome.state.status,
                            "output_artifact_ids": list(outcome.state.output_artifact_ids),
                            "messages": list(outcome.output.messages or [])
                            if outcome.output is not None
                            else [],
                            "remaining_ready_nodes": [
                                item.node.node_id
                                for item in frontier_outcomes[outcome_index + 1 :]
                            ],
                        }
                    )
                    self._emit_main_agent_progress(
                        phase="subagent_result",
                        current_action=(observed or {}).get("current", fallback_current),
                        next_action=(observed or {}).get("next", fallback_next),
                        source="llm" if observed else "runtime",
                        node_id=outcome.node.node_id,
                    )
                snapshot.updated_at = utc_now()
                self._refresh_compound_statuses(snapshot)
                graph = self._evaluate_frontier(snapshot, graph, frontier)
                snapshot.graph = graph
                self._persist(snapshot)
                if snapshot.status in {"paused", "waiting_approval"}:
                    return self._result(snapshot)
                step_count += 1
                pending_after_step = self._node_ids(snapshot, "pending") | self._node_ids(snapshot, "waiting")
                if max_steps is not None and step_count >= max_steps and pending_after_step:
                    self._set_run_status(snapshot, "paused")
                    snapshot.updated_at = utc_now()
                    self._persist(snapshot)
                    self._emit(
                        "graph_step_yielded",
                        step_count=step_count,
                        pending_node_ids=sorted(pending_after_step),
                        graph_revision=snapshot.graph.revision,
                    )
                    return self._result(snapshot)

            snapshot.gate_evaluations = self._evaluate_gates(snapshot)
            self._refresh_compound_statuses(snapshot)
            blocking = [
                gate
                for gate in snapshot.gate_evaluations
                if gate.blocking and gate.verdict != "pass"
            ]
            self._set_run_status(snapshot, "gated" if blocking else "completed")
            snapshot.updated_at = utc_now()
            self._persist(snapshot)
            if snapshot.status == "completed":
                self._record_episodic_memory(snapshot)
            self._emit(
                "graph_run_finished",
                status=snapshot.status,
                blocking_gate_ids=[gate.gate_id for gate in blocking],
            )
            self._emit_main_agent_progress(
                phase="delivery",
                current_action=f"ExecutionGraph 已以 {snapshot.status} 状态完成",
                next_action="持久化最终 Artifact 并完成交付",
                status=snapshot.status,
            )
            return self._result(snapshot)
        except GraphTimedOutError:
            self._set_run_status(snapshot, "timed_out")
            snapshot.updated_at = utc_now()
            for state in snapshot.node_states.values():
                if state.status in {"pending", "waiting", "running"}:
                    self._set_node_status(state, "cancelled")
                    state.completed_at = utc_now()
            self._persist(snapshot)
            self._emit("graph_run_timed_out")
            return self._result(snapshot)
        except GraphCancelledError:
            self._set_run_status(snapshot, "cancelled")
            snapshot.updated_at = utc_now()
            for state in snapshot.node_states.values():
                if state.status in {"pending", "waiting", "running"}:
                    self._set_node_status(state, "cancelled")
                    state.completed_at = utc_now()
            self._persist(snapshot)
            self._emit("graph_run_cancelled")
            return self._result(snapshot)
        except _GraphPaused:
            return self._result(snapshot)
        except Exception:
            if snapshot.status not in {"failed", "cancelled", "paused"}:
                self._set_run_status(snapshot, "failed")
                snapshot.updated_at = utc_now()
                self._persist(snapshot)
            raise

    def step(
        self,
        *,
        graph: ExecutionGraph,
        run_id: str,
        thread_id: str,
        request_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        seed_artifacts: list[ArtifactDraft] | None = None,
        resume: bool = False,
    ) -> GraphStepOutcome:
        """Advance exactly one frontier and return at a RegularExecutor safe point."""
        result = self.run(
            graph=graph,
            run_id=run_id,
            thread_id=thread_id,
            request_id=request_id,
            user_id=user_id,
            tenant_id=tenant_id,
            seed_artifacts=seed_artifacts,
            resume=resume,
            max_steps=1,
        )
        status = result.snapshot.status
        control = read_json(self.control_path) or {}
        waiting = status == "waiting_approval"
        externally_paused = bool(control.get("pause_requested") or control.get("cancel_requested"))
        should_continue = status == "paused" and not waiting and not externally_paused
        return GraphStepOutcome(
            result=result,
            progressed=True,
            safe_point=True,
            should_continue=should_continue,
        )

    def recover_terminal(self) -> ExecutionRunResult:
        """Reopen a timed-out terminal graph through an explicit continuation.

        The original terminal snapshot is preserved by the enclosing Turn
        history.  This method only prepares the Graph checkpoint for the new
        continuation; normal progress still goes through ``step``.
        """
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot recover a run without an execution snapshot")
        if snapshot.status not in {"timed_out", "failed", "cancelled"}:
            raise GraphExecutionError(
                f"graph recovery requires a terminal recoverable status, got {snapshot.status}"
            )
        source_status = snapshot.status
        for node_id, state in snapshot.node_states.items():
            if state.status not in {"failed", "cancelled", "timed_out"}:
                continue
            self._set_node_status(state, "pending")
            state.error = "reopened_by_recovery_continuation"
            state.started_at = None
            state.completed_at = None
            node = self._node(snapshot.graph, node_id)
            if node.status != "pending":
                node.status = transition_status(scope="node", current=node.status, target="pending")
        self._set_run_status(snapshot, "paused")
        snapshot.updated_at = utc_now()
        self._write_control(pause_requested=False, cancel_requested=False)
        self._persist(snapshot)
        self._emit(
            "graph_recovery_continuation_prepared",
            source_status=source_status,
            pending_node_ids=sorted(
                node_id
                for node_id, state in snapshot.node_states.items()
                if state.status == "pending"
            ),
        )
        return self._result(snapshot)

    def recover_worker_restart(self) -> ExecutionRunResult:
        """Requeue in-flight nodes after a process dies at a checkpoint."""
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot recover a run without an execution snapshot")
        if snapshot.status != "running":
            return self._result(snapshot)
        requeued: list[str] = []
        for node_id, state in snapshot.node_states.items():
            if state.status != "running":
                continue
            self._set_node_status(state, "pending")
            state.error = "requeued_after_worker_restart"
            state.started_at = None
            state.completed_at = None
            node = self._node(snapshot.graph, node_id)
            if node.status != "pending":
                node.status = transition_status(scope="node", current=node.status, target="pending")
            requeued.append(node_id)
        self._set_run_status(snapshot, "paused")
        snapshot.updated_at = utc_now()
        control = read_json(self.control_path) or {}
        self._write_control(
            pause_requested=False,
            cancel_requested=bool(control.get("cancel_requested")),
        )
        self._persist(snapshot)
        self._emit(
            "graph_running_nodes_requeued_after_restart",
            node_ids=sorted(requeued),
        )
        return self._result(snapshot)

    def resume(self) -> ExecutionRunResult:
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot resume a run without an execution snapshot")
        # A process crash can leave a node marked running without a live worker.
        # Requeue those nodes before entering the normal scheduler loop.
        recovered = False
        for node_id, state in snapshot.node_states.items():
            if state.status != "running":
                continue
            self._set_node_status(state, "pending")
            state.error = "requeued_after_worker_restart"
            state.started_at = None
            state.completed_at = None
            node = self._node(snapshot.graph, node_id)
            node.status = transition_status(scope="node", current=node.status, target="pending")
            recovered = True
        if recovered:
            self._set_run_status(snapshot, "paused")
            snapshot.updated_at = utc_now()
            self._persist(snapshot)
            self._emit("graph_running_nodes_requeued_after_restart")
        if snapshot.status in {"gated", "completed_partial"}:
            # An explicit resume may re-evaluate gates or newly available
            # inputs. Completed runs remain non-resumable; these two statuses
            # are the recoverable terminal outcomes of a partial Graph run.
            self._set_run_status(snapshot, "paused")
            snapshot.updated_at = utc_now()
            self._persist(snapshot)
        self._pause_requested.clear()
        control = read_json(self.control_path) or {}
        self._write_control(
            pause_requested=False,
            cancel_requested=bool(control.get("cancel_requested")),
        )
        return self.run(
            graph=snapshot.graph,
            run_id=snapshot.run_id,
            thread_id=snapshot.thread_id,
            request_id=snapshot.request_id,
            user_id=snapshot.user_id,
            tenant_id=snapshot.tenant_id,
            resume=True,
        )

    def rerun_from(self, node_id: str) -> ExecutionRunSnapshot:
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot rerun without an execution snapshot")
        rerun_ids = {node_id, *self.scheduler.dependency_descendants(snapshot.graph, node_id)}
        invalidated_artifact_ids = {
            artifact_id
            for rerun_id in rerun_ids
            for artifact_id in (
                snapshot.node_states.get(rerun_id).output_artifact_ids
                if snapshot.node_states.get(rerun_id) is not None
                else []
            )
        }
        invalidated_artifact_ids.update(
            artifact_id
            for rerun_id in rerun_ids
            for artifact_id in self._node(snapshot.graph, rerun_id).output_artifact_ids
        )
        snapshot.invalidated_artifact_ids = list(
            dict.fromkeys([*snapshot.invalidated_artifact_ids, *sorted(invalidated_artifact_ids)])
        )
        self.board.bind_invalidated_artifacts(snapshot.invalidated_artifact_ids)
        for rerun_id in rerun_ids:
            node = self._node(snapshot.graph, rerun_id)
            snapshot.node_states[rerun_id] = NodeExecutionState(node_id=rerun_id)
            node.status = transition_status(scope="node", current=node.status, target="pending")
            node.output_artifact_ids = []
            node.attempt_count = 0
        self._set_run_status(snapshot, "paused")
        snapshot.updated_at = utc_now()
        self._persist(snapshot)
        self._emit(
            "graph_partial_rerun_prepared",
            node_id=node_id,
            rerun_node_ids=sorted(rerun_ids),
            invalidated_artifact_ids=sorted(invalidated_artifact_ids),
        )
        return snapshot

    def replay(self) -> ExecutionRunSnapshot:
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot replay without an execution snapshot")
        events = read_jsonl(self.events_path)
        if not any(event.get("event_type") == "graph_snapshot_created" for event in events):
            raise GraphExecutionError("execution event history is incomplete")
        last_persisted = [event for event in events if event.get("event_type") == "graph_snapshot_persisted"]
        if last_persisted:
            snapshot_event = last_persisted[-1]
        else:
            # A process may have crashed after the durable commit-intent event
            # but before the final acknowledgement. The intent payload is a
            # recoverable snapshot receipt, not an untrusted partial state.
            commit_intents = [
                event
                for event in events
                if event.get("event_type") == "graph_snapshot_commit_started"
            ]
            if not commit_intents:
                raise GraphExecutionError("execution event history has no persisted snapshots")
            snapshot_event = commit_intents[-1]
        replayed = ExecutionRunSnapshot.model_validate(snapshot_event["snapshot"])
        if replayed.model_dump(mode="json") != snapshot.model_dump(mode="json"):
            raise GraphExecutionError("execution replay does not match the persisted snapshot")
        return replayed

    def _new_snapshot(
        self,
        *,
        graph: ExecutionGraph,
        run_id: str,
        thread_id: str,
        request_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ExecutionRunSnapshot:
        graph = self._prepare_approvals(
            graph,
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        validation = self.scheduler.validate(graph)
        if not validation.valid:
            raise GraphExecutionError(f"invalid execution graph: {';'.join(validation.errors)}")
        self._validate_graph_governance(graph, run_id=run_id)
        snapshot = ExecutionRunSnapshot(
            run_id=run_id,
            thread_id=thread_id,
            request_id=request_id,
            user_id=user_id,
            tenant_id=tenant_id,
            graph=graph.model_copy(deep=True),
            node_states={
                node.node_id: NodeExecutionState(node_id=node.node_id, status=node.status)
                for node in graph.nodes
                if node.node_kind == "subagent"
            },
        )
        pending_approvals = self._pending_approval_ids(snapshot)
        if pending_approvals:
            self._set_run_status(snapshot, "waiting_approval")
            self._emit("graph_approval_required", approval_ids=pending_approvals)
        self._emit("graph_snapshot_created", snapshot=snapshot.model_dump(mode="json"))
        return snapshot

    def _validate_graph_governance(self, graph: ExecutionGraph, *, run_id: str) -> None:
        governance = graph.governance
        for node in graph.nodes:
            if node.node_kind != "subagent":
                continue
            unknown_tools = set(node.tools) - set(governance.allowed_tools)
            if unknown_tools:
                raise GraphGovernanceError(
                    f"node requests tools outside sandbox: {node.node_id}:{sorted(unknown_tools)}"
                )
            unknown_permissions = set(node.permissions) - set(governance.allowed_permissions)
            if unknown_permissions:
                raise GraphGovernanceError(
                    f"node requests permissions outside sandbox: {node.node_id}:{sorted(unknown_permissions)}"
                )
            if node.max_attempts > governance.max_retries_per_node + 1:
                raise GraphGovernanceError(
                    f"node retry limit exceeds governance: {node.node_id}"
                )
            if (
                governance.idempotency_required
                and node.side_effect_level in {"write_local", "write_external"}
                and not node.idempotency_key
            ):
                raise GraphGovernanceError(
                    f"side-effecting node requires idempotency key: {node.node_id}"
                )
        estimated_tokens = sum(node.estimated_tokens for node in graph.nodes)
        estimated_cost = sum(node.estimated_cost for node in graph.nodes)
        if governance.token_budget is not None and estimated_tokens > governance.token_budget:
            raise GraphGovernanceError("estimated token budget exceeded")
        if governance.cost_budget is not None and estimated_cost > governance.cost_budget:
            raise GraphGovernanceError("estimated cost budget exceeded")

    def _node_requires_approval(self, node: ExecutionNode) -> bool:
        if node.side_effect_level in {
            "write_local",
            "write_external",
        }:
            return True
        if self.tool_registry is None:
            return False
        for tool_name in node.tools:
            try:
                if self.tool_registry.definition(tool_name).requires_approval:
                    return True
            except Exception:
                continue
        return False

    def _prepare_approvals(
        self,
        graph: ExecutionGraph,
        *,
        run_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ExecutionGraph:
        if not graph.governance.require_human_approval_for_high_risk:
            return graph
        changed = False
        nodes: list[ExecutionNode] = []
        for node in graph.nodes:
            if node.node_kind != "subagent" or not self._node_requires_approval(node):
                nodes.append(node)
                continue
            approval_id = node.approval_id or f"approval_{generate_ulid()}"
            domain_version = str(graph.metadata.get("domain_version") or "").strip() or None
            policy_fingerprint = approval_policy_fingerprint(graph, node)
            existing = self.approvals.get(approval_id)
            bound = existing is not None and self.approvals.validate(
                approval_id=approval_id,
                run_id=run_id,
                node_id=node.node_id,
                capability=str(node.capability),
                graph_revision=graph.revision,
                domain_id=graph.domain_id,
                domain_version=domain_version,
                user_id=user_id,
                tenant_id=tenant_id,
                policy_fingerprint=policy_fingerprint,
                require_binding=True,
            )
            if existing is not None and existing.status == "pending" and not bound:
                # Rebind an unapproved request to the current graph revision;
                # approvals are only decisions, so no human action is lost.
                self.approvals.request(
                    approval_id=approval_id,
                    run_id=run_id,
                    node_id=node.node_id,
                    capability=str(node.capability),
                    reason=(
                        f"高风险 Agent OS 节点 {node.node_id} "
                        f"({node.capability}) 需要审批。"
                    ),
                    graph_revision=graph.revision,
                    domain_id=graph.domain_id,
                    domain_version=domain_version,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    policy_fingerprint=policy_fingerprint,
                )
            elif existing is None:
                self.approvals.request(
                    approval_id=approval_id,
                    run_id=run_id,
                    node_id=node.node_id,
                    capability=str(node.capability),
                    reason=(
                        f"高风险 Agent OS 节点 {node.node_id} "
                        f"({node.capability}) 需要审批。"
                    ),
                    graph_revision=graph.revision,
                    domain_id=graph.domain_id,
                    domain_version=domain_version,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    policy_fingerprint=policy_fingerprint,
                )
            elif existing.status == "approved" and not bound:
                # A prior decision must never silently authorize an unbound or
                # changed graph, Pack version, policy, or identity scope.
                approval_id = f"approval_{generate_ulid()}"
                node = node.model_copy(update={"approval_id": approval_id})
                changed = True
                self.approvals.request(
                    approval_id=approval_id,
                    run_id=run_id,
                    node_id=node.node_id,
                    capability=str(node.capability),
                    reason=(
                        f"节点 {node.node_id} ({node.capability}) 的治理上下文已变更，"
                        "需要重新审批。"
                    ),
                    graph_revision=graph.revision,
                    domain_id=graph.domain_id,
                    domain_version=domain_version,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    policy_fingerprint=policy_fingerprint,
                )
            if approval_id != node.approval_id:
                node = node.model_copy(update={"approval_id": approval_id})
                changed = True
            nodes.append(node)
        if not changed:
            return graph
        return graph.model_copy(update={"nodes": nodes})

    def _pending_approval_ids_for_graph(
        self,
        graph: ExecutionGraph,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[str]:
        if not graph.governance.require_human_approval_for_high_risk:
            return []
        pending: list[str] = []
        for node in graph.nodes:
            if node.node_kind != "subagent" or not self._node_requires_approval(node):
                continue
            if node.approval_id and not self.approvals.validate(
                approval_id=node.approval_id,
                run_id=run_id,
                node_id=node.node_id,
                capability=str(node.capability),
                graph_revision=graph.revision,
                domain_id=graph.domain_id,
                domain_version=str(graph.metadata.get("domain_version") or "") or None,
                user_id=user_id,
                tenant_id=tenant_id,
                policy_fingerprint=approval_policy_fingerprint(graph, node),
                require_binding=True,
            ):
                pending.append(node.approval_id)
        return pending

    def _pending_approval_ids(self, snapshot: ExecutionRunSnapshot) -> list[str]:
        return self._pending_approval_ids_for_graph(
            snapshot.graph,
            snapshot.run_id,
            user_id=snapshot.user_id,
            tenant_id=snapshot.tenant_id,
        )

    def _rejected_approval_ids(self, snapshot: ExecutionRunSnapshot) -> list[str]:
        rejected: list[str] = []
        for node in snapshot.graph.nodes:
            if node.node_kind != "subagent" or not self._node_requires_approval(node):
                continue
            if not node.approval_id:
                continue
            approval = self.approvals.get(node.approval_id)
            if approval is not None and approval.status in {"rejected", "revoked", "expired"}:
                rejected.append(node.approval_id)
        return rejected

    def _mark_approval_rejection(self, snapshot: ExecutionRunSnapshot) -> None:
        approval_ids = self._rejected_approval_ids(snapshot)
        self._set_run_status(snapshot, "gated")
        existing = {item.gate_id for item in snapshot.gate_evaluations}
        for approval_id in approval_ids:
            gate_id = f"approval:{approval_id}"
            if gate_id in existing:
                continue
            snapshot.gate_evaluations.append(
                GateEvaluation(
                    gate_id=gate_id,
                    artifact_id="approval",
                    verdict="fail",
                    reason="人工审批已被拒绝、撤销或过期。",
                    blocking=True,
                )
            )
        self._emit("graph_run_gated_by_approval", approval_ids=approval_ids)

    @staticmethod
    def _graph_domain_version(snapshot: ExecutionRunSnapshot) -> str | None:
        value = str(snapshot.graph.metadata.get("domain_version") or "").strip()
        return value or None

    @classmethod
    def _node_domain_version(
        cls,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
    ) -> str | None:
        value = str(node.metadata.get("domain_version") or "").strip()
        return value or cls._graph_domain_version(snapshot)

    def _seed_artifacts(
        self,
        snapshot: ExecutionRunSnapshot,
        drafts: list[ArtifactDraft],
    ) -> None:
        domain_version = self._graph_domain_version(snapshot)
        declared = {
            item.artifact_type
            for item in self.registry.get(
                snapshot.graph.domain_id,
                version=domain_version,
            ).artifact_types
        }
        for draft in drafts:
            if draft.artifact_type not in declared:
                raise GraphExecutionError(
                    f"seed artifact is not declared by domain pack: {draft.artifact_type}"
                )
            try:
                jsonschema.validate(
                    instance=draft.content,
                    schema=self.registry.artifact_schema(
                        domain_id=snapshot.graph.domain_id,
                        artifact_type=draft.artifact_type,
                        version=domain_version,
                    ),
                )
            except jsonschema.ValidationError as exc:
                raise GraphExecutionError(
                    f"seed artifact schema validation failed: {draft.artifact_type}:{exc.message}"
                ) from exc
            self.board.add(
                ArtifactRecord(
                    run_id=snapshot.run_id,
                    thread_id=snapshot.thread_id,
                    request_id=snapshot.request_id,
                    user_id=snapshot.user_id,
                    tenant_id=snapshot.tenant_id,
                    artifact_type=draft.artifact_type,
                    schema_version=draft.schema_version,
                    producer=ArtifactProducer(
                        executor="user",
                        capability="task.intake",
                        domain_id=snapshot.graph.domain_id,
                    ),
                    content=draft.content,
                    source_refs=(
                        draft.source_refs
                        or [{"type": "task_intake", "id": snapshot.request_id}]
                    ),
                    parent_artifact_ids=draft.parent_artifact_ids,
                    confidence=draft.confidence,
                    risk_flags=draft.risk_flags,
                    status=draft.status,
                    metadata=draft.metadata,
                )
            )

    def _execute_frontier(
        self,
        snapshot: ExecutionRunSnapshot,
        frontier: list[ExecutionNode],
    ) -> list[_NodeOutcome]:
        if len(frontier) == 1:
            return [self._execute_node(snapshot, frontier[0])]
        outcomes: list[_NodeOutcome] = []
        with ThreadPoolExecutor(
            max_workers=min(len(frontier), snapshot.graph.governance.max_concurrency),
            thread_name_prefix="cognitrade-graph",
        ) as pool:
            futures: dict[Future[_NodeOutcome], ExecutionNode] = {
                pool.submit(self._execute_node, snapshot, node): node for node in frontier
            }
            outcomes.extend(future.result() for future in as_completed(futures))
        order = {node.node_id: index for index, node in enumerate(frontier)}
        outcomes.sort(key=lambda item: order[item.node.node_id])
        return outcomes

    def _execute_node(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
    ) -> _NodeOutcome:
        cancellation = Event()
        with self._lock:
            self._node_cancellations[node.node_id] = cancellation
        state = snapshot.node_states[node.node_id].model_copy(deep=True)
        self._set_node_status(state, "running")
        node.status = transition_status(scope="node", current=node.status, target="running")
        state.started_at = utc_now()
        self._validate_node_authorization(snapshot, node)
        inputs = self._select_input_artifacts(snapshot, node)
        state.input_artifact_ids = [artifact.artifact_id for artifact in inputs]
        follow_up_messages = list(node.metadata.get("pending_followups") or [])
        self._emit(
            "graph_node_started",
            node_id=node.node_id,
            input_artifact_ids=state.input_artifact_ids,
        )
        self._emit(
            "agent_progress",
            node_id=node.node_id,
            actor=f"subagent:{node.node_id}",
            message=f"Subagent {node.capability} started.",
        )
        last_error: Exception | None = None
        try:
            idempotent_output = self._load_idempotency_output(snapshot, node)
            if idempotent_output is not None:
                artifacts = self._commit_outputs(snapshot, node, inputs, idempotent_output)
                state.output_artifact_ids = [artifact.artifact_id for artifact in artifacts]
                state.usage = idempotent_output.usage
                self._set_node_status(state, "completed")
                state.completed_at = utc_now()
                node.status = transition_status(scope="node", current=node.status, target="completed")
                node.input_artifact_ids = list(state.input_artifact_ids)
                node.output_artifact_ids = list(state.output_artifact_ids)
                self._emit(
                    "graph_node_idempotency_hit",
                    node_id=node.node_id,
                    idempotency_key=node.idempotency_key,
                    output_artifact_ids=state.output_artifact_ids,
                )
                return _NodeOutcome(node=node, state=state, output=idempotent_output)
            cached_output = self._load_cached_output(snapshot, node, inputs)
            if cached_output is not None:
                artifacts = self._commit_outputs(snapshot, node, inputs, cached_output)
                state.output_artifact_ids = [artifact.artifact_id for artifact in artifacts]
                state.usage = cached_output.usage
                self._set_node_status(state, "completed")
                state.completed_at = utc_now()
                node.status = transition_status(scope="node", current=node.status, target="completed")
                node.input_artifact_ids = list(state.input_artifact_ids)
                node.output_artifact_ids = list(state.output_artifact_ids)
                self._emit(
                    "graph_node_cache_hit",
                    node_id=node.node_id,
                    output_artifact_ids=state.output_artifact_ids,
                )
                return _NodeOutcome(node=node, state=state, output=cached_output)
            executor = self.registry.resolve_executor(
                domain_id=snapshot.graph.domain_id,
                capability=str(node.capability),
                version=self._node_domain_version(snapshot, node),
                subagent_type=str(node.executor or "").strip() or None,
            )
            for attempt in range(state.attempt_count + 1, node.max_attempts + 1):
                state.attempt_count = attempt
                if self._cancel_requested.is_set() or cancellation.is_set():
                    raise GraphCancelledError(f"node cancelled: {node.node_id}")
                started = perf_counter()
                try:
                    injected_reason = self._consume_failure_injection(
                        "capability",
                        str(node.capability),
                    )
                    if injected_reason:
                        raise GraphExecutionError(
                            f"injected capability failure for {node.capability}: {injected_reason}"
                        )
                    context = ExecutionNodeContext(
                        run_id=snapshot.run_id,
                        thread_id=snapshot.thread_id,
                        request_id=snapshot.request_id,
                        user_id=snapshot.user_id,
                        tenant_id=snapshot.tenant_id,
                        graph=snapshot.graph,
                        node=node,
                        input_artifacts=inputs,
                        artifact_board=self.board,
                        cache_root=self.cache_root,
                        cancellation=cancellation,
                        tool_registry=self.tool_registry,
                        skill_runtime=self._skill_runtime_for(snapshot, node),
                        memory_store=self.memory_store,
                        settings=self.settings,
                        llm_provider=self.llm_provider,
                        event_callback=self.event_callback,
                        turn_id=self._turn_id,
                        progress_observer=self.progress_observer,
                        progress_emitter=self._emit_observer_progress,
                        authorize_tool_call=lambda: self._authorize_tool_call(snapshot),
                        tool_semaphore=self._tool_semaphore,
                        approval_store=self.approvals,
                        failure_injection=self._consume_failure_injection,
                        follow_up_messages=follow_up_messages,
                    )
                    raw_output = self._execute_node_executor(
                        executor,
                        context,
                        timeout_seconds=self._node_timeout_seconds(node),
                    )
                    output = self._normalize_output(raw_output)
                    input_contract = self._evaluate_method_input_contract(node, inputs)
                    if input_contract is not None:
                        self._annotate_method_input_contract(output, input_contract)
                        self._emit(
                            "method_input_contract_evaluated",
                            node_id=node.node_id,
                            capability=node.capability,
                            status=input_contract["status"],
                            missing_fields=input_contract["missing_fields"],
                        )
                    elapsed = perf_counter() - started
                    output.usage.duration_seconds += elapsed
                    state.usage = self._add_usage(state.usage, output.usage)
                    for message in output.messages:
                        text = str(message or "").strip()
                        if not text:
                            continue
                        self._emit(
                            "agent_progress",
                            node_id=node.node_id,
                            actor=f"subagent:{node.node_id}",
                            message=text,
                            attempt=attempt,
                        )
                    self._reserve_output_budget(snapshot, node, output)
                    try:
                        artifacts = self._commit_outputs(snapshot, node, inputs, output)
                    except Exception:
                        self._release_output_budget(output)
                        raise
                    state.output_artifact_ids = [artifact.artifact_id for artifact in artifacts]
                    self._set_node_status(state, "completed")
                    state.completed_at = utc_now()
                    node.status = transition_status(scope="node", current=node.status, target="completed")
                    node.attempt_count = state.attempt_count
                    node.input_artifact_ids = list(state.input_artifact_ids)
                    node.output_artifact_ids = list(state.output_artifact_ids)
                    self._save_cached_output(snapshot, node, inputs, output)
                    self._save_idempotency_output(snapshot, node, output)
                    self._emit(
                        "graph_node_completed",
                        node_id=node.node_id,
                        attempt=attempt,
                        output_artifact_ids=state.output_artifact_ids,
                        usage=output.usage.model_dump(mode="json"),
                    )
                    self._emit(
                        "agent_progress",
                        node_id=node.node_id,
                        actor=f"subagent:{node.node_id}",
                        message=(
                            f"Subagent {node.capability} completed and committed "
                            f"{len(artifacts)} Artifact(s)."
                        ),
                        artifact_ids=state.output_artifact_ids,
                    )
                    return _NodeOutcome(node=node, state=state, output=output)
                except GraphCancelledError:
                    raise
                except GraphNodeTimeoutError as exc:
                    last_error = exc
                    self._emit(
                        "graph_node_attempt_failed",
                        node_id=node.node_id,
                        attempt=attempt,
                        error=f"{exc.__class__.__name__}: {exc}",
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    self._emit(
                        "graph_node_attempt_failed",
                        node_id=node.node_id,
                        attempt=attempt,
                        error=f"{exc.__class__.__name__}: {exc}",
                    )
                    if attempt >= node.max_attempts:
                        break
            self._set_node_status(state, "failed")
            state.error = f"{last_error.__class__.__name__}: {last_error}" if last_error else "unknown"
            state.completed_at = utc_now()
            node.status = transition_status(scope="node", current=node.status, target="failed")
            node.attempt_count = state.attempt_count
            self._emit("graph_node_failed", node_id=node.node_id, error=state.error)
            self._emit(
                "agent_progress",
                node_id=node.node_id,
                actor=f"subagent:{node.node_id}",
                message=f"子 Agent {node.capability} 执行失败。",
            )
            return _NodeOutcome(node=node, state=state, error=last_error)
        except GraphCancelledError as exc:
            self._set_node_status(state, "cancelled")
            state.error = str(exc)
            state.completed_at = utc_now()
            node.status = transition_status(scope="node", current=node.status, target="cancelled")
            return _NodeOutcome(node=node, state=state, error=exc)
        finally:
            with self._lock:
                self._node_cancellations.pop(node.node_id, None)

    def _skill_runtime_for(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
    ) -> Any | None:
        if self.skill_runtime_factory is None:
            return None
        return self.skill_runtime_factory(
            snapshot.graph.domain_id,
            self._node_domain_version(snapshot, node),
        )

    def _select_input_artifacts(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
    ) -> list[ArtifactRecord]:
        selected: list[ArtifactRecord] = []
        for port in node.consumes:
            if node.input_artifact_ids:
                candidates = [
                    artifact
                    for artifact_id in node.input_artifact_ids
                    for artifact in [self.board.get(artifact_id)]
                    if artifact is not None
                    and artifact.status == "active"
                    and artifact.artifact_type == port.artifact_type
                ]
            else:
                candidates = self.board.list_artifacts(
                    run_id=snapshot.run_id,
                    artifact_type=port.artifact_type,
                    status="active",
                )
            candidates = [
                artifact
                for artifact in candidates
                if self._artifact_matches_selector(artifact, port.selector)
            ]
            producer_node_id = str(port.selector.get("producer_node_id") or "").strip()
            if producer_node_id:
                candidates = [
                    artifact
                    for artifact in candidates
                    if artifact.producer.node_id == producer_node_id
                ]
            if not candidates and port.required:
                raise GraphExecutionError(
                    f"node input contract is unsatisfied: {node.node_id}:{port.artifact_type}"
                )
            selected.extend(candidates if port.multiple else candidates[-1:])
        return list({artifact.artifact_id: artifact for artifact in selected}.values())

    @staticmethod
    def _artifact_matches_selector(
        artifact: ArtifactRecord,
        selector: dict[str, Any] | None,
    ) -> bool:
        selector = dict(selector or {})
        content = artifact.content if isinstance(artifact.content, dict) else {}
        metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
        for key, expected in selector.items():
            if key == "producer_node_id":
                continue
            if key == "dimensions":
                actual = str(
                    content.get("snapshot_type")
                    or content.get("dimension")
                    or metadata.get("snapshot_type")
                    or metadata.get("dimension")
                    or ""
                )
                if actual not in {str(item) for item in list(expected or [])}:
                    return False
                continue
            if key == "quality_status":
                actual = str(
                    content.get("quality_status")
                    or metadata.get("quality_status")
                    or ""
                )
                if actual not in {str(item) for item in list(expected or [])}:
                    return False
                continue
            actual = content.get(key, metadata.get(key, ""))
            if str(actual or "") != str(expected):
                return False
        return True

    @staticmethod
    def _node_timeout_seconds(node: ExecutionNode) -> float | None:
        raw = (node.metadata or {}).get("timeout_seconds")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _execute_node_executor(
        executor: Callable,
        context: Any,
        *,
        timeout_seconds: float | None,
    ) -> Any:
        if timeout_seconds is None:
            return executor(context)
        if not bool(getattr(executor, "supports_timeout", False)):
            raise GraphNodeTimeoutError(
                "executor does not support a governed node timeout"
            )
        pool = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cognitrade-graph-node-timeout",
        )
        future = pool.submit(executor, context)
        timed_out = False
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            cancellation = getattr(context, "cancellation", None)
            if cancellation is not None:
                cancellation.set()
            # Python cannot kill an already-running thread. Wait for a
            # cooperative executor to leave before GraphRunner reports the
            # failure, preventing retries or later nodes from overlapping it.
            timed_out = True
            pool.shutdown(wait=True, cancel_futures=True)
            raise GraphNodeTimeoutError(
                f"graph_node_timeout: {timeout_seconds}s"
            ) from exc
        finally:
            if not timed_out:
                pool.shutdown(wait=False, cancel_futures=True)

    def _normalize_output(self, output: Any) -> NodeExecutionOutput:
        if isinstance(output, NodeExecutionOutput):
            return output
        if isinstance(output, ArtifactDraft):
            return NodeExecutionOutput(artifacts=[output])
        if isinstance(output, list):
            return NodeExecutionOutput(
                artifacts=[
                    item if isinstance(item, ArtifactDraft) else ArtifactDraft.model_validate(item)
                    for item in output
                ]
            )
        if isinstance(output, dict):
            if (
                "artifacts" in output
                or "usage" in output
                or "notes" in output
                or "messages" in output
                or "control_requests" in output
            ):
                return NodeExecutionOutput.model_validate(output)
            return NodeExecutionOutput(artifacts=[ArtifactDraft.model_validate(output)])
        raise TypeError(f"unsupported capability output: {type(output).__name__}")

    def _evaluate_method_input_contract(
        self,
        node: ExecutionNode,
        inputs: list[ArtifactRecord],
    ) -> dict[str, Any] | None:
        requirements = [
            item for item in list(node.metadata.get("method_input_requirements") or [])
            if isinstance(item, dict)
        ]
        if not requirements:
            return None
        missing_fields: list[str] = []
        quality_violations: list[str] = []
        checked_fields: list[str] = []
        for requirement in requirements:
            artifact_type = str(requirement.get("artifact_type") or "").strip()
            selector = dict(requirement.get("selector") or {})
            candidates = [
                artifact
                for artifact in inputs
                if (not artifact_type or artifact.artifact_type == artifact_type)
                and self._artifact_matches_selector(artifact, selector)
            ]
            fields = [
                field for field in list(requirement.get("required_fields") or [])
                if isinstance(field, dict)
            ]
            if not candidates:
                missing_fields.extend(
                    f"{artifact_type or 'artifact'}.{field.get('path') or 'required_field'}"
                    for field in fields
                    if bool(field.get("source_required", True))
                )
                continue
            allowed_quality = {
                str(item).strip().lower()
                for item in list(requirement.get("allowed_quality_statuses") or ["available"])
            }
            for artifact in candidates:
                quality = self._artifact_content_quality(artifact)
                if quality not in allowed_quality:
                    quality_violations.append(f"{artifact.artifact_type}:{quality}")
            for field in fields:
                if not bool(field.get("source_required", True)):
                    continue
                path = str(field.get("path") or "").strip()
                aliases = [str(item).strip() for item in list(field.get("aliases") or []) if str(item).strip()]
                checked_fields.append(path)
                if not any(
                    self._field_present(artifact.content, candidate_path)
                    for artifact in candidates
                    for candidate_path in [path, *aliases]
                ):
                    missing_fields.append(path)
        missing_fields = list(dict.fromkeys(item for item in missing_fields if item))
        quality_violations = list(dict.fromkeys(item for item in quality_violations if item))
        status = "available"
        if missing_fields:
            status = "data_gap"
        elif quality_violations:
            status = "degraded"
        return {
            "status": status,
            "missing_fields": missing_fields,
            "quality_violations": quality_violations,
            "checked_fields": checked_fields,
        }

    @staticmethod
    def _field_present(content: dict[str, Any], path: str) -> bool:
        current: Any = content
        for part in [item for item in str(path).split(".") if item]:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return current is not None and current != "" and current != []

    @staticmethod
    def _artifact_content_quality(artifact: ArtifactRecord) -> str:
        content = artifact.content if isinstance(artifact.content, dict) else {}
        declared = str(
            content.get("quality_status")
            or artifact.metadata.get("quality_status")
            or "available"
        ).strip().lower()
        if declared in {"available", "degraded", "data_gap", "assumption_only"}:
            return declared
        if list(content.get("data_gap") or []):
            return "degraded"
        return "available"

    @staticmethod
    def _annotate_method_input_contract(
        output: NodeExecutionOutput,
        contract: dict[str, Any],
    ) -> None:
        if contract.get("status") == "available":
            return
        for draft in output.artifacts:
            content = dict(draft.content or {})
            content["method_input_contract"] = dict(contract)
            current = str(content.get("quality_status") or "available").strip().lower()
            if contract.get("status") == "data_gap":
                content["quality_status"] = "data_gap" if current == "available" else current
            elif current == "available":
                content["quality_status"] = "degraded"
            draft.content = content
            draft.risk_flags = list(
                dict.fromkeys([*draft.risk_flags, "method_input_contract_gap"])
            )

    def _commit_outputs(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
        inputs: list[ArtifactRecord],
        output: NodeExecutionOutput,
    ) -> list[ArtifactRecord]:
        ports = {port.artifact_type: port for port in node.produces}
        counts: dict[str, int] = {}
        committed: list[ArtifactRecord] = []
        default_parents = [artifact.artifact_id for artifact in inputs]
        for draft in output.artifacts:
            port = ports.get(draft.artifact_type)
            if port is None:
                raise GraphExecutionError(
                    f"node emitted undeclared artifact: {node.node_id}:{draft.artifact_type}"
                )
            if draft.schema_version != port.schema_version:
                raise GraphExecutionError(
                    f"artifact schema version mismatch: {node.node_id}:{draft.artifact_type}"
                )
            try:
                jsonschema.validate(
                    instance=draft.content,
                    schema=self.registry.artifact_schema(
                        domain_id=snapshot.graph.domain_id,
                        artifact_type=draft.artifact_type,
                        version=self._node_domain_version(snapshot, node),
                    ),
                )
            except jsonschema.ValidationError as exc:
                raise GraphExecutionError(
                    f"artifact schema validation failed: {node.node_id}:{draft.artifact_type}:{exc.message}"
                ) from exc
            counts[draft.artifact_type] = counts.get(draft.artifact_type, 0) + 1
            if counts[draft.artifact_type] > 1 and not port.multiple:
                raise GraphExecutionError(
                    f"node emitted multiple artifacts for a singular port: {node.node_id}:{draft.artifact_type}"
                )
            parents = draft.parent_artifact_ids or default_parents
            inherited_source_refs = list(
                {
                    json.dumps(source_ref, sort_keys=True): source_ref
                    for artifact in inputs
                    for source_ref in artifact.source_refs
                }.values()
            )
            source_refs = draft.source_refs or inherited_source_refs
            if snapshot.graph.governance.require_source_refs and not source_refs:
                raise GraphGovernanceError(
                    f"professional artifact has no provenance: {node.node_id}:{draft.artifact_type}"
                )
            if snapshot.graph.governance.require_confidence and draft.confidence is None:
                raise GraphGovernanceError(
                    f"professional artifact has no confidence: {node.node_id}:{draft.artifact_type}"
                )
            previous = self.board.list_artifacts(
                run_id=snapshot.run_id,
                artifact_type=draft.artifact_type,
                node_id=node.node_id,
                status="active",
                include_invalidated=True,
            )
            supersedes_artifact_id = draft.supersedes_artifact_id
            if supersedes_artifact_id is None and previous:
                supersedes_artifact_id = previous[-1].artifact_id
            committed.append(
                ArtifactRecord(
                    run_id=snapshot.run_id,
                    thread_id=snapshot.thread_id,
                    request_id=snapshot.request_id,
                    user_id=snapshot.user_id,
                    tenant_id=snapshot.tenant_id,
                    artifact_type=draft.artifact_type,
                    schema_version=draft.schema_version,
                    producer=ArtifactProducer(
                        executor=str(node.executor),
                        capability=str(node.capability),
                        node_id=node.node_id,
                        subagent_type=str(node.executor),
                        domain_id=snapshot.graph.domain_id,
                        responsibility_node_id=node.node_id,
                    ),
                    content=draft.content,
                    source_refs=source_refs,
                    parent_artifact_ids=parents,
                    confidence=draft.confidence,
                    risk_flags=draft.risk_flags,
                    status=draft.status,
                    supersedes_artifact_id=supersedes_artifact_id,
                    metadata=draft.metadata,
                )
            )
        missing = [
            port.artifact_type
            for port in node.produces
            if port.required and counts.get(port.artifact_type, 0) == 0
        ]
        if missing:
            raise GraphExecutionError(
                f"node did not satisfy output contract: {node.node_id}:{','.join(missing)}"
            )
        persisted = self.board.add_many(committed)
        self._emit(
            "artifact_committed",
            node_id=node.node_id,
            artifact_ids=[artifact.artifact_id for artifact in persisted],
            artifact_types=[artifact.artifact_type for artifact in persisted],
        )
        for artifact in persisted:
            summary = self._artifact_progress_summary(artifact)
            if not summary:
                continue
            self._emit(
                "agent_progress",
                node_id=node.node_id,
                actor=f"subagent:{node.node_id}",
                message=summary,
                artifact_id=artifact.artifact_id,
                artifact_type=artifact.artifact_type,
            )
        return persisted

    @staticmethod
    def _artifact_progress_summary(artifact: ArtifactRecord) -> str:
        """Expose only authored artifact summaries, never model scratch reasoning."""

        content = artifact.content if isinstance(artifact.content, dict) else {}
        for key in (
            "summary",
            "conclusion",
            "analysis",
            "recommendation",
            "message",
        ):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                text = " ".join(value.split())
                return f"{artifact.artifact_type}: {text[:600]}"
        return ""

    def _reserve_output_budget(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
        output: NodeExecutionOutput,
    ) -> None:
        governance = snapshot.graph.governance
        with self._lock:
            tokens = self._reserved_tokens + output.usage.tokens
            cost = self._reserved_cost + output.usage.cost
            if governance.token_budget is not None and tokens > governance.token_budget:
                raise GraphGovernanceError(f"token budget exceeded by node: {node.node_id}")
            if governance.cost_budget is not None and cost > governance.cost_budget:
                raise GraphGovernanceError(f"cost budget exceeded by node: {node.node_id}")
            self._reserved_tokens = tokens
            self._reserved_cost = cost

    def _authorize_tool_call(self, snapshot: ExecutionRunSnapshot) -> bool:
        with self._lock:
            limit = snapshot.graph.governance.max_tool_calls
            if snapshot.tool_call_count >= limit:
                self._emit(
                    "tool_call_budget_rejected",
                    tool_call_count=snapshot.tool_call_count,
                    max_tool_calls=limit,
                )
                return False
            snapshot.tool_call_count += 1
            return True

    def _release_output_budget(self, output: NodeExecutionOutput) -> None:
        with self._lock:
            self._reserved_tokens = max(self._reserved_tokens - output.usage.tokens, 0)
            self._reserved_cost = max(self._reserved_cost - output.usage.cost, 0.0)

    def _enforce_total_budget(self, snapshot: ExecutionRunSnapshot) -> None:
        governance = snapshot.graph.governance
        if governance.token_budget is not None and snapshot.usage.tokens > governance.token_budget:
            raise GraphGovernanceError("token budget exceeded")
        if governance.cost_budget is not None and snapshot.usage.cost > governance.cost_budget:
            raise GraphGovernanceError("cost budget exceeded")

    def _evaluate_gates(self, snapshot: ExecutionRunSnapshot) -> list[GateEvaluation]:
        manifest = self.registry.get(
            snapshot.graph.domain_id,
            version=self._graph_domain_version(snapshot),
        )
        evaluations: list[GateEvaluation] = []
        gates = {gate.gate_id: gate for gate in manifest.quality_gates}
        required_types = set(snapshot.graph.metadata.get("required_artifact_types") or [])
        for contract in manifest.report_contracts:
            if required_types and contract.artifact_type not in required_types:
                continue
            if (
                not required_types
                and not (set(contract.required_gate_ids) & set(snapshot.graph.governance.required_gate_ids))
                and not self.board.list_artifacts(
                    run_id=snapshot.run_id,
                    artifact_type=contract.artifact_type,
                    status="active",
                )
            ):
                continue
            artifacts = self.board.list_artifacts(
                run_id=snapshot.run_id,
                artifact_type=contract.artifact_type,
                status="active",
            )
            if not artifacts:
                evaluations.extend(
                    GateEvaluation(
                        gate_id=gate_id,
                        artifact_id="missing",
                        verdict="fail",
                        reason=f"缺少必需交付物：{contract.artifact_type}",
                        blocking=True,
                    )
                    for gate_id in contract.required_gate_ids or [f"{contract.contract_id}.present"]
                )
                continue
            for artifact in artifacts:
                missing_sections = [
                    section
                    for section in contract.required_sections
                    if section not in artifact.content
                ]
                for gate_id in contract.required_gate_ids:
                    gate = gates[gate_id]
                    reasons: list[str] = []
                    if missing_sections:
                        reasons.append(f"缺少章节：{','.join(missing_sections)}")
                    if contract.required_source_refs and not artifact.source_refs:
                        reasons.append("缺少来源引用")
                    if artifact.status in {"needs_review", "rejected"}:
                        reasons.append(f"artifact 状态为 {artifact.status}")
                    evaluator_result = None
                    if not reasons and gate.evaluator_capability:
                        evaluator_result = self._run_gate_evaluator(
                            snapshot=snapshot,
                            gate=gate,
                            artifact=artifact,
                            artifacts=artifacts,
                        )
                        if evaluator_result.verdict != "pass":
                            reasons.append(evaluator_result.reason)
                    evaluations.append(
                        GateEvaluation(
                            gate_id=gate_id,
                            artifact_id=artifact.artifact_id,
                            verdict="fail" if reasons else "pass",
                            reason=(
                                "; ".join(reasons)
                                or (evaluator_result.reason if evaluator_result else None)
                                or gate.description
                                or "质量门禁已通过"
                            ),
                            blocking=gate.blocking,
                            evidence_refs=(
                                list(evaluator_result.evidence_refs)
                                if evaluator_result is not None
                                else list(artifact.source_refs)
                            ),
                            score=(evaluator_result.score if evaluator_result else None),
                            evaluator=gate.evaluator_capability,
                        )
                    )
        required = set(snapshot.graph.governance.required_gate_ids)
        evaluated = {item.gate_id for item in evaluations}
        # Method contracts can require gates for artifacts that are not wrapped
        # in a ReportContract (for example a DCF-only request). Evaluate those
        # gates directly against their declared artifact type.
        for gate_id in sorted(required - evaluated):
            gate = gates.get(gate_id)
            if gate is None:
                evaluations.append(
                    GateEvaluation(
                        gate_id=gate_id,
                        artifact_id="missing",
                        verdict="fail",
                        reason="Pack 未声明必需的质量门禁。",
                        blocking=True,
                    )
                )
                continue
            artifacts = self.board.list_artifacts(
                run_id=snapshot.run_id,
                artifact_type=gate.artifact_type,
                status="active",
            )
            if not artifacts:
                evaluations.append(
                    GateEvaluation(
                        gate_id=gate_id,
                        artifact_id="missing",
                        verdict="fail",
                        reason=f"缺少质量门禁所需的 artifact：{gate.artifact_type}",
                        blocking=gate.blocking,
                    )
                )
                continue
            for artifact in artifacts:
                evaluator_result = (
                    self._run_gate_evaluator(
                        snapshot=snapshot,
                        gate=gate,
                        artifact=artifact,
                        artifacts=artifacts,
                    )
                    if gate.evaluator_capability
                    else None
                )
                reasons: list[str] = []
                if artifact.status in {"needs_review", "rejected"}:
                    reasons.append(f"artifact 状态为 {artifact.status}")
                if evaluator_result is not None and evaluator_result.verdict != "pass":
                    reasons.append(evaluator_result.reason)
                evaluations.append(
                    GateEvaluation(
                        gate_id=gate_id,
                        artifact_id=artifact.artifact_id,
                        verdict="fail" if reasons else "pass",
                        reason="；".join(reasons) or (evaluator_result.reason if evaluator_result else gate.description or "质量门禁已通过"),
                        blocking=gate.blocking,
                        evidence_refs=(
                            list(evaluator_result.evidence_refs)
                            if evaluator_result is not None
                            else list(artifact.source_refs)
                        ),
                        score=evaluator_result.score if evaluator_result is not None else None,
                        evaluator=gate.evaluator_capability,
                    )
                )
        evaluated = {item.gate_id for item in evaluations}
        evaluations.extend(
            GateEvaluation(
                gate_id=gate_id,
                artifact_id="missing",
                verdict="fail",
                reason="必需的治理门禁尚未完成评估。",
                blocking=True,
            )
            for gate_id in sorted(required - evaluated)
        )
        return evaluations

    def _run_gate_evaluator(
        self,
        *,
        snapshot: ExecutionRunSnapshot,
        gate,
        artifact: ArtifactRecord,
        artifacts: list[ArtifactRecord],
    ) -> GateEvaluationResult:
        """Run the Pack evaluator and fail closed when its contract is broken."""

        capability = str(gate.evaluator_capability or "").strip()
        try:
            injected_reason = self._consume_failure_injection("gate", gate.gate_id)
            if injected_reason:
                self._emit(
                    "gate_evaluator_injected_failure",
                    gate_id=gate.gate_id,
                    evaluator=capability,
                    artifact_id=artifact.artifact_id,
                    reason=injected_reason,
                )
                return GateEvaluationResult(
                    verdict="fail",
                    reason=f"已注入质量门禁失败：{injected_reason}",
                    evidence_refs=list(artifact.source_refs),
                )
            evaluator = self.registry.resolve_executor(
                domain_id=snapshot.graph.domain_id,
                capability=capability,
                version=self._graph_domain_version(snapshot),
            )
            raw = evaluator(
                GateEvaluationContext(
                    run_id=snapshot.run_id,
                    thread_id=snapshot.thread_id,
                    request_id=snapshot.request_id,
                    graph=snapshot.graph,
                    artifact=artifact,
                    artifacts=tuple(artifacts),
                    artifact_board=self.board,
                    config=dict(gate.config),
                )
            )
            result = GateEvaluationResult.model_validate(raw)
            self._emit(
                "gate_evaluator_completed",
                gate_id=gate.gate_id,
                evaluator=capability,
                artifact_id=artifact.artifact_id,
                verdict=result.verdict,
                evidence_refs=result.evidence_refs,
            )
            return result
        except Exception as exc:
            self._emit(
                "gate_evaluator_failed",
                gate_id=gate.gate_id,
                evaluator=capability,
                artifact_id=artifact.artifact_id,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            return GateEvaluationResult(
                verdict="needs_review",
                reason="质量门禁评估器执行失败，已按不通过处理。",
            )

    def _consume_failure_injection(self, kind: str, target: str) -> str | None:
        with self._lock:
            for injection in self._failure_injections:
                if (
                    injection["kind"] == kind
                    and injection["target"] == target
                    and injection["count"] > 0
                ):
                    injection["count"] -= 1
                    reason = injection["reason"]
                    self._emit(
                        "evaluation_failure_injected",
                        injection_kind=kind,
                        injection_target=target,
                        reason=reason,
                        remaining_count=injection["count"],
                    )
                    return reason
        return None

    def _load_idempotency_output(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
    ) -> NodeExecutionOutput | None:
        path = self._idempotency_path(snapshot, node)
        if path is None:
            return None
        payload = read_json(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("output"), dict):
            return None
        output = NodeExecutionOutput.model_validate(payload["output"])
        output.usage = ExecutionUsage()
        output.artifacts = [
            draft.model_copy(
                update={
                    "parent_artifact_ids": [],
                    "supersedes_artifact_id": None,
                    # Cached receipts are portable execution results, not
                    # provenance for the current Run's input artifacts.
                    "source_refs": [],
                },
                deep=True,
            )
            for draft in output.artifacts
        ]
        return output

    def _save_idempotency_output(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
        output: NodeExecutionOutput,
    ) -> None:
        path = self._idempotency_path(snapshot, node)
        if path is None:
            return
        receipt_output = output.model_copy(
            update={
                "artifacts": [
                    draft.model_copy(
                        update={"parent_artifact_ids": [], "supersedes_artifact_id": None},
                        deep=True,
                    )
                    for draft in output.artifacts
                ]
            },
            deep=True,
        )
        write_json(
            path,
            {
                "domain_id": snapshot.graph.domain_id,
                "capability": node.capability,
                "executor": node.executor,
                "idempotency_key": node.idempotency_key,
                "output": receipt_output.model_dump(mode="json"),
            },
        )

    def _idempotency_path(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
    ) -> Path | None:
        if (
            node.side_effect_level not in {"write_local", "write_external"}
            or not node.idempotency_key
        ):
            return None
        digest = hashlib.sha256(
            json.dumps(
                {
                    "domain_id": snapshot.graph.domain_id,
                    "capability": node.capability,
                    "idempotency_key": node.idempotency_key,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return self.cache_root / "idempotency" / snapshot.graph.domain_id / f"{digest}.json"

    def _load_cached_output(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
        inputs: list[ArtifactRecord],
    ) -> NodeExecutionOutput | None:
        path = self._cache_path(snapshot, node, inputs)
        if path is None:
            return None
        payload = read_json(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("output"), dict):
            return None
        expected_context = self._cache_context(snapshot, node, inputs)
        if payload.get("cache_context") != expected_context:
            self._emit(
                "graph_node_cache_rejected",
                node_id=node.node_id,
                reason="cache_context_mismatch",
            )
            return None
        ttl = self._node_freshness_ttl(node)
        if ttl == 0:
            self._emit(
                "graph_node_cache_rejected",
                node_id=node.node_id,
                reason="freshness_ttl_zero",
            )
            return None
        if ttl is not None:
            expires_at = self._parse_cache_time(payload.get("expires_at"))
            if expires_at is None:
                created_at = self._parse_cache_time(payload.get("created_at"))
                expires_at = created_at + timedelta(seconds=ttl) if created_at else None
            if expires_at is None:
                self._emit(
                    "graph_node_cache_rejected",
                    node_id=node.node_id,
                    reason="cache_freshness_metadata_missing",
                )
                return None
            if expires_at <= datetime.now(timezone.utc):
                self._emit(
                    "graph_node_cache_expired",
                    node_id=node.node_id,
                    expires_at=expires_at.isoformat(),
                    freshness_ttl_seconds=ttl,
                )
                return None
        output = NodeExecutionOutput.model_validate(payload["output"])
        output.usage = ExecutionUsage()
        output.artifacts = [
            draft.model_copy(
                update={
                    "parent_artifact_ids": [],
                    "supersedes_artifact_id": None,
                    "source_refs": [],
                },
                deep=True,
            )
            for draft in output.artifacts
        ]
        return output

    def _save_cached_output(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
        inputs: list[ArtifactRecord],
        output: NodeExecutionOutput,
    ) -> None:
        path = self._cache_path(snapshot, node, inputs)
        if path is None:
            return
        ttl = self._node_freshness_ttl(node)
        if ttl == 0:
            return
        cache_output = output.model_copy(
            update={
                "artifacts": [
                    draft.model_copy(
                        update={"parent_artifact_ids": [], "supersedes_artifact_id": None},
                        deep=True,
                    )
                    for draft in output.artifacts
                ]
            },
            deep=True,
        )
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(seconds=ttl) if ttl is not None else None
        write_json(
            path,
            {
                "domain_id": snapshot.graph.domain_id,
                "domain_version": snapshot.graph.metadata.get("domain_version"),
                "capability": node.capability,
                "executor": node.executor,
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "freshness_ttl_seconds": ttl,
                "cache_scope": node.cache_scope,
                "cache_context": self._cache_context(snapshot, node, inputs),
                "output": cache_output.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _node_freshness_ttl(node: ExecutionNode) -> int | None:
        value = node.metadata.get("freshness_ttl_seconds")
        if value is None:
            return None
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_cache_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _cache_path(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
        inputs: list[ArtifactRecord],
    ) -> Path | None:
        if (
            not snapshot.graph.governance.cache_enabled
            or not node.replayable
            or node.cache_scope is None
            or node.side_effect_level in {"write_local", "write_external"}
        ):
            return None
        key_payload = self._cache_context(snapshot, node, inputs)
        digest = hashlib.sha256(
            json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if node.cache_scope == "run":
            root = self.run_root / "cache" / "run"
        elif node.cache_scope == "thread":
            root = self.cache_root / "thread" / snapshot.thread_id
        else:
            root = self.cache_root / "global"
        return root / f"{digest}.json"

    def _cache_context(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
        inputs: list[ArtifactRecord],
    ) -> dict[str, Any]:
        input_payload = [
            {
                "artifact_type": artifact.artifact_type,
                "schema_version": artifact.schema_version,
                "content_hash": artifact.content_hash,
                "status": artifact.status,
                "source_refs": artifact.source_refs,
            }
            for artifact in inputs
        ]
        input_fingerprint = hashlib.sha256(
            json.dumps(input_payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        node_inputs = dict(node.inputs or {})
        assumption_payload = node.metadata.get("assumptions") or node_inputs.get("assumptions") or {}
        assumption_fingerprint = node.metadata.get("assumption_fingerprint")
        if not assumption_fingerprint:
            assumption_fingerprint = hashlib.sha256(
                json.dumps(assumption_payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        providers = sorted(
            {
                str(ref.get("provider") or ref.get("provider_id") or ref.get("id") or "")
                for artifact in inputs
                for ref in artifact.source_refs
                if isinstance(ref, dict)
                and str(ref.get("provider") or ref.get("provider_id") or ref.get("id") or "")
            }
        )
        return {
            "domain_id": snapshot.graph.domain_id,
            "domain_version": snapshot.graph.metadata.get("domain_version"),
            "capability": node.capability,
            "executor": node.executor,
            "artifact_type": sorted({port.artifact_type for port in node.produces}),
            "snapshot_dimension": node.metadata.get("dimension") or node.metadata.get("snapshot_dimension"),
            "cache_key": node.cache_key,
            "provider": providers,
            "profile": node.metadata.get("research_profile") or snapshot.graph.metadata.get("research_profile"),
            "capability_version": node.metadata.get("capability_version"),
            "methodology_version": node.metadata.get("methodology_version"),
            "policy_version": node.metadata.get("policy_version"),
            "data_period": node.metadata.get("data_period_policy") or {},
            "input_artifact_fingerprint": input_fingerprint,
            "assumption_fingerprint": assumption_fingerprint,
            "universe_or_scope": node.metadata.get("universe_or_scope") or node_inputs.get("universe_or_scope") or {},
            "node_inputs": hashlib.sha256(
                json.dumps(node.inputs, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "freshness_ttl_seconds": node.metadata.get("freshness_ttl_seconds"),
            "cache_scope": node.cache_scope,
        }

    def _expand_ready_compounds(self, snapshot: ExecutionRunSnapshot) -> ExecutionGraph:
        graph = snapshot.graph
        expandable = self.scheduler.expandable_compounds(
            graph,
            completed_node_ids=self._node_ids(snapshot, "completed"),
            skipped_node_ids=self._node_ids(snapshot, "skipped"),
            failed_node_ids=self._node_ids(snapshot, "failed"),
        )
        if not expandable:
            return graph
        if self.dynamic_planner is None:
            raise GraphExecutionError(
                f"graph has lazy compounds but no dynamic planner: {expandable[0].node_id}"
            )
        for compound in expandable:
            planner_method = getattr(self.dynamic_planner, "decide_expand_compound", None)
            decision_or_patch = (
                planner_method(
                    graph=graph.model_copy(deep=True),
                    compound=compound.model_copy(deep=True),
                    artifacts=self.board.list_artifacts(run_id=snapshot.run_id),
                )
                if callable(planner_method)
                else self.dynamic_planner.expand_compound(
                    graph=graph.model_copy(deep=True),
                    compound=compound.model_copy(deep=True),
                    artifacts=self.board.list_artifacts(run_id=snapshot.run_id),
                )
            )
            decision, patch = self._normalize_planner_decision(
                decision_or_patch,
                graph=graph,
            )
            self._emit(
                "plan_decision",
                actor=decision.actor,
                decision=decision.model_dump(mode="json"),
            )
            self._emit_main_agent_progress(
                phase="planner_decision",
                current_action=(
                    f"PlanAgent 决策：{decision.action}"
                    + (f"；{decision.reason}" if decision.reason else "")
                ),
                next_action=(
                    "应用图补丁操作："
                    + ", ".join(
                        f"{operation.op_type}:{operation.node.node_id if operation.node else operation.target_node_id}"
                        for operation in patch.operations
                    )
                    if patch is not None and patch.operations
                    else "继续当前图前沿"
                ),
            )
            if patch is None:
                self._apply_non_patch_plan_decision(snapshot, decision)
                return graph
            graph = self._apply_planner_patch(
                snapshot=snapshot,
                graph=graph,
                patch=patch,
                event_type="graph_compound_expanded",
            )
            self._emit(
                "graph_compound_expansion_completed",
                node_id=compound.node_id,
                patch_id=patch.patch_id,
                revision=graph.revision,
            )
            self._emit_main_agent_progress(
                phase="graph_expansion",
                current_action=(
                    f"已展开 compound 节点 {compound.node_id}："
                    + ", ".join(
                        f"{operation.op_type}:{operation.node.node_id if operation.node else operation.target_node_id}"
                        for operation in patch.operations
                    )
                ),
                next_action="重新计算更新后的可执行前沿",
            )
        return graph

    def _apply_pending_patches(
        self,
        snapshot: ExecutionRunSnapshot,
        graph: ExecutionGraph,
    ) -> ExecutionGraph:
        with self._lock:
            patches = self._patches[:]
            self._patches.clear()
        for patch in patches:
            try:
                graph = self._apply_planner_patch(
                    snapshot=snapshot,
                    graph=graph,
                    patch=patch,
                    event_type="graph_patch_applied",
                )
            except (GraphExecutionError, GraphGovernanceError, ValueError) as exc:
                self._emit(
                    "graph_patch_rejected",
                    patch_id=patch.patch_id,
                    base_revision=patch.base_revision,
                    reason=f"{exc.__class__.__name__}: {exc}",
                    revision=graph.revision,
                )
                continue
            if patch.decision_type == "stop":
                for state in snapshot.node_states.values():
                    if state.status in {"pending", "waiting"}:
                        self._set_node_status(state, "skipped")
                        state.error = patch.termination_reason or "planner_stopped_execution"
                        state.completed_at = utc_now()
        return graph

    def _evaluate_frontier(
        self,
        snapshot: ExecutionRunSnapshot,
        graph: ExecutionGraph,
        frontier: list[ExecutionNode],
    ) -> ExecutionGraph:
        evaluator = getattr(self.dynamic_planner, "evaluate_frontier", None)
        if evaluator is None:
            return graph
        planner_graph = graph.model_copy(deep=True)
        if snapshot.pending_control_requests:
            planner_graph.metadata["control_requests"] = [
                request.model_dump(mode="json")
                for request in snapshot.pending_control_requests
            ]
        decision_method = getattr(self.dynamic_planner, "decide_frontier", None)
        decision_or_patch = (
            decision_method(
                graph=planner_graph,
                frontier=[node.model_copy(deep=True) for node in frontier],
                artifacts=self.board.list_artifacts(run_id=snapshot.run_id),
            )
            if callable(decision_method)
            else evaluator(
                graph=planner_graph,
                frontier=[node.model_copy(deep=True) for node in frontier],
                artifacts=self.board.list_artifacts(run_id=snapshot.run_id),
            )
        )
        if decision_or_patch is None:
            snapshot.pending_control_requests.clear()
            return graph
        decision, patch = self._normalize_planner_decision(
            decision_or_patch,
            graph=graph,
        )
        self._emit(
            "plan_decision",
            actor=decision.actor,
            decision=decision.model_dump(mode="json"),
            message=(
                f"PlanAgent {decision.action}: "
                f"{decision.reason or 'updated the execution frontier.'}"
            ),
        )
        self._emit_main_agent_progress(
            phase="planner_decision",
            current_action=(
                f"PlanAgent 决策：{decision.action}"
                + (f"；{decision.reason}" if decision.reason else "")
            ),
            next_action=(
                "应用图补丁操作："
                + ", ".join(
                    f"{operation.op_type}:{operation.node.node_id if operation.node else operation.target_node_id}"
                    for operation in patch.operations
                )
                if patch is not None and patch.operations
                else "继续当前图前沿"
            ),
        )
        if patch is None:
            self._apply_non_patch_plan_decision(snapshot, decision)
            snapshot.pending_control_requests.clear()
            return graph
        updated_graph = self._apply_planner_patch(
            snapshot=snapshot,
            graph=graph,
            patch=patch,
            event_type="graph_replan_applied",
        )
        snapshot.pending_control_requests.clear()
        return updated_graph

    @staticmethod
    def _normalize_planner_decision(
        decision_or_patch: PlanDecision | ExecutionGraphPatch,
        *,
        graph: ExecutionGraph,
    ) -> tuple[PlanDecision, ExecutionGraphPatch | None]:
        if isinstance(decision_or_patch, ExecutionGraphPatch):
            patch = decision_or_patch
            action = "continue"
            if patch.decision_type == "replan":
                action = "replan"
            elif patch.decision_type == "stop":
                action = "stop"
            elif any(operation.op_type == "rerun_from" for operation in patch.operations):
                action = "retry"
            return (
                PlanDecision(
                    action=action,
                    reason=(
                        patch.notes[-1]
                        if patch.notes
                        else "Planner updated the execution frontier."
                    ),
                    graph_patch=patch,
                    confidence=patch.confidence,
                ),
                patch,
            )
        if not isinstance(decision_or_patch, PlanDecision):
            raise GraphGovernanceError(
                "planner must return PlanDecision or ExecutionGraphPatch"
            )
        patch = decision_or_patch.graph_patch
        if patch is None and decision_or_patch.action == "retry":
            operations = [
                ExecutionGraphPatchOperation(
                    op_type="rerun_from",
                    target_node_id=node_id,
                    reason=decision_or_patch.reason,
                )
                for node_id in decision_or_patch.target_node_ids
            ]
            if operations:
                patch = ExecutionGraphPatch(
                    base_revision=graph.revision,
                    decision_type="continue",
                    operations=operations,
                    policy_source="hybrid",
                    actor=decision_or_patch.actor,
                    notes=[decision_or_patch.reason],
                )
        if patch is not None and patch.actor != decision_or_patch.actor:
            raise GraphGovernanceError(
                "PlanDecision patch actor does not match decision actor"
            )
        return decision_or_patch, patch

    def _apply_non_patch_plan_decision(
        self,
        snapshot: ExecutionRunSnapshot,
        decision: PlanDecision,
    ) -> None:
        if decision.action == "clarify":
            self._set_run_status(snapshot, "paused")
            snapshot.clarification_question = decision.clarification_question
            snapshot.missing_fields = list(decision.missing_fields)
            self._emit(
                "planner_clarification_requested",
                actor=decision.actor,
                question=decision.clarification_question,
                missing_fields=list(decision.missing_fields),
                decision_id=decision.decision_id,
            )
        elif decision.action == "wait_approval":
            self._set_run_status(snapshot, "waiting_approval")
            self._emit(
                "planner_waiting_approval",
                actor=decision.actor,
                approval_ids=list(decision.approval_ids),
                decision_id=decision.decision_id,
            )
        elif decision.action == "stop":
            for state in snapshot.node_states.values():
                if state.status in {"pending", "waiting"}:
                    self._set_node_status(state, "skipped")
                    state.error = decision.reason
                    state.completed_at = utc_now()
            self._emit(
                "planner_stopped_execution",
                actor=decision.actor,
                reason=decision.reason,
                decision_id=decision.decision_id,
            )

    def _apply_planner_patch(
        self,
        *,
        snapshot: ExecutionRunSnapshot,
        graph: ExecutionGraph,
        patch: ExecutionGraphPatch,
        event_type: str,
    ) -> ExecutionGraph:
        if patch.actor != "main_agent.plan_agent":
            raise GraphGovernanceError(
                "only the Main Agent PlanAgent role may apply a graph patch"
            )
        if patch.patch_id in snapshot.applied_patch_ids:
            self._emit(
                "graph_patch_duplicate_ignored",
                patch_id=patch.patch_id,
                revision=graph.revision,
            )
            return graph
        if len(snapshot.applied_patch_ids) >= graph.governance.max_graph_patches:
            raise GraphGovernanceError("maximum graph patch count exceeded")
        if patch.base_revision != graph.revision:
            raise GraphExecutionError(
                f"stale graph patch: {patch.base_revision}!={graph.revision}"
            )
        affected_node_ids = affected_node_ids_for_patch(graph, patch)
        invalidated_artifact_ids = {
            artifact_id
            for node_id in affected_node_ids
            for artifact_id in (
                snapshot.node_states.get(node_id).output_artifact_ids
                if snapshot.node_states.get(node_id) is not None
                else []
            )
        }
        invalidated_artifact_ids.update(
            artifact_id
            for node_id in affected_node_ids
            for artifact_id in self._node(graph, node_id).output_artifact_ids
        )
        graph = apply_execution_graph_patch(graph, patch)
        graph = self._prepare_approvals(
            graph,
            run_id=snapshot.run_id,
            user_id=snapshot.user_id,
            tenant_id=snapshot.tenant_id,
        )
        self._validate_graph_governance(graph, run_id=snapshot.run_id)
        if self._pending_approval_ids_for_graph(
            graph,
            snapshot.run_id,
            user_id=snapshot.user_id,
            tenant_id=snapshot.tenant_id,
        ):
            self._set_run_status(snapshot, "waiting_approval")
        snapshot.applied_patch_ids.append(patch.patch_id)
        snapshot.invalidated_artifact_ids = list(
            dict.fromkeys(
                [*snapshot.invalidated_artifact_ids, *sorted(invalidated_artifact_ids)]
            )
        )
        self.board.bind_invalidated_artifacts(snapshot.invalidated_artifact_ids)
        snapshot.patch_impacts.append(
            GraphPatchImpact(
                patch_id=patch.patch_id,
                revision=graph.revision,
                invalidated_node_ids=sorted(affected_node_ids),
                invalidated_artifact_ids=sorted(invalidated_artifact_ids),
                reason=(
                    patch.notes[-1]
                    if patch.notes
                    else "Graph patch changed the execution frontier."
                ),
            )
        )
        self._sync_node_states(snapshot, graph)
        self._emit(
            event_type,
            patch_id=patch.patch_id,
            revision=graph.revision,
            actor=patch.actor,
            invalidated_node_ids=sorted(affected_node_ids),
            invalidated_artifact_ids=sorted(invalidated_artifact_ids),
            graph=graph.model_dump(mode="json"),
        )
        return graph

    def _sync_node_states(
        self,
        snapshot: ExecutionRunSnapshot,
        graph: ExecutionGraph,
    ) -> None:
        graph_leaf_ids = {node.node_id for node in graph.nodes if node.node_kind == "subagent"}
        snapshot.node_states = {
            node_id: state
            for node_id, state in snapshot.node_states.items()
            if node_id in graph_leaf_ids
        }
        for node_id in graph_leaf_ids:
            graph_node = self._node(graph, node_id)
            state = snapshot.node_states.setdefault(
                node_id,
                NodeExecutionState(node_id=node_id, status=graph_node.status),
            )
            if graph_node.status == "pending" and state.status in {
                "completed",
                "skipped",
                "failed",
                "cancelled",
            }:
                snapshot.node_states[node_id] = NodeExecutionState(node_id=node_id)
            elif graph_node.status != "pending" and state.status != graph_node.status:
                state.status = graph_node.status

    def _governed_frontier(
        self,
        graph: ExecutionGraph,
        ready: list[ExecutionNode],
    ) -> list[ExecutionNode]:
        limit = graph.governance.max_concurrency
        unsafe = [node for node in ready if not node.parallel_safe]
        if unsafe:
            return [unsafe[0]]
        selected: list[ExecutionNode] = []
        counts: dict[str, int] = {}
        for node in ready:
            capability = str(node.capability)
            capability_limit = node.max_concurrency or limit
            if counts.get(capability, 0) >= capability_limit:
                continue
            selected.append(node)
            counts[capability] = counts.get(capability, 0) + 1
            if len(selected) >= limit:
                break
        return selected

    def _validate_node_authorization(
        self,
        snapshot: ExecutionRunSnapshot,
        node: ExecutionNode,
    ) -> None:
        governance = snapshot.graph.governance
        if (
            governance.require_human_approval_for_high_risk
            and self._node_requires_approval(node)
            and (
                not node.approval_id
                or not self.approvals.validate(
                    approval_id=node.approval_id,
                    run_id=snapshot.run_id,
                    node_id=node.node_id,
                    capability=str(node.capability),
                    graph_revision=snapshot.graph.revision,
                    domain_id=snapshot.graph.domain_id,
                    domain_version=str(snapshot.graph.metadata.get("domain_version") or "") or None,
                    user_id=snapshot.user_id,
                    tenant_id=snapshot.tenant_id,
                    policy_fingerprint=approval_policy_fingerprint(snapshot.graph, node),
                    require_binding=True,
                )
            )
        ):
            raise GraphGovernanceError(
                f"high-risk node requires current human approval: {node.node_id}"
            )

    def _refresh_compound_statuses(self, snapshot: ExecutionRunSnapshot) -> None:
        children = self.scheduler.children_by_parent(snapshot.graph)
        node_map = self.scheduler.node_map(snapshot.graph)

        def depth(node: ExecutionNode) -> int:
            resolved = 0
            parent_id = node.parent_node_id
            while parent_id is not None:
                resolved += 1
                parent_id = node_map[parent_id].parent_node_id
            return resolved

        compounds = sorted(
            [node for node in snapshot.graph.nodes if node.node_kind == "compound"],
            key=depth,
            reverse=True,
        )
        for compound in compounds:
            child_nodes = [node_map[node_id] for node_id in children.get(compound.node_id, [])]
            if not child_nodes:
                continue
            statuses = {node.status for node in child_nodes}
            if "running" in statuses:
                compound.status = "running"
            elif "failed" in statuses:
                compound.status = "failed"
            elif statuses.issubset({"completed", "skipped"}):
                compound.status = "completed"
            elif "waiting" in statuses:
                compound.status = "waiting"
            else:
                compound.status = "pending"

    def _enforce_run_controls(
        self,
        snapshot: ExecutionRunSnapshot,
        started: float,
    ) -> None:
        if self._cancel_requested.is_set():
            raise GraphCancelledError("run cancellation requested")
        control = read_json(self.control_path) or {}
        if control.get("cancel_requested"):
            self._cancel_requested.set()
            raise GraphCancelledError("run cancellation requested")
        # When mounted inside TurnRunner, the root Turn owns the deadline.
        # Applying the graph-local timer here would restart that budget on
        # every safe-point ``step`` invocation.
        timeout = snapshot.graph.governance.timeout_seconds
        if self._deadline_at is None and timeout is not None and perf_counter() - started >= timeout:
            raise GraphTimedOutError("run timeout exceeded")
        if self._deadline_at is not None and monotonic() >= self._deadline_at:
            raise GraphTimedOutError("turn timeout exceeded")
        if self._pause_requested.is_set() or control.get("pause_requested"):
            self._set_run_status(snapshot, "paused")
            snapshot.updated_at = utc_now()
            self._persist(snapshot)
            self._emit("graph_run_paused")
            raise _GraphPaused

    def _write_control(
        self,
        *,
        pause_requested: bool | None = None,
        cancel_requested: bool | None = None,
    ) -> None:
        with self._lock:
            payload = read_json(self.control_path) or {
                "pause_requested": False,
                "cancel_requested": False,
            }
            if pause_requested is not None:
                payload["pause_requested"] = pause_requested
            if cancel_requested is not None:
                payload["cancel_requested"] = cancel_requested
            payload["updated_at"] = utc_now().isoformat()
            write_json(self.control_path, payload)

    def _persist(self, snapshot: ExecutionRunSnapshot) -> None:
        snapshot.graph = ExecutionGraph.model_validate(snapshot.graph.model_dump(mode="python"))
        payload = snapshot.model_dump(mode="json")
        self._emit(
            "graph_snapshot_commit_started",
            snapshot=payload,
        )
        write_json(self.graph_path, snapshot.graph.model_dump(mode="json"))
        write_json(self.snapshot_path, payload)
        self._emit("graph_snapshot_persisted", snapshot=payload)

    def _load_snapshot(self) -> ExecutionRunSnapshot | None:
        payload = read_json(self.snapshot_path)
        return ExecutionRunSnapshot.model_validate(payload) if payload else None

    def _validate_resume_identity(
        self,
        *,
        snapshot: ExecutionRunSnapshot,
        graph: ExecutionGraph,
        run_id: str,
        thread_id: str,
        request_id: str,
    ) -> None:
        if (snapshot.run_id, snapshot.thread_id, snapshot.request_id) != (
            run_id,
            thread_id,
            request_id,
        ):
            raise GraphExecutionError("resume identity does not match persisted execution")
        if snapshot.graph.graph_id != graph.graph_id:
            raise GraphExecutionError("resume graph does not match persisted execution")

    def _result(self, snapshot: ExecutionRunSnapshot) -> ExecutionRunResult:
        snapshot.pending_approval_ids = self._pending_approval_ids(snapshot)
        # Keep the normalized approval view recoverable even when the caller
        # is returning immediately from an approval safe point.
        write_json(self.snapshot_path, snapshot.model_dump(mode="json"))
        artifacts = self.board.list_artifacts(run_id=snapshot.run_id)
        final_types = {
            contract.artifact_type
            for contract in self.registry.get(
                snapshot.graph.domain_id,
                version=self._graph_domain_version(snapshot),
            ).report_contracts
        }
        return ExecutionRunResult(
            snapshot=snapshot,
            artifact_ids=[artifact.artifact_id for artifact in artifacts],
            final_artifact_ids=[
                artifact.artifact_id
                for artifact in artifacts
                if artifact.artifact_type in final_types
            ],
        )

    def _record_episodic_memory(self, snapshot: ExecutionRunSnapshot) -> None:
        if self.memory_store is None:
            return
        final_types = {
            contract.artifact_type
            for contract in self.registry.get(
                snapshot.graph.domain_id,
                version=self._graph_domain_version(snapshot),
            ).report_contracts
        }
        gate_rows = [gate.model_dump(mode="json") for gate in snapshot.gate_evaluations]
        for artifact in self.board.list_artifacts(run_id=snapshot.run_id, status="active"):
            if artifact.artifact_type not in final_types:
                continue
            self.memory_store.add_episode(
                domain_id=snapshot.graph.domain_id,
                memory_type=artifact.artifact_type,
                content=artifact.content,
                source_run_id=snapshot.run_id,
                source_artifact_ids=[artifact.artifact_id],
                gated=True,
                gate_evaluations=[
                    row for row in gate_rows if row.get("artifact_id") == artifact.artifact_id
                ],
                scope=(
                    "user"
                    if snapshot.user_id
                    else "tenant"
                    if snapshot.tenant_id
                    else "domain"
                ),
                tenant_id=snapshot.tenant_id,
                user_id=snapshot.user_id,
            )

    def _emit(self, event_type: str, **payload: Any) -> None:
        revision = payload.get("revision", self._graph_revision)
        if isinstance(revision, int):
            self._graph_revision = revision
        event = {
            "run_id": self._run_id or "unknown",
            "thread_id": self._thread_id or "unknown",
            "request_id": self._request_id or "unknown",
            "user_id": self._user_id,
            "tenant_id": self._tenant_id,
            "turn_id": self._turn_id,
            "task_type": self._task_type,
            "execution_mode": self._execution_mode,
            "graph_revision": revision,
            "event_type": event_type,
            "actor": payload.get("actor", "execution_graph_runner"),
            **payload,
        }
        event = EventEnvelope.model_validate(event).model_dump(mode="json")
        with self._lock:
            event["sequence"] = 0
            event = append_canonical_event(self.events_path, event)
        if self.event_callback is not None:
            self.event_callback(event)

    def _emit_main_agent_progress(
        self,
        *,
        phase: str,
        current_action: str,
        next_action: str,
        status: str = "running",
        source: str = "runtime",
        node_id: str | None = None,
        progress_percent: int | float | None = None,
        stage_index: int | None = None,
        stage_total: int | None = None,
        progress_details: dict[str, Any] | None = None,
    ) -> None:
        """Publish a factual or Main Agent-generated graph update."""
        current = str(current_action).strip()
        next_step = str(next_action).strip()
        details = {
            "progress_percent": progress_percent,
            "stage_index": stage_index,
            "stage_total": stage_total,
            **dict(progress_details or {}),
        }
        self._emit(
            "agent_progress",
            actor="main_agent",
            progress_kind="main_agent_graph",
            phase=phase,
            status=status,
            progress_source=source,
            node_id=node_id,
            current_action=current,
            next_action=next_step,
            message=f"当前：{current}；下一步：{next_step}",
            **{key: value for key, value in details.items() if value is not None},
        )

    def _emit_observer_progress(self, payload: dict[str, Any]) -> None:
        self._emit_main_agent_progress(
            phase=str(payload.get("phase") or "tool_result"),
            status=str(payload.get("status") or "running"),
            source=str(payload.get("progress_source") or "llm"),
            node_id=str(payload.get("node_id") or "") or None,
            current_action=str(payload.get("current_action") or ""),
            next_action=str(payload.get("next_action") or ""),
            progress_percent=payload.get("progress_percent"),
            stage_index=payload.get("stage_index"),
            stage_total=payload.get("stage_total"),
            progress_details={
                key: value
                for key, value in payload.items()
                if key not in {
                    "node_id", "phase", "status", "progress_source",
                    "current_action", "next_action", "progress_percent",
                    "stage_index", "stage_total",
                }
            },
        )

    def _observe_main_agent_progress(self, facts: dict[str, Any]) -> dict[str, str] | None:
        if self.progress_observer is None:
            return None
        policy = self._progress_policy
        try:
            max_updates = max(int(policy.get("max_model_updates") or 0), 0)
        except (TypeError, ValueError):
            max_updates = 0
        if max_updates and self._progress_model_updates >= max_updates:
            if self._progress_model_updates == max_updates:
                self._emit(
                    "main_agent_progress_budget_exhausted",
                    progress_model_updates=self._progress_model_updates,
                    max_model_updates=max_updates,
                )
            return None
        try:
            min_interval = max(float(policy.get("min_interval_seconds") or 0.0), 0.0)
        except (TypeError, ValueError):
            min_interval = 0.0
        now = perf_counter()
        if (
            min_interval
            and self._progress_last_model_at
            and now - self._progress_last_model_at < min_interval
        ):
            return None
        self._progress_model_updates += 1
        self._progress_last_model_at = now
        try:
            return self.progress_observer(facts)
        except Exception:
            return None

    @staticmethod
    def _node_ids(snapshot: ExecutionRunSnapshot, status: str) -> set[str]:
        return {
            node_id
            for node_id, state in snapshot.node_states.items()
            if state.status == status
        }

    @staticmethod
    def _node(graph: ExecutionGraph, node_id: str) -> ExecutionNode:
        for node in graph.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"unknown execution graph node: {node_id}")

    @staticmethod
    def _add_usage(left: ExecutionUsage, right: ExecutionUsage) -> ExecutionUsage:
        return ExecutionUsage(
            tokens=left.tokens + right.tokens,
            cost=left.cost + right.cost,
            duration_seconds=left.duration_seconds + right.duration_seconds,
        )


class _GraphPaused(Exception):
    pass


__all__ = [
    "DynamicGraphPlanner",
    "ExecutionGraphRunner",
    "ExecutionNodeContext",
    "GraphCancelledError",
    "GraphExecutionError",
    "GraphGovernanceError",
    "GraphTimedOutError",
]
