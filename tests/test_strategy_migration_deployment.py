from __future__ import annotations

import json
from importlib.util import find_spec
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_system.runtime.diagnostics.status.deployment_preflight import (
    _strategy_storage_checks,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.migration_backup import backup_migration_state


REPO_ROOT = Path(__file__).resolve().parents[1]


class StrategyMigrationBackupTests(unittest.TestCase):
    def test_sqlite_and_workspace_indexes_are_backed_up_before_migration(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            database_path = workspace / "cognitrade.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
                connection.execute("INSERT INTO sample(value) VALUES ('before-migration')")

            run_root = workspace / "runs" / "run-1"
            run_root.mkdir(parents=True)
            (run_root / "turn.json").write_text('{"status":"completed"}\n', encoding="utf-8")
            marker = workspace / ".migrations" / "legacy.json"
            marker.parent.mkdir(parents=True)
            marker.write_text('{"indexed":true}\n', encoding="utf-8")

            result = backup_migration_state(
                workspace,
                database_url=f"sqlite:///{database_path}",
            )

            self.assertEqual(result["database"]["status"], "backed_up")
            self.assertEqual(result["workspace_index"]["file_count"], 2)
            backup_database = Path(result["database"]["path"])
            with sqlite3.connect(backup_database) as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM sample").fetchone()[0],
                    "before-migration",
                )
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertNotIn("password", json.dumps(manifest))
            copied = {
                item["relative_path"] for item in manifest["workspace_index"]["files"]
            }
            self.assertEqual(copied, {"runs/run-1/turn.json", ".migrations/legacy.json"})

    def test_postgresql_records_external_backup_requirement_without_credentials(self) -> None:
        with TemporaryDirectory() as temporary:
            result = backup_migration_state(
                Path(temporary),
                database_url="postgresql+psycopg://user:secret@example.invalid/cognitrade",
            )
        self.assertEqual(result["database"]["status"], "external_backup_required")
        self.assertNotIn("secret", json.dumps(result))


class StrategyDeploymentPreflightTests(unittest.TestCase):
    @unittest.skipUnless(
        os.name == "posix"
        and find_spec("vnpy") is not None
        and find_spec("vnpy_paperaccount") is not None,
        "VeighNa runtime isolation test requires POSIX and native dependencies",
    )
    def test_veighna_probe_uses_isolated_runtime_with_read_only_home(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            read_only_home = root / "read-only-home"
            read_only_home.mkdir()
            read_only_home.chmod(0o500)
            runtime_root = root / "workspace" / "paper_trading" / "veighna-runtime"
            runtime_root.parent.mkdir(parents=True)
            environment = {
                **os.environ,
                "HOME": str(read_only_home),
                "COGNITRADE_VEIGHNA_RUNTIME_ROOT": str(runtime_root),
            }
            try:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import os; from pathlib import Path; "
                        "from research_system.runtime.diagnostics.status.deployment_preflight "
                        "import _probe_veighna_runtime; "
                        "print(_probe_veighna_runtime(Path("
                        "os.environ['COGNITRADE_VEIGHNA_RUNTIME_ROOT'])))",
                    ],
                    cwd=REPO_ROOT,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                    check=False,
                )
            finally:
                read_only_home.chmod(0o700)

            self.assertEqual(process.returncode, 0, process.stdout)
            self.assertIn(str(runtime_root.resolve()), process.stdout)
            self.assertTrue((runtime_root / ".vntrader").is_dir())

    def test_fresh_sqlite_alembic_lifecycle_reaches_storage_health(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            object_store = workspace / "strategy_objects"
            object_store.mkdir()
            database_url = f"sqlite:///{workspace / 'migrated.sqlite3'}"
            environment = {
                **os.environ,
                "COGNITRADE_DATABASE_URL": database_url,
                "COGNITRADE_OBJECT_STORE_ROOT": str(object_store),
                "COGNITRADE_DATABASE_AUTO_BOOTSTRAP": "false",
            }
            for arguments in (
                ("upgrade", "head"),
                ("check",),
            ):
                process = subprocess.run(
                    [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
                    cwd=REPO_ROOT,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(process.returncode, 0, process.stdout)
            with patch.dict(os.environ, environment, clear=False):
                checks = _strategy_storage_checks(workspace)
            self.assertTrue(all(check["passed"] for check in checks), checks)

            downgraded = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "downgrade", "base"],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
            self.assertEqual(downgraded.returncode, 0, downgraded.stdout)
            database = StrategyDatabase(database_url)
            try:
                self.assertFalse(database.health()["ready"])
            finally:
                database.close()

    def test_migrated_database_object_store_and_policy_pass(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            object_store = workspace / "external-objects"
            object_store.mkdir()
            database_url = f"sqlite:///{workspace / 'strategy.sqlite3'}"
            database = StrategyDatabase(database_url)
            database.create_schema()
            database.close()
            with patch.dict(
                os.environ,
                {
                    "COGNITRADE_DATABASE_URL": database_url,
                    "COGNITRADE_OBJECT_STORE_ROOT": str(object_store),
                    "COGNITRADE_DATABASE_AUTO_BOOTSTRAP": "false",
                },
                clear=False,
            ):
                checks = _strategy_storage_checks(workspace)
        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_unmigrated_database_and_implicit_bootstrap_fail_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            object_store = workspace / "strategy_objects"
            object_store.mkdir()
            database_url = f"sqlite:///{workspace / 'unmigrated.sqlite3'}"
            with patch.dict(
                os.environ,
                {
                    "COGNITRADE_DATABASE_URL": database_url,
                    "COGNITRADE_OBJECT_STORE_ROOT": str(object_store),
                    "COGNITRADE_DATABASE_AUTO_BOOTSTRAP": "true",
                },
                clear=False,
            ):
                checks = _strategy_storage_checks(workspace)
        by_name = {check["name"]: check for check in checks}
        self.assertFalse(by_name["strategy-database"]["passed"])
        self.assertFalse(by_name["strategy-database-migration-policy"]["passed"])


if __name__ == "__main__":
    unittest.main()
