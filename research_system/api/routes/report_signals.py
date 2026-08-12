from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_report_orchestration,
    get_signal_engine,
    require_principal,
    require_role,
)
from research_system.api.errors import ApiError
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.service import owner_scope


router = APIRouter(prefix="/finance", tags=["strategy reports and signals"])


class ReportJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str = Field(min_length=1, max_length=64)
    as_of_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    source_run_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid_dates(self) -> "ReportJobRequest":
        if self.as_of_date is not None and (self.start_date is not None or self.end_date is not None):
            raise ValueError("as_of_date 不能与日期区间同时提供")
        if self.as_of_date is None and (self.start_date is None or self.end_date is None):
            raise ValueError("请提供 as_of_date，或同时提供 start_date/end_date")
        return self

    def date_range(self) -> tuple[date, date]:
        if self.as_of_date is not None:
            return self.as_of_date, self.as_of_date
        assert self.start_date is not None and self.end_date is not None
        return self.start_date, self.end_date


class RetryReportJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(default_factory=list, max_length=20)
    full_rerun: bool = False
    confirmed: bool = False


class SignalPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str = Field(min_length=1, max_length=64)
    report_set_id: str = Field(min_length=1, max_length=64)
    draft_strategy_id: str | None = Field(default=None, max_length=64)


class GenerateSignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str = Field(min_length=1, max_length=64)
    report_set_id: str = Field(min_length=1, max_length=64)


class SchedulerTickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now: datetime | None = None


@router.post("/report-backfills/estimate")
async def estimate_report_backfill(
    payload: ReportJobRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    start_date, end_date = payload.date_range()
    return await _call(
        service.estimate,
        principal,
        assignment_id=payload.assignment_id,
        start_date=start_date,
        end_date=end_date,
        source_run_id=payload.source_run_id,
    )


@router.post("/scheduler/tick")
async def run_scheduler_tick(
    payload: SchedulerTickRequest,
    _principal: ApiPrincipal = Depends(require_role("admin")),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    job_ids = await run_in_threadpool(service.run_due_schedules, now=payload.now)
    return {"job_ids": job_ids, "count": len(job_ids)}


@router.post("/report-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_report_job(
    payload: ReportJobRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    start_date, end_date = payload.date_range()
    return await _call(
        service.create_job,
        principal,
        assignment_id=payload.assignment_id,
        start_date=start_date,
        end_date=end_date,
        source_run_id=payload.source_run_id,
        manual=True,
    )


@router.post("/report-backfills", status_code=status.HTTP_202_ACCEPTED)
async def create_report_backfill(
    payload: ReportJobRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await create_report_job(payload, principal, service)


@router.get("/report-jobs")
async def list_report_jobs(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await _call(service.list_jobs, principal, limit=limit)


@router.get("/report-jobs/{job_id}")
async def get_report_job(
    job_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await _call(service.get_job, principal, job_id)


@router.post("/report-jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_report_job(
    job_id: str,
    payload: RetryReportJobRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await _call(
        service.retry,
        principal,
        job_id,
        symbols=payload.symbols,
        full_rerun=payload.full_rerun,
        confirmed=payload.confirmed,
    )


@router.post("/report-jobs/{job_id}/pause")
async def pause_report_job(
    job_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await _call(service.pause, principal, job_id)


@router.post("/report-jobs/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_report_job(
    job_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await _call(service.resume, principal, job_id)


@router.get("/daily-report-sets")
async def list_daily_report_sets(
    fund_code: str | None = None,
    as_of_date: date | None = None,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await _call(
        service.list_report_sets,
        principal,
        fund_code=fund_code,
        as_of_date=as_of_date,
    )


@router.get("/daily-report-sets/{report_set_id}")
async def get_daily_report_set(
    report_set_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_report_orchestration),
) -> dict[str, Any]:
    return await _call(service.get_report_set, principal, report_set_id)


@router.post("/signal-previews")
async def preview_signal(
    payload: SignalPreviewRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_signal_engine),
) -> dict[str, Any]:
    return await _call(
        service.preview,
        principal,
        assignment_id=payload.assignment_id,
        report_set_id=payload.report_set_id,
        draft_strategy_id=payload.draft_strategy_id,
    )


@router.post("/trade-signals/recompute")
async def recompute_trade_signals(
    payload: GenerateSignalRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_signal_engine),
) -> dict[str, Any]:
    return await _call(
        service.recompute,
        principal,
        assignment_id=payload.assignment_id,
        report_set_id=payload.report_set_id,
    )


@router.get("/trade-signals")
async def list_trade_signals(
    assignment_id: str | None = None,
    subject_code: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_signal_engine),
) -> dict[str, Any]:
    return await _call(
        service.list_signals,
        principal,
        assignment_id=assignment_id,
        subject_code=subject_code,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/trade-signals/{signal_id}")
async def get_trade_signal(
    signal_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_signal_engine),
) -> dict[str, Any]:
    return await _call(service.get_signal, principal, signal_id)


async def _call(operation, principal: ApiPrincipal, *args, **kwargs):
    scope = owner_scope(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        actor_id=principal.actor_id,
    )
    try:
        return await run_in_threadpool(operation, scope, *args, **kwargs)
    except StrategyDomainError as exc:
        raise ApiError(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        ) from exc


__all__ = ["router"]
