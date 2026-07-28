from __future__ import annotations

from typing import TYPE_CHECKING, Any

from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.core_runtime import MainAgentState, RequestIntakeDecision
from research_system.schemas.execution_runtime import ExecutionRunResult
from research_system.schemas.protocol import ProgressState, ResponseEnvelope

if TYPE_CHECKING:
    from research_system.runtime.agent_os import AgentOS


class CanonicalGraphExecutor:
    """Forward the legacy Graph API into the canonical TurnRunner lifecycle."""

    def __init__(self, settings, *, agent_os: "AgentOS | None" = None) -> None:
        self.settings = settings
        self._agent_os = agent_os

    def _runtime(self) -> "AgentOS":
        if self._agent_os is None:
            from research_system.runtime.agent_os import AgentOS

            self._agent_os = AgentOS(self.settings)
        return self._agent_os

    def execute(self, *, context: Any, request: Any, graph: Any) -> None:
        runtime = self._runtime()
        thread_id = str(
            getattr(getattr(context, "thread", None), "thread_id", None)
            or getattr(request, "thread_id", None)
            or f"thread_{context.run_id}"
        )
        intent = str(getattr(request, "intent", None) or f"domain:{graph.domain_id}")
        resolved_request = request.model_copy(
            update={"thread_id": thread_id, "intent": intent}
        )
        decision = RequestIntakeDecision(
            status="accepted",
            request_kind="domain_task",
            recommended_mode="graph",
            intent=intent,
            domain_id=graph.domain_id,
            confidence=1.0,
            candidate_capabilities=[
                str(node.capability)
                for node in list(getattr(graph, "nodes", []) or [])
                if getattr(node, "capability", None)
            ],
        )
        input_payload = dict(resolved_request.slots.get("user_request") or {})
        input_payload.setdefault("query", resolved_request.user_query)
        declared_inputs = {
            port.artifact_type
            for node in list(getattr(graph, "nodes", []) or [])
            for port in list(getattr(node, "consumes", []) or [])
        }
        seed_artifacts = (
            [
                ArtifactDraft(
                    artifact_type="user_request",
                    content=input_payload,
                    source_refs=[{"type": "request", "id": resolved_request.request_id}],
                )
            ]
            if "user_request" in declared_inputs
            else []
        )
        outcome = runtime.turn_runner.execute_task(
            request=resolved_request,
            decision=decision,
            run_id=context.run_id,
            thread_id=thread_id,
            execution_mode="graph",
            graph=graph,
            seed_artifacts=seed_artifacts,
            resume=(context.paths.root / "execution_snapshot.json").exists(),
        )
        result = outcome.result
        if not isinstance(result, ExecutionRunResult):
            raise TypeError("TurnRunner Graph compatibility adapter returned an invalid result")
        runtime.turn_runner.finalize(
            outcome=outcome,
            response_factory=lambda value: ResponseEnvelope(
                request_id=resolved_request.request_id,
                run_id=context.run_id,
                thread_id=thread_id,
                mode="graph",
                status=outcome.status,
                message=f"Agent OS graph run {value.snapshot.status}.",
                progress=ProgressState(
                    current_phase="execution_graph",
                    active_agent="main_agent",
                    latest_event=f"graph_run_{value.snapshot.status}",
                    status_message=f"Agent OS graph run {value.snapshot.status}.",
                    percent=(
                        100
                        if outcome.status
                        in {"completed", "completed_partial", "gated", "failed", "cancelled", "timed_out"}
                        else 0
                    ),
                ),
                result={
                    "protocol": "agent_os",
                    "graph_revision": value.snapshot.graph.revision,
                    "artifact_ids": list(value.artifact_ids),
                    "final_artifact_ids": list(value.final_artifact_ids),
                },
                artifact_ids=list(value.artifact_ids),
                final_artifact_ids=list(value.final_artifact_ids),
            ),
        )
        context.execution_graph = result.snapshot.graph
        context.state.completed_nodes = [
            node_id
            for node_id, state in result.snapshot.node_states.items()
            if state.status == "completed"
        ]
        context.state.pending_nodes = [
            node_id
            for node_id, state in result.snapshot.node_states.items()
            if state.status in {"pending", "waiting", "running"}
        ]
        write_json(
            context.paths.root / "main_agent_state.json",
            MainAgentState(
                run_id=context.run_id,
                thread_id=thread_id,
                request_id=resolved_request.request_id,
                status=result.snapshot.status,
                mode="graph",
                intent=intent,
                request_kind="domain_task",
                graph_id=result.snapshot.graph.graph_id,
                graph_revision=result.snapshot.graph.revision,
                artifact_ids=list(result.artifact_ids),
            ).model_dump(mode="json"),
        )
        write_json(
            context.paths.result,
            {
                "protocol": "agent_os",
                "status": result.snapshot.status,
                "artifact_ids": list(result.artifact_ids),
                "final_artifact_ids": list(result.final_artifact_ids),
                "graph_revision": result.snapshot.graph.revision,
            },
        )


__all__ = ["CanonicalGraphExecutor"]
