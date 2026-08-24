from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from research_system.strategies.models import Base
# Import the market-data models for their shared Base metadata registration.
# Keeping one metadata graph makes create_schema and Alembic behave identically.
from research_system.market_data import models as _market_data_models  # noqa: F401


REQUIRED_STRATEGY_TABLES = frozenset(
    table.name for table in Base.metadata.sorted_tables
)


class StrategyDatabaseError(RuntimeError):
    pass


def resolve_database_url(settings) -> str:
    configured = str(getattr(settings, "cognitrade_database_url", "") or "").strip()
    if configured:
        return configured
    path = Path(settings.workspace_root) / "cognitrade.sqlite3"
    return f"sqlite:///{path}"


class StrategyDatabase:
    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self.database_url = str(database_url)
        engine_options: dict = {
            "future": True,
            "pool_pre_ping": True,
            "echo": echo,
        }
        if self.database_url.startswith("sqlite"):
            engine_options["connect_args"] = {
                "check_same_thread": False,
                "timeout": 30,
            }
            if self.database_url in {"sqlite://", "sqlite:///:memory:"}:
                engine_options["poolclass"] = StaticPool
        self.engine: Engine = create_engine(self.database_url, **engine_options)
        if self.database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        self._sessions = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

    @staticmethod
    def _configure_sqlite(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        if str(cursor.execute("PRAGMA database_list").fetchone()[2] or "") not in {"", ":memory:"}:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_schema(self) -> None:
        Base.metadata.drop_all(self.engine)

    def bootstrap_or_validate(self, *, allow_bootstrap: bool) -> None:
        existing = set(inspect(self.engine).get_table_names())
        if allow_bootstrap and self.is_sqlite:
            # SQLite development/test databases can already contain the legacy
            # strategy tables while missing tables introduced by a later
            # metadata revision (for example the market-data center). Running
            # create_all is idempotent and fills those additive gaps.
            self.create_schema()
            existing = set(inspect(self.engine).get_table_names())
        if REQUIRED_STRATEGY_TABLES.issubset(existing):
            return
        missing = ", ".join(sorted(REQUIRED_STRATEGY_TABLES - existing))
        raise StrategyDatabaseError(
            f"策略数据库尚未迁移，缺少表：{missing}。请先执行 Alembic upgrade head。"
        )

    def health(self) -> dict[str, object]:
        tables = set(inspect(self.engine).get_table_names())
        missing = sorted(REQUIRED_STRATEGY_TABLES - tables)
        return {
            "dialect": self.engine.dialect.name,
            "ready": not missing,
            "table_count": len(tables),
            "required_table_count": len(REQUIRED_STRATEGY_TABLES),
            "missing_tables": missing,
        }

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()


__all__ = [
    "REQUIRED_STRATEGY_TABLES",
    "StrategyDatabase",
    "StrategyDatabaseError",
    "resolve_database_url",
]
