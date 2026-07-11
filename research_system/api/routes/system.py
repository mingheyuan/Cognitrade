from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import get_mutation_lock, get_repo_root, get_settings, require_role
from research_system.runtime.diagnostics.doctoring.doctor import build_status, run_doctor
from research_system.runtime.platform.checks import run_checks


router = APIRouter(tags=["system"])


@router.get("/system/status")
async def system_status(settings=Depends(get_settings)) -> dict:
    return await run_in_threadpool(build_status, settings)


@router.post("/system/doctor")
async def system_doctor(settings=Depends(get_settings)) -> dict:
    return await run_in_threadpool(run_doctor, settings)


@router.post("/admin/system/checks")
async def system_checks(
    principal=Depends(require_role("admin")),
    repo_root=Depends(get_repo_root),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await run_in_threadpool(run_checks, repo_root)


__all__ = ["router"]
