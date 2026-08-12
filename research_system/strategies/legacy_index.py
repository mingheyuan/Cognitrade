from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import select

from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase, resolve_database_url
from research_system.strategies.models import ArtifactObjectRef


MIGRATION_ID = "strategy_legacy_object_index_v1"


def index_legacy_objects(
    database: StrategyDatabase,
    *,
    workspace_root: Path,
    object_store_root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    marker = workspace / ".migrations" / f"{MIGRATION_ID}.json"
    if marker.exists() and not force and not dry_run:
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        return {
            **dict(previous),
            "migration_id": MIGRATION_ID,
            "workspace_root": str(workspace),
            "already_completed": True,
        }

    candidates = list(
        _candidate_files(
            workspace,
            object_store_root=(Path(object_store_root).resolve() if object_store_root else None),
        )
    )
    report: dict[str, Any] = {
        "migration_id": MIGRATION_ID,
        "workspace_root": str(workspace),
        "scanned_files": len(candidates),
        "indexed_files": 0,
        "existing_files": 0,
        "missing_files": 0,
        "bytes_indexed": 0,
        "dry_run": bool(dry_run),
        "already_completed": False,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    for ref_type, path in candidates:
        if not path.is_file():
            report["missing_files"] += 1
            continue
        content_hash, size_bytes = _hash_file(path)
        uri = str(path.resolve())
        with database.session() as session:
            existing = session.scalar(
                select(ArtifactObjectRef.id).where(
                    ArtifactObjectRef.content_hash == content_hash,
                    ArtifactObjectRef.uri == uri,
                )
            )
            if existing is not None:
                report["existing_files"] += 1
                continue
            report["indexed_files"] += 1
            report["bytes_indexed"] += size_bytes
            if dry_run:
                continue
            identity = hashlib.sha256(f"{ref_type}:{uri}".encode("utf-8")).hexdigest()[:32]
            session.add(
                ArtifactObjectRef(
                    ref_type=ref_type,
                    ref_id=identity,
                    content_hash=content_hash,
                    uri=uri,
                    size_bytes=size_bytes,
                    retention_level="permanent",
                    reference_count=1,
                )
            )
    if not dry_run:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return report


def _candidate_files(
    workspace: Path,
    *,
    object_store_root: Path | None,
) -> Iterable[tuple[str, Path]]:
    seen: set[str] = set()

    def emit(ref_type: str, paths: Iterable[Path]):
        for path in sorted(paths):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            yield ref_type, path

    runs_root = workspace / "runs"
    if runs_root.is_dir():
        yield from emit(
            "legacy_run_artifact",
            (
                path
                for artifacts in runs_root.rglob("artifacts")
                if artifacts.is_dir()
                for path in artifacts.rglob("*")
            ),
        )
    backtests_root = workspace / "backtests"
    if backtests_root.is_dir():
        yield from emit("legacy_technical_backtest", backtests_root.glob("*.json"))
    resolved_object_root = object_store_root or workspace / "strategy_objects"
    if resolved_object_root.is_dir():
        yield from emit("legacy_strategy_object", resolved_object_root.rglob("*.json"))


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Index legacy run artifacts and backtests as permanent strategy object references."
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--object-store", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    settings = Settings.from_env(repo_root)
    workspace = (args.workspace or settings.workspace_root).resolve()
    database = StrategyDatabase(resolve_database_url(settings))
    try:
        database.bootstrap_or_validate(allow_bootstrap=False)
        report = index_legacy_objects(
            database,
            workspace_root=workspace,
            object_store_root=args.object_store or settings.strategy_object_store_root,
            dry_run=args.dry_run,
            force=args.force,
        )
    finally:
        database.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MIGRATION_ID", "index_legacy_objects", "main"]
