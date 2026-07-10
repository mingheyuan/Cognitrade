from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.execution.core.execution_graph import ExecutionGraphScheduler
from research_system.runtime.execution.core.execution_graph_compat import (
    subagent_specs_to_execution_graph,
)
from research_system.runtime.storage.runs.subagent_dag_cache_store import SubagentDagCacheStore
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


@dataclass(frozen=True)
class SubagentDagNodeSpec:
    node_id: str
    build_invocation: Callable[[Mapping[str, "SubagentDagNodeResult"]], SubagentInvocation]
    depends_on: tuple[str, ...] = ()
    failure_mode: Literal["raise", "isolate"] = "raise"
    allow_partial_dependencies: bool = False
    max_attempts: int = 1
    replayable: bool = False
    cache_scope: Literal["run", "thread", "global"] | None = None
    cache_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentDagNodeResult:
    node_id: str
    status: Literal["completed", "degraded", "failed", "skipped"]
    outputs: Mapping[str, Any] | None = None
    artifact_id: str | None = None
    record: SubagentExecutionRecord | None = None
    invocation_id: str | None = None
    attempts: int = 0
    blocked_by: list[str] = field(default_factory=list)
    retry_errors: list[str] = field(default_factory=list)
    error_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cache_hit: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status in {"completed", "degraded"}


class _ArtifactContentView(Mapping[str, Any]):
    def __init__(self, board: ArtifactBoard, artifact_id: str) -> None:
        self.board = board
        self.artifact_id = artifact_id

    def _content(self) -> dict[str, Any]:
        artifact = self.board.get(self.artifact_id)
        if artifact is None:
            raise KeyError(f"missing subagent result artifact: {self.artifact_id}")
        return artifact.content

    def __getitem__(self, key: str) -> Any:
        return self._content()[key]

    def __iter__(self):
        return iter(self._content())

    def __len__(self) -> int:
        return len(self._content())


@dataclass
class _SubagentDagNodeOutcome:
    spec: SubagentDagNodeSpec
    invocation: SubagentInvocation
    attempts: int
    outputs: dict[str, Any] | None = None
    record: SubagentExecutionRecord | None = None
    retry_errors: list[str] = field(default_factory=list)
    error: Exception | None = None

    @property
    def error_summary(self) -> str | None:
        if self.error is None:
            return None
        return f"{self.error.__class__.__name__}: {self.error}"


class SubagentDagExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubagentDagCacheContext:
    dag_id: str
    run_root: Path
    run_id: str
    thread_id: str
    turn_id: str
    request_id: str
    source_run_root: Path | None = None
    source_thread_id: str | None = None
    action_id: str | None = None
    action_node_id: str | None = None


class SubagentDagExecutor:
    def __init__(
        self,
        *,
        invoke_subagent: Callable[[SubagentInvocation], tuple[dict[str, Any], SubagentExecutionRecord]],
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        self.invoke_subagent = invoke_subagent
        self.emit_event = emit_event

    def execute(
        self,
        nodes: list[SubagentDagNodeSpec],
        *,
        max_workers: int | None = None,
        cache_context: SubagentDagCacheContext | None = None,
        start_from_node_id: str | None = None,
    ) -> dict[str, SubagentDagNodeResult]:
        if not nodes:
            return {}
        node_map = self._build_node_map(nodes)
        self._validate_graph(node_map)
        try:
            execution_graph = subagent_specs_to_execution_graph(
                nodes,
                graph_id=cache_context.dag_id if cache_context is not None else "runtime-subagent-dag",
            )
        except ValueError as exc:
            raise SubagentDagExecutionError(f"invalid canonical subagent graph: {exc}") from exc
        graph_scheduler = ExecutionGraphScheduler()
        rerun_scope = self._rerun_scope(node_map, start_from_node_id)
        active_node_ids = self._active_node_ids(node_map, rerun_scope)
        results: dict[str, SubagentDagNodeResult] = {}
        artifact_board = self._artifact_board(cache_context)
        pending = {
            node_id: spec
            for node_id, spec in node_map.items()
            if node_id in active_node_ids
        }
        self._emit(
            event_type="subagent_dag_initialized",
            message="Initialized subagent DAG execution",
            payload={
                "dag_id": cache_context.dag_id if cache_context is not None else None,
                "dag_node_ids": list(node_map.keys()),
                "dag_node_count": len(node_map),
                "dag_edge_count": sum(len(spec.depends_on) for spec in node_map.values()),
            },
        )
        if start_from_node_id:
            self._emit(
                event_type="subagent_dag_partial_rerun_requested",
                message=f"Requested partial rerun from DAG node: {start_from_node_id}",
                payload={
                    "dag_id": cache_context.dag_id if cache_context is not None else None,
                    "start_from_node_id": start_from_node_id,
                    "active_node_ids": sorted(active_node_ids),
                    "rerun_scope": sorted(rerun_scope),
                },
            )
        if cache_context is not None:
            self._restore_cached_results(
                results=results,
                pending=pending,
                cache_context=cache_context,
                rerun_scope=rerun_scope,
                artifact_board=artifact_board,
            )

        while pending:
            completed_ids = [node_id for node_id, result in results.items() if result.succeeded]
            failed_ids = [node_id for node_id, result in results.items() if not result.succeeded]
            active_ids = list(pending)
            ready_ids = {
                node.node_id
                for node in graph_scheduler.ready_leaf_nodes(
                    execution_graph,
                    completed_node_ids=completed_ids,
                    failed_node_ids=failed_ids,
                    active_node_ids=active_ids,
                )
            }
            blocked_ids = {
                node.node_id
                for node in graph_scheduler.blocked_leaf_nodes(
                    execution_graph,
                    failed_node_ids=failed_ids,
                    active_node_ids=active_ids,
                )
            }
            ready_specs = [spec for node_id, spec in pending.items() if node_id in ready_ids]
            blocked_specs = [
                (
                    spec,
                    [dependency_id for dependency_id in spec.depends_on if dependency_id in failed_ids],
                )
                for node_id, spec in pending.items()
                if node_id in blocked_ids
            ]

            for spec, blocked_by in blocked_specs:
                pending.pop(spec.node_id, None)
                results[spec.node_id] = SubagentDagNodeResult(
                    node_id=spec.node_id,
                    status="skipped",
                    attempts=0,
                    blocked_by=list(blocked_by),
                    error_summary=f"blocked_by_dependencies:{','.join(blocked_by)}",
                    metadata=dict(spec.metadata),
                )
                self._emit(
                    event_type="subagent_dag_dependency_blocked",
                    message=f"Blocked DAG node: {spec.node_id}",
                    payload=self._event_payload(
                        spec,
                        {
                            "status": "skipped",
                            "blocked_by": list(blocked_by),
                            "depends_on": list(spec.depends_on),
                        },
                    ),
                )

            if not ready_specs:
                if not pending:
                    break
                raise SubagentDagExecutionError(
                    f"subagent dag deadlock or cycle detected: {sorted(pending)}"
                )

            self._emit(
                event_type="subagent_dag_frontier_updated",
                message="Updated subagent DAG frontier",
                payload={
                    "dag_ready_node_ids": [spec.node_id for spec in ready_specs],
                    "dag_pending_node_ids": sorted(pending),
                    "dag_completed_node_ids": [
                        node_id
                        for node_id, result in results.items()
                        if result.succeeded
                    ],
                    "status": "running",
                },
            )
            results_snapshot = dict(results)
            frontier_outcomes = self._execute_frontier(
                ready_specs,
                results_snapshot=results_snapshot,
                max_workers=max_workers,
            )

            for outcome in frontier_outcomes:
                spec = outcome.spec
                pending.pop(spec.node_id, None)
                for index, retry_error in enumerate(outcome.retry_errors, start=1):
                    self._emit(
                        event_type="subagent_dag_retry_scheduled",
                        message=f"Retrying DAG node: {spec.node_id}",
                        payload=self._event_payload(
                            spec,
                            {
                                "status": "retrying",
                                "attempt": index,
                                "max_attempts": max(spec.max_attempts, 1),
                                "error_summary": retry_error,
                            },
                            invocation=outcome.invocation,
                        ),
                    )
                if outcome.error is None and outcome.outputs is not None and outcome.record is not None:
                    artifact = self._persist_node_output(
                        artifact_board=artifact_board,
                        cache_context=cache_context,
                        spec=spec,
                        outputs=outcome.outputs,
                        record=outcome.record,
                        dependency_results=results,
                        invocation=outcome.invocation,
                    )
                    result = SubagentDagNodeResult(
                        node_id=spec.node_id,
                        status=str(outcome.record.status or "completed"),  # type: ignore[arg-type]
                        outputs=(
                            _ArtifactContentView(artifact_board, artifact.artifact_id)
                            if artifact is not None and artifact_board is not None
                            else outcome.outputs
                        ),
                        artifact_id=artifact.artifact_id if artifact is not None else None,
                        record=outcome.record,
                        invocation_id=outcome.invocation.invocation_id or outcome.record.invocation_id,
                        attempts=outcome.attempts,
                        retry_errors=list(outcome.retry_errors),
                        metadata=dict(spec.metadata),
                    )
                    results[spec.node_id] = result
                    self._save_cached_result(
                        cache_context=cache_context,
                        spec=spec,
                        result=result,
                    )
                    self._emit(
                        event_type="subagent_dag_node_completed",
                        message=f"Completed DAG node: {spec.node_id}",
                        payload=self._event_payload(
                            spec,
                            {
                                "status": result.status,
                                "attempts": outcome.attempts,
                                "notes": list(outcome.record.notes or []),
                                "output_keys": list(outcome.record.output_keys or []),
                                "degrade_reason": outcome.record.degrade_reason,
                                "degrade_reasons": list(outcome.record.degrade_reasons or []),
                            },
                            invocation=outcome.invocation,
                        ),
                    )
                    continue

                failed_result = SubagentDagNodeResult(
                    node_id=spec.node_id,
                    status="failed",
                    invocation_id=outcome.invocation.invocation_id,
                    attempts=outcome.attempts,
                    retry_errors=list(outcome.retry_errors),
                    error_summary=outcome.error_summary,
                    metadata=dict(spec.metadata),
                )
                if spec.failure_mode == "isolate":
                    results[spec.node_id] = failed_result
                    self._emit(
                        event_type="subagent_dag_node_isolated_failure",
                        message=f"Isolated DAG node failure: {spec.node_id}",
                        payload=self._event_payload(
                            spec,
                            {
                                "status": "failed",
                                "attempts": outcome.attempts,
                                "error_summary": outcome.error_summary,
                            },
                            invocation=outcome.invocation,
                        ),
                    )
                    continue
                self._emit(
                    event_type="subagent_dag_node_failed",
                    message=f"Failed DAG node: {spec.node_id}",
                    payload=self._event_payload(
                        spec,
                        {
                            "status": "failed",
                            "attempts": outcome.attempts,
                            "error_summary": outcome.error_summary,
                        },
                        invocation=outcome.invocation,
                    ),
                )
                raise SubagentDagExecutionError(
                    f"subagent dag node failed: {spec.node_id}: {outcome.error_summary}"
                ) from outcome.error

        return results

    def _restore_cached_results(
        self,
        *,
        results: dict[str, SubagentDagNodeResult],
        pending: dict[str, SubagentDagNodeSpec],
        cache_context: SubagentDagCacheContext,
        rerun_scope: set[str],
        artifact_board: ArtifactBoard | None,
    ) -> None:
        cache_store = SubagentDagCacheStore(run_root=cache_context.run_root)
        restored_node_ids: list[str] = []
        source_thread_id = str(cache_context.source_thread_id or cache_context.thread_id or "").strip()
        if not source_thread_id:
            return
        for node_id, spec in list(pending.items()):
            should_restore = bool(cache_context.source_run_root and spec.replayable)
            if rerun_scope and node_id in rerun_scope:
                should_restore = False
            if not should_restore:
                continue
            payload = cache_store.load_snapshot(
                dag_id=cache_context.dag_id,
                node_id=node_id,
                cache_scope=spec.cache_scope,
                cache_key=spec.cache_key,
                thread_id=source_thread_id,
                source_run_root=cache_context.source_run_root,
            )
            if not isinstance(payload, dict):
                continue
            restored_result = self._restored_result(spec=spec, payload=payload)
            if restored_result is None:
                continue
            restored_record = restored_result.record
            if restored_record is not None and restored_result.outputs is not None:
                artifact = self._persist_node_output(
                    artifact_board=artifact_board,
                    cache_context=cache_context,
                    spec=spec,
                    outputs=dict(restored_result.outputs),
                    record=restored_record,
                    dependency_results=results,
                    invocation=None,
                )
                if artifact is not None and artifact_board is not None:
                    restored_result.outputs = _ArtifactContentView(
                        artifact_board,
                        artifact.artifact_id,
                    )
                    restored_result.artifact_id = artifact.artifact_id
            results[node_id] = restored_result
            pending.pop(node_id, None)
            restored_node_ids.append(node_id)
            self._emit(
                event_type="subagent_dag_cache_hit",
                message=f"Restored cached DAG node: {node_id}",
                payload=self._event_payload(
                    spec,
                    {
                        "status": restored_result.status,
                        "cache_scope": spec.cache_scope,
                        "cache_key": spec.cache_key,
                        "source_run_id": payload.get("run_id"),
                        "source_thread_id": payload.get("thread_id"),
                        "cached_attempts": restored_result.attempts,
                    },
                ),
            )
            restored_record = restored_result.record
            if restored_record is not None:
                self._emit(
                    event_type="subagent_completed",
                    message=f"Restored cached subagent DAG node: {node_id}",
                    payload=self._event_payload(
                        spec,
                        {
                            "thread_id": cache_context.thread_id,
                            "turn_id": cache_context.turn_id,
                            "status": restored_result.status,
                            "objective": restored_record.objective,
                            "subagent_type": restored_record.subagent_type,
                            "invocation_id": restored_result.invocation_id,
                            "notes": list(restored_record.notes or []),
                            "degrade_reason": restored_record.degrade_reason,
                            "degrade_reasons": list(restored_record.degrade_reasons or []),
                            "output_keys": list(restored_record.output_keys or []),
                            "duration_ms": 0,
                        },
                    ),
                )
        if restored_node_ids:
            self._emit(
                event_type="subagent_dag_cache_restored",
                message="Restored cached subagent DAG nodes",
                payload={
                    "dag_id": cache_context.dag_id,
                    "cached_node_ids": restored_node_ids,
                    "cached_node_count": len(restored_node_ids),
                    "source_run_id": (
                        cache_context.source_run_root.name
                        if cache_context.source_run_root is not None
                        else None
                    ),
                },
            )

    def _save_cached_result(
        self,
        *,
        cache_context: SubagentDagCacheContext | None,
        spec: SubagentDagNodeSpec,
        result: SubagentDagNodeResult,
    ) -> None:
        if cache_context is None or not spec.replayable or result.outputs is None:
            return
        cache_store = SubagentDagCacheStore(run_root=cache_context.run_root)
        record_payload = (
            result.record.model_dump(mode="json")
            if result.record is not None
            else None
        )
        cache_store.save_snapshot(
            dag_id=cache_context.dag_id,
            node_id=spec.node_id,
            cache_scope=spec.cache_scope,
            cache_key=spec.cache_key,
            run_id=cache_context.run_id,
            thread_id=cache_context.thread_id,
            request_id=cache_context.request_id,
            status=result.status,
            attempts=result.attempts,
            outputs=dict(result.outputs),
            record_payload=record_payload,
            metadata=dict(result.metadata or {}),
        )

    def _restored_result(
        self,
        *,
        spec: SubagentDagNodeSpec,
        payload: dict[str, Any],
    ) -> SubagentDagNodeResult | None:
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict):
            return None
        raw_record = payload.get("record")
        record = None
        if isinstance(raw_record, dict) and raw_record:
            record = SubagentExecutionRecord.model_validate(raw_record)
            notes = list(record.notes or [])
            cache_note = f"cache_hit={payload.get('run_id')}:{spec.node_id}"
            if cache_note not in notes:
                notes.append(cache_note)
            record = record.model_copy(update={"notes": notes})
        metadata = dict(spec.metadata)
        if isinstance(payload.get("metadata"), dict):
            metadata.update(dict(payload.get("metadata") or {}))
        metadata["cache_hit"] = True
        metadata["cache_source_run_id"] = payload.get("run_id")
        metadata["cache_source_thread_id"] = payload.get("thread_id")
        status = str(payload.get("status") or (record.status if record is not None else "completed"))
        return SubagentDagNodeResult(
            node_id=spec.node_id,
            status=status,  # type: ignore[arg-type]
            outputs=dict(outputs),
            record=record,
            invocation_id=str(payload.get("invocation_id") or (record.invocation_id if record is not None else "")) or None,
            attempts=int(payload.get("attempts") or 0),
            metadata=metadata,
            cache_hit=True,
        )

    @staticmethod
    def _artifact_board(
        cache_context: SubagentDagCacheContext | None,
    ) -> ArtifactBoard | None:
        if cache_context is None:
            return None
        board = ArtifactBoard(cache_context.run_root / "artifact_board.jsonl")
        board.bind_thread(cache_context.thread_id)
        return board

    @staticmethod
    def _persist_node_output(
        *,
        artifact_board: ArtifactBoard | None,
        cache_context: SubagentDagCacheContext | None,
        spec: SubagentDagNodeSpec,
        outputs: dict[str, Any],
        record: SubagentExecutionRecord,
        dependency_results: Mapping[str, SubagentDagNodeResult],
        invocation: SubagentInvocation | None,
    ) -> ArtifactRecord | None:
        if artifact_board is None or cache_context is None:
            return None
        parent_artifact_ids = [
            str(dependency_results[dependency_id].artifact_id)
            for dependency_id in spec.depends_on
            if dependency_id in dependency_results
            and dependency_results[dependency_id].artifact_id
        ]
        source_refs = [
            {
                "type": "subagent_invocation",
                "invocation_id": str(
                    (invocation.invocation_id if invocation is not None else None)
                    or record.invocation_id
                    or ""
                ),
                "subagent_type": record.subagent_type,
            }
        ]
        return artifact_board.add(
            ArtifactRecord(
                run_id=cache_context.run_id,
                thread_id=cache_context.thread_id,
                request_id=cache_context.request_id,
                artifact_type="subagent_execution_output",
                schema_version="legacy.subagent.v1",
                producer=ArtifactProducer(
                    executor=record.subagent_type,
                    capability=str(spec.metadata.get("capability") or record.subagent_type),
                    node_id=spec.node_id,
                    subagent_type=record.subagent_type,
                    domain_id=str(spec.metadata.get("domain_id") or "finance_ashare"),
                    responsibility_node_id=spec.node_id,
                ),
                content=dict(outputs),
                source_refs=source_refs,
                parent_artifact_ids=parent_artifact_ids,
                confidence=(
                    float(outputs["confidence"])
                    if isinstance(outputs.get("confidence"), (int, float))
                    else (0.5 if record.status == "degraded" else 1.0)
                ),
                status=("needs_review" if record.status == "degraded" else "active"),
                metadata={
                    "dag_id": cache_context.dag_id,
                    "action_id": cache_context.action_id,
                    "action_node_id": cache_context.action_node_id,
                    "invocation_id": record.invocation_id,
                    "attempts": 0,
                },
            )
        )

    def _rerun_scope(
        self,
        node_map: dict[str, SubagentDagNodeSpec],
        start_from_node_id: str | None,
    ) -> set[str]:
        normalized = str(start_from_node_id or "").strip()
        if not normalized:
            return set()
        if normalized not in node_map:
            raise ValueError(f"unknown subagent dag rerun node: {normalized}")
        children: dict[str, set[str]] = {node_id: set() for node_id in node_map}
        for node_id, spec in node_map.items():
            for dependency_id in spec.depends_on:
                children.setdefault(dependency_id, set()).add(node_id)
        resolved = {normalized}
        stack = [normalized]
        while stack:
            current = stack.pop()
            for child_id in children.get(current, set()):
                if child_id in resolved:
                    continue
                resolved.add(child_id)
                stack.append(child_id)
        return resolved

    def _active_node_ids(
        self,
        node_map: dict[str, SubagentDagNodeSpec],
        rerun_scope: set[str],
    ) -> set[str]:
        if not rerun_scope:
            return set(node_map)
        resolved = set(rerun_scope)
        stack = list(rerun_scope)
        while stack:
            current = stack.pop()
            for dependency_id in node_map[current].depends_on:
                if dependency_id in resolved:
                    continue
                resolved.add(dependency_id)
                stack.append(dependency_id)
        return resolved

    def _execute_frontier(
        self,
        ready_specs: list[SubagentDagNodeSpec],
        *,
        results_snapshot: dict[str, SubagentDagNodeResult],
        max_workers: int | None,
    ) -> list[_SubagentDagNodeOutcome]:
        if len(ready_specs) <= 1 or (max_workers is not None and max_workers <= 1):
            return [
                self._execute_node(spec, results_snapshot=results_snapshot)
                for spec in ready_specs
            ]
        worker_count = min(len(ready_specs), max_workers or len(ready_specs))
        outcomes: list[_SubagentDagNodeOutcome | None] = [None] * len(ready_specs)
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="cognitrade-subagent-dag") as executor:
            futures = {
                executor.submit(self._execute_node, spec, results_snapshot=results_snapshot): index
                for index, spec in enumerate(ready_specs)
            }
            for future in as_completed(futures):
                outcomes[futures[future]] = future.result()
        return [outcome for outcome in outcomes if outcome is not None]

    def _execute_node(
        self,
        spec: SubagentDagNodeSpec,
        *,
        results_snapshot: dict[str, SubagentDagNodeResult],
    ) -> _SubagentDagNodeOutcome:
        invocation = spec.build_invocation(results_snapshot)
        invocation = invocation.model_copy(
            update={
                "inputs": self._materialize_artifact_views(invocation.inputs),
                "context": self._materialize_artifact_views(invocation.context),
                "fallback_inputs": self._materialize_artifact_views(invocation.fallback_inputs),
                "fallback_context": self._materialize_artifact_views(invocation.fallback_context),
                "error_fallback_inputs": self._materialize_artifact_views(
                    invocation.error_fallback_inputs
                ),
                "error_fallback_context": self._materialize_artifact_views(
                    invocation.error_fallback_context
                ),
            },
            deep=False,
        )
        retry_errors: list[str] = []
        attempts = max(int(spec.max_attempts or 1), 1)
        for attempt in range(1, attempts + 1):
            try:
                outputs, record = self.invoke_subagent(invocation)
                return _SubagentDagNodeOutcome(
                    spec=spec,
                    invocation=invocation,
                    attempts=attempt,
                    outputs=outputs,
                    record=record,
                    retry_errors=retry_errors,
                )
            except Exception as exc:
                if attempt >= attempts:
                    return _SubagentDagNodeOutcome(
                        spec=spec,
                        invocation=invocation,
                        attempts=attempt,
                        retry_errors=retry_errors,
                        error=exc,
                    )
                retry_errors.append(f"{exc.__class__.__name__}: {exc}")
        raise AssertionError("unreachable")

    @classmethod
    def _materialize_artifact_views(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): cls._materialize_artifact_views(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._materialize_artifact_views(item) for item in value]
        if isinstance(value, tuple):
            return [cls._materialize_artifact_views(item) for item in value]
        return value

    def _build_node_map(self, nodes: list[SubagentDagNodeSpec]) -> dict[str, SubagentDagNodeSpec]:
        node_map: dict[str, SubagentDagNodeSpec] = {}
        for spec in nodes:
            node_id = str(spec.node_id or "").strip()
            if not node_id:
                raise ValueError("subagent dag node_id is required")
            if node_id in node_map:
                raise ValueError(f"duplicate subagent dag node_id: {node_id}")
            node_map[node_id] = spec
        return node_map

    def _validate_graph(self, node_map: dict[str, SubagentDagNodeSpec]) -> None:
        indegree: dict[str, int] = {node_id: 0 for node_id in node_map}
        children: dict[str, list[str]] = {node_id: [] for node_id in node_map}
        for node_id, spec in node_map.items():
            for dependency_id in spec.depends_on:
                if dependency_id not in node_map:
                    raise ValueError(
                        f"unknown subagent dag dependency: {node_id} -> {dependency_id}"
                    )
                indegree[node_id] += 1
                children[dependency_id].append(node_id)
        queue = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            current = queue.pop(0)
            visited += 1
            for child_id in children[current]:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)
        if visited != len(node_map):
            raise ValueError("subagent dag contains a cycle")

    def _event_payload(
        self,
        spec: SubagentDagNodeSpec,
        payload: dict[str, Any],
        *,
        invocation: SubagentInvocation | None = None,
    ) -> dict[str, Any]:
        resolved = dict(spec.metadata)
        resolved.update(payload)
        resolved.setdefault("event", payload.get("event"))
        resolved["dag_node_id"] = spec.node_id
        resolved["depends_on"] = list(spec.depends_on)
        if spec.cache_scope:
            resolved.setdefault("cache_scope", spec.cache_scope)
        if spec.cache_key:
            resolved.setdefault("cache_key", spec.cache_key)
        if invocation is not None:
            invocation_context = dict(invocation.context or {})
            resolved.setdefault("objective", invocation.objective)
            resolved.setdefault("subagent_type", invocation.subagent_type)
            resolved.setdefault("invocation_id", invocation.invocation_id)
            if invocation_context.get("task_name"):
                resolved.setdefault("task_name", invocation_context["task_name"])
            if invocation_context.get("agent_path"):
                resolved.setdefault("agent_path", invocation_context["agent_path"])
            if invocation_context.get("child_thread_id"):
                resolved.setdefault("child_thread_id", invocation_context["child_thread_id"])
            if invocation_context.get("child_turn_id"):
                resolved.setdefault("child_turn_id", invocation_context["child_turn_id"])
        return resolved

    def _emit(self, *, event_type: str, message: str, payload: dict[str, Any]) -> None:
        if self.emit_event is None:
            return
        self.emit_event(
            event_type=event_type,
            message=message,
            payload=dict(payload) | {"event": event_type},
        )


__all__ = [
    "SubagentDagCacheContext",
    "SubagentDagExecutionError",
    "SubagentDagExecutor",
    "SubagentDagNodeResult",
    "SubagentDagNodeSpec",
]
