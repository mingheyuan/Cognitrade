"""serialize Bundle revisions across cutoff variants

Revision ID: 20260820_0005
Revises: 20260820_0004
Create Date: 2026-08-31 16:00:00
"""

from collections.abc import Sequence
from itertools import groupby

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0005"
down_revision: str | Sequence[str] | None = "20260820_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _repair_duplicate_revisions() -> None:
    """Make legacy cutoff-scoped revisions unique before tightening the key."""

    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                """
                SELECT id, symbol, market, as_of_date, contract_version,
                       revision, cutoff_at, published_at
                FROM market_data_bundles
                ORDER BY symbol, market, as_of_date, contract_version,
                         revision, cutoff_at, published_at, id
                """
            )
        ).mappings()
    )
    scope = lambda row: (
        row["symbol"],
        row["market"],
        row["as_of_date"],
        row["contract_version"],
    )
    for _, grouped_rows in groupby(rows, key=scope):
        group = list(grouped_rows)
        revisions = [int(row["revision"]) for row in group]
        if len(revisions) == len(set(revisions)):
            continue
        # The old key still includes cutoff_at while this repair runs. Move the
        # whole group to unused negative values first, then assign dense positive
        # revisions, avoiding transient collisions on either database.
        temporary_start = min(revisions) - len(group) - 1
        for index, row in enumerate(group):
            connection.execute(
                sa.text(
                    "UPDATE market_data_bundles SET revision = :revision WHERE id = :id"
                ),
                {"id": row["id"], "revision": temporary_start + index},
            )
        for revision_number, row in enumerate(group, start=1):
            connection.execute(
                sa.text(
                    "UPDATE market_data_bundles SET revision = :revision WHERE id = :id"
                ),
                {"id": row["id"], "revision": revision_number},
            )


def upgrade() -> None:
    _repair_duplicate_revisions()
    with op.batch_alter_table("market_data_bundles") as batch_op:
        batch_op.drop_constraint("uq_market_data_bundle_revision", type_="unique")
        batch_op.create_unique_constraint(
            "uq_market_data_bundle_revision",
            ["symbol", "market", "as_of_date", "contract_version", "revision"],
        )


def downgrade() -> None:
    with op.batch_alter_table("market_data_bundles") as batch_op:
        batch_op.drop_constraint("uq_market_data_bundle_revision", type_="unique")
        batch_op.create_unique_constraint(
            "uq_market_data_bundle_revision",
            [
                "symbol",
                "market",
                "as_of_date",
                "cutoff_at",
                "contract_version",
                "revision",
            ],
        )
