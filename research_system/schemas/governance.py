from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HumanApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(default_factory=generate_ulid)
    run_id: str
    node_id: str
    capability: str
    # These fields bind a decision to the exact governed execution context.
    # They remain optional for reading pre-canonical approval records; new
    # Agent OS approvals always populate them.
    graph_revision: int | None = Field(default=None, ge=1)
    domain_id: str | None = None
    domain_version: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    policy_fingerprint: str | None = None
    approved_by: str | None = None
    reason: str
    status: Literal["pending", "approved", "rejected", "revoked", "expired"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    decided_at: datetime | None = None


class GateEvaluationResult(BaseModel):
    """Provider-neutral result returned by a Pack quality-gate evaluator."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "fail", "needs_review"]
    reason: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0.0, le=1.0)


__all__ = ["GateEvaluationResult", "HumanApproval"]
