from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import get_mutation_lock, get_repo_root, get_settings, require_role
from research_system.runtime.diagnostics.doctoring.doctor import build_status, run_doctor
from research_system.runtime.platform.checks import run_checks
from research_system.runtime.presentation.display import localize_public_error_fields


router = APIRouter(tags=["system"])


@router.get("/system/status")
async def system_status(settings=Depends(get_settings)) -> dict:
    result = await run_in_threadpool(build_status, settings)
    return localize_public_error_fields(result)


@router.post("/system/doctor")
async def system_doctor(settings=Depends(get_settings)) -> dict:
    result = await run_in_threadpool(run_doctor, settings)
    return localize_public_error_fields(result)


@router.post("/admin/system/checks")
async def system_checks(
    principal=Depends(require_role("admin")),
    repo_root=Depends(get_repo_root),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        result = await run_in_threadpool(run_checks, repo_root)
        return localize_public_error_fields(result)


__all__ = ["router"]
