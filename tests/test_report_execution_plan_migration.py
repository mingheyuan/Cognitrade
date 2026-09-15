from __future__ import annotations

from importlib import import_module

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_additive_plan_migration_preserves_legacy_signal_and_hash():
    engine = sa.create_engine("sqlite:///:memory:")
    migration = import_module(
        "migrations.versions.20260906_0006_report_execution_plans"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE trade_signals (id TEXT PRIMARY KEY, state TEXT, content_hash TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO trade_signals VALUES ('legacy', 'ALLOW_BUY', 'unchanged-hash')"
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        row = connection.exec_driver_sql(
            "SELECT id, state, content_hash, execution_plan_json FROM trade_signals"
        ).one()
        assert tuple(row) == ("legacy", "ALLOW_BUY", "unchanged-hash", None)
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert tuple(
            connection.exec_driver_sql(
                "SELECT id, state, content_hash FROM trade_signals"
            ).one()
        ) == (
            "legacy",
            "ALLOW_BUY",
            "unchanged-hash",
        )
