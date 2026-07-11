from __future__ import annotations

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.core.main_agent import MainAgentPlanRole
from research_system.runtime.core.tool_registry import (
    build_core_tool_registry,
    register_domain_pack_tools,
    register_mcp_tools,
)
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.planning.capability_planner import (
    CapabilityDynamicPlanner,
    CapabilityPlanner,
)
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.core_runtime import MainAgentState
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.memory.layers import LayeredMemoryStore


class CanonicalGraphExecutor:
    """Executes the canonical Agent OS graph for an orchestrator request."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.registry = DomainPackRegistry.load(
            settings.domain_packs_root,
            require_integrity=getattr(settings, "domain_pack_require_integrity", False),
            trusted_digests=getattr(settings, "domain_pack_trusted_digests", {}),
        )
        self.plan_agent = MainAgentPlanRole(
            CapabilityDynamicPlanner(CapabilityPlanner(self.registry))
        )
        self.tool_registry = register_mcp_tools(
            register_domain_pack_tools(
                build_core_tool_registry(settings=settings),
                self.registry,
            ),
            settings=settings,
        )
        memory_retrieval_backend = None
        if getattr(settings, "memory_vector_search_enabled", False) or settings.qdrant_url:
            try:
                from research_system.adapters.storage.qdrant_adapter import QdrantAdapter

                memory_retrieval_backend = QdrantAdapter(settings)
            except Exception:
                memory_retrieval_backend = None
        self.memory = LayeredMemoryStore(
            settings.memory_root / "agent_os",
            retrieval_backend=memory_retrieval_backend,
        )

    def execute(self, *, context, request: RequestEnvelope, graph: ExecutionGraph) -> None:
        runner = ExecutionGraphRunner(
            registry=self.registry,
            run_root=context.paths.root,
            dynamic_planner=self.plan_agent,
            memory_store=self.memory,
            tool_registry=self.tool_registry,
            settings=self.settings,
        )
        input_payload = dict(request.slots.get("user_request") or {})
        input_payload.setdefault("query", request.user_query)
        input_payload.setdefault("symbols", list(request.slots.get("symbols") or []))
        result = runner.run(
            graph=graph,
            run_id=context.run_id,
            thread_id=context.state.thread_id or context.run_id,
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
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
            context.paths.root / "main_agent_state.json",
            MainAgentState(
                run_id=context.run_id,
                thread_id=context.state.thread_id or context.run_id,
                request_id=request.request_id,
                status=result.snapshot.status,
                mode="graph",
                intent=request.intent,
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
                "artifact_ids": result.artifact_ids,
                "final_artifact_ids": result.final_artifact_ids,
                "graph_revision": result.snapshot.graph.revision,
            },
        )


__all__ = ["CanonicalGraphExecutor"]
