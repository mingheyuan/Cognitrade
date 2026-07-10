from __future__ import annotations

from collections import Counter
from typing import Any

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.storage.jsonl import read_json, read_jsonl
from research_system.schemas.execution_runtime import ExecutionRunResult, ExecutionRunSnapshot
from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope


class AgentOSRunService:
    """Versioned API adapter backed exclusively by canonical Agent OS state."""

    def __init__(self, agent_os: AgentOS) -> None:
        self.agent_os = agent_os

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        return self._response(request, self.agent_os.run_request(request))

    def history(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.agent_os.run_store.list_runs():
            run_id = str(row.get("run_id") or "").strip()
            if not run_id:
                continue
            try:
                snapshot = self._snapshot(run_id)
            except (FileNotFoundError, KeyError, ValueError):
                continue
            rows.append(
                {
                    "run_id": run_id,
                    "request_id": snapshot.request_id,
                    "intent": self._intent(snapshot),
                    "domain_id": snapshot.graph.domain_id,
                    "status": snapshot.status,
                    "graph_id": snapshot.graph.graph_id,
                    "graph_revision": snapshot.graph.revision,
                    "updated_at": snapshot.updated_at,
                }
            )
        return rows

    def replay(self, run_id: str) -> dict[str, Any]:
        detail = self.agent_os.inspect_run(run_id)
        snapshot = self.agent_os.replay(run_id)
        return {
            "run_id": run_id,
            "request": detail.get("request"),
            "snapshot": snapshot.model_dump(mode="json"),
            "artifacts": detail.get("artifacts") or [],
            "events": self._execution_events(run_id),
        }

    def events(self, run_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(run_id)
        events = self._execution_events(run_id)
        return {
            "run_id": run_id,
            "status": snapshot.status,
            "events": events,
            "event_counts": {
                "events": len(events),
                "distinct_event_types": len(
                    {str(event.get("event_type") or "") for event in events}
                ),
            },
        }

    def trace(self, run_id: str) -> dict[str, Any]:
        detail = self.agent_os.inspect_run(run_id)
        snapshot = self._snapshot(run_id)
        events = self._execution_events(run_id)
        return {
            "run_id": run_id,
            "request": detail.get("request"),
            "snapshot": snapshot.model_dump(mode="json"),
            "artifacts": detail.get("artifacts") or [],
            "dynamic_trace": events,
            "event_counts": {
                "events": len(events),
                "graph_events": len(events),
            },
        }

    def steps(self, run_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(run_id)
        rows = [
            {
                "step_index": index,
                "node_id": node.node_id,
                "node_kind": node.node_kind,
                "capability": node.capability,
                "executor": node.executor,
                "objective": node.objective,
                "status": (
                    snapshot.node_states[node.node_id].status
                    if node.node_id in snapshot.node_states
                    else node.status
                ),
                "depends_on": list(node.depends_on),
                "tags": list(node.tags),
                "input_artifact_types": [port.artifact_type for port in node.consumes],
                "output_artifact_types": [port.artifact_type for port in node.produces],
            }
            for index, node in enumerate(snapshot.graph.nodes, start=1)
        ]
        status_counts = Counter(row["status"] for row in rows)
        return {
            "run_id": run_id,
            "graph_id": snapshot.graph.graph_id,
            "domain_id": snapshot.graph.domain_id,
            "intent": self._intent(snapshot),
            "strategy": snapshot.graph.strategy,
            "revision": snapshot.graph.revision,
            "notes": list(snapshot.graph.notes),
            "steps": rows,
            "step_counts": {
                "total": len(rows),
                "active": status_counts.get("running", 0),
                "completed": status_counts.get("completed", 0),
                "pending": status_counts.get("pending", 0),
            },
            "status_counts": dict(status_counts),
        }

    def metrics(self, run_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(run_id)
        event_counts = Counter(
            str(event.get("event_type") or "")
            for event in self._execution_events(run_id)
        )
        node_status_counts = Counter(
            state.status for state in snapshot.node_states.values()
        )
        return {
            "run_id": run_id,
            "status": snapshot.status,
            "counts": {
                "nodes": len(snapshot.node_states),
                "completed_nodes": node_status_counts.get("completed", 0),
                "failed_nodes": node_status_counts.get("failed", 0),
                "artifacts": len(self.agent_os.inspect_run(run_id).get("artifacts") or []),
                "gates": len(snapshot.gate_evaluations),
            },
            "usage": snapshot.usage.model_dump(mode="json"),
            "node_status_counts": dict(node_status_counts),
            "event_type_counts": dict(event_counts),
            "graph_event_counts": dict(event_counts),
        }

    def thread(self, run_id: str, thread_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(run_id)
        if thread_id != snapshot.thread_id:
            raise KeyError(f"thread not found: {thread_id}")
        return self._thread_payload(run_id, snapshot)

    def thread_replay(self, run_id: str, thread_id: str) -> dict[str, Any]:
        return self.thread(run_id, thread_id)

    def thread_metrics(self, run_id: str, thread_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(run_id)
        if thread_id != snapshot.thread_id:
            raise KeyError(f"thread not found: {thread_id}")
        payload = self.metrics(run_id)
        payload.update(
            {
                "thread_id": thread_id,
                "scope": "execution_graph",
                "scope_thread_ids": [thread_id],
            }
        )
        return payload

    def subagent(self, run_id: str, invocation_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(run_id)
        node = next(
            (node for node in snapshot.graph.nodes if node.node_id == invocation_id),
            None,
        )
        if node is None or node.node_kind != "subagent":
            raise KeyError(f"subagent node not found: {invocation_id}")
        state = snapshot.node_states[invocation_id]
        events = [
            event
            for event in self._execution_events(run_id)
            if str(event.get("node_id") or "") == invocation_id
        ]
        return {
            "run_id": run_id,
            "invocation_id": invocation_id,
            "node_id": node.node_id,
            "executor": node.executor,
            "capability": node.capability,
            "status": state.status,
            "attempt_count": state.attempt_count,
            "input_artifact_ids": list(state.input_artifact_ids),
            "output_artifact_ids": list(state.output_artifact_ids),
            "error": state.error,
            "events": events,
        }

    def resume(self, run_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(run_id)
        return {
            "run_id": run_id,
            "status": snapshot.status,
            "resumable": snapshot.status == "paused",
            "resume_from": {
                "graph_revision": snapshot.graph.revision,
                "pending_nodes": [
                    node_id
                    for node_id, state in snapshot.node_states.items()
                    if state.status in {"pending", "waiting", "running"}
                ],
            },
        }

    def cancel(self, run_id: str) -> dict[str, Any]:
        cancelled = self.agent_os.cancel(run_id)
        snapshot = self._snapshot(run_id)
        return {
            "run_id": run_id,
            "status": snapshot.status,
            "message": "Cancellation requested" if cancelled else "Run is not active",
            "cancellation_requested": cancelled,
        }

    def rerun_from(self, run_id: str, *, from_node: str) -> ResponseEnvelope:
        request = self._request_envelope(run_id)
        return self._response(request, self.agent_os.rerun_from(run_id, from_node))

    def _response(
        self,
        request: RequestEnvelope,
        result: ExecutionRunResult,
    ) -> ResponseEnvelope:
        snapshot = result.snapshot
        response_status = {
            "paused": "running",
            "gated": "failed",
        }.get(snapshot.status, snapshot.status)
        artifacts = self.agent_os.inspect_run(snapshot.run_id).get("artifacts") or []
        final_ids = set(result.final_artifact_ids)
        return ResponseEnvelope(
            request_id=snapshot.request_id,
            run_id=snapshot.run_id,
            intent=self._intent(snapshot),
            status=response_status,
            message=f"Agent OS run {snapshot.status}",
            progress=ProgressState(
                current_phase="execution_graph",
                latest_event=f"graph_run_{snapshot.status}",
                status_message=f"Agent OS run {snapshot.status}",
                percent=100 if snapshot.status in {"completed", "failed", "cancelled", "gated"} else 0,
            ),
            result={
                "protocol": "agent_os",
                "graph_id": snapshot.graph.graph_id,
                "graph_revision": snapshot.graph.revision,
                "artifact_ids": list(result.artifact_ids),
                "final_artifact_ids": list(result.final_artifact_ids),
            },
            artifacts=[
                artifact for artifact in artifacts
                if str(artifact.get("artifact_id") or "") in final_ids
            ],
        )

    def _snapshot(self, run_id: str) -> ExecutionRunSnapshot:
        detail = self.agent_os.inspect_run(run_id)
        return ExecutionRunSnapshot.model_validate(detail["snapshot"])

    def _request_envelope(self, run_id: str) -> RequestEnvelope:
        root = self.agent_os.run_store.load_run_paths(run_id).root
        payload = read_json(root / "request.json")
        if not isinstance(payload, dict):
            raise ValueError(f"missing request payload: {run_id}")
        return RequestEnvelope.model_validate(payload)

    def _execution_events(self, run_id: str) -> list[dict[str, Any]]:
        root = self.agent_os.run_store.load_run_paths(run_id).root
        return [
            row for row in read_jsonl(root / "execution_events.jsonl")
            if isinstance(row, dict)
        ]

    def _thread_payload(
        self,
        run_id: str,
        snapshot: ExecutionRunSnapshot,
    ) -> dict[str, Any]:
        detail = self.agent_os.inspect_run(run_id)
        return {
            "run_id": run_id,
            "thread_id": snapshot.thread_id,
            "scope": "execution_graph",
            "scope_thread_ids": [snapshot.thread_id],
            "snapshot": snapshot.model_dump(mode="json"),
            "artifacts": detail.get("artifacts") or [],
            "events": self._execution_events(run_id),
        }

    @staticmethod
    def _intent(snapshot: ExecutionRunSnapshot) -> str:
        return str(
            snapshot.graph.metadata.get("intent")
            or snapshot.graph.intent
            or f"domain:{snapshot.graph.domain_id}"
        )


__all__ = ["AgentOSRunService"]
