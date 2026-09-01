from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "uzi-3.9.4-b3665e8-v1"
LEGACY_UZI_CONTRACT_VERSIONS = frozenset({"uzi-3.9.4-1b5cdcb-v1"})
SUPPORTED_UZI_CONTRACT_VERSIONS = frozenset(
    {CONTRACT_VERSION, *LEGACY_UZI_CONTRACT_VERSIONS}
)


def is_supported_uzi_contract_version(value: object) -> bool:
    """Return whether an immutable UZI v3.9.4 contract remains readable."""

    return str(value or "") in SUPPORTED_UZI_CONTRACT_VERSIONS


class EvidenceGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"

    @property
    def report_eligible(self) -> bool:
        return self in {EvidenceGrade.A, EvidenceGrade.B}


class SnapshotStatus(str, Enum):
    AVAILABLE = "available"
    VERIFIED_EMPTY = "verified_empty"
    DATA_GAP = "data_gap"
    REVOKED = "revoked"

    @property
    def structurally_complete(self) -> bool:
        return self in {SnapshotStatus.AVAILABLE, SnapshotStatus.VERIFIED_EMPTY}


class ConflictStatus(str, Enum):
    OPEN = "open"
    AUTO_RESOLVED = "auto_resolved"
    MANUALLY_RESOLVED = "manually_resolved"
    DISMISSED = "dismissed"

    @property
    def blocks_reports(self) -> bool:
        return self == ConflictStatus.OPEN


class BundleStatus(str, Enum):
    PUBLISHED = "published"
    INVALIDATED = "invalidated"


class ManifestStatus(str, Enum):
    FROZEN = "frozen"
    INVALIDATED = "invalidated"


class CollectionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


def require_aware_utc(value: datetime, *, field_name: str = "datetime") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def cutoff_at_for_date(as_of_date: date, exchange_timezone: str) -> datetime:
    try:
        zone = ZoneInfo(exchange_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown exchange timezone: {exchange_timezone}") from exc
    local_cutoff = datetime.combine(as_of_date, time.max, tzinfo=zone)
    return local_cutoff.astimezone(timezone.utc)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return require_aware_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=128)
    source_url: str | None = Field(default=None, max_length=2048)
    source_kind: str = Field(default="structured", max_length=64)
    title: str | None = Field(default=None, max_length=512)
    mime_type: str | None = Field(default=None, max_length=128)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    content_ref: str | None = Field(default=None, max_length=2048)
    inline_content: str | None = None
    published_at: datetime
    observed_at: datetime
    grade: EvidenceGrade
    immutable_proof: bool = False
    is_manual: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("published_at", "observed_at")
    @classmethod
    def _aware_times(cls, value: datetime, info) -> datetime:
        return require_aware_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _manual_and_reconstruction_rules(self) -> EvidenceInput:
        if self.published_at > self.observed_at:
            raise ValueError("evidence cannot be observed before it is published")
        if self.is_manual and self.grade == EvidenceGrade.A:
            raise ValueError("manual evidence cannot receive grade A")
        if self.grade == EvidenceGrade.B and not self.immutable_proof:
            raise ValueError("grade B evidence requires immutable_proof")
        if not any((self.content_hash, self.content_ref, self.inline_content, self.source_url)):
            raise ValueError("evidence requires content, a content reference, hash, or source URL")
        if self.inline_content is not None and len(self.inline_content.encode("utf-8")) > 262_144:
            raise ValueError("inline evidence content exceeds the 256 KiB database limit")
        return self

    def resolved_content_hash(self) -> str:
        if self.content_hash:
            return self.content_hash.lower()
        return canonical_hash(
            {
                "inline_content": self.inline_content,
                "content_ref": self.content_ref,
                "source_url": self.source_url,
                "metadata": self.metadata,
            }
        )

    def evidence_key(self) -> str:
        return canonical_hash(
            {
                "provider": self.provider,
                "source_url": self.source_url,
                "content_hash": self.resolved_content_hash(),
                "published_at": self.published_at,
                "grade": self.grade,
                "immutable_proof": self.immutable_proof,
                "is_manual": self.is_manual,
            }
        )


class SeriesPointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series_key: str = Field(min_length=1, max_length=256)
    point_at: datetime
    effective_at: datetime
    published_at: datetime
    observed_at: datetime
    value: dict[str, Any]
    source_version: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("point_at", "effective_at", "published_at", "observed_at")
    @classmethod
    def _aware_times(cls, value: datetime, info) -> datetime:
        return require_aware_utc(value, field_name=info.field_name)


class SnapshotIngestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=64)
    market: str = Field(min_length=1, max_length=32)
    exchange_timezone: str = Field(min_length=1, max_length=64)
    dimension: str = Field(min_length=1, max_length=64)
    contract_version: str = CONTRACT_VERSION
    as_of_date: date
    status: SnapshotStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    effective_at: datetime
    published_at: datetime
    observed_at: datetime
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_version: str | None = Field(default=None, max_length=128)
    source_priority: int = Field(default=100, ge=0, le=10000)
    origin: Literal["provider", "manual", "derived"] = "provider"
    query_manifest: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceInput] = Field(default_factory=list)
    series_points: list[SeriesPointInput] = Field(default_factory=list)
    observation_metadata: dict[str, Any] = Field(default_factory=dict)
    collection_task_id: str | None = Field(default=None, max_length=26)
    provider_attempt_id: str | None = Field(default=None, max_length=128)

    @field_validator("symbol", "market")
    @classmethod
    def _normalize_identifier(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("dimension", "exchange_timezone", "contract_version")
    @classmethod
    def _strip_identifier(cls, value: str) -> str:
        return value.strip()

    @field_validator("effective_at", "published_at", "observed_at", "ingested_at")
    @classmethod
    def _aware_times(cls, value: datetime, info) -> datetime:
        return require_aware_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _causal_order(self) -> SnapshotIngestInput:
        # A provider can ingest an already observed item later, but it cannot claim
        # to have observed evidence after the write that records the observation.
        if self.observed_at > self.ingested_at:
            raise ValueError("observed_at cannot be later than ingested_at")
        if self.published_at > self.observed_at:
            raise ValueError("snapshot cannot be observed before it is published")
        if any(item.observed_at > self.ingested_at for item in self.evidence):
            raise ValueError("snapshot cannot ingest evidence before that evidence was observed")
        if self.origin == "manual" and any(item.grade == EvidenceGrade.A for item in self.evidence):
            raise ValueError("manual snapshots cannot contain grade A evidence")
        return self

    @property
    def cutoff_at(self) -> datetime:
        return cutoff_at_for_date(self.as_of_date, self.exchange_timezone)


class DimensionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    ordinal: int
    label: str
    status: SnapshotStatus | None = None
    snapshot_id: str | None = None
    content_hash: str | None = None
    evidence_grade: EvidenceGrade | None = None
    stale: bool = False
    conflict: bool = False
    eligible: bool = False
    reason: str | None = None
    published_at: datetime | None = None
    observed_at: datetime | None = None
    degraded: bool = False
    excluded_from_scoring: bool = False
    native_quality: str | None = None
    audit_eligible: bool = False
    audit_reason: str | None = None
    audit_errors: list[str] = Field(default_factory=list)


class CoverageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    market: str
    exchange_timezone: str
    as_of_date: date
    cutoff_at: datetime
    contract_version: str
    expected_dimension_count: int
    ready_dimension_count: int
    available_count: int
    verified_empty_count: int
    gap_count: int
    stale_count: int
    conflict_count: int
    structurally_complete: bool
    report_ready: bool
    dimensions: list[DimensionCoverage]
    resolved_dimension_count: int = 0
    degraded_dimension_count: int = 0
    report_generatable: bool = False
    quality: Literal["strict", "degraded", "blocked"] = "blocked"
    gate_policy: str = "uzi-3.9.4-medium"
    uzi_integrity: dict[str, Any] = Field(default_factory=dict)
    uzi_integrity_by_profile: dict[str, dict[str, Any]] = Field(default_factory=dict)
    audit_ready_dimension_count: int = 0
    audit_ready: bool = False

    def snapshot_ids(self) -> dict[str, str]:
        return {
            item.dimension: item.snapshot_id
            for item in self.dimensions
            if item.eligible and item.snapshot_id is not None
        }


class MaterializedBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    symbol: str
    market: str
    as_of_date: date
    cutoff_at: datetime
    contract_version: str
    manifest_hash: str
    snapshots: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]


__all__ = [
    "CONTRACT_VERSION",
    "LEGACY_UZI_CONTRACT_VERSIONS",
    "SUPPORTED_UZI_CONTRACT_VERSIONS",
    "BundleStatus",
    "CollectionStatus",
    "ConflictStatus",
    "CoverageResult",
    "DimensionCoverage",
    "EvidenceGrade",
    "EvidenceInput",
    "ManifestStatus",
    "MaterializedBundle",
    "SeriesPointInput",
    "SnapshotIngestInput",
    "SnapshotStatus",
    "canonical_hash",
    "canonical_json",
    "cutoff_at_for_date",
    "is_supported_uzi_contract_version",
    "require_aware_utc",
]
