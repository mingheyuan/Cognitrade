from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Literal

from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


@dataclass(frozen=True)
class SubagentDagNodeSpec:
    node_id: str
    build_invocation: Callable[[Mapping[str, "SubagentDagNodeResult"]], SubagentInvocation]
    depends_on: tuple[str, ...] = ()
    failure_mode: Literal["raise", "isolate"] = "raise"
    allow_partial_dependencies: bool = False
    max_attempts: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentDagNodeResult:
    node_id: str
    status: Literal["completed", "degraded", "failed", "skipped"]
    outputs: dict[str, Any] | None = None
    record: SubagentExecutionRecord | None = None
    invocation_id: str | None = None
    attempts: int = 0
    blocked_by: list[str] = field(default_factory=list)
    retry_errors: list[str] = field(default_factory=list)
    error_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status in {"completed", "degraded"}


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
    ) -> dict[str, SubagentDagNodeResult]:
        if not nodes:
            return {}
        node_map = self._build_node_map(nodes)
        self._validate_graph(node_map)
        results: dict[str, SubagentDagNodeResult] = {}
        pending = dict(node_map)
        self._emit(
            event_type="subagent_dag_initialized",
            message="Initialized subagent DAG execution",
            payload={
                "dag_node_ids": list(node_map.keys()),
                "dag_node_count": len(node_map),
                "dag_edge_count": sum(len(spec.depends_on) for spec in node_map.values()),
            },
        )

        while pending:
            ready_specs: list[SubagentDagNodeSpec] = []
            blocked_specs: list[tuple[SubagentDagNodeSpec, list[str]]] = []
            for spec in list(pending.values()):
                if not all(dependency_id in results for dependency_id in spec.depends_on):
                    continue
                blocked_by = [
                    dependency_id
                    for dependency_id in spec.depends_on
                    if not results[dependency_id].succeeded
                ]
                if blocked_by:
                    if spec.allow_partial_dependencies:
                        ready_specs.append(spec)
                        continue
                    blocked_specs.append((spec, blocked_by))
                    continue
                ready_specs.append(spec)

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
                    result = SubagentDagNodeResult(
                        node_id=spec.node_id,
                        status=str(outcome.record.status or "completed"),  # type: ignore[arg-type]
                        outputs=outcome.outputs,
                        record=outcome.record,
                        invocation_id=outcome.invocation.invocation_id or outcome.record.invocation_id,
                        attempts=outcome.attempts,
                        retry_errors=list(outcome.retry_errors),
                        metadata=dict(spec.metadata),
                    )
                    results[spec.node_id] = result
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
    "SubagentDagExecutionError",
    "SubagentDagExecutor",
    "SubagentDagNodeResult",
    "SubagentDagNodeSpec",
]
