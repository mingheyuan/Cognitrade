from __future__ import annotations

from research_system.runtime.storage.runs.run_store import RunStore
from research_system.runtime.platform.settings import Settings


def build_config_file_checks(settings: Settings) -> list[dict]:
    return [
        {
            "name": name,
            "status": "passed" if path.exists() else "failed",
            "path": str(path),
        }
        for name, path in {
            "retrieval_policy": settings.retrieval_policy_path,
            "methodology_manifest": settings.methodology_manifest_path,
            "provider_policy": settings.provider_policy_path,
            "data_sources": settings.data_sources_path,
            "mcp_servers": settings.mcp_servers_path,
            "taxonomy_dimensions": settings.taxonomy_dimensions_path,
            "taxonomy_dimension_aliases": settings.taxonomy_dimension_aliases_path,
            "taxonomy_dimension_patterns": settings.taxonomy_dimension_patterns_path,
            "review_policy": settings.review_policy_path,
        }.items()
    ]


def build_workspace_path_checks(settings: Settings) -> list[dict]:
    return [
        {
            "name": name,
            "status": "passed" if path.exists() else "failed",
            "path": str(path),
        }
        for name, path in {
            "workspace_root": settings.workspace_root,
            "runs_root": settings.runs_root,
            "reports_root": settings.reports_root,
            "memory_root": settings.memory_root,
            "taxonomy_root": settings.taxonomy_root,
        }.items()
    ]


def build_qdrant_checks(settings: Settings) -> list[dict]:
    if settings.qdrant_url:
        return [
            {
                "name": "qdrant_endpoint",
                "status": "passed",
                "detail": settings.qdrant_url,
            }
        ]
    local_qdrant = settings.workspace_root / "qdrant_local"
    return [
        {
            "name": "qdrant_local_dir",
            "status": "passed" if local_qdrant.exists() else "skipped",
            "path": str(local_qdrant),
        }
    ]


def build_runs_presence_check(settings: Settings) -> dict:
    runs = RunStore(settings.runs_root).list_runs()
    return {
        "name": "runs_presence",
        "status": "passed" if runs else "skipped",
        "detail": f"runs={len(runs)}",
    }


__all__ = [
    "build_config_file_checks",
    "build_workspace_path_checks",
    "build_qdrant_checks",
    "build_runs_presence_check",
]
