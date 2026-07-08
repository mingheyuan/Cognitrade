from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_RUNTIME_REGISTRATION_HINT_SECTIONS = (
    "snapshot_skill",
    "skill",
    "http_json",
    "openapi",
)


class _BaseRuntimeRegistrationHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str | None = None
    summary: str | None = None


class SnapshotSkillRuntimeRegistrationHint(_BaseRuntimeRegistrationHint):
    skill_name: str
    snapshot_type: str


class SkillRuntimeRegistrationHint(_BaseRuntimeRegistrationHint):
    skill_name: str


class HttpJsonRuntimeRegistrationHint(_BaseRuntimeRegistrationHint):
    source_id: str
    endpoint_path: str
    http_method: Literal["GET", "POST"]
    timeout_seconds: float | None = Field(default=None, gt=0.0)


class OpenApiRuntimeRegistrationHint(_BaseRuntimeRegistrationHint):
    source_id: str | None = None
    spec_uri: str | None = None
    operation_id: str | None = None
    spec: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_minimum_openapi_hint(self) -> "OpenApiRuntimeRegistrationHint":
        if self.operation_id or self.spec_uri or self.spec:
            return self
        raise ValueError("openapi runtime registration hints require operation_id, spec_uri, or spec")


class SubagentRuntimeRegistrationHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_skill: SnapshotSkillRuntimeRegistrationHint | None = None
    skill: SkillRuntimeRegistrationHint | None = None
    http_json: HttpJsonRuntimeRegistrationHint | None = None
    openapi: OpenApiRuntimeRegistrationHint | None = None


def runtime_registration_hint_payload(
    hints: SubagentRuntimeRegistrationHints | dict[str, Any] | None,
    *,
    section: str,
) -> dict[str, Any]:
    normalized_section = str(section or "").strip()
    if not normalized_section:
        return {}
    if isinstance(hints, SubagentRuntimeRegistrationHints):
        scoped = getattr(hints, normalized_section, None)
        if isinstance(scoped, BaseModel):
            return scoped.model_dump(mode="python", exclude_none=True)
        return {}
    if not isinstance(hints, dict):
        return {}
    scoped = hints.get(normalized_section)
    if isinstance(scoped, BaseModel):
        return scoped.model_dump(mode="python", exclude_none=True)
    if isinstance(scoped, dict):
        return dict(scoped)
    return {}


__all__ = [
    "HttpJsonRuntimeRegistrationHint",
    "OpenApiRuntimeRegistrationHint",
    "SkillRuntimeRegistrationHint",
    "SnapshotSkillRuntimeRegistrationHint",
    "SubagentRuntimeRegistrationHints",
    "SUPPORTED_RUNTIME_REGISTRATION_HINT_SECTIONS",
    "runtime_registration_hint_payload",
]
