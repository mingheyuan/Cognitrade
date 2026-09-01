from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from research_system.strategies.models import Base, new_id, utc_now


class MarketDataEvidence(Base):
    __tablename__ = "market_data_evidence"
    __table_args__ = (
        UniqueConstraint("evidence_key", name="uq_market_data_evidence_key"),
        Index("ix_market_data_evidence_provider_published", "provider", "published_at"),
        Index("ix_market_data_evidence_content_hash", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    evidence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(64), default="structured", nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    inline_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    grade: Mapped[str] = mapped_column(String(1), nullable=False)
    immutable_proof: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataSnapshot(Base):
    __tablename__ = "market_data_snapshots"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_market_data_snapshot_content_hash"),
        Index(
            "ix_market_data_snapshot_resolve",
            "symbol",
            "market",
            "dimension",
            "contract_version",
            "published_at",
        ),
        Index("ix_market_data_snapshot_status_revoke", "status", "revoked_at"),
        Index("ix_market_data_snapshot_as_of", "symbol", "market", "as_of_date"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_grade: Mapped[str] = mapped_column(String(1), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), default="provider", nullable=False)
    query_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    field_sources_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    collection_task_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("market_data_collection_tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataSnapshotEvidence(Base):
    __tablename__ = "market_data_snapshot_evidence"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "evidence_id", "field_path", "purpose",
            name="uq_market_data_snapshot_evidence_use",
        ),
        Index("ix_market_data_snapshot_evidence_snapshot", "snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_evidence.id", ondelete="RESTRICT"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(String(512), default="$", nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), default="supports", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataObservation(Base):
    __tablename__ = "market_data_observations"
    __table_args__ = (
        Index("ix_market_data_observation_snapshot_time", "snapshot_id", "observed_at"),
        Index("ix_market_data_observation_attempt", "provider_attempt_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    evidence_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("market_data_evidence.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataConflict(Base):
    __tablename__ = "market_data_conflicts"
    __table_args__ = (
        Index(
            "ix_market_data_conflict_blocking",
            "symbol", "market", "dimension", "status", "as_of_date",
        ),
        Index("ix_market_data_conflict_snapshot", "snapshot_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    candidate_values_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    candidate_evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    resolution_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataSeriesPoint(Base):
    __tablename__ = "market_data_series_points"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_market_data_series_point_hash"),
        Index(
            "ix_market_data_series_lookup",
            "symbol", "market", "dimension", "series_key", "point_at",
        ),
        Index("ix_market_data_series_published", "published_at", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    series_key: Mapped[str] = mapped_column(String(256), nullable=False)
    point_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataSnapshotSeriesPoint(Base):
    __tablename__ = "market_data_snapshot_series_points"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "series_point_id", name="uq_market_data_snapshot_series_point"),
        Index("ix_market_data_snapshot_series_snapshot", "snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    series_point_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_series_points.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataCollectionJob(Base):
    __tablename__ = "market_data_collection_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "idempotency_key", name="uq_market_data_collection_job_key"
        ),
        Index("ix_market_data_collection_job_owner_status", "tenant_id", "user_id", "status"),
        Index("ix_market_data_collection_job_lease", "status", "lease_until"),
        Index("ix_market_data_collection_job_origin", "origin", "subscription_id", "status"),
        Index("ix_market_data_collection_job_parent", "parent_job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    subscription_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    parent_job_id: Mapped[str | None] = mapped_column(
        String(26),
        ForeignKey("market_data_collection_jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    job_type: Mapped[str] = mapped_column(String(64), default="historical_backfill", nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="missing_or_stale", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    symbols_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    dimensions_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    publish_when_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketDataCollectionTask(Base):
    __tablename__ = "market_data_collection_tasks"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "symbol", "market", "dimension", "start_date", "end_date",
            name="uq_market_data_collection_task_scope",
        ),
        Index("ix_market_data_collection_task_job_status", "job_id", "status"),
        Index("ix_market_data_collection_task_retry", "status", "next_retry_at"),
        Index("ix_market_data_collection_task_lease", "status", "lease_until"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_collection_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketDataCollectionTaskResult(Base):
    __tablename__ = "market_data_collection_task_results"
    __table_args__ = (
        UniqueConstraint("task_id", "as_of_date", "attempt", name="uq_market_data_task_result_attempt"),
        Index("ix_market_data_task_result_task_date", "task_id", "as_of_date"),
        Index("ix_market_data_task_result_snapshot", "snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_collection_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataBundle(Base):
    __tablename__ = "market_data_bundles"
    __table_args__ = (
        UniqueConstraint("manifest_hash", name="uq_market_data_bundle_manifest_hash"),
        UniqueConstraint(
            "symbol", "market", "as_of_date", "contract_version", "revision",
            name="uq_market_data_bundle_revision",
        ),
        Index(
            "ix_market_data_bundle_resolve",
            "symbol", "market", "as_of_date", "contract_version", "published_at",
        ),
        Index("ix_market_data_bundle_status", "status", "invalidated_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="published", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expected_dimension_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ready_dimension_count: Mapped[int] = mapped_column(Integer, nullable=False)
    available_count: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_empty_count: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_bundle_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("market_data_bundles.id", ondelete="RESTRICT"), nullable=True
    )
    published_by: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketDataBundleItem(Base):
    __tablename__ = "market_data_bundle_items"
    __table_args__ = (
        UniqueConstraint("bundle_id", "dimension", name="uq_market_data_bundle_dimension"),
        UniqueConstraint("bundle_id", "ordinal", name="uq_market_data_bundle_ordinal"),
        Index("ix_market_data_bundle_item_snapshot", "snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    bundle_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_bundles.id", ondelete="RESTRICT"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_grade: Mapped[str] = mapped_column(String(1), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MarketDataSubscription(Base):
    __tablename__ = "market_data_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "symbol", "market", name="uq_market_data_subscription_owner_symbol"
        ),
        Index("ix_market_data_subscription_enabled", "enabled", "market", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dimensions_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    schedule_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_job_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("market_data_collection_jobs.id", ondelete="SET NULL"), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ReportDataManifest(Base):
    __tablename__ = "report_data_manifests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "manifest_hash", name="uq_report_data_manifest_owner_hash"
        ),
        Index("ix_report_data_manifest_owner_status", "tenant_id", "user_id", "status"),
        Index("ix_report_data_manifest_evaluation", "evaluation_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    report_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="frozen", nullable=False)
    contract_version: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    workflow_pins_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_by: Mapped[str] = mapped_column(String(128), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReportDataManifestItem(Base):
    __tablename__ = "report_data_manifest_items"
    __table_args__ = (
        UniqueConstraint("manifest_id", "symbol", "market", name="uq_report_data_manifest_symbol"),
        UniqueConstraint("manifest_id", "ordinal", name="uq_report_data_manifest_ordinal"),
        Index("ix_report_data_manifest_item_bundle", "bundle_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    manifest_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("report_data_manifests.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    bundle_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_bundles.id", ondelete="RESTRICT"), nullable=False
    )
    bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ReportDataManifestDependency(Base):
    """Immutable non-report input pinned alongside the stock Bundle items.

    A fund-holding report still analyses ordinary stock Bundles.  This row pins
    the parent fund disclosure that selected those stocks, so changing or
    revoking the disclosure invalidates the report input just like changing a
    stock Bundle would.
    """

    __tablename__ = "report_data_manifest_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id",
            "dependency_kind",
            "symbol",
            "market",
            name="uq_report_data_manifest_dependency_scope",
        ),
        UniqueConstraint(
            "manifest_id",
            "ordinal",
            name="uq_report_data_manifest_dependency_ordinal",
        ),
        Index("ix_report_data_manifest_dependency_bundle", "bundle_id"),
        Index("ix_report_data_manifest_dependency_snapshot", "snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    manifest_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("report_data_manifests.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    dependency_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    bundle_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_bundles.id", ondelete="RESTRICT"), nullable=False
    )
    bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("market_data_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    snapshot_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


def _reject_immutable_changes(target: object, fields: tuple[str, ...], object_name: str) -> None:
    state = inspect(target)
    changed = [field for field in fields if state.attrs[field].history.has_changes()]
    if changed:
        raise ValueError(f"{object_name} is immutable; attempted to change: {', '.join(changed)}")


def _reject_physical_delete(_mapper, _connection, target: object) -> None:
    raise ValueError(
        f"{target.__class__.__name__} is append-only; revoke or invalidate it instead of deleting"
    )


@event.listens_for(MarketDataEvidence, "before_update")
def _evidence_is_immutable(_mapper, _connection, target: MarketDataEvidence) -> None:
    _reject_immutable_changes(
        target,
        (
            "evidence_key", "provider", "source_url", "source_kind", "content_hash",
            "content_ref", "inline_content", "published_at", "observed_at", "grade",
            "immutable_proof", "is_manual", "metadata_json",
        ),
        "market-data evidence",
    )


@event.listens_for(MarketDataSnapshot, "before_update")
def _snapshot_payload_is_immutable(_mapper, _connection, target: MarketDataSnapshot) -> None:
    _reject_immutable_changes(
        target,
        (
            "symbol", "market", "exchange_timezone", "dimension", "contract_version",
            "as_of_date", "status", "evidence_grade", "payload_json", "effective_at",
            "published_at", "observed_at", "ingested_at", "source_version", "source_priority",
            "origin", "query_manifest_json", "validation_json", "field_sources_json", "content_hash",
        ),
        "market-data snapshot payload",
    )


@event.listens_for(MarketDataSeriesPoint, "before_update")
def _series_payload_is_immutable(_mapper, _connection, target: MarketDataSeriesPoint) -> None:
    _reject_immutable_changes(
        target,
        (
            "symbol", "market", "dimension", "series_key", "point_at", "effective_at",
            "published_at", "observed_at", "value_json", "source_version", "metadata_json",
            "content_hash",
        ),
        "market-data series point",
    )


@event.listens_for(MarketDataBundle, "before_update")
def _bundle_manifest_is_immutable(_mapper, _connection, target: MarketDataBundle) -> None:
    _reject_immutable_changes(
        target,
        (
            "symbol", "market", "exchange_timezone", "as_of_date", "cutoff_at",
            "contract_version", "revision", "expected_dimension_count", "ready_dimension_count",
            "available_count", "verified_empty_count", "coverage_json", "manifest_hash",
            "supersedes_bundle_id", "published_by", "published_at",
        ),
        "market-data bundle manifest",
    )
    status_history = inspect(target).attrs.status.history
    if status_history.has_changes():
        old = status_history.deleted[0] if status_history.deleted else None
        new = status_history.added[0] if status_history.added else target.status
        if old != "published" or new != "invalidated":
            raise ValueError("bundle status can only transition from published to invalidated")
        if target.invalidated_at is None or not str(target.invalidation_reason or "").strip():
            raise ValueError("bundle invalidation requires timestamp and reason")


@event.listens_for(MarketDataBundleItem, "before_update")
def _bundle_item_is_immutable(_mapper, _connection, target: MarketDataBundleItem) -> None:
    _reject_immutable_changes(
        target,
        (
            "bundle_id", "dimension", "ordinal", "snapshot_id", "snapshot_content_hash",
            "snapshot_status", "evidence_grade",
        ),
        "market-data bundle item",
    )


@event.listens_for(ReportDataManifest, "before_update")
def _report_manifest_is_immutable(_mapper, _connection, target: ReportDataManifest) -> None:
    _reject_immutable_changes(
        target,
        (
            "tenant_id", "user_id", "report_kind", "contract_version", "evaluation_at",
            "as_of_date", "workflow_pins_json", "input_context_json", "manifest_hash",
            "frozen_by", "frozen_at",
        ),
        "report-data manifest",
    )
    state = inspect(target)
    status_history = state.attrs.status.history
    invalidation_changed = (
        state.attrs.invalidated_at.history.has_changes()
        or state.attrs.invalidation_reason.history.has_changes()
    )
    if status_history.has_changes():
        old = status_history.deleted[0] if status_history.deleted else None
        new = status_history.added[0] if status_history.added else target.status
        if old != "frozen" or new != "invalidated":
            raise ValueError(
                "report-data manifest status can only transition from frozen to invalidated"
            )
        if target.invalidated_at is None or not str(target.invalidation_reason or "").strip():
            raise ValueError("report-data manifest invalidation requires timestamp and reason")
    elif invalidation_changed:
        raise ValueError(
            "report-data manifest invalidation metadata can only change during invalidation"
        )


@event.listens_for(ReportDataManifestItem, "before_update")
def _report_manifest_item_is_immutable(
    _mapper, _connection, target: ReportDataManifestItem
) -> None:
    _reject_immutable_changes(
        target,
        (
            "manifest_id", "ordinal", "symbol", "market", "bundle_id",
            "bundle_manifest_hash", "weight", "context_json", "created_at",
        ),
        "report-data manifest item",
    )


@event.listens_for(ReportDataManifestDependency, "before_update")
def _report_manifest_dependency_is_immutable(
    _mapper, _connection, target: ReportDataManifestDependency
) -> None:
    _reject_immutable_changes(
        target,
        (
            "manifest_id", "ordinal", "dependency_kind", "symbol", "market",
            "bundle_id", "bundle_manifest_hash", "snapshot_id",
            "snapshot_content_hash", "context_json", "created_at",
        ),
        "report-data manifest dependency",
    )


for _append_only_model in (
    MarketDataEvidence,
    MarketDataSnapshot,
    MarketDataSeriesPoint,
    MarketDataBundle,
    MarketDataBundleItem,
    ReportDataManifest,
    ReportDataManifestItem,
    ReportDataManifestDependency,
):
    event.listen(_append_only_model, "before_delete", _reject_physical_delete)


__all__ = [
    "MarketDataBundle",
    "MarketDataBundleItem",
    "MarketDataCollectionJob",
    "MarketDataCollectionTask",
    "MarketDataCollectionTaskResult",
    "MarketDataConflict",
    "MarketDataEvidence",
    "MarketDataObservation",
    "MarketDataSeriesPoint",
    "MarketDataSnapshot",
    "MarketDataSnapshotEvidence",
    "MarketDataSnapshotSeriesPoint",
    "MarketDataSubscription",
    "ReportDataManifest",
    "ReportDataManifestDependency",
    "ReportDataManifestItem",
]
