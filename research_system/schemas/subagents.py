from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.subagent_runtime_registration import (
    RuntimeRegistrationType,
    validate_runtime_registration_shape,
    validate_runtime_registration_template_defaults,
)
from research_system.schemas.subagent_runtime_registration_hints import SubagentRuntimeRegistrationHints


class SubagentInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    invocation_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    objective: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    requires_llm: bool = False
    fallback_subagent_type: str | None = None
    fallback_inputs: dict[str, Any] = Field(default_factory=dict)
    fallback_objective: str | None = None
    fallback_context: dict[str, Any] = Field(default_factory=dict)
    error_fallback_subagent_type: str | None = None
    error_fallback_inputs: dict[str, Any] = Field(default_factory=dict)
    error_fallback_objective: str | None = None
    error_fallback_context: dict[str, Any] = Field(default_factory=dict)


class RoleContract(BaseModel):
    """Governed capability contract for builtin, Pack, or runtime roles."""

    model_config = ConfigDict(extra="forbid")

    role_id: str
    role_version: str
    display_name: str
    objective: str
    input_artifact_types: list[str] = Field(default_factory=list)
    output_artifact_types: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    allowed_permissions: list[str] = Field(default_factory=list)
    max_model_turns: int = Field(default=1, ge=1)
    timeout_seconds: int = Field(default=30, ge=1)
    requires_approval: bool = False
    can_spawn_child_turn: bool = False
    failure_mode: Literal["halt", "continue", "report"] = "halt"
    source: Literal["builtin", "pack", "runtime", "user"] = "builtin"

    @model_validator(mode="after")
    def validate_tools(self) -> "RoleContract":
        overlap = set(self.allowed_tools) & set(self.denied_tools)
        if overlap:
            raise ValueError(f"role tools cannot be both allowed and denied: {sorted(overlap)}")
        return self


class SubagentExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    invocation_id: str | None = None
    status: Literal["completed", "skipped", "degraded", "failed"] = "completed"
    objective: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_keys: list[str] = Field(default_factory=list)
    degrade_reason: str | None = None
    degrade_reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SubagentRuntimeRegistrationTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    registration_type: RuntimeRegistrationType
    summary: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_template_defaults(self) -> "SubagentRuntimeRegistrationTemplate":
        validate_runtime_registration_template_defaults(
            registration_type=str(self.registration_type or "").strip(),
            defaults=dict(self.defaults or {}),
        )
        return self


class SubagentCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_type: str
    summary: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    stage: str | None = None
    cost_class: Literal["low", "medium", "high"] | None = None
    latency_class: Literal["low", "medium", "high"] | None = None
    side_effect_level: Literal["none", "read_only", "write_local", "write_external"] = "none"
    suitable_intents: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    requires_llm: bool = False
    allow_parallel: bool = False
    max_concurrency: int | None = None
    fallback_subagent_type: str | None = None
    error_fallback_subagent_type: str | None = None
    input_schema_name: str | None = None
    output_schema_name: str | None = None
    input_json_schema: dict[str, Any] | None = None
    output_json_schema: dict[str, Any] | None = None
    runtime_registration_hints: SubagentRuntimeRegistrationHints = Field(default_factory=SubagentRuntimeRegistrationHints)
    runtime_registration_templates: list[SubagentRuntimeRegistrationTemplate] = Field(default_factory=list)


class SubagentCapabilityGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str = Field(default_factory=generate_ulid)
    intent: str | None = None
    stage: str | None = None
    capability: str
    preferred_subagent_type: str | None = None
    requires_llm: bool | None = None
    reason: str
    candidate_subagent_types: list[str] = Field(default_factory=list)
    available_stage_capabilities: list[str] = Field(default_factory=list)
    available_intents: list[str] = Field(default_factory=list)
    policy_source: Literal["rule", "llm", "hybrid", "explicit"] = "rule"
    notes: list[str] = Field(default_factory=list)


class SubagentToolDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovery_request_id: str = Field(default_factory=generate_ulid)
    intent: str | None = None
    stage: str | None = None
    capability: str
    search_query: str
    preferred_protocols: list[str] = Field(default_factory=lambda: ["mcp", "openapi", "skill", "http_json"])
    reason: str
    policy_source: Literal["rule", "llm", "hybrid", "explicit"] = "rule"
    notes: list[str] = Field(default_factory=list)


class SubagentDiscoveryCandidateRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_subagent_type: str
    source_protocol: str | None = None
    reason: str
    policy_source: Literal["rule", "llm", "hybrid", "explicit", "llm_shadow"] = "rule"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class SubagentDiscoveryReviewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["pending", "approved", "rejected"]
    reviewer: str
    reason: str
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    previous_status: Literal["pending", "approved", "rejected"] | None = None
    source: str = "manual"


class SubagentDiscoveryTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    capability_gap: SubagentCapabilityGap
    tool_discovery_request: SubagentToolDiscoveryRequest
    reviewer: str | None = None
    review_reason: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    candidate_catalog_entries: list[SubagentCatalogEntry] = Field(default_factory=list)
    candidate_recommendation: SubagentDiscoveryCandidateRecommendation | None = None
    candidate_shadow_recommendation: SubagentDiscoveryCandidateRecommendation | None = None
    activated_entries: list["SubagentActivationEntry"] = Field(default_factory=list)
    review_history: list[SubagentDiscoveryReviewEvent] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SubagentRuntimeRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_type: RuntimeRegistrationType
    skill_name: str | None = None
    snapshot_type: str | None = None
    source_id: str | None = None
    tool_name: str | None = None
    endpoint_path: str | None = None
    http_method: Literal["GET", "POST"] | None = None
    openapi_spec_uri: str | None = None
    openapi_operation_id: str | None = None
    registration_template_id: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registration_shape(self) -> "SubagentRuntimeRegistration":
        registration_type = str(self.registration_type or "").strip()
        normalized_skill_name = str(self.skill_name or "").strip()
        normalized_snapshot_type = str(self.snapshot_type or "").strip()
        normalized_source_id = str(self.source_id or "").strip()
        normalized_tool_name = str(self.tool_name or "").strip()
        normalized_endpoint_path = str(self.endpoint_path or "").strip()
        normalized_http_method = str(self.http_method or "").strip().upper()
        normalized_openapi_spec_uri = str(self.openapi_spec_uri or "").strip()
        normalized_openapi_operation_id = str(self.openapi_operation_id or "").strip()
        validate_runtime_registration_shape(
            registration_type=registration_type,
            skill_name=normalized_skill_name,
            snapshot_type=normalized_snapshot_type,
            source_id=normalized_source_id,
            tool_name=normalized_tool_name,
            endpoint_path=normalized_endpoint_path,
            http_method=normalized_http_method,
            openapi_spec_uri=normalized_openapi_spec_uri,
            openapi_operation_id=normalized_openapi_operation_id,
            has_timeout_seconds=self.timeout_seconds is not None,
        )
        return self


class SubagentActivationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_id: str = Field(default_factory=generate_ulid)
    ticket_id: str
    subagent_type: str
    delegate_subagent_type: str | None = None
    runtime_registration: SubagentRuntimeRegistration | None = None
    catalog_entry: SubagentCatalogEntry
    status: Literal["active", "inactive"] = "active"
    activated_by: str | None = None
    activation_reason: str | None = None
    activated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_activation_target(self) -> "SubagentActivationEntry":
        has_delegate = bool(str(self.delegate_subagent_type or "").strip())
        has_runtime_registration = self.runtime_registration is not None
        if has_delegate == has_runtime_registration:
            raise ValueError("SubagentActivationEntry requires exactly one of delegate_subagent_type or runtime_registration")
        return self
