from __future__ import annotations

from research_system.runtime.run_store import RunStore
from research_system.runtime.settings import Settings


def build_runtime_status(settings: Settings) -> dict:
    run_store = RunStore(settings.runs_root)
    runs = run_store.list_runs()
    recent_runs = runs[:5]

    status_counts: dict[str, int] = {}
    for row in runs:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "project_root": str(settings.workspace_root.parent),
        "workspace_root": str(settings.workspace_root),
        "runs_root": str(settings.runs_root),
        "qdrant_mode": "remote" if settings.qdrant_url else "local",
        "qdrant_collection_name": settings.qdrant_collection_name,
        "provider_name": settings.provider_name,
        "default_model": settings.default_model,
        "total_runs": len(runs),
        "status_counts": status_counts,
        "recent_runs": recent_runs,
    }
