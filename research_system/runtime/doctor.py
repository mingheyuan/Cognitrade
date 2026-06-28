from __future__ import annotations

from pathlib import Path

from research_system.runtime.run_store import RunStore
from research_system.runtime.settings import Settings


def build_status(settings: Settings) -> dict:
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


def run_doctor(settings: Settings) -> dict:
    checks: list[dict] = []

    root = settings.workspace_root.parent
    env_path = root / ".env"
    checks.append(
        {
            "name": "env_file",
            "status": "passed" if env_path.exists() else "failed",
            "path": str(env_path),
        }
    )
    checks.append(
        {
            "name": "openai_api_key",
            "status": "passed" if bool(settings.openai_api_key) else "failed",
            "detail": "configured" if settings.openai_api_key else "missing",
        }
    )
    checks.append(
        {
            "name": "openai_base_url",
            "status": "passed" if settings.openai_base_url is not None else "skipped",
            "detail": settings.openai_base_url or "not_set",
        }
    )
    checks.append(
        {
            "name": "llm_request_timeout_seconds",
            "status": "passed" if settings.llm_request_timeout_seconds > 0 else "failed",
            "detail": str(settings.llm_request_timeout_seconds),
        }
    )
    checks.append(
        {
            "name": "distill_llm_timeout_budget",
            "status": "passed" if settings.llm_request_timeout_seconds < settings.run_timeout_seconds else "failed",
            "detail": (
                f"llm_timeout={settings.llm_request_timeout_seconds}s, "
                f"run_timeout={settings.run_timeout_seconds}s"
            ),
        }
    )

    for name, path in {
        "retrieval_policy": settings.retrieval_policy_path,
        "methodology_manifest": settings.methodology_manifest_path,
        "taxonomy_dimensions": settings.taxonomy_dimensions_path,
        "review_policy": settings.review_policy_path,
    }.items():
        checks.append(
            {
                "name": name,
                "status": "passed" if path.exists() else "failed",
                "path": str(path),
            }
        )

    for name, path in {
        "workspace_root": settings.workspace_root,
        "runs_root": settings.runs_root,
        "reports_root": settings.reports_root,
        "memory_root": settings.memory_root,
        "taxonomy_root": settings.taxonomy_root,
    }.items():
        checks.append(
            {
                "name": name,
                "status": "passed" if path.exists() else "failed",
                "path": str(path),
            }
        )

    if settings.qdrant_url:
        checks.append(
            {
                "name": "qdrant_endpoint",
                "status": "passed",
                "detail": settings.qdrant_url,
            }
        )
    else:
        local_qdrant = settings.workspace_root / "qdrant_local"
        checks.append(
            {
                "name": "qdrant_local_dir",
                "status": "passed" if local_qdrant.exists() else "skipped",
                "path": str(local_qdrant),
            }
        )

    runs = RunStore(settings.runs_root).list_runs()
    checks.append(
        {
            "name": "runs_presence",
            "status": "passed" if runs else "skipped",
            "detail": f"runs={len(runs)}",
        }
    )

    overall_status = "completed" if all(item["status"] in {"passed", "skipped"} for item in checks) else "failed"
    return {
        "status": overall_status,
        "checks": checks,
    }
