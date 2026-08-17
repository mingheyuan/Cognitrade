from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest

from research_system.runtime.core.id_utils import generate_ulid
from research_system.strategies.contracts import BacktestMode
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyNotFound
from research_system.strategies.service import OwnerScope, StrategyService


class StrategyRepositoryContractMixin:
    database_url: str
    auto_bootstrap: bool
    expected_dialect: str

    def exercise_repository_contract(self) -> None:
        unique = generate_ulid().lower()
        with TemporaryDirectory() as temporary:
            database = StrategyDatabase(self.database_url)
            service = StrategyService(
                database,
                workspace_root=Path(temporary),
                auto_bootstrap=self.auto_bootstrap,
            )
            owner = OwnerScope(f"tenant-{unique}", f"user-{unique}", f"actor-{unique}")
            other = OwnerScope(f"tenant-{unique}", f"other-{unique}", f"other-{unique}")
            try:
                health = service.health()
                self.assertTrue(health["ready"])
                self.assertEqual(health["dialect"], self.expected_dialect)

                catalog = service.list_catalog(owner)
                self.assertTrue(catalog["strategies"])
                self.assertTrue(catalog["strategies"][0]["is_system_template"])

                created = service.create_strategy(owner, name=f"repository-contract-{unique}")
                document = created["draft"]["document"]
                updated = service.update_draft(
                    owner,
                    created["id"],
                    expected_revision=created["draft"]["draft_revision"],
                    document=document,
                    name=f"repository-contract-updated-{unique}",
                )
                self.assertEqual(updated["draft"]["draft_revision"], 2)
                with self.assertRaises(StrategyNotFound):
                    service.get_strategy(other, created["id"])

                version = service.publish(owner, created["id"])
                self.assertEqual(version["version_number"], 1)
                assignment = service.create_assignment(
                    owner,
                    strategy_id=created["id"],
                    fund_code="110011",
                    fund_name="合同测试基金",
                    mode=BacktestMode.FUND_ONLY,
                )
                self.assertEqual(assignment["active_version_id"], version["id"])
                self.assertEqual(
                    len(service.list_assignments(owner, strategy_id=created["id"])["assignments"]),
                    1,
                )

                archived = service.archive(owner, created["id"])
                self.assertEqual(archived["lifecycle"], "archived")
                restored = service.restore(owner, created["id"])
                self.assertEqual(restored["lifecycle"], "published")
                self.assertGreaterEqual(len(service.list_events(owner, created["id"])["events"]), 5)
            finally:
                service.close()


class SQLiteStrategyRepositoryContractTests(
    StrategyRepositoryContractMixin,
    unittest.TestCase,
):
    database_url = "sqlite:///:memory:"
    auto_bootstrap = True
    expected_dialect = "sqlite"

    def test_repository_contract(self) -> None:
        self.exercise_repository_contract()

    def test_in_memory_sessions_serialize_the_shared_static_pool_connection(self) -> None:
        database = StrategyDatabase(self.database_url)
        entered = Event()
        finished = Event()

        def open_session() -> None:
            with database.session():
                entered.set()
            finished.set()

        worker = Thread(target=open_session)
        try:
            with database.session():
                worker.start()
                self.assertFalse(entered.wait(0.2))
            self.assertTrue(entered.wait(1.0))
            self.assertTrue(finished.wait(1.0))
        finally:
            worker.join(timeout=1.0)
            database.close()


@unittest.skipUnless(
    os.environ.get("COGNITRADE_TEST_POSTGRES_URL"),
    "COGNITRADE_TEST_POSTGRES_URL is required for the PostgreSQL contract",
)
class PostgreSQLStrategyRepositoryContractTests(
    StrategyRepositoryContractMixin,
    unittest.TestCase,
):
    database_url = os.environ.get("COGNITRADE_TEST_POSTGRES_URL", "")
    auto_bootstrap = False
    expected_dialect = "postgresql"

    def test_repository_contract(self) -> None:
        self.exercise_repository_contract()


if __name__ == "__main__":
    unittest.main()
