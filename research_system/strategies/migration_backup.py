from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Sequence

from sqlalchemy.engine import make_url

from research_system.runtime.core.id_utils import generate_ulid


_WORKSPACE_INDEX_NAMES = frozenset(
    {
        "request.json",
        "agent_os_request.json",
        "run_state.json",
        "main_agent_state.json",
        "execution_snapshot.json",
        "turn_state.json",
        "turn.json",
        "task_state.json",
        "task.json",
    }
)


def backup_migration_state(
    workspace_root: Path,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    configured_url = str(database_url or os.environ.get("COGNITRADE_DATABASE_URL") or "").strip()
    if not configured_url:
        configured_url = f"sqlite:///{workspace / 'cognitrade.sqlite3'}"

    backup_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{generate_ulid()}"
    backup_root = workspace / "backups" / "strategy_migrations" / backup_id
    backup_root.mkdir(parents=True, exist_ok=False)

    database_result = _backup_database(configured_url, backup_root)
    index_result = _backup_workspace_indexes(workspace, backup_root)
    manifest = {
        "backup_id": backup_id,
        "created_at": datetime.now(UTC).isoformat(),
        "workspace_root": str(workspace),
        "database": database_result,
        "workspace_index": index_result,
    }
    manifest_path = backup_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _backup_database(database_url: str, backup_root: Path) -> dict[str, Any]:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return {
            "dialect": url.get_backend_name(),
            "status": "external_backup_required",
            "detail": "Use the managed PostgreSQL snapshot/backup policy before production migration.",
        }
    database_name = str(url.database or "")
    if not database_name or database_name == ":memory:":
        return {"dialect": "sqlite", "status": "not_applicable"}
    source = Path(database_name).expanduser().resolve()
    if not source.is_file():
        return {
            "dialect": "sqlite",
            "status": "source_missing",
            "source": str(source),
        }

    destination = backup_root / "database" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)
    return {
        "dialect": "sqlite",
        "status": "backed_up",
        "source": str(source),
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
    }


def _backup_workspace_indexes(workspace: Path, backup_root: Path) -> dict[str, Any]:
    candidates: set[Path] = set()
    runs_root = workspace / "runs"
    if runs_root.is_dir():
        candidates.update(
            path
            for path in runs_root.rglob("*")
            if path.is_file() and path.name in _WORKSPACE_INDEX_NAMES
        )
    migrations_root = workspace / ".migrations"
    if migrations_root.is_dir():
        candidates.update(path for path in migrations_root.rglob("*") if path.is_file())

    destination_root = backup_root / "workspace_index"
    copied: list[dict[str, Any]] = []
    for source in sorted(candidates):
        relative = source.relative_to(workspace)
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(
            {
                "relative_path": relative.as_posix(),
                "size_bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )
    return {"status": "backed_up", "file_count": len(copied), "files": copied}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a pre-migration SQLite and workspace-index backup."
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--database-url", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = backup_migration_state(args.workspace, database_url=args.database_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["backup_migration_state"]
