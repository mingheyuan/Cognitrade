"""Persist immutable native-report execution conditions without rewriting V1 signals."""

import sqlalchemy as sa
from alembic import op

revision = "20260906_0006"
down_revision = "20260820_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trade_signals") as batch:
        batch.add_column(sa.Column("execution_plan_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trade_signals") as batch:
        batch.drop_column("execution_plan_json")
