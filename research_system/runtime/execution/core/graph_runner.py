from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Event, RLock
from time import perf_counter
from typing import Any, Callable, Protocol

from typing import TYPE_CHECKING

import jsonschema

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.execution_graph import (
    ExecutionGraphScheduler,
    apply_execution_graph_patch,
)
from research_system.runtime.storage.jsonl import append_jsonl, read_json, read_jsonl, write_json
from research_system.schemas.artifacts import ArtifactDraft, ArtifactProducer, ArtifactRecord
from research_system.schemas.execution_graph import (
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionNode,
)
from research_system.schemas.execution_runtime import (
    ExecutionRunResult,
    ExecutionRunSnapshot,
    ExecutionUsage,
    GateEvaluation,
    NodeExecutionOutput,
    NodeExecutionState,
)

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


class DynamicGraphPlanner(Protocol):
    def expand_compound(
        self,
        *,
        graph: ExecutionGraph,
        compound: ExecutionNode,
        artifacts: list[ArtifactRecord],
    ) -> ExecutionGraphPatch: ...


@dataclass(frozen=True)
class ExecutionNodeContext:
    run_id: str
    thread_id: str
    request_id: str
    graph: ExecutionGraph
    node: ExecutionNode
    input_artifacts: list[ArtifactRecord]
    artifact_board: ArtifactBoard
    cancellation: Event

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
        cache_root: Path | None = None,
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
        self.cache_root = Path(cache_root) if cache_root is not None else self.run_root / "cache"
        self.scheduler = ExecutionGraphScheduler()
        self._pause_requested = Event()
        self._cancel_requested = Event()
        self._node_cancellations: dict[str, Event] = {}
        self._patches: list[ExecutionGraphPatch] = []
        self._lock = RLock()
        self._reserved_tokens = 0
        self._reserved_cost = 0.0

    def request_pause(self) -> None:
        self._pause_requested.set()
        self._write_control(pause_requested=True)

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        self._write_control(cancel_requested=True)
        with self._lock:
            for cancellation in self._node_cancellations.values():
                cancellation.set()

    def submit_patch(self, patch: ExecutionGraphPatch) -> None:
        with self._lock:
            self._patches.append(patch)
            for node_id in patch.cancel_in_flight_node_ids:
                cancellation = self._node_cancellations.get(node_id)
                if cancellation is not None:
                    cancellation.set()
        self._emit(
            "graph_patch_requested",
            patch_id=patch.patch_id,
            base_revision=patch.base_revision,
            cancel_in_flight_node_ids=patch.cancel_in_flight_node_ids,
        )

    def run(
        self,
        *,
        graph: ExecutionGraph,
        run_id: str,
        thread_id: str,
        request_id: str,
        seed_artifacts: list[ArtifactDraft] | None = None,
        resume: bool = False,
    ) -> ExecutionRunResult:
        self.board.bind_thread(thread_id)
        snapshot = self._load_snapshot() if resume else None
        if snapshot is None:
            snapshot = self._new_snapshot(
                graph=graph,
                run_id=run_id,
                thread_id=thread_id,
                request_id=request_id,
            )
            self._seed_artifacts(snapshot, seed_artifacts or [])
        else:
            self._validate_resume_identity(
                snapshot=snapshot,
                graph=graph,
                run_id=run_id,
                thread_id=thread_id,
                request_id=request_id,
            )
            graph = snapshot.graph
            if snapshot.status == "completed":
                return self._result(snapshot)
            snapshot.status = "running"
            snapshot.updated_at = utc_now()
        snapshot.status = "running"
        if not self.control_path.exists():
            self._write_control(pause_requested=False, cancel_requested=False)
        self._reserved_tokens = snapshot.usage.tokens
        self._reserved_cost = snapshot.usage.cost
        self._persist(snapshot)
        started = perf_counter()
        self._emit("graph_run_started", graph_id=graph.graph_id, revision=graph.revision)

        try:
            while True:
                self._enforce_run_controls(snapshot, started)
                graph = self._expand_ready_compounds(snapshot)
                graph = self._apply_pending_patches(snapshot, graph)
                snapshot.graph = graph

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
                    state.status = "skipped"
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
                for outcome in self._execute_frontier(snapshot, frontier):
                    snapshot.node_states[outcome.node.node_id] = outcome.state
                    if outcome.output is not None:
                        snapshot.usage = self._add_usage(snapshot.usage, outcome.output.usage)
                        self._enforce_total_budget(snapshot)
                    if (
                        outcome.error is not None
                        and not isinstance(outcome.error, GraphCancelledError)
                        and outcome.node.failure_mode == "halt"
                    ):
                        snapshot.status = "failed"
                        self._persist(snapshot)
                        raise GraphExecutionError(
                            f"execution node failed: {outcome.node.node_id}: {outcome.state.error}"
                        ) from outcome.error
                snapshot.updated_at = utc_now()
                self._refresh_compound_statuses(snapshot)
                self._persist(snapshot)

            snapshot.gate_evaluations = self._evaluate_gates(snapshot)
            self._refresh_compound_statuses(snapshot)
            blocking = [
                gate
                for gate in snapshot.gate_evaluations
                if gate.blocking and gate.verdict != "pass"
            ]
            snapshot.status = "gated" if blocking else "completed"
            snapshot.updated_at = utc_now()
            self._persist(snapshot)
            if snapshot.status == "completed":
                self._record_episodic_memory(snapshot)
            self._emit(
                "graph_run_finished",
                status=snapshot.status,
                blocking_gate_ids=[gate.gate_id for gate in blocking],
            )
            return self._result(snapshot)
        except GraphCancelledError:
            snapshot.status = "cancelled"
            snapshot.updated_at = utc_now()
            for state in snapshot.node_states.values():
                if state.status in {"pending", "waiting", "running"}:
                    state.status = "cancelled"
                    state.completed_at = utc_now()
            self._persist(snapshot)
            self._emit("graph_run_cancelled")
            return self._result(snapshot)
        except _GraphPaused:
            return self._result(snapshot)
        except Exception:
            if snapshot.status not in {"failed", "cancelled", "paused"}:
                snapshot.status = "failed"
                snapshot.updated_at = utc_now()
                self._persist(snapshot)
            raise

    def resume(self) -> ExecutionRunResult:
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot resume a run without an execution snapshot")
        self._pause_requested.clear()
        self._write_control(pause_requested=False, cancel_requested=False)
        return self.run(
            graph=snapshot.graph,
            run_id=snapshot.run_id,
            thread_id=snapshot.thread_id,
            request_id=snapshot.request_id,
            resume=True,
        )

    def rerun_from(self, node_id: str) -> ExecutionRunSnapshot:
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot rerun without an execution snapshot")
        rerun_ids = {node_id, *self.scheduler.dependency_descendants(snapshot.graph, node_id)}
        for rerun_id in rerun_ids:
            node = self._node(snapshot.graph, rerun_id)
            snapshot.node_states[rerun_id] = NodeExecutionState(node_id=rerun_id)
            node.status = "pending"
            node.output_artifact_ids = []
            node.attempt_count = 0
        snapshot.status = "paused"
        snapshot.updated_at = utc_now()
        self._persist(snapshot)
        self._emit("graph_partial_rerun_prepared", node_id=node_id, rerun_node_ids=sorted(rerun_ids))
        return snapshot

    def replay(self) -> ExecutionRunSnapshot:
        snapshot = self._load_snapshot()
        if snapshot is None:
            raise GraphExecutionError("cannot replay without an execution snapshot")
        events = read_jsonl(self.events_path)
        if not events or events[0].get("event_type") != "graph_snapshot_created":
            raise GraphExecutionError("execution event history is incomplete")
        last_persisted = [event for event in events if event.get("event_type") == "graph_snapshot_persisted"]
        if not last_persisted:
            raise GraphExecutionError("execution event history has no persisted snapshots")
        replayed = ExecutionRunSnapshot.model_validate(last_persisted[-1]["snapshot"])
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
    ) -> ExecutionRunSnapshot:
        validation = self.scheduler.validate(graph)
        if not validation.valid:
            raise GraphExecutionError(f"invalid execution graph: {';'.join(validation.errors)}")
        self._validate_graph_governance(graph, run_id=run_id)
        snapshot = ExecutionRunSnapshot(
            run_id=run_id,
            thread_id=thread_id,
            request_id=request_id,
            graph=graph.model_copy(deep=True),
            node_states={
                node.node_id: NodeExecutionState(node_id=node.node_id, status=node.status)
                for node in graph.nodes
                if node.node_kind == "subagent"
            },
        )
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
                governance.require_human_approval_for_high_risk
                and node.side_effect_level in governance.high_risk_side_effect_levels
                and (
                    not node.approval_id
                    or not self.approvals.validate(
                        approval_id=node.approval_id,
                        run_id=run_id,
                        node_id=node.node_id,
                        capability=str(node.capability),
                    )
                )
            ):
                raise GraphGovernanceError(
                    f"high-risk node requires human approval: {node.node_id}"
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

    def _seed_artifacts(
        self,
        snapshot: ExecutionRunSnapshot,
        drafts: list[ArtifactDraft],
    ) -> None:
        declared = {item.artifact_type for item in self.registry.get(snapshot.graph.domain_id).artifact_types}
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
        state.status = "running"
        state.started_at = utc_now()
        self._validate_node_authorization(snapshot, node)
        inputs = self._select_input_artifacts(snapshot, node)
        state.input_artifact_ids = [artifact.artifact_id for artifact in inputs]
        self._emit(
            "graph_node_started",
            node_id=node.node_id,
            input_artifact_ids=state.input_artifact_ids,
        )
        last_error: Exception | None = None
        try:
            idempotent_output = self._load_idempotency_output(snapshot, node)
            if idempotent_output is not None:
                artifacts = self._commit_outputs(snapshot, node, inputs, idempotent_output)
                state.output_artifact_ids = [artifact.artifact_id for artifact in artifacts]
                state.status = "completed"
                state.completed_at = utc_now()
                node.status = "completed"
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
                state.status = "completed"
                state.completed_at = utc_now()
                node.status = "completed"
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
            )
            for attempt in range(state.attempt_count + 1, node.max_attempts + 1):
                state.attempt_count = attempt
                if self._cancel_requested.is_set() or cancellation.is_set():
                    raise GraphCancelledError(f"node cancelled: {node.node_id}")
                started = perf_counter()
                try:
                    raw_output = executor(
                        ExecutionNodeContext(
                            run_id=snapshot.run_id,
                            thread_id=snapshot.thread_id,
                            request_id=snapshot.request_id,
                            graph=snapshot.graph,
                            node=node,
                            input_artifacts=inputs,
                            artifact_board=self.board,
                            cancellation=cancellation,
                        )
                    )
                    output = self._normalize_output(raw_output)
                    elapsed = perf_counter() - started
                    output.usage.duration_seconds += elapsed
                    self._reserve_output_budget(snapshot, node, output)
                    try:
                        artifacts = self._commit_outputs(snapshot, node, inputs, output)
                    except Exception:
                        self._release_output_budget(output)
                        raise
                    state.output_artifact_ids = [artifact.artifact_id for artifact in artifacts]
                    state.status = "completed"
                    state.completed_at = utc_now()
                    node.status = "completed"
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
            state.status = "failed"
            state.error = f"{last_error.__class__.__name__}: {last_error}" if last_error else "unknown"
            state.completed_at = utc_now()
            node.status = "failed"
            node.attempt_count = state.attempt_count
            self._emit("graph_node_failed", node_id=node.node_id, error=state.error)
            return _NodeOutcome(node=node, state=state, error=last_error)
        except GraphCancelledError as exc:
            state.status = "cancelled"
            state.error = str(exc)
            state.completed_at = utc_now()
            node.status = "cancelled"
            return _NodeOutcome(node=node, state=state, error=exc)
        finally:
            with self._lock:
                self._node_cancellations.pop(node.node_id, None)

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
            if "artifacts" in output or "usage" in output:
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
            )
            supersedes_artifact_id = draft.supersedes_artifact_id
            if supersedes_artifact_id is None and previous:
                supersedes_artifact_id = previous[-1].artifact_id
            committed.append(
                self.board.add(
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
        return committed

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
        manifest = self.registry.get(snapshot.graph.domain_id)
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
                    evaluations.append(
                        GateEvaluation(
                            gate_id=gate_id,
                            artifact_id=artifact.artifact_id,
                            verdict="fail" if reasons else "pass",
                            reason="; ".join(reasons) or gate.description or "gate passed",
                            blocking=gate.blocking,
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
            patch = self.dynamic_planner.expand_compound(
                graph=graph.model_copy(deep=True),
                compound=compound.model_copy(deep=True),
                artifacts=self.board.list_artifacts(run_id=snapshot.run_id),
            )
            if patch.base_revision != graph.revision:
                raise GraphExecutionError("dynamic planner returned a stale graph patch")
            graph = apply_execution_graph_patch(graph, patch)
            self._validate_graph_governance(graph, run_id=snapshot.run_id)
            snapshot.applied_patch_ids.append(patch.patch_id)
            self._sync_node_states(snapshot, graph)
            self._emit(
                "graph_compound_expanded",
                node_id=compound.node_id,
                patch_id=patch.patch_id,
                graph=graph.model_dump(mode="json"),
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
            if patch.base_revision != graph.revision:
                raise GraphExecutionError(
                    f"stale queued graph patch: {patch.base_revision}!={graph.revision}"
                )
            graph = apply_execution_graph_patch(graph, patch)
            self._validate_graph_governance(graph, run_id=snapshot.run_id)
            snapshot.applied_patch_ids.append(patch.patch_id)
            self._sync_node_states(snapshot, graph)
            if patch.decision_type == "stop":
                for state in snapshot.node_states.values():
                    if state.status in {"pending", "waiting"}:
                        state.status = "skipped"
                        state.error = patch.termination_reason or "planner_stopped_execution"
                        state.completed_at = utc_now()
            self._emit(
                "graph_patch_applied",
                patch_id=patch.patch_id,
                revision=graph.revision,
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
            and node.side_effect_level in governance.high_risk_side_effect_levels
            and (
                not node.approval_id
                or not self.approvals.validate(
                    approval_id=node.approval_id,
                    run_id=snapshot.run_id,
                    node_id=node.node_id,
                    capability=str(node.capability),
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
            raise GraphCancelledError("run timeout exceeded")
        if self._pause_requested.is_set() or control.get("pause_requested"):
            snapshot.status = "paused"
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
        write_json(self.graph_path, snapshot.graph.model_dump(mode="json"))
        write_json(self.snapshot_path, snapshot.model_dump(mode="json"))
        self._emit("graph_snapshot_persisted", snapshot=snapshot.model_dump(mode="json"))

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
            for contract in self.registry.get(snapshot.graph.domain_id).report_contracts
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
            for contract in self.registry.get(snapshot.graph.domain_id).report_contracts
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
            )

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {
            "event_type": event_type,
            "created_at": utc_now().isoformat(),
            **payload,
        }
        with self._lock:
            append_jsonl(self.events_path, event)
        if self.event_callback is not None:
            self.event_callback(event)

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
]
