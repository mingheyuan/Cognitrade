"""add Point-in-Time market-data store and immutable report manifests

Revision ID: 20260820_0003
Revises: 20260812_0002
Create Date: 2026-08-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0003"
down_revision: str | Sequence[str] | None = "20260812_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_data_evidence",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("evidence_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_ref", sa.String(length=2048), nullable=True),
        sa.Column("inline_content", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("grade", sa.String(length=1), nullable=False),
        sa.Column("immutable_proof", sa.Boolean(), nullable=False),
        sa.Column("is_manual", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_key", name="uq_market_data_evidence_key"),
    )
    op.create_index(
        "ix_market_data_evidence_provider_published",
        "market_data_evidence",
        ["provider", "published_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_evidence_content_hash",
        "market_data_evidence",
        ["content_hash"],
        unique=False,
    )

    op.create_table(
        "market_data_collection_jobs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("subscription_id", sa.String(length=26), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("symbols_json", sa.JSON(), nullable=False),
        sa.Column("dimensions_json", sa.JSON(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("publish_when_complete", sa.Boolean(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("progress_json", sa.JSON(), nullable=False),
        sa.Column("checkpoint_json", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "idempotency_key",
            name="uq_market_data_collection_job_key",
        ),
    )
    op.create_index(
        "ix_market_data_collection_job_owner_status",
        "market_data_collection_jobs",
        ["tenant_id", "user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_collection_job_lease",
        "market_data_collection_jobs",
        ["status", "lease_until"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_collection_job_origin",
        "market_data_collection_jobs",
        ["origin", "subscription_id", "status"],
        unique=False,
    )

    op.create_table(
        "market_data_collection_tasks",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("job_id", sa.String(length=26), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("exchange_timezone", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("checkpoint_json", sa.JSON(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["market_data_collection_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "symbol",
            "market",
            "dimension",
            "start_date",
            "end_date",
            name="uq_market_data_collection_task_scope",
        ),
    )
    op.create_index(
        "ix_market_data_collection_task_job_status",
        "market_data_collection_tasks",
        ["job_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_collection_task_retry",
        "market_data_collection_tasks",
        ["status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_collection_task_lease",
        "market_data_collection_tasks",
        ["status", "lease_until"],
        unique=False,
    )

    op.create_table(
        "market_data_snapshots",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("exchange_timezone", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=128), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_grade", sa.String(length=1), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("source_priority", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("query_manifest_json", sa.JSON(), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("field_sources_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("revoked_by", sa.String(length=128), nullable=True),
        sa.Column("superseded_by_id", sa.String(length=26), nullable=True),
        sa.Column("collection_task_id", sa.String(length=26), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_task_id"], ["market_data_collection_tasks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["market_data_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_market_data_snapshot_content_hash"),
    )
    op.create_index(
        "ix_market_data_snapshot_resolve",
        "market_data_snapshots",
        ["symbol", "market", "dimension", "contract_version", "published_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_snapshot_status_revoke",
        "market_data_snapshots",
        ["status", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_snapshot_as_of",
        "market_data_snapshots",
        ["symbol", "market", "as_of_date"],
        unique=False,
    )

    op.create_table(
        "market_data_snapshot_evidence",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("snapshot_id", sa.String(length=26), nullable=False),
        sa.Column("evidence_id", sa.String(length=26), nullable=False),
        sa.Column("field_path", sa.String(length=512), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["market_data_evidence.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["market_data_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "evidence_id",
            "field_path",
            "purpose",
            name="uq_market_data_snapshot_evidence_use",
        ),
    )
    op.create_index(
        "ix_market_data_snapshot_evidence_snapshot",
        "market_data_snapshot_evidence",
        ["snapshot_id"],
        unique=False,
    )

    op.create_table(
        "market_data_observations",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("snapshot_id", sa.String(length=26), nullable=False),
        sa.Column("evidence_id", sa.String(length=26), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("provider_attempt_id", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["market_data_evidence.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["market_data_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_market_data_observation_snapshot_time",
        "market_data_observations",
        ["snapshot_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_observation_attempt",
        "market_data_observations",
        ["provider_attempt_id"],
        unique=False,
    )

    op.create_table(
        "market_data_conflicts",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_id", sa.String(length=26), nullable=True),
        sa.Column("field_path", sa.String(length=512), nullable=False),
        sa.Column("candidate_values_json", sa.JSON(), nullable=False),
        sa.Column("candidate_evidence_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolution_json", sa.JSON(), nullable=False),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["market_data_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_market_data_conflict_blocking",
        "market_data_conflicts",
        ["symbol", "market", "dimension", "status", "as_of_date"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_conflict_snapshot",
        "market_data_conflicts",
        ["snapshot_id", "status"],
        unique=False,
    )

    op.create_table(
        "market_data_series_points",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("series_key", sa.String(length=256), nullable=False),
        sa.Column("point_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_market_data_series_point_hash"),
    )
    op.create_index(
        "ix_market_data_series_lookup",
        "market_data_series_points",
        ["symbol", "market", "dimension", "series_key", "point_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_series_published",
        "market_data_series_points",
        ["published_at", "revoked_at"],
        unique=False,
    )

    op.create_table(
        "market_data_snapshot_series_points",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("snapshot_id", sa.String(length=26), nullable=False),
        sa.Column("series_point_id", sa.String(length=26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["series_point_id"], ["market_data_series_points.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["market_data_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "series_point_id", name="uq_market_data_snapshot_series_point"
        ),
    )
    op.create_index(
        "ix_market_data_snapshot_series_snapshot",
        "market_data_snapshot_series_points",
        ["snapshot_id"],
        unique=False,
    )

    op.create_table(
        "market_data_collection_task_results",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("task_id", sa.String(length=26), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_status", sa.String(length=64), nullable=True),
        sa.Column("snapshot_id", sa.String(length=26), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["market_data_snapshots.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["market_data_collection_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "as_of_date", "attempt", name="uq_market_data_task_result_attempt"
        ),
    )
    op.create_index(
        "ix_market_data_task_result_task_date",
        "market_data_collection_task_results",
        ["task_id", "as_of_date"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_task_result_snapshot",
        "market_data_collection_task_results",
        ["snapshot_id"],
        unique=False,
    )

    op.create_table(
        "market_data_bundles",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("exchange_timezone", sa.String(length=64), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contract_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("expected_dimension_count", sa.Integer(), nullable=False),
        sa.Column("ready_dimension_count", sa.Integer(), nullable=False),
        sa.Column("available_count", sa.Integer(), nullable=False),
        sa.Column("verified_empty_count", sa.Integer(), nullable=False),
        sa.Column("coverage_json", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_bundle_id", sa.String(length=26), nullable=True),
        sa.Column("published_by", sa.String(length=128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["supersedes_bundle_id"], ["market_data_bundles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manifest_hash", name="uq_market_data_bundle_manifest_hash"),
        sa.UniqueConstraint(
            "symbol",
            "market",
            "as_of_date",
            "cutoff_at",
            "contract_version",
            "revision",
            name="uq_market_data_bundle_revision",
        ),
    )
    op.create_index(
        "ix_market_data_bundle_resolve",
        "market_data_bundles",
        ["symbol", "market", "as_of_date", "contract_version", "published_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_data_bundle_status",
        "market_data_bundles",
        ["status", "invalidated_at"],
        unique=False,
    )

    op.create_table(
        "market_data_bundle_items",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("bundle_id", sa.String(length=26), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(length=26), nullable=False),
        sa.Column("snapshot_content_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_status", sa.String(length=32), nullable=False),
        sa.Column("evidence_grade", sa.String(length=1), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bundle_id"], ["market_data_bundles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["market_data_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", "dimension", name="uq_market_data_bundle_dimension"),
        sa.UniqueConstraint("bundle_id", "ordinal", name="uq_market_data_bundle_ordinal"),
    )
    op.create_index(
        "ix_market_data_bundle_item_snapshot",
        "market_data_bundle_items",
        ["snapshot_id"],
        unique=False,
    )

    op.create_table(
        "market_data_subscriptions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("exchange_timezone", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("dimensions_json", sa.JSON(), nullable=False),
        sa.Column("schedule_json", sa.JSON(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job_id", sa.String(length=26), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_job_id"], ["market_data_collection_jobs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "symbol",
            "market",
            name="uq_market_data_subscription_owner_symbol",
        ),
    )
    op.create_index(
        "ix_market_data_subscription_enabled",
        "market_data_subscriptions",
        ["enabled", "market", "next_run_at"],
        unique=False,
    )

    op.create_table(
        "report_data_manifests",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("report_kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("contract_version", sa.String(length=128), nullable=False),
        sa.Column("evaluation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("workflow_pins_json", sa.JSON(), nullable=False),
        sa.Column("input_context_json", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("frozen_by", sa.String(length=128), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "manifest_hash",
            name="uq_report_data_manifest_owner_hash",
        ),
    )
    op.create_index(
        "ix_report_data_manifest_owner_status",
        "report_data_manifests",
        ["tenant_id", "user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_report_data_manifest_evaluation",
        "report_data_manifests",
        ["evaluation_at"],
        unique=False,
    )

    op.create_table(
        "report_data_manifest_items",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("manifest_id", sa.String(length=26), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("bundle_id", sa.String(length=26), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bundle_id"], ["market_data_bundles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["report_data_manifests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_id", "symbol", "market", name="uq_report_data_manifest_symbol"
        ),
        sa.UniqueConstraint("manifest_id", "ordinal", name="uq_report_data_manifest_ordinal"),
    )
    op.create_index(
        "ix_report_data_manifest_item_bundle",
        "report_data_manifest_items",
        ["bundle_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("report_data_manifest_items")
    op.drop_table("report_data_manifests")
    op.drop_table("market_data_subscriptions")
    op.drop_table("market_data_bundle_items")
    op.drop_table("market_data_bundles")
    op.drop_table("market_data_collection_task_results")
    op.drop_table("market_data_snapshot_series_points")
    op.drop_table("market_data_series_points")
    op.drop_table("market_data_conflicts")
    op.drop_table("market_data_observations")
    op.drop_table("market_data_snapshot_evidence")
    op.drop_table("market_data_snapshots")
    op.drop_table("market_data_collection_tasks")
    op.drop_table("market_data_collection_jobs")
    op.drop_table("market_data_evidence")
