from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.schemas.execution_graph import ArtifactPort, ExecutionGovernance
from research_system.schemas.tools import ToolDefinition


class ArtifactTypeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    schema_version: str = "1"
    description: str | None = None
    json_schema: dict[str, Any] = Field(default_factory=dict)


class DomainPackDependency(BaseModel):
    """A dependency that must be satisfied before a Pack is activated."""

    model_config = ConfigDict(extra="forbid")

    domain_id: str
    min_version: str | None = None
    max_version: str | None = None
    optional: bool = False

    @model_validator(mode="after")
    def validate_dependency(self) -> "DomainPackDependency":
        self.domain_id = self.domain_id.strip()
        if not self.domain_id:
            raise ValueError("domain pack dependency requires domain_id")
        if self.min_version and self.max_version:
            if _version_key(self.min_version) > _version_key(self.max_version):
                raise ValueError(
                    f"domain pack dependency has an inverted version range: {self.domain_id}"
                )
        if self.min_version:
            _version_key(self.min_version)
        if self.max_version:
            _version_key(self.max_version)
        return self


class PackIntegrity(BaseModel):
    """Integrity metadata for the Pack payload (excluding domain.yaml)."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["sha256"] = "sha256"
    payload_sha256: str | None = None
    signer: str | None = None

    @model_validator(mode="after")
    def validate_integrity(self) -> "PackIntegrity":
        if self.payload_sha256 is not None:
            normalized = self.payload_sha256.strip().lower()
            if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
                raise ValueError("Pack integrity payload_sha256 must be a 64-character SHA-256 digest")
            self.payload_sha256 = normalized
        if self.signer is not None:
            self.signer = self.signer.strip() or None
        return self


def _version_key(value: str) -> tuple[int, ...]:
    """Normalize the intentionally small Pack compatibility version format."""

    raw = str(value or "").strip()
    parts = raw.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"Pack versions must be numeric dotted versions: {value}")
    return tuple(int(part) for part in parts)


class SkillDefinition(BaseModel):
    """Versioned Pack-owned Skill contract.

    A Skill is an instruction/methodology bundle. Its implementation still
    executes through the Agent OS Tool Registry and is not a second runtime.
    """

    model_config = ConfigDict(extra="forbid")

    skill_name: str
    version: str = "1"
    description: str
    implementation: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    allowed_callers: list[Literal["main_agent", "subagent", "system"]] = Field(
        default_factory=lambda: ["subagent"]
    )
    side_effect_level: Literal["none", "read_only", "write_local", "write_external"] = "none"
    source_refs_required: bool = False

    @model_validator(mode="after")
    def validate_skill(self) -> "SkillDefinition":
        self.skill_name = self.skill_name.strip()
        self.version = self.version.strip()
        self.description = self.description.strip()
        self.implementation = self.implementation.strip()
        if not self.skill_name or not self.version or not self.description or not self.implementation:
            raise ValueError("Pack Skill requires name, version, description and implementation")
        if not self.allowed_callers:
            raise ValueError(f"Pack Skill has no allowed callers: {self.skill_name}")
        return self


class IntentDefinition(BaseModel):
    """A Pack-owned request intent and its planning hints."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str
    description: str | None = None
    examples: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)
    optional_slots: list[str] = Field(default_factory=list)
    candidate_capabilities: list[str] = Field(default_factory=list)
    output_artifact_types: list[str] = Field(default_factory=list)
    clarification_questions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_intent(self) -> "IntentDefinition":
        self.intent_id = self.intent_id.strip()
        if not self.intent_id:
            raise ValueError("Pack intent_id is required")
        if set(self.required_slots) & set(self.optional_slots):
            raise ValueError(
                f"Pack intent declares a slot as both required and optional: {self.intent_id}"
            )
        for slot, question in self.clarification_questions.items():
            if not str(slot).strip() or not str(question).strip():
                raise ValueError(
                    f"Pack intent clarification questions must have non-empty keys and values: {self.intent_id}"
                )
        return self


class PackIntakeDefinition(BaseModel):
    """The declarative boundary between RequestIntake and a Pack resolver."""

    model_config = ConfigDict(extra="forbid")

    implementation: str | None = None
    intents: list[IntentDefinition] = Field(default_factory=list)
    minimum_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_intake(self) -> "PackIntakeDefinition":
        intent_ids = [item.intent_id for item in self.intents]
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("Pack intake contains duplicate intent ids")
        if self.intents and not str(self.implementation or "").strip():
            raise ValueError("Pack intake with declared intents requires an implementation")
        return self


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
    failure_mode: Literal["halt", "isolate"] = "halt"
    parallel_safe: bool = False
    max_concurrency: int | None = Field(default=None, ge=1)
    requires_llm: bool = False
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0.0)
    replayable: bool = True
    cache_scope: Literal["run", "thread", "global"] | None = None
    fallback_capabilities: list[str] = Field(default_factory=list)
    fallback_subagent_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


PackToolDefinition = ToolDefinition


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
    dependencies: list[DomainPackDependency] = Field(default_factory=list)
    integrity: PackIntegrity = Field(default_factory=PackIntegrity)
    artifact_types: list[ArtifactTypeDefinition]
    capabilities: list[CapabilityDefinition]
    skills: list[SkillDefinition] = Field(default_factory=list)
    intake: PackIntakeDefinition = Field(default_factory=PackIntakeDefinition)
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
        _version_key(self.version)
        dependency_ids = [item.domain_id for item in self.dependencies]
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("domain pack contains duplicate dependencies")
        if self.domain_id in set(dependency_ids):
            raise ValueError("domain pack cannot depend on itself")
        artifact_ids = [item.artifact_type for item in self.artifact_types]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("domain pack contains duplicate artifact types")
        subagent_types = [item.subagent_type for item in self.capabilities]
        if len(subagent_types) != len(set(subagent_types)):
            raise ValueError("domain pack contains duplicate subagent types")
        skill_names = [item.skill_name for item in self.skills]
        if len(skill_names) != len(set(skill_names)):
            raise ValueError("domain pack contains duplicate Skill names")
        declared_artifacts = set(artifact_ids)
        capability_ids = {item.capability for item in self.capabilities}
        subagent_types = {item.subagent_type for item in self.capabilities}
        declared_intents = {item.intent_id for item in self.intake.intents}
        for intent in self.intake.intents:
            unknown_capabilities = set(intent.candidate_capabilities) - capability_ids
            if unknown_capabilities:
                raise ValueError(
                    f"Pack intent references unknown capabilities: "
                    f"{intent.intent_id}:{sorted(unknown_capabilities)}"
                )
            unknown_artifacts = set(intent.output_artifact_types) - declared_artifacts
            if unknown_artifacts:
                raise ValueError(
                    f"Pack intent references undeclared artifacts: "
                    f"{intent.intent_id}:{sorted(unknown_artifacts)}"
                )
        legacy_intents = set(self.planner_policies.get("supported_intents") or [])
        if declared_intents and legacy_intents and not declared_intents.issuperset(legacy_intents):
            raise ValueError(
                "Pack intake intents must include every legacy supported_intents entry"
            )
        for capability in self.capabilities:
            unknown_fallback_capabilities = set(capability.fallback_capabilities) - capability_ids
            if unknown_fallback_capabilities:
                raise ValueError(
                    f"capability references unknown fallback capabilities: "
                    f"{capability.capability}:{sorted(unknown_fallback_capabilities)}"
                )
            unknown_fallback_subagents = set(capability.fallback_subagent_types) - subagent_types
            if unknown_fallback_subagents:
                raise ValueError(
                    f"capability references unknown fallback subagents: "
                    f"{capability.capability}:{sorted(unknown_fallback_subagents)}"
                )
            for port in [*capability.consumes, *capability.produces]:
                if port.artifact_type not in declared_artifacts:
                    raise ValueError(
                        f"capability references undeclared artifact: {capability.capability}->{port.artifact_type}"
                    )
        gate_ids = {gate.gate_id for gate in self.quality_gates}
        for gate in self.quality_gates:
            if gate.artifact_type not in declared_artifacts:
                raise ValueError(f"quality gate references undeclared artifact: {gate.gate_id}")
            if gate.evaluator_capability and gate.evaluator_capability not in capability_ids:
                raise ValueError(
                    f"quality gate references unknown evaluator capability: "
                    f"{gate.gate_id}:{gate.evaluator_capability}"
                )
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
    "DomainPackDependency",
    "DomainPackManifest",
    "IntentDefinition",
    "PackIntegrity",
    "PackIntakeDefinition",
    "PackToolDefinition",
    "QualityGateDefinition",
    "ReportContractDefinition",
    "SkillDefinition",
]
