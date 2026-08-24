"""pin fund-holding inputs and collection parentage

Revision ID: 20260820_0004
Revises: 20260820_0003
Create Date: 2026-08-20 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0004"
down_revision: str | Sequence[str] | None = "20260820_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("market_data_collection_jobs") as batch_op:
        batch_op.add_column(sa.Column("parent_job_id", sa.String(length=26), nullable=True))
        batch_op.create_foreign_key(
            "fk_market_data_collection_job_parent",
            "market_data_collection_jobs",
            ["parent_job_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_market_data_collection_job_parent",
            ["parent_job_id", "status"],
            unique=False,
        )

    op.create_table(
        "report_data_manifest_dependencies",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("manifest_id", sa.String(length=26), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("dependency_kind", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("bundle_id", sa.String(length=26), nullable=False),
        sa.Column("bundle_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.String(length=26), nullable=False),
        sa.Column("snapshot_content_hash", sa.String(length=64), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["report_data_manifests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["bundle_id"], ["market_data_bundles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["market_data_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_id",
            "dependency_kind",
            "symbol",
            "market",
            name="uq_report_data_manifest_dependency_scope",
        ),
        sa.UniqueConstraint(
            "manifest_id",
            "ordinal",
            name="uq_report_data_manifest_dependency_ordinal",
        ),
    )
    op.create_index(
        "ix_report_data_manifest_dependency_bundle",
        "report_data_manifest_dependencies",
        ["bundle_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_data_manifest_dependency_snapshot",
        "report_data_manifest_dependencies",
        ["snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_data_manifest_dependency_snapshot",
        table_name="report_data_manifest_dependencies",
    )
    op.drop_index(
        "ix_report_data_manifest_dependency_bundle",
        table_name="report_data_manifest_dependencies",
    )
    op.drop_table("report_data_manifest_dependencies")
    with op.batch_alter_table("market_data_collection_jobs") as batch_op:
        batch_op.drop_index("ix_market_data_collection_job_parent")
        batch_op.drop_constraint(
            "fk_market_data_collection_job_parent",
            type_="foreignkey",
        )
        batch_op.drop_column("parent_job_id")
