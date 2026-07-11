from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Event, RLock, Semaphore
from time import perf_counter
from typing import Any, Callable, Protocol

from typing import TYPE_CHECKING

import jsonschema

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.approvals import (
    HumanApprovalStore,
    approval_policy_fingerprint,
)
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.core.tool_registry import CoreToolContext, CoreToolRegistry
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.execution_graph import (
    ExecutionGraphScheduler,
    affected_node_ids_for_patch,
    apply_execution_graph_patch,
)
from research_system.runtime.storage.jsonl import append_jsonl, read_json, read_jsonl, write_json
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
    GateEvaluation,
    GraphPatchImpact,
    NodeExecutionOutput,
    NodeExecutionState,
)
from research_system.schemas.events import EventEnvelope
from research_system.schemas.governance import GateEvaluationResult
from research_system.schemas.state_machine import transition_status

if TYPE_CHECKING:
    from research_system.services.memory.layers import LayeredMemoryStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GraphExecutionError(RuntimeError):
    pass


class GraphGovernanceError(GraphExecutionError):
    pass


class GraphCancelledError(GraphExecutionError):
    pass


class GraphTimedOutError(GraphExecutionError):
    pass


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
class ExecutionNodeContext:
    run_id: str
    thread_id: str
    request_id: str
    user_id: str | None
    tenant_id: str | None
    graph: ExecutionGraph
    node: ExecutionNode
    input_artifacts: list[ArtifactRecord]
    artifact_board: ArtifactBoard
    cancellation: Event
    tool_registry: CoreToolRegistry | None = None
    skill_runtime: Any | None = None
    memory_store: Any | None = None
    settings: Any | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None
    authorize_tool_call: Callable[[], bool] | None = None
    tool_semaphore: Semaphore | None = None
    approval_store: HumanApprovalStore | None = None
    failure_injection: Callable[[str, str], str | None] | None = None

    def artifacts(self, artifact_type: str) -> list[ArtifactRecord]:
        return [
            artifact
            for artifact in self.input_artifacts
            if artifact.artifact_type == artifact_type
        ]

    def require_artifact(self, artifact_type: str) -> ArtifactRecord:
        artifacts = self.artifacts(artifact_type)
        if not artifacts:
            raise GraphExecutionError(
                f"missing required artifact for {self.node.node_id}: {artifact_type}"
            )
        return artifacts[-1]

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ):
        """Invoke a node-declared tool through the shared governed registry."""
        if self.tool_registry is None:
            raise GraphExecutionError(
                f"Tool Registry is not configured for node: {self.node.node_id}"
            )
        if self.failure_injection is not None:
            injected_reason = self.failure_injection("tool", tool_name)
            if injected_reason:
                raise GraphExecutionError(
                    f"injected Tool failure for {tool_name}: {injected_reason}"
                )
        context = CoreToolContext(
            run_id=self.run_id,
            thread_id=self.thread_id,
            request_id=self.request_id,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            run_root=Path(self.artifact_board.path).parent,
            actor=f"subagent:{self.node.node_id}",
            artifact_board=self.artifact_board,
            skill_runtime=self.skill_runtime,
            memory_store=self.memory_store,
            settings=self.settings,
            graph_supplier=lambda: self.graph.model_dump(mode="json"),
            cancellation=self.cancellation,
            event_callback=self.event_callback,
            allowed_tools=frozenset(self.node.tools),
            allowed_permissions=frozenset(self.node.permissions),
            authorize_call=self.authorize_tool_call,
            authorize_approval=(
                lambda approval_id: self.approval_store.validate(
                    approval_id=approval_id,
                    run_id=self.run_id,
                    node_id=self.node.node_id,
                    capability=str(self.node.capability),
                    graph_revision=self.graph.revision,
                    domain_id=self.graph.domain_id,
                    domain_version=str(self.graph.metadata.get("domain_version") or "") or None,
                    user_id=self.user_id,
                    tenant_id=self.tenant_id,
                    policy_fingerprint=approval_policy_fingerprint(
                        self.graph,
                        self.node,
                    ),
                    require_binding=True,
                )
                if self.approval_store is not None
                else False
            ),
            approval_id=self.node.approval_id,
            idempotency_key=(
                f"{self.node.idempotency_key}:{tool_name}"
                if self.node.idempotency_key
                else None
            ),
            rate_limit_key=self.thread_id,
            tool_semaphore=self.tool_semaphore,
        )
        return self.tool_registry.invoke(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            graph_revision=self.graph.revision,
            node_id=self.node.node_id,
        )


@dataclass(frozen=True)
class GateEvaluationContext:
    """Read-only context exposed to a DomainPack quality-gate evaluator."""

    run_id: str
    thread_id: str
    request_id: str
    graph: ExecutionGraph
    artifact: ArtifactRecord
    artifacts: tuple[ArtifactRecord, ...]
    artifact_board: ArtifactBoard
    config: dict[str, Any]

    def require_artifact(self, artifact_type: str) -> ArtifactRecord:
        for artifact in reversed(self.artifacts):
            if artifact.artifact_type == artifact_type:
                return artifact
        raise GraphExecutionError(
            f"quality-gate evaluator input is missing: {artifact_type}"
        )


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
        cache_root: Path | None = None,
        failure_injections: list[dict[str, Any]] | None = None,
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
        self._graph_revision: int | None = None
        self._tool_semaphore: Semaphore | None = None
        self._failure_injections = [
            {
                "kind": str(row.get("kind") or "").strip(),
                "target": str(row.get("target") or "").strip(),
                "count": max(int(row.get("count") or 1), 0),
                "reason": str(row.get("reason") or "evaluation failure injection").strip(),
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
                    reason="stale graph revision",
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
                    reason="cancellation target is not running",
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
    ) -> ExecutionRunResult:
        self._run_id = run_id
        self._thread_id = thread_id
        self._request_id = request_id
        self._graph_revision = graph.revision
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
            if snapshot.status == "waiting_approval":
                self._persist(snapshot)
                self._emit(
                    "graph_run_waiting_approval",
                    approval_ids=self._pending_approval_ids(snapshot),
                )
                return self._result(snapshot)
        else:
            self._validate_resume_identity(
                snapshot=snapshot,
                graph=graph,
                run_id=run_id,
                thread_id=thread_id,
                request_id=request_id,
            )
            graph = snapshot.graph
            if snapshot.status in {"completed", "gated", "failed", "cancelled", "timed_out"}:
                return self._result(snapshot)
            if self._rejected_approval_ids(snapshot):
                self._mark_approval_rejection(snapshot)
                self._persist(snapshot)
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
        self.board.bind_invalidated_artifacts(snapshot.invalidated_artifact_ids)
        self._set_run_status(snapshot, "running")
        if not self.control_path.exists():
            self._write_control(pause_requested=False, cancel_requested=False)
        self._reserved_tokens = snapshot.usage.tokens
        self._reserved_cost = snapshot.usage.cost
        self._tool_semaphore = Semaphore(snapshot.graph.governance.max_concurrent_tool_calls)
        self._persist(snapshot)
        started = perf_counter()
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
            current_action="正在启动受治理的 ExecutionGraph",
            next_action="展开当前任务图并计算第一批可执行叶子节点",
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
                        "已确定本轮就绪节点："
                        + ", ".join(node.node_id for node in frontier)
                    ),
                    next_action="并发执行就绪 Subagent，并等待 ArtifactBoard 结果",
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
                    self._emit_main_agent_progress(
                        phase="subagent_result",
                        current_action=f"已收到 Subagent {outcome.node.node_id} 的结果",
                        next_action=(
                            "继续处理本轮其他就绪 Subagent 的结果"
                            if remaining
                            else "检查 ArtifactBoard 和质量状态，决定继续、重规划或结束"
                        ),
                    )
                snapshot.updated_at = utc_now()
                self._refresh_compound_statuses(snapshot)
                graph = self._evaluate_frontier(snapshot, graph, frontier)
                snapshot.graph = graph
                self._persist(snapshot)
                if snapshot.status in {"paused", "waiting_approval"}:
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
                current_action=f"ExecutionGraph 已结束，状态为 {snapshot.status}",
                next_action="整理最终 Artifact 并交付用户",
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
        self._pause_requested.clear()
        self._write_control(pause_requested=False, cancel_requested=False)
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
                        f"Approval required for high-risk Agent OS node "
                        f"{node.node_id} ({node.capability})."
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
                        f"Approval required for high-risk Agent OS node "
                        f"{node.node_id} ({node.capability})."
                    ),
                    graph_revision=graph.revision,
                    domain_id=graph.domain_id,
                    domain_version=domain_version,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    policy_fingerprint=policy_fingerprint,
                )
            elif existing.status == "approved" and not bound:
                if (
                    graph.revision == 1
                    and existing.graph_revision is None
                    and existing.domain_id is None
                    and existing.domain_version is None
                    and existing.policy_fingerprint is None
                ):
                    self.approvals.bind_legacy(
                        approval_id,
                        graph_revision=graph.revision,
                        domain_id=graph.domain_id,
                        domain_version=domain_version,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        policy_fingerprint=policy_fingerprint,
                    )
                    self._emit(
                        "approval_legacy_record_bound",
                        node_id=node.node_id,
                        approval_id=approval_id,
                        reason="Migrated an initial-revision approval to canonical context binding.",
                    )
                    bound = True
                else:
                    # A prior decision must never silently authorize a changed
                    # graph, Pack version, policy, or identity scope.
                    approval_id = f"approval_{generate_ulid()}"
                    node = node.model_copy(update={"approval_id": approval_id})
                    changed = True
                    self.approvals.request(
                        approval_id=approval_id,
                        run_id=run_id,
                        node_id=node.node_id,
                        capability=str(node.capability),
                        reason=(
                            f"Fresh approval required after governed context changed "
                            f"for {node.node_id} ({node.capability})."
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
            if not node.approval_id or not self.approvals.validate(
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
                if node.approval_id:
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
                    reason="Human approval was rejected, revoked, or expired.",
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
            for future in as_completed(futures):
                outcomes.append(future.result())
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
                    raw_output = executor(
                        ExecutionNodeContext(
                            run_id=snapshot.run_id,
                            thread_id=snapshot.thread_id,
                            request_id=snapshot.request_id,
                            user_id=snapshot.user_id,
                            tenant_id=snapshot.tenant_id,
                            graph=snapshot.graph,
                            node=node,
                            input_artifacts=inputs,
                            artifact_board=self.board,
                            cancellation=cancellation,
                            tool_registry=self.tool_registry,
                            skill_runtime=self._skill_runtime_for(snapshot, node),
                            memory_store=self.memory_store,
                            settings=self.settings,
                            event_callback=self.event_callback,
                            authorize_tool_call=lambda: self._authorize_tool_call(snapshot),
                            tool_semaphore=self._tool_semaphore,
                            approval_store=self.approvals,
                            failure_injection=self._consume_failure_injection,
                        )
                    )
                    output = self._normalize_output(raw_output)
                    elapsed = perf_counter() - started
                    output.usage.duration_seconds += elapsed
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
                message=f"Subagent {node.capability} failed: {state.error}",
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
            candidates = self.board.list_artifacts(
                run_id=snapshot.run_id,
                artifact_type=port.artifact_type,
                status="active",
            )
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
                for gate_id in contract.required_gate_ids or [f"{contract.contract_id}.present"]:
                    evaluations.append(
                        GateEvaluation(
                            gate_id=gate_id,
                            artifact_id="missing",
                            verdict="fail",
                            reason=f"required deliverable is missing: {contract.artifact_type}",
                            blocking=True,
                        )
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
                        reasons.append(f"missing sections: {','.join(missing_sections)}")
                    if contract.required_source_refs and not artifact.source_refs:
                        reasons.append("missing source references")
                    if artifact.status in {"needs_review", "rejected"}:
                        reasons.append(f"artifact status is {artifact.status}")
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
                                or "gate passed"
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
        for gate_id in sorted(required - evaluated):
            evaluations.append(
                GateEvaluation(
                    gate_id=gate_id,
                    artifact_id="missing",
                    verdict="fail",
                    reason="required governance gate was not evaluated",
                    blocking=True,
                )
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
                    reason=f"injected quality-gate failure: {injected_reason}",
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
                reason=f"quality-gate evaluator failed closed: {exc.__class__.__name__}: {exc}",
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
                update={"parent_artifact_ids": [], "supersedes_artifact_id": None},
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
        output = NodeExecutionOutput.model_validate(payload["output"])
        output.usage = ExecutionUsage()
        output.artifacts = [
            draft.model_copy(
                update={"parent_artifact_ids": [], "supersedes_artifact_id": None},
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
        write_json(
            path,
            {
                "domain_id": snapshot.graph.domain_id,
                "domain_version": snapshot.graph.metadata.get("domain_version"),
                "capability": node.capability,
                "executor": node.executor,
                "output": cache_output.model_dump(mode="json"),
            },
        )

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
        key_payload = {
            "domain_id": snapshot.graph.domain_id,
            "domain_version": snapshot.graph.metadata.get("domain_version"),
            "capability": node.capability,
            "executor": node.executor,
            "cache_key": node.cache_key,
            "inputs": [
                {
                    "artifact_type": artifact.artifact_type,
                    "schema_version": artifact.schema_version,
                    "content_hash": artifact.content_hash,
                }
                for artifact in inputs
            ],
        }
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
                current_action=f"已根据当前任务和 Board 扩展子图 {compound.node_id}",
                next_action="重新计算就绪叶子节点并进入统一调度",
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
        timeout = snapshot.graph.governance.timeout_seconds
        if timeout is not None and perf_counter() - started >= timeout:
            raise GraphTimedOutError("run timeout exceeded")
        if self._pause_requested.is_set() or control.get("pause_requested"):
            self._set_run_status(snapshot, "paused")
            snapshot.updated_at = utc_now()
            self._persist(snapshot)
            self._emit("graph_run_paused")
            raise _GraphPaused()

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
            "graph_revision": revision,
            "event_type": event_type,
            "actor": payload.get("actor", "execution_graph_runner"),
            **payload,
        }
        event = EventEnvelope.model_validate(event).model_dump(mode="json")
        with self._lock:
            append_jsonl(self.events_path, event)
        if self.event_callback is not None:
            self.event_callback(event)

    def _emit_main_agent_progress(
        self,
        *,
        phase: str,
        current_action: str,
        next_action: str,
        status: str = "running",
    ) -> None:
        """Publish an operational graph update without exposing hidden reasoning."""
        current = str(current_action).strip()
        next_step = str(next_action).strip()
        self._emit(
            "agent_progress",
            actor="main_agent",
            progress_kind="main_agent_graph",
            phase=phase,
            status=status,
            current_action=current,
            next_action=next_step,
            message=f"当前：{current}；下一步：{next_step}",
        )

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
