from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import inspect, select

from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase, resolve_database_url
from research_system.strategies.models import ArtifactObjectRef


class RunRetentionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def cleanup_artifacts(self, runs_root: Path) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.settings.artifacts_retention_days)
        removed_files: list[str] = []
        skipped_runs: list[str] = []
        protected_paths, reference_error = self._protected_paths()
        if reference_error is not None:
            return {
                "artifacts_retention_days": self.settings.artifacts_retention_days,
                "max_artifacts_size_mb": self.settings.max_artifacts_size_mb,
                "removed_files": [],
                "protected_files": [],
                "skipped_runs": [],
                "artifact_bytes_after_cleanup": self._artifact_bytes(runs_root),
                "artifact_size_limit_bytes": self.settings.max_artifacts_size_mb * 1024 * 1024,
                "size_limit_exceeded": False,
                "cleanup_blocked": True,
                "reference_lookup_error": reference_error,
            }
        protected_files: list[str] = []

        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            artifacts_dir = run_dir / "artifacts"
            if not artifacts_dir.exists():
                continue
            try:
                run_mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
            except OSError:
                skipped_runs.append(run_dir.name)
                continue
            if run_mtime >= cutoff:
                continue
            for path in artifacts_dir.rglob("*"):
                if path.is_file():
                    if str(path.resolve()) in protected_paths:
                        protected_files.append(str(path))
                        continue
                    try:
                        path.unlink()
                        removed_files.append(str(path))
                    except OSError:
                        skipped_runs.append(run_dir.name)
            for directory in sorted([p for p in artifacts_dir.rglob("*") if p.is_dir()], reverse=True):
                with contextlib.suppress(OSError):
                    directory.rmdir()

        total_artifact_bytes = self._artifact_bytes(runs_root)
        return {
            "artifacts_retention_days": self.settings.artifacts_retention_days,
            "max_artifacts_size_mb": self.settings.max_artifacts_size_mb,
            "removed_files": removed_files,
            "protected_files": protected_files,
            "skipped_runs": sorted(set(skipped_runs)),
            "artifact_bytes_after_cleanup": total_artifact_bytes,
            "artifact_size_limit_bytes": self.settings.max_artifacts_size_mb * 1024 * 1024,
            "size_limit_exceeded": total_artifact_bytes > self.settings.max_artifacts_size_mb * 1024 * 1024,
            "cleanup_blocked": False,
            "reference_lookup_error": None,
        }

    def _protected_paths(self) -> tuple[set[str], str | None]:
        database = StrategyDatabase(resolve_database_url(self.settings))
        try:
            if "artifact_object_refs" not in inspect(database.engine).get_table_names():
                return set(), "artifact_object_refs 表不存在；请先执行 Alembic 迁移和旧对象索引"
            with database.session() as session:
                uris = list(
                    session.scalars(
                        select(ArtifactObjectRef.uri).where(
                            ArtifactObjectRef.reference_count > 0,
                        )
                    )
                )
            protected: set[str] = set()
            for uri in uris:
                value = str(uri or "").strip()
                if not value or "://" in value:
                    continue
                protected.add(str(Path(value).resolve()))
            return protected, None
        except Exception as exc:
            return set(), f"{exc.__class__.__name__}: 无法读取 Artifact 引用索引"
        finally:
            database.close()

    def _artifact_bytes(self, runs_root: Path) -> int:
        total = 0
        for path in runs_root.rglob("*"):
            if path.is_file() and "artifacts" in path.parts:
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total


__all__ = ["RunRetentionService"]
