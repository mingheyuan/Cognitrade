from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


MemoryLayer = Literal["episodic", "domain", "policy"]
MemoryScope = Literal["run", "thread", "user", "tenant", "domain", "global"]


class MemoryEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    evaluator: str
    verdict: Literal["pass", "fail", "needs_review"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=utc_now)


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(default_factory=generate_ulid)
    layer: MemoryLayer
    domain_id: str
    memory_type: str
    content: dict[str, Any]
    scope: MemoryScope = "domain"
    tenant_id: str | None = None
    user_id: str | None = None
    thread_id: str | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    source_run_ids: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    parent_memory_ids: list[str] = Field(default_factory=list)
    evaluations: list[MemoryEvaluation] = Field(default_factory=list)
    status: Literal["candidate", "verified", "promoted", "rejected", "revoked", "expired"] = "candidate"
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    promoted_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provenance(self) -> "MemoryRecord":
        if not self.source_artifact_ids and not self.parent_memory_ids:
            raise ValueError("memory record requires artifact or memory provenance")
        if self.scope == "tenant" and not self.tenant_id:
            raise ValueError("tenant-scoped memory requires tenant_id")
        if self.scope == "user" and not self.user_id:
            raise ValueError("user-scoped memory requires user_id")
        if self.scope == "thread" and not self.thread_id:
            raise ValueError("thread-scoped memory requires thread_id")
        return self


__all__ = ["MemoryEvaluation", "MemoryLayer", "MemoryRecord", "MemoryScope"]
