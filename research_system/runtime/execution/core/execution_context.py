from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from threading import Event, Semaphore
from typing import Any, Callable

from research_system.runtime.core.approvals import (
    HumanApprovalStore,
    approval_policy_fingerprint,
)
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.tool_registry import CoreToolContext, CoreToolRegistry
from research_system.schemas.artifacts import ArtifactRecord
from research_system.schemas.execution_graph import ExecutionGraph, ExecutionNode


class GraphExecutionError(RuntimeError):
    pass


class GraphGovernanceError(GraphExecutionError):
    pass


class GraphCancelledError(GraphExecutionError):
    pass


class GraphTimedOutError(GraphExecutionError):
    pass


class GraphNodeTimeoutError(GraphExecutionError):
    pass


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
    cache_root: Path
    cancellation: Event
    tool_registry: CoreToolRegistry | None = None
    skill_runtime: Any | None = None
    memory_store: Any | None = None
    settings: Any | None = None
    llm_provider: Any | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None
    turn_id: str | None = None
    progress_observer: Callable[[dict[str, Any]], dict[str, str] | None] | None = None
    progress_emitter: Callable[[dict[str, Any]], None] | None = None
    authorize_tool_call: Callable[[], bool] | None = None
    tool_semaphore: Semaphore | None = None
    approval_store: HumanApprovalStore | None = None
    failure_injection: Callable[[str, str], str | None] | None = None
    follow_up_messages: list[dict[str, Any]] = field(default_factory=list)

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
            turn_id=self.turn_id,
            cache_root=self.cache_root,
            artifact_board=self.artifact_board,
            skill_runtime=self.skill_runtime,
            memory_store=self.memory_store,
            settings=self.settings,
            graph_supplier=lambda: self.graph.model_dump(mode="json"),
            cancellation=self.cancellation,
            event_callback=self.event_callback,
            progress_emitter=self.progress_emitter,
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
                    policy_fingerprint=approval_policy_fingerprint(self.graph, self.node),
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
        result = self.tool_registry.invoke(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            graph_revision=self.graph.revision,
            node_id=self.node.node_id,
        )
        if self.progress_observer is not None and self.progress_emitter is not None:
            facts = {
                "event": "tool_completed",
                "run_id": self.run_id,
                "graph_revision": self.graph.revision,
                "node_id": self.node.node_id,
                "capability": self.node.capability,
                "tool_name": tool_name,
                "status": result.status,
                "output": json.dumps(result.output, ensure_ascii=False, default=str)[:2_000],
                "error": result.error,
            }
            try:
                progress = self.progress_observer(facts)
            except Exception:
                progress = None
            if progress:
                self.progress_emitter(
                    {
                        "node_id": self.node.node_id,
                        "phase": "tool_result",
                        "status": result.status,
                        "progress_source": "llm",
                        "current_action": progress["current"],
                        "next_action": progress["next"],
                    }
                )
        return result


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


__all__ = [
    "ExecutionNodeContext",
    "GateEvaluationContext",
    "GraphCancelledError",
    "GraphExecutionError",
    "GraphGovernanceError",
    "GraphNodeTimeoutError",
    "GraphTimedOutError",
]
