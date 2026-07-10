from __future__ import annotations

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.protocol import RequestEnvelope


class CanonicalGraphExecutor:
    """Executes the canonical Agent OS graph for an orchestrator request."""

    def __init__(self, settings) -> None:
        self.settings = settings

    def execute(self, *, context, request: RequestEnvelope, graph: ExecutionGraph) -> None:
        runner = ExecutionGraphRunner(
            registry=DomainPackRegistry.load(self.settings.domain_packs_root),
            run_root=context.paths.root,
        )
        input_payload = dict(request.slots.get("user_request") or {})
        input_payload.setdefault("query", request.user_query)
        input_payload.setdefault("symbols", list(request.slots.get("symbols") or []))
        result = runner.run(
            graph=graph,
            run_id=context.run_id,
            thread_id=context.state.thread_id or context.run_id,
            request_id=request.request_id,
            seed_artifacts=[
                ArtifactDraft(
                    artifact_type="user_request",
                    content=input_payload,
                    source_refs=[{"type": "request", "id": request.request_id}],
                )
            ],
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
            context.paths.result,
            {
                "protocol": "agent_os",
                "status": result.snapshot.status,
                "artifact_ids": result.artifact_ids,
                "final_artifact_ids": result.final_artifact_ids,
                "graph_revision": result.snapshot.graph.revision,
            },
        )


__all__ = ["CanonicalGraphExecutor"]
