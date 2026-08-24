from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from research_system.strategies.models import Base
from research_system.market_data import models as _market_data_models  # noqa: F401


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("COGNITRADE_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # SQLite batch migrations rebuild a table by copying it and then
            # dropping the original. Existing child rows would make that drop
            # fail while foreign-key enforcement is enabled. Disable enforcement
            # only on this migration connection, then verify the complete graph
            # before turning it back on. Application connections still enable
            # foreign_keys independently in StrategyDatabase.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("PRAGMA busy_timeout=30000")
            # SQLAlchemy 2 starts an implicit transaction for PRAGMA calls.
            # Finish it before Alembic opens its own migration transaction;
            # otherwise the version-row write is rolled back on connection close.
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
        if sqlite:
            connection.commit()
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    "SQLite migration left foreign-key violations: "
                    + repr(violations[:20])
                )
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
