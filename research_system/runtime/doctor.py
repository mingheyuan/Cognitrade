from __future__ import annotations

from pathlib import Path

from research_system.runtime.provider_health import ProviderHealthService
from research_system.runtime.run_store import RunStore
from research_system.runtime.settings import Settings
from research_system.services.route_table import build_intent_route_table_store


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
    route_store = build_intent_route_table_store(settings)
    route_table = route_store.load_table()
    route_runtime = route_store.describe_runtime_state()
    provider_health = ProviderHealthService(settings).build_report()

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
    checks.append(
        {
            "name": "router_defaults",
            "status": "passed" if settings.router_defaults_path.exists() else "failed",
            "path": str(settings.router_defaults_path),
        }
    )
    checks.append(
        {
            "name": "router_cache_ttl_seconds",
            "status": "passed" if settings.router_rules_cache_ttl_seconds >= 0 else "failed",
            "detail": str(settings.router_rules_cache_ttl_seconds),
        }
    )
    checks.append(
        {
            "name": "router_runtime",
            "status": "passed",
            "detail": (
                f"version={route_table.version}, "
                f"refresh_source={route_runtime.get('refresh_source')}, "
                f"cached_version={route_runtime.get('cached_version')}, "
                f"last_refresh={route_runtime.get('last_refresh_timestamp')}"
            ),
        }
    )
    redis_health = route_store.probe_backend_health()
    checks.append(
        {
            "name": "router_redis",
            "status": (
                "passed"
                if settings.router_redis_url and redis_health.get("reachable")
                else "failed"
                if settings.router_redis_url
                else "skipped"
            ),
            "detail": (
                f"{settings.router_redis_url}; health={redis_health.get('detail')}"
                if settings.router_redis_url
                else "not_configured"
            ),
        }
    )
    for row in provider_health.get("provider_rows", []):
        checks.append(
            {
                "name": f"provider_chain_{row.get('dim')}",
                "status": "passed" if row.get("provider_count", 0) >= 1 else "failed",
                "detail": (
                    f"providers={','.join(row.get('provider_ids') or [])}; "
                    f"fallback={'yes' if row.get('has_fallback') else 'no'}"
                ),
            }
        )
    network_profile = provider_health.get("network_profile") or {}
    checks.append(
        {
            "name": "network_domestic",
            "status": "passed" if network_profile.get("domestic_reachable") else "failed",
            "detail": str(network_profile.get("domestic") or []),
        }
    )
    checks.append(
        {
            "name": "network_overseas",
            "status": "passed" if network_profile.get("overseas_reachable") else "skipped",
            "detail": str(network_profile.get("overseas") or []),
        }
    )

    for name, path in {
        "retrieval_policy": settings.retrieval_policy_path,
        "methodology_manifest": settings.methodology_manifest_path,
        "provider_policy": settings.provider_policy_path,
        "data_sources": settings.data_sources_path,
        "taxonomy_dimensions": settings.taxonomy_dimensions_path,
        "taxonomy_dimension_aliases": settings.taxonomy_dimension_aliases_path,
        "taxonomy_dimension_patterns": settings.taxonomy_dimension_patterns_path,
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
