from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_system.runtime.platform.settings import Settings


class RunRetentionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def apply_on_success(self, run_root: Path) -> None:
        checkpoint_path = run_root / "checkpoint.json"
        if checkpoint_path.exists():
            checkpoint_path.unlink()

    def apply_on_terminal_failure(self, run_root: Path) -> None:
        # Phase 0 only fixes checkpoint retention semantics.
        # Artifact pruning is intentionally conservative and remains a manual operation for now.
        _ = run_root

    def cleanup_artifacts(self, runs_root: Path) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.settings.artifacts_retention_days)
        removed_files: list[str] = []
        skipped_runs: list[str] = []

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
                    try:
                        path.unlink()
                        removed_files.append(str(path))
                    except OSError:
                        skipped_runs.append(run_dir.name)
            for directory in sorted([p for p in artifacts_dir.rglob("*") if p.is_dir()], reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass

        total_artifact_bytes = self._artifact_bytes(runs_root)
        return {
            "artifacts_retention_days": self.settings.artifacts_retention_days,
            "max_artifacts_size_mb": self.settings.max_artifacts_size_mb,
            "removed_files": removed_files,
            "skipped_runs": sorted(set(skipped_runs)),
            "artifact_bytes_after_cleanup": total_artifact_bytes,
            "artifact_size_limit_bytes": self.settings.max_artifacts_size_mb * 1024 * 1024,
            "size_limit_exceeded": total_artifact_bytes > self.settings.max_artifacts_size_mb * 1024 * 1024,
        }

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
