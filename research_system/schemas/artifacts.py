from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceRef(BaseModel):
    """Canonical provenance contract with legacy ``type/id`` aliases."""

    model_config = ConfigDict(extra="allow")

    source_type: str
    source_id: str | None = None
    uri: str | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = None
    locator: str | None = None
    provider: str | None = None
    version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.setdefault("source_type", payload.get("type"))
        payload.setdefault("source_id", payload.get("id") or payload.get("path"))
        payload.setdefault("locator", payload.get("path"))
        return payload

    @model_validator(mode="after")
    def validate_reference(self) -> "SourceRef":
        self.source_type = self.source_type.strip()
        if not self.source_type:
            raise ValueError("source ref source_type is required")
        if not any((self.source_id, self.uri, self.locator)):
            raise ValueError("source ref requires source_id, uri, or locator")
        return self


def normalize_source_refs(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("source_refs must be a list")
    normalized: list[dict[str, Any]] = []
    for item in value:
        reference = SourceRef.model_validate(item)
        payload = dict(item)
        canonical = reference.model_dump(mode="json", exclude_none=True)
        payload.update(canonical)
        # Preserve the fields used by existing Packs and historical runs.
        payload.setdefault("type", reference.source_type)
        if reference.source_id is not None:
            payload.setdefault("id", reference.source_id)
        normalized.append(payload)
    return normalized


class ArtifactProducer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executor: str
    capability: str
    node_id: str | None = None
    subagent_type: str | None = None
    domain_id: str | None = None
    responsibility_node_id: str | None = None


class ArtifactDraft(BaseModel):
    """A node output before the runtime assigns run and producer identity."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    schema_version: str = "1"
    content: dict[str, Any]
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    parent_artifact_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    status: Literal["active", "superseded", "needs_review", "rejected"] = "active"
    supersedes_artifact_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_contract_parent_name(cls, value):
        if isinstance(value, dict) and "parent_artifacts" in value and "parent_artifact_ids" not in value:
            value = {
                **{key: item for key, item in value.items() if key != "parent_artifacts"},
                "parent_artifact_ids": value["parent_artifacts"],
            }
        if isinstance(value, dict) and "source_refs" in value:
            value = {**value, "source_refs": normalize_source_refs(value["source_refs"])}
        return value

    @property
    def parent_artifacts(self) -> list[str]:
        return list(self.parent_artifact_ids)


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(default_factory=generate_ulid)
    artifact_type: str
    schema_version: str = "1"
    artifact_version: int = Field(default=1, ge=1)
    run_id: str
    thread_id: str
    request_id: str
    producer: ArtifactProducer
    content: dict[str, Any]
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    parent_artifact_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    status: Literal["active", "superseded", "needs_review", "rejected"] = "active"
    supersedes_artifact_id: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def accept_contract_parent_name(cls, value):
        if isinstance(value, dict) and "parent_artifacts" in value and "parent_artifact_ids" not in value:
            value = {
                **{key: item for key, item in value.items() if key != "parent_artifacts"},
                "parent_artifact_ids": value["parent_artifacts"],
            }
        if isinstance(value, dict) and "source_refs" in value:
            value = {**value, "source_refs": normalize_source_refs(value["source_refs"])}
        return value

    @model_validator(mode="after")
    def validate_artifact(self) -> "ArtifactRecord":
        self.artifact_type = self.artifact_type.strip()
        self.schema_version = self.schema_version.strip()
        if not self.artifact_type:
            raise ValueError("artifact_type is required")
        if not self.schema_version:
            raise ValueError("artifact schema_version is required")
        if self.artifact_id in self.parent_artifact_ids:
            raise ValueError("artifact cannot reference itself as parent")
        if self.supersedes_artifact_id == self.artifact_id:
            raise ValueError("artifact cannot supersede itself")
        if self.producer.responsibility_node_id is None and self.producer.node_id is not None:
            self.producer.responsibility_node_id = self.producer.node_id
        return self

    @property
    def parent_artifacts(self) -> list[str]:
        """Canonical contract name retained alongside the storage field."""
        return list(self.parent_artifact_ids)


__all__ = ["ArtifactDraft", "ArtifactProducer", "ArtifactRecord", "SourceRef", "normalize_source_refs"]
