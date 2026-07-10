from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.schemas.execution_graph import ArtifactPort, ExecutionGovernance


class ArtifactTypeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    schema_version: str = "1"
    description: str | None = None
    json_schema: dict[str, Any] = Field(default_factory=dict)


class CapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    capability: str
    description: str | None = None
    consumes: list[ArtifactPort] = Field(default_factory=list)
    produces: list[ArtifactPort] = Field(default_factory=list)
    implementation: str | None = None
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    cost_class: Literal["low", "medium", "high"] = "low"
    latency_class: Literal["low", "medium", "high"] = "low"
    reliability: float = Field(default=1.0, ge=0.0, le=1.0)
    side_effect_level: Literal["none", "read_only", "write_local", "write_external"] = "none"
    parallel_safe: bool = False
    max_concurrency: int | None = Field(default=None, ge=1)
    requires_llm: bool = False
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0.0)
    replayable: bool = True
    cache_scope: Literal["run", "thread", "global"] | None = None
    tags: list[str] = Field(default_factory=list)


class QualityGateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str
    artifact_type: str
    evaluator_capability: str | None = None
    blocking: bool = True
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ReportContractDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str
    artifact_type: str
    required_sections: list[str] = Field(default_factory=list)
    required_source_refs: bool = True
    required_gate_ids: list[str] = Field(default_factory=list)


class DomainPackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_id: str
    version: str
    description: str
    artifact_types: list[ArtifactTypeDefinition]
    capabilities: list[CapabilityDefinition]
    quality_gates: list[QualityGateDefinition] = Field(default_factory=list)
    report_contracts: list[ReportContractDefinition] = Field(default_factory=list)
    planner_policies: dict[str, Any] = Field(default_factory=dict)
    evaluations: list[str] = Field(default_factory=list)
    governance: ExecutionGovernance = Field(default_factory=ExecutionGovernance)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_manifest(self) -> "DomainPackManifest":
        self.domain_id = self.domain_id.strip()
        self.version = self.version.strip()
        if not self.domain_id or not self.version:
            raise ValueError("domain pack requires domain_id and version")
        artifact_ids = [item.artifact_type for item in self.artifact_types]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("domain pack contains duplicate artifact types")
        subagent_types = [item.subagent_type for item in self.capabilities]
        if len(subagent_types) != len(set(subagent_types)):
            raise ValueError("domain pack contains duplicate subagent types")
        declared_artifacts = set(artifact_ids)
        for capability in self.capabilities:
            for port in [*capability.consumes, *capability.produces]:
                if port.artifact_type not in declared_artifacts:
                    raise ValueError(
                        f"capability references undeclared artifact: {capability.capability}->{port.artifact_type}"
                    )
        gate_ids = {gate.gate_id for gate in self.quality_gates}
        for gate in self.quality_gates:
            if gate.artifact_type not in declared_artifacts:
                raise ValueError(f"quality gate references undeclared artifact: {gate.gate_id}")
        for contract in self.report_contracts:
            if contract.artifact_type not in declared_artifacts:
                raise ValueError(f"report contract references undeclared artifact: {contract.contract_id}")
            unknown_gate_ids = set(contract.required_gate_ids) - gate_ids
            if unknown_gate_ids:
                raise ValueError(
                    f"report contract references unknown gates: {contract.contract_id}:{sorted(unknown_gate_ids)}"
                )
        return self


class CapabilityPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_id: str
    objective: str
    available_artifact_types: list[str]
    required_artifact_types: list[str]
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_permissions: list[str] = Field(default_factory=list)
    allowed_side_effect_levels: list[
        Literal["none", "read_only", "write_local", "write_external"]
    ] = Field(default_factory=lambda: ["none", "read_only"])
    preferred_capabilities: list[str] = Field(default_factory=list)
    max_cost_class: Literal["low", "medium", "high"] = "high"
    max_latency_class: Literal["low", "medium", "high"] = "high"
    minimum_reliability: float = Field(default=0.0, ge=0.0, le=1.0)
    token_budget: int | None = Field(default=None, ge=1)
    cost_budget: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ArtifactTypeDefinition",
    "CapabilityDefinition",
    "CapabilityPlanningRequest",
    "DomainPackManifest",
    "QualityGateDefinition",
    "ReportContractDefinition",
]
