from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from starlette.concurrency import run_in_threadpool

from domain_packs.finance_ashare.holding_reports import (
    FundCandidateUnavailableError,
    FundSnapshotUnavailableError,
    FundTypeUnsupportedError,
    StaleHoldingsConfirmationRequired,
)
from research_system.api.dependencies import (
    ApiPrincipal,
    get_fund_holding_reports,
    require_principal,
)
from research_system.api.errors import ApiError, public_error_message
from research_system.api.fund_holding_report_service import (
    FundHoldingBatchConflict,
    FundHoldingBatchNotFound,
    FundHoldingReportsUnavailable,
)
from research_system.api.models import (
    FundHoldingReportCreateRequest,
    FundHoldingReportRetryRequest,
)
from research_system.api.validation import safe_identifier
from research_system.runtime.presentation.display import localize_public_error_fields


router = APIRouter(tags=["fund-holding-reports"])


async def _public_call(operation, *args, **kwargs):
    result = await run_in_threadpool(operation, *args, **kwargs)
    return localize_public_error_fields(result)


def _scope(principal: ApiPrincipal) -> dict[str, str]:
    if not principal.authenticated:
        return {}
    return {
        "user_id": str(principal.user_id or ""),
        "tenant_id": str(principal.tenant_id or ""),
    }


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, FundHoldingBatchNotFound):
        return ApiError(404, "fund_holding_batch_not_found", "未找到基金持仓报告批次")
    if isinstance(exc, FundSnapshotUnavailableError):
        return ApiError(409, "fund_snapshot_unavailable", "原运行中没有可用的 fund_snapshot")
    if isinstance(exc, FundCandidateUnavailableError):
        return ApiError(409, "fund_candidate_unavailable", "原快照中不存在所选基金")
    if isinstance(exc, FundTypeUnsupportedError):
        return ApiError(409, "fund_type_not_supported", "持仓股票报告仅支持权益类基金")
    if isinstance(exc, StaleHoldingsConfirmationRequired):
        return ApiError(
            409,
            "stale_holdings_confirmation_required",
            "持仓披露已超过 180 天，需要明确确认后才能继续",
            details={
                "disclosure_date": exc.frozen.get("disclosure_date"),
                "disclosure_age_days": exc.frozen.get("disclosure_age_days"),
                "stale_level": exc.frozen.get("stale_level"),
            },
        )
    if isinstance(exc, FundHoldingBatchConflict):
        return ApiError(409, "fund_holding_batch_conflict", public_error_message(exc, fallback="基金持仓报告批次状态冲突"))
    if isinstance(exc, FundHoldingReportsUnavailable):
        return ApiError(409, "fund_holding_reports_unavailable", public_error_message(exc, fallback="没有可用的基金持仓报告"))
    if isinstance(exc, ValueError):
        return ApiError(422, "invalid_fund_holding_report_request", public_error_message(exc, fallback="基金持仓报告请求无效"))
    raise exc


@router.post("/runs/{source_run_id}/holding-reports", status_code=202)
async def create_holding_reports(
    source_run_id: str,
    payload: FundHoldingReportCreateRequest,
    service=Depends(get_fund_holding_reports),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(source_run_id, field_name="source_run_id")
    try:
        return await _public_call(
            service.create_batch,
            resolved,
            fund_code=payload.fund_code,
            profile=payload.profile,
            source_artifact_id=payload.source_artifact_id,
            confirm_stale=payload.confirm_stale,
            force=payload.force,
            top_n=payload.top_n,
            **_scope(principal),
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/runs/{source_run_id}/holding-report-batches")
async def list_holding_report_batches(
    source_run_id: str,
    fund_code: Annotated[str | None, Query(pattern=r"^\d{6}$")] = None,
    service=Depends(get_fund_holding_reports),
    principal: ApiPrincipal = Depends(require_principal),
) -> list[dict]:
    resolved = safe_identifier(source_run_id, field_name="source_run_id")
    return await _public_call(
        service.list_batches,
        source_run_id=resolved,
        fund_code=fund_code,
        **_scope(principal),
    )


@router.get("/holding-report-batches/{batch_id}")
async def holding_report_batch_detail(
    batch_id: str,
    service=Depends(get_fund_holding_reports),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(batch_id, field_name="batch_id")
    try:
        return await _public_call(service.detail, resolved, **_scope(principal))
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/holding-report-batches/{batch_id}/cancel")
async def cancel_holding_report_batch(
    batch_id: str,
    service=Depends(get_fund_holding_reports),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(batch_id, field_name="batch_id")
    try:
        return await _public_call(service.cancel, resolved, **_scope(principal))
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/holding-report-batches/{batch_id}/retry")
async def retry_holding_report_batch(
    batch_id: str,
    payload: FundHoldingReportRetryRequest,
    service=Depends(get_fund_holding_reports),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(batch_id, field_name="batch_id")
    try:
        return await _public_call(
            service.retry,
            resolved,
            symbols=payload.symbols,
            include_cancelled=False,
            force=payload.force,
            **_scope(principal),
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.post("/holding-report-batches/{batch_id}/continue")
async def continue_holding_report_batch(
    batch_id: str,
    payload: FundHoldingReportRetryRequest,
    service=Depends(get_fund_holding_reports),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(batch_id, field_name="batch_id")
    try:
        return await _public_call(
            service.retry,
            resolved,
            symbols=payload.symbols,
            include_cancelled=True,
            **_scope(principal),
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.get("/holding-report-batches/{batch_id}/reports.zip")
async def download_holding_report_zip(
    batch_id: str,
    service=Depends(get_fund_holding_reports),
    principal: ApiPrincipal = Depends(require_principal),
) -> Response:
    resolved = safe_identifier(batch_id, field_name="batch_id")
    try:
        archive, filename, included = await run_in_threadpool(
            service.build_pdf_zip,
            resolved,
            **_scope(principal),
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return Response(
        archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
            ),
            "X-Cognitrade-Report-Count": str(included),
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
