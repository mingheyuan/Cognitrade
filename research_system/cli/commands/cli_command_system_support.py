from __future__ import annotations

from pathlib import Path

from research_system.runtime.platform.checks import run_checks
from research_system.runtime.diagnostics.doctoring.doctor import build_status, run_doctor
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings


def execute_cleanup_command(
    settings: Settings,
    **kwargs: object,
) -> object:
    orchestrator_cls = kwargs.get("orchestrator_cls", ResearchOrchestrator)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.cleanup()


def execute_reindex_command(
    settings: Settings,
    **kwargs: object,
) -> object:
    orchestrator_cls = kwargs.get("orchestrator_cls", ResearchOrchestrator)
    with orchestrator_cls(settings) as orchestrator:
        return orchestrator.rebuild_qdrant()


def execute_check_command(
    settings: Settings,
    **kwargs: object,
) -> object:
    run_checks_fn = kwargs.get("run_checks_fn", run_checks)
    return run_checks_fn(Path.cwd())


def execute_status_command(
    settings: Settings,
    **kwargs: object,
) -> object:
    build_status_fn = kwargs.get("build_status_fn", build_status)
    return build_status_fn(settings)


def execute_doctor_command(
    settings: Settings,
    **kwargs: object,
) -> object:
    run_doctor_fn = kwargs.get("run_doctor_fn", run_doctor)
    return run_doctor_fn(settings)
