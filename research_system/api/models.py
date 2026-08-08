from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

class ApiRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RerunRequest(ApiRequestModel):
    from_node: str = Field(min_length=1, max_length=128)


class FundHoldingReportCreateRequest(ApiRequestModel):
    fund_code: str = Field(pattern=r"^\d{6}$")
    profile: Literal["lite", "medium", "deep"]
    source_artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    confirm_stale: bool = False
    force: bool = False


class FundHoldingReportRetryRequest(ApiRequestModel):
    symbols: list[str] = Field(default_factory=list, max_length=10)
    force: bool = False

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: object) -> object:
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        return list(
            dict.fromkeys(
                str(item).strip()
                for item in value
                if str(item).strip()
            )
        )


class RunApprovalRequest(ApiRequestModel):
    node_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=256)
    approval_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2_000)


class RunApprovalRejectRequest(ApiRequestModel):
    reason: str = Field(min_length=1, max_length=2_000)


class MemoryPromotionRequest(ApiRequestModel):
    target_layer: Literal["domain", "policy"]
    evaluation_id: str = Field(min_length=1, max_length=128)
    evaluator: str = Field(min_length=1, max_length=256)
    verdict: Literal["pass", "fail", "needs_review"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    content: dict[str, Any] | None = None


class MemoryRevokeRequest(ApiRequestModel):
    reason: str = Field(min_length=1, max_length=2_000)


class ChatThreadRenameRequest(ApiRequestModel):
    thread_label: str = Field(min_length=1, max_length=120)

    @field_validator("thread_label", mode="before")
    @classmethod
    def normalize_thread_label(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return " ".join(value.split())


class ChatResearchRequest(ApiRequestModel):
    intent: str | None = Field(default=None, max_length=128)
    user_query: str = Field(min_length=1, max_length=20_000)
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    slots: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ApiRequestModel",
    "ChatResearchRequest",
    "ChatThreadRenameRequest",
    "FundHoldingReportCreateRequest",
    "FundHoldingReportRetryRequest",
    "MemoryPromotionRequest",
    "MemoryRevokeRequest",
    "RerunRequest",
    "RunApprovalRequest",
    "RunApprovalRejectRequest",
]
