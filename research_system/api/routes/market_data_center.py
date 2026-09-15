from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_market_data_center,
    require_principal,
    require_role,
)
from research_system.api.errors import ApiError
from research_system.market_data.service import MarketDataServiceError

router = APIRouter(prefix="/finance/market-data", tags=["market data center"])
_MARKET_DATA_CENTER_DEPENDENCY = Depends(get_market_data_center)
_PRINCIPAL_DEPENDENCY = Depends(require_principal)
_ADMIN_DEPENDENCY = Depends(require_role("admin"))


class CollectionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    dimensions: list[str] | Literal["all"] = "all"
    mode: Literal["missing_or_stale", "force_all"] = "missing_or_stale"
    max_concurrency: int = Field(default=5, ge=1, le=5)
    publish_when_complete: bool = False
    include_research_archive: bool | None = Field(
        default=None,
        description="股票任务显式启用原文归档及研究；省略时保留完整任务启用、单维任务关闭的默认行为",
    )
    research_document_requests: list[dict[str, str]] = Field(default_factory=list, max_length=12)
    asset_family: Literal["auto", "equity", "fund"] = "auto"
    top_n: Literal[10] = 10

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item or "").strip().upper() for item in value))
        if not normalized or any(not item for item in normalized):
            raise ValueError("symbols must contain non-empty values")
        return normalized

    @field_validator("end_date")
    @classmethod
    def validate_range(cls, value: date, info):
        start = info.data.get("start_date")
        if isinstance(start, date) and value < start:
            raise ValueError("end_date must not be earlier than start_date")
        return value


class CollectionRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(default_factory=list, max_length=100)
    dimensions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("symbols")
    @classmethod
    def normalize_retry_symbols(cls, value: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                str(item or "").strip().upper() for item in value if str(item or "").strip()
            )
        )


    @field_validator("dimensions")
    @classmethod
    def normalize_retry_dimensions(cls, value: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                str(item or "").strip() for item in value if str(item or "").strip()
            )
        )


class CollectionFreezeConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    acknowledge_degraded: bool = False


class ManualSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=64)
    market: str = Field(default="cn_equity", min_length=1, max_length=64)
    dimension_id: str = Field(min_length=1, max_length=64)
    as_of_date: date
    cutoff_at: datetime
    effective_at: datetime | None = None
    published_at: datetime
    evidence_grade: Literal["B"] = "B"
    status: Literal["available", "verified_empty"]
    payload: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, Any] = Field(default_factory=dict)
    query_manifest: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=2000)
    empty_reason: str | None = Field(default=None, max_length=1000)


class RevokeSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)
    superseded_by_id: str | None = Field(default=None, max_length=26)


class ResolveConflictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_snapshot_id: str | None = Field(default=None, max_length=26)
    resolution: Literal["select_candidate", "mark_invalid", "manual_override"]
    reason: str = Field(min_length=3, max_length=2000)
    manual_value: Any | None = None


class PublishBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=64)
    market: str = Field(default="cn_equity", min_length=1, max_length=64)
    as_of_date: date
    cutoff_at: datetime | None = None


class SubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str = Field(default="cn_equity", min_length=1, max_length=64)
    enabled: bool
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    local_time: str = Field(default="23:15", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    dimensions: list[str] | Literal["all"] = "all"
    max_concurrency: int = Field(default=5, ge=1, le=5)


class ReportManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=100)
    market_by_symbol: dict[str, str] = Field(default_factory=dict)
    as_of_date: date
    evaluation_at: datetime | None = None
    report_kind: str = Field(default="single_stock", min_length=1, max_length=64)
    workflow_pins: dict[str, Any] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    asset_family: Literal["equity", "fund"] = "equity"
    fund_bundle_id: str | None = Field(default=None, min_length=1, max_length=26)


class InstrumentResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=256)
    asset_family: Literal["auto", "equity", "fund"] = "auto"


def _owner(principal: ApiPrincipal) -> tuple[str, str, str]:
    return (
        str(principal.tenant_id or "local"),
        str(principal.user_id or principal.actor_id or "local_development"),
        str(principal.actor_id or principal.user_id or "local_development"),
    )


def _call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except MarketDataServiceError as exc:
        raise ApiError(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        ) from exc


async def _call_service(callable_, *args, **kwargs):
    return await run_in_threadpool(_call, callable_, *args, **kwargs)


@router.get("/contract")
async def get_contract(
    market: Annotated[str, Query(min_length=1, max_length=64)] = "CN_EQUITY",
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    return await _call_service(service.get_contract, market=market)


@router.post("/instruments/resolve")
async def resolve_instrument(
    request: InstrumentResolveRequest,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    del principal
    return await _call_service(
        service.resolve_instrument,
        query=request.query,
        asset_family=request.asset_family,
        allow_network=True,
    )


@router.get("/coverage")
async def get_coverage(
    symbol: Annotated[str, Query(min_length=1, max_length=64)],
    as_of_date: date,
    market: Annotated[str, Query(min_length=1, max_length=64)] = "cn_equity",
    cutoff_at: datetime | None = None,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    return await _call_service(
        service.get_coverage,
        symbol=symbol,
        market=market,
        as_of_date=as_of_date,
        cutoff_at=cutoff_at,
    )


@router.get("/snapshots")
async def list_snapshots(
    symbol: str | None = None,
    dimension_id: str | None = None,
    as_of_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    return await _call_service(
        service.list_snapshots,
        symbol=symbol,
        dimension_id=dimension_id,
        as_of_date=as_of_date,
        limit=limit,
        offset=offset,
    )


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, service=_MARKET_DATA_CENTER_DEPENDENCY) -> dict[str, Any]:
    return await _call_service(service.get_snapshot, snapshot_id)


@router.post("/collection-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_collection_job(
    request: CollectionJobRequest,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.create_collection_job,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        request=request.model_dump(mode="json"),
    )


@router.get("/collection-jobs")
async def list_collection_jobs(
    job_status: str | None = Query(default=None, alias="status"),
    symbol: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    asset_family: Literal["equity", "fund"] | None = None,
    as_of_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, _ = _owner(principal)
    return await _call_service(
        service.list_collection_jobs,
        tenant_id=tenant_id,
        user_id=user_id,
        status=job_status,
        symbol=symbol,
        asset_family=asset_family,
        as_of_date=as_of_date,
        limit=limit,
        offset=offset,
    )


@router.get("/collection-jobs/{job_id}")
async def get_collection_job(
    job_id: str,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, _ = _owner(principal)
    return await _call_service(
        service.get_collection_job,
        job_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )


@router.post("/collection-jobs/{job_id}/retry-failed", status_code=status.HTTP_202_ACCEPTED)
async def retry_failed_collection_tasks(
    job_id: str,
    request: CollectionRetryRequest | None = None,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.retry_failed,
        job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        symbols=list(request.symbols) if request else None,
        dimensions=list(request.dimensions) if request else None,
    )


@router.post("/collection-jobs/{job_id}/confirm-freeze")
async def confirm_collection_freeze(
    job_id: str,
    request: CollectionFreezeConfirmRequest,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.confirm_collection_freeze,
        job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        proposal_hash=request.proposal_hash,
        acknowledge_degraded=request.acknowledge_degraded,
    )


@router.post(
    "/collection-jobs/{job_id}/agent-recovery",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_collection_agent_recovery(
    job_id: str,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.start_collection_agent_recovery,
        job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
    )


@router.post("/collection-jobs/{job_id}/cancel")
async def cancel_collection_job(
    job_id: str,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.cancel_collection_job,
        job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
    )


@router.post("/manual-snapshots", status_code=status.HTTP_201_CREATED)
async def create_manual_snapshot(
    request: ManualSnapshotRequest,
    principal: ApiPrincipal = _ADMIN_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.create_manual_snapshot,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        request=request.model_dump(mode="json"),
    )


@router.post("/snapshots/{snapshot_id}/revoke")
async def revoke_snapshot(
    snapshot_id: str,
    request: RevokeSnapshotRequest,
    principal: ApiPrincipal = _ADMIN_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    _, _, actor_id = _owner(principal)
    return await _call_service(
        service.revoke_snapshot,
        snapshot_id,
        actor_id=actor_id,
        **request.model_dump(),
    )


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    conflict_id: str,
    request: ResolveConflictRequest,
    principal: ApiPrincipal = _ADMIN_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    _, _, actor_id = _owner(principal)
    return await _call_service(
        service.resolve_conflict,
        conflict_id,
        actor_id=actor_id,
        request=request.model_dump(mode="json"),
    )


@router.get("/bundles")
async def list_bundles(
    symbol: str | None = None,
    as_of_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    return await _call_service(
        service.list_bundles,
        symbol=symbol,
        as_of_date=as_of_date,
        limit=limit,
        offset=offset,
    )


@router.get("/bundles/{bundle_id}")
async def get_bundle(bundle_id: str, service=_MARKET_DATA_CENTER_DEPENDENCY) -> dict[str, Any]:
    return await _call_service(service.get_bundle, bundle_id)


@router.post("/bundles/publish", status_code=status.HTTP_201_CREATED)
async def publish_bundle(
    request: PublishBundleRequest,
    principal: ApiPrincipal = _ADMIN_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    _, _, actor_id = _owner(principal)
    return await _call_service(
        service.publish_bundle,
        actor_id=actor_id,
        request=request.model_dump(mode="json"),
    )


@router.get("/subscriptions")
async def list_subscriptions(
    symbol: str | None = None,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, _ = _owner(principal)
    return await _call_service(
        service.list_subscriptions,
        tenant_id=tenant_id,
        user_id=user_id,
        symbol=symbol,
    )


@router.put("/subscriptions/{symbol}")
async def upsert_subscription(
    symbol: str,
    request: SubscriptionRequest,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.upsert_subscription,
        symbol=symbol,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        request=request.model_dump(mode="json"),
    )


@router.get("/providers")
async def list_providers(service=_MARKET_DATA_CENTER_DEPENDENCY) -> dict[str, Any]:
    return await _call_service(service.list_providers)


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, service=_MARKET_DATA_CENTER_DEPENDENCY) -> dict[str, Any]:
    return await _call_service(service.test_provider, provider_id)


@router.post("/report-manifests", status_code=status.HTTP_201_CREATED)
async def create_report_manifest(
    request: ReportManifestRequest,
    principal: ApiPrincipal = _PRINCIPAL_DEPENDENCY,
    service=_MARKET_DATA_CENTER_DEPENDENCY,
) -> dict[str, Any]:
    tenant_id, user_id, actor_id = _owner(principal)
    return await _call_service(
        service.create_report_manifest,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_id=actor_id,
        request=request.model_dump(mode="json"),
    )


__all__ = ["router"]
