from __future__ import annotations

from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.state_machine import NodeStatus


ExecutionNodeKind = Literal["compound", "subagent"]
ExecutionNodeStatus = NodeStatus
ExecutionFailureMode = Literal["halt", "isolate"]
ExecutionGraphStrategy = Literal[
    "template",
    "static",
    "dynamic",
    "catalog",
    "rule",
    "llm",
    "hybrid",
]


class ArtifactPort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    schema_version: str = "1"
    required: bool = True
    multiple: bool = False
    selector: dict[str, Any] = Field(default_factory=dict)


class ExecutionGovernance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_nodes: int = Field(default=256, ge=1)
    max_depth: int = Field(default=16, ge=1)
    max_concurrency: int = Field(default=32, ge=1)
    max_retries_per_node: int = Field(default=3, ge=0)
    max_graph_patches: int = Field(default=32, ge=0)
    max_tool_calls: int = Field(default=256, ge=0)
    max_concurrent_tool_calls: int = Field(default=32, ge=1)
    token_budget: int | None = Field(default=None, ge=1)
    cost_budget: float | None = Field(default=None, ge=0.0)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    required_gate_ids: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_permissions: list[str] = Field(default_factory=list)
    require_source_refs: bool = True
    require_confidence: bool = True
    require_human_approval_for_high_risk: bool = True
    high_risk_side_effect_levels: list[str] = Field(
        default_factory=lambda: ["write_external"]
    )
    cache_enabled: bool = True
    idempotency_required: bool = True


class ExecutionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_kind: ExecutionNodeKind
    executor: str | None = None
    capability: str | None = None
    parent_node_id: str | None = None
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    consumes: list[ArtifactPort] = Field(default_factory=list)
    produces: list[ArtifactPort] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    status: ExecutionNodeStatus = "pending"
    tags: list[str] = Field(default_factory=list)
    failure_mode: ExecutionFailureMode = "halt"
    allow_partial_dependencies: bool = False
    max_attempts: int = Field(default=1, ge=1)
    attempt_count: int = Field(default=0, ge=0)
    replayable: bool = True
    cache_scope: Literal["run", "thread", "global"] | None = None
    cache_key: str | None = None
    parallel_safe: bool = False
    max_concurrency: int | None = Field(default=None, ge=1)
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0.0)
    side_effect_level: Literal["none", "read_only", "write_local", "write_external"] = "none"
    approval_id: str | None = None
    idempotency_key: str | None = None
    expansion_policy: Literal["eager", "lazy"] = "eager"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_node_shape(self) -> "ExecutionNode":
        self.node_id = self.node_id.strip()
        if not self.node_id:
            raise ValueError("execution node_id is required")
        if self.parent_node_id == self.node_id:
            raise ValueError(f"execution node cannot parent itself: {self.node_id}")
        if self.node_id in self.depends_on:
            raise ValueError(f"execution node cannot depend on itself: {self.node_id}")
        if self.node_kind == "subagent":
            if not str(self.executor or "").strip():
                raise ValueError(f"subagent execution node requires executor: {self.node_id}")
            if not str(self.capability or "").strip():
                raise ValueError(f"subagent execution node requires capability: {self.node_id}")
        elif self.executor is not None:
            raise ValueError(f"compound execution node cannot declare executor: {self.node_id}")
        return self


class ExecutionGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_id: str = Field(default_factory=generate_ulid)
    domain_id: str
    objective: str
    intent: str | None = None
    strategy: ExecutionGraphStrategy = "dynamic"
    revision: int = Field(default=1, ge=1)
    nodes: list[ExecutionNode] = Field(default_factory=list)
    governance: ExecutionGovernance = Field(default_factory=ExecutionGovernance)
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionGraph":
        self.domain_id = self.domain_id.strip()
        self.objective = self.objective.strip()
        if not self.domain_id:
            raise ValueError("execution graph domain_id is required")
        if not self.objective:
            raise ValueError("execution graph objective is required")
        if len(self.nodes) > self.governance.max_nodes:
            raise ValueError(
                f"execution graph node limit exceeded: {len(self.nodes)}>{self.governance.max_nodes}"
            )

        node_map: dict[str, ExecutionNode] = {}
        for node in self.nodes:
            if node.node_id in node_map:
                raise ValueError(f"duplicate execution graph node: {node.node_id}")
            node_map[node.node_id] = node

        for node in self.nodes:
            if node.parent_node_id is not None:
                parent = node_map.get(node.parent_node_id)
                if parent is None:
                    raise ValueError(
                        f"unknown execution graph parent: {node.node_id}->{node.parent_node_id}"
                    )
                if parent.node_kind != "compound":
                    raise ValueError(
                        f"execution graph parent must be compound: {node.node_id}->{node.parent_node_id}"
                    )
            for dependency_id in node.depends_on:
                if dependency_id not in node_map:
                    raise ValueError(
                        f"unknown execution graph dependency: {node.node_id}->{dependency_id}"
                    )
            for port in node.consumes:
                producer_node_id = str(
                    port.selector.get("producer_node_id") or ""
                ).strip()
                if not producer_node_id:
                    continue
                if producer_node_id not in node_map:
                    raise ValueError(
                        f"unknown artifact producer: {node.node_id}:{port.artifact_type}"
                        f"<-{producer_node_id}"
                    )
                if producer_node_id not in node.depends_on:
                    raise ValueError(
                        f"artifact producer must be a dependency: {node.node_id}"
                        f"->{producer_node_id}"
                    )
                producer = node_map[producer_node_id]
                if not any(
                    output.artifact_type == port.artifact_type
                    for output in producer.produces
                ):
                    raise ValueError(
                        f"artifact producer does not produce {port.artifact_type}: "
                        f"{producer_node_id}"
                    )

        self._validate_acyclic(
            node_map=node_map,
            edge_resolver=lambda node: list(node.depends_on),
            label="dependency",
        )
        self._validate_acyclic(
            node_map=node_map,
            edge_resolver=lambda node: [node.parent_node_id] if node.parent_node_id else [],
            label="hierarchy",
        )
        self._validate_depth(node_map)
        return self

    @staticmethod
    def _validate_acyclic(*, node_map, edge_resolver, label: str) -> None:
        indegree = {node_id: 0 for node_id in node_map}
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_map}
        for node in node_map.values():
            for upstream_id in edge_resolver(node):
                adjacency[upstream_id].append(node.node_id)
                indegree[node.node_id] += 1
        frontier = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = 0
        while frontier:
            node_id = frontier.popleft()
            visited += 1
            for downstream_id in adjacency[node_id]:
                indegree[downstream_id] -= 1
                if indegree[downstream_id] == 0:
                    frontier.append(downstream_id)
        if visited != len(node_map):
            raise ValueError(f"execution graph contains {label} cycle")

    def _validate_depth(self, node_map: dict[str, ExecutionNode]) -> None:
        for node in self.nodes:
            depth = 1
            parent_id = node.parent_node_id
            while parent_id is not None:
                depth += 1
                if depth > self.governance.max_depth:
                    raise ValueError(
                        f"execution graph depth limit exceeded: {node.node_id}>{self.governance.max_depth}"
                    )
                parent_id = node_map[parent_id].parent_node_id


class ExecutionGraphPatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(default_factory=generate_ulid)
    op_type: Literal[
        "insert_node",
        "remove_node",
        "rewire_dependency",
        "replace_subgraph",
        "rerun_from",
        "mark_terminal",
        "request_replan",
    ]
    target_node_id: str | None = None
    node: ExecutionNode | None = None
    replacement_nodes: list[ExecutionNode] = Field(default_factory=list)
    new_depends_on: list[str] = Field(default_factory=list)
    remove_depends_on: list[str] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> "ExecutionGraphPatchOperation":
        if self.op_type == "insert_node" and self.node is None:
            raise ValueError("insert_node requires node")
        if self.op_type in {"remove_node", "rewire_dependency", "replace_subgraph", "rerun_from", "mark_terminal"}:
            if not str(self.target_node_id or "").strip():
                raise ValueError(f"{self.op_type} requires target_node_id")
        if self.op_type == "replace_subgraph" and not self.replacement_nodes:
            raise ValueError("replace_subgraph requires replacement_nodes")
        return self


class ExecutionGraphPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_id: str = Field(default_factory=generate_ulid)
    base_revision: int = Field(ge=1)
    decision_type: Literal["continue", "stop", "replan"] = "continue"
    operations: list[ExecutionGraphPatchOperation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    policy_source: Literal["rule", "llm", "hybrid", "explicit"] = "rule"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    actor: str = "main_agent.plan_agent"
    termination_reason: str | None = None
    requested_at_safe_point: bool = True
    cancel_in_flight_node_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_patch(self) -> "ExecutionGraphPatch":
        if not self.operations:
            raise ValueError("execution graph patch requires at least one operation")
        if len(self.cancel_in_flight_node_ids) != len(set(self.cancel_in_flight_node_ids)):
            raise ValueError("execution graph patch contains duplicate cancellation targets")
        if not self.actor.strip():
            raise ValueError("execution graph patch actor is required")
        return self


__all__ = [
    "ArtifactPort",
    "ExecutionGraph",
    "ExecutionGraphPatch",
    "ExecutionGraphPatchOperation",
    "ExecutionGovernance",
    "ExecutionNode",
]
