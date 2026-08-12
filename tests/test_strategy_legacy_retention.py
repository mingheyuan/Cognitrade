from __future__ import annotations

import hashlib
import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select

from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.legacy_index import index_legacy_objects
from research_system.strategies.models import ArtifactObjectRef


REPO_ROOT = Path(__file__).resolve().parents[1]


class StrategyLegacyRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.database_path = self.workspace / "cognitrade.sqlite3"
        self.settings = Settings.from_env(REPO_ROOT).model_copy(
            update={
                "workspace_root": self.workspace,
                "runs_root": self.workspace / "runs",
                "reports_root": self.workspace / "reports",
                "memory_root": self.workspace / "memory",
                "cognitrade_database_url": f"sqlite:///{self.database_path}",
                "cognitrade_database_auto_bootstrap": False,
                "strategy_object_store_root": self.workspace / "strategy_objects",
                "artifacts_retention_days": 1,
            }
        )
        ensure_workspace_layout(self.settings)
        self.database = StrategyDatabase(self.settings.cognitrade_database_url)
        self.database.create_schema()

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_legacy_index_is_idempotent_and_keeps_old_files_read_only(self) -> None:
        run_artifact = self.workspace / "runs" / "run-old" / "artifacts" / "report.html"
        legacy_backtest = self.workspace / "backtests" / "legacy-backtest.json"
        strategy_object = self.workspace / "strategy_objects" / "aa" / "object.json"
        for path, content in (
            (run_artifact, "report"),
            (legacy_backtest, '{"strategy":"legacy_technical"}'),
            (strategy_object, '{"result":"strategy"}'),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        indexed = index_legacy_objects(
            self.database,
            workspace_root=self.workspace,
            object_store_root=self.settings.strategy_object_store_root,
        )
        self.assertEqual(indexed["indexed_files"], 3)
        self.assertTrue(run_artifact.exists())
        self.assertTrue(legacy_backtest.exists())
        self.assertTrue(strategy_object.exists())
        with self.database.session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ArtifactObjectRef)),
                3,
            )
            self.assertEqual(
                set(session.scalars(select(ArtifactObjectRef.ref_type))),
                {"legacy_run_artifact", "legacy_technical_backtest", "legacy_strategy_object"},
            )
        replay = index_legacy_objects(
            self.database,
            workspace_root=self.workspace,
            object_store_root=self.settings.strategy_object_store_root,
        )
        self.assertTrue(replay["already_completed"])

    def test_cleanup_deletes_only_unreferenced_old_artifacts(self) -> None:
        run_root = self.workspace / "runs" / "run-old"
        protected = run_root / "artifacts" / "protected.json"
        disposable = run_root / "artifacts" / "disposable.json"
        protected.parent.mkdir(parents=True, exist_ok=True)
        protected.write_text("protected", encoding="utf-8")
        disposable.write_text("disposable", encoding="utf-8")
        old = (datetime.now(UTC) - timedelta(days=10)).timestamp()
        os.utime(run_root, (old, old))
        content = protected.read_bytes()
        with self.database.session() as session:
            session.add(
                ArtifactObjectRef(
                    ref_type="test_reference",
                    ref_id="protected-artifact",
                    content_hash=hashlib.sha256(content).hexdigest(),
                    uri=str(protected.resolve()),
                    size_bytes=len(content),
                    retention_level="permanent",
                    reference_count=1,
                )
            )

        result = RunRetentionService(self.settings).cleanup_artifacts(self.settings.runs_root)
        self.assertFalse(result["cleanup_blocked"])
        self.assertTrue(protected.exists())
        self.assertFalse(disposable.exists())
        self.assertEqual(result["protected_files"], [str(protected)])
        self.assertEqual(result["removed_files"], [str(disposable)])


if __name__ == "__main__":
    unittest.main()
