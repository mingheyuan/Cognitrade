from __future__ import annotations

from pathlib import Path

from research_system.runtime.diagnostics.doctoring.doctor_environment_checks import build_environment_checks
from research_system.runtime.diagnostics.doctoring.doctor_provider_checks import build_provider_checks
from research_system.runtime.diagnostics.doctoring.doctor_router_checks import build_router_checks
from research_system.runtime.diagnostics.doctoring.doctor_workspace_checks import (
    build_config_file_checks,
    build_qdrant_checks,
    build_runs_presence_check,
    build_workspace_path_checks,
)
from research_system.runtime.diagnostics.status.provider_health import ProviderHealthService
from research_system.runtime.platform.settings import Settings
from research_system.services.routing.tables import build_intent_route_table_store


def build_doctor_checks(settings: Settings) -> list[dict]:
    checks: list[dict] = []
    route_store = build_intent_route_table_store(settings)
    route_table = route_store.load_table()
    route_runtime = route_store.describe_runtime_state()
    provider_health = ProviderHealthService(settings).build_report()

    root = settings.workspace_root.parent
    env_path = root / ".env"
    checks.extend(build_environment_checks(settings, env_path))
    checks.extend(build_router_checks(settings, route_store, route_table.version, route_runtime))
    checks.extend(build_provider_checks(provider_health))
    checks.extend(build_config_file_checks(settings))
    checks.extend(build_workspace_path_checks(settings))
    checks.extend(build_qdrant_checks(settings))
    checks.append(build_runs_presence_check(settings))
    return checks


def build_doctor_payload(settings: Settings) -> dict:
    checks = build_doctor_checks(settings)
    overall_status = "completed" if all(item["status"] in {"passed", "skipped"} for item in checks) else "failed"
    return {
        "status": overall_status,
        "checks": checks,
    }


__all__ = ["build_doctor_checks", "build_doctor_payload"]
