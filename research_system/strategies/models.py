from __future__ import annotations

from datetime import date, datetime, timezone
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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from research_system.runtime.core.id_utils import generate_ulid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return generate_ulid()


class Base(DeclarativeBase):
    pass


class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (
        Index("ix_strategies_owner_updated", "tenant_id", "user_id", "updated_at"),
        UniqueConstraint("system_key", name="uq_strategies_system_key"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), default="fund_report_composite", nullable=False)
    system_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_system_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class StrategyDraft(Base):
    __tablename__ = "strategy_drafts"

    strategy_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategies.id", ondelete="CASCADE"), primary_key=True
    )
    base_version_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    draft_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    document_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version_number", name="uq_strategy_version_number"),
        UniqueConstraint("strategy_id", "content_hash", name="uq_strategy_version_hash"),
        Index("ix_strategy_versions_strategy_published", "strategy_id", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    strategy_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    document_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    governance_state: Mapped[str] = mapped_column(String(32), default="published", nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    artifact_run_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    published_by: Mapped[str] = mapped_column(String(128), nullable=False)


class StrategyEvent(Base):
    __tablename__ = "strategy_events"
    __table_args__ = (Index("ix_strategy_events_strategy_created", "strategy_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    strategy_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ApiMutationRecord(Base):
    __tablename__ = "api_mutation_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "idempotency_key",
            name="uq_api_mutation_owner_key",
        ),
        Index("ix_api_mutations_lease", "status", "lease_until"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    request_target: Mapped[str] = mapped_column(String(1024), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="in_progress", nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    response_headers_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StrategyAssignment(Base):
    __tablename__ = "strategy_assignments"
    __table_args__ = (
        Index("ix_assignments_owner_fund", "tenant_id", "user_id", "fund_code"),
        UniqueConstraint(
            "tenant_id", "user_id", "fund_code", "strategy_id", "mode",
            name="uq_assignment_owner_fund_strategy_mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fund_code: Mapped[str] = mapped_column(String(32), nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    strategy_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False
    )
    active_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    pending_version_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    schedule_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class StrategyVerification(Base):
    __tablename__ = "strategy_verifications"
    __table_args__ = (Index("ix_verifications_version_created", "strategy_version_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    backtest_run_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("backtest_runs.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    checks_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    thresholds_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    machine_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class PaperStrategyBinding(Base):
    __tablename__ = "paper_strategy_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "account_id", "fund_code", "mode",
            name="uq_paper_primary_binding",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fund_code: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    promoted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    promoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class OrchestrationJob(Base):
    __tablename__ = "orchestration_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_orchestration_job_idempotency"),
        Index("ix_jobs_owner_status", "tenant_id", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CanonicalUziAnalysis(Base):
    """Cross-job reservation and immutable result pointer for one canonical UZI input."""

    __tablename__ = "canonical_uzi_analyses"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "canonical_key",
            name="uq_canonical_uzi_owner_key",
        ),
        Index(
            "ix_canonical_uzi_owner_status_lease",
            "tenant_id",
            "user_id",
            "status",
            "lease_until",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(256), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    pin_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="reserved", nullable=False)
    producer_job_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("orchestration_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    data_gaps_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (Index("ix_job_attempts_job_started", "job_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("orchestration_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    parent_attempt_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("job_attempts.id", ondelete="RESTRICT"), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_nodes_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    artifact_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DailyReportSet(Base):
    __tablename__ = "daily_report_sets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "fund_code", "as_of_date", "pin_set_hash", "revision",
            name="uq_daily_report_revision",
        ),
        Index("ix_daily_reports_owner_fund_date", "tenant_id", "user_id", "fund_code", "as_of_date"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    assignment_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("strategy_assignments.id", ondelete="RESTRICT"), nullable=True
    )
    fund_code: Mapped[str] = mapped_column(String(32), nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    pin_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="building", nullable=False)
    max_top_n: Mapped[int] = mapped_column(Integer, nullable=False)
    count_coverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    weight_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_run_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    source_artifact_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    data_gaps_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailyReportNode(Base):
    __tablename__ = "daily_report_nodes"
    __table_args__ = (
        UniqueConstraint("report_set_id", "node_type", "subject_code", name="uq_daily_report_node"),
        Index("ix_report_nodes_canonical", "tenant_id", "user_id", "canonical_key"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    report_set_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("daily_report_sets.id", ondelete="RESTRICT"), nullable=False
    )
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disclosed_weight_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_weight_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_key: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_gaps_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class EvidenceObservation(Base):
    __tablename__ = "evidence_observations"
    __table_args__ = (
        Index("ix_evidence_subject_event", "subject_code", "event_date"),
        UniqueConstraint("source", "content_hash", name="uq_evidence_source_content"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class FactorSnapshot(Base):
    __tablename__ = "factor_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "subject_type", "subject_code", "as_of_date", "extractor_version", "source_hash",
            name="uq_factor_snapshot",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    report_set_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("daily_report_sets.id", ondelete="RESTRICT"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    factors_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    missing_factors_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    score_lower_bound: Mapped[float] = mapped_column(Float, nullable=False)
    score_upper_bound: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class TradeSignal(Base):
    __tablename__ = "trade_signals"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "kind", "subject_code", "strategy_version_id", "as_of_date", "revision",
            name="uq_trade_signal_revision",
        ),
        UniqueConstraint("content_hash", name="uq_trade_signal_hash"),
        Index("ix_signals_assignment_effective", "assignment_id", "effective_from"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    assignment_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_assignments.id", ondelete="RESTRICT"), nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    score_lower_bound: Mapped[float] = mapped_column(Float, nullable=False)
    score_upper_bound: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    factor_snapshot_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("factor_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    source_report_set_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("daily_report_sets.id", ondelete="RESTRICT"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    expires_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    carried_from_signal_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("trade_signals.id", ondelete="RESTRICT"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class StrategyPaperOrder(Base):
    __tablename__ = "strategy_paper_orders"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "client_order_id",
            name="uq_strategy_paper_order_client",
        ),
        Index("ix_strategy_paper_orders_binding_created", "binding_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    binding_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("paper_strategy_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    signal_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("trade_signals.id", ondelete="RESTRICT"), nullable=False
    )
    source_report_set_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("daily_report_sets.id", ondelete="RESTRICT"), nullable=False
    )
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    paper_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    technical_reason: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    order_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (Index("ix_backtests_owner_created", "tenant_id", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    parent_backtest_run_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("backtest_runs.id", ondelete="RESTRICT"), nullable=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    strategy_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fund_code: Mapped[str] = mapped_column(String(32), nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    initial_cash: Mapped[float] = mapped_column(Float, nullable=False)
    benchmark_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_object_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestSignalLink(Base):
    __tablename__ = "backtest_signal_links"
    __table_args__ = (
        UniqueConstraint("backtest_run_id", "signal_id", "trading_date", name="uq_backtest_signal_link"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    backtest_run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("backtest_runs.id", ondelete="RESTRICT"), nullable=False
    )
    signal_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("trade_signals.id", ondelete="RESTRICT"), nullable=False
    )
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    usage_reason: Mapped[str] = mapped_column(String(128), nullable=False)


class ArtifactObjectRef(Base):
    __tablename__ = "artifact_object_refs"
    __table_args__ = (UniqueConstraint("content_hash", "uri", name="uq_artifact_object_ref"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    ref_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retention_level: Mapped[str] = mapped_column(String(32), default="permanent", nullable=False)
    reference_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


__all__ = [
    "ApiMutationRecord",
    "ArtifactObjectRef",
    "BacktestRun",
    "BacktestSignalLink",
    "Base",
    "CanonicalUziAnalysis",
    "DailyReportNode",
    "DailyReportSet",
    "EvidenceObservation",
    "FactorSnapshot",
    "JobAttempt",
    "OrchestrationJob",
    "PaperStrategyBinding",
    "Strategy",
    "StrategyAssignment",
    "StrategyDraft",
    "StrategyEvent",
    "StrategyPaperOrder",
    "StrategyVerification",
    "StrategyVersion",
    "TradeSignal",
]
