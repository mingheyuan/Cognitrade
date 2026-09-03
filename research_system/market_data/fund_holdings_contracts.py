from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_system.market_data.contracts import (
    EvidenceGrade,
    SnapshotIngestInput,
    SnapshotStatus,
    canonical_hash,
    require_aware_utc,
)
from research_system.market_data.dimension_contracts import (
    CoverageValueRequirement,
    DimensionContract,
    DimensionRegistry,
    FieldQueryRequirement,
    FieldValueRequirement,
    ItemCountRequirement,
    ItemStatusRequirement,
)

FUND_HOLDINGS_CONTRACT_VERSION = "cognitrade-fund-top-holdings-pit-v1"
FUND_TOP_HOLDINGS_DIMENSION = "fund_top_holdings"
FUND_TOP_HOLDINGS_COUNT = 10


class FundTopHolding(BaseModel):
    """One canonical A-share selected from a fund disclosure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1, le=FUND_TOP_HOLDINGS_COUNT)
    source_rank: int = Field(ge=1)
    symbol: str = Field(pattern=r"^\d{6}\.(?:SH|SZ|BJ)$")
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=256)
    asset_type: Literal["stock"] = "stock"
    market: Literal["CN_EQUITY"] = "CN_EQUITY"
    exchange: Literal["SH", "SZ", "BJ"]
    status: Literal["available"] = "available"
    fund_weight_pct: float = Field(ge=0.0, le=100.0)
    disclosed_weight_pct: float = Field(ge=0.0, le=100.0)
    holding_source: Literal[
        "direct_equity_disclosure",
        "target_etf_lookthrough",
    ]
    disclosure_date: date

    @field_validator("symbol", "code", "market", "exchange")
    @classmethod
    def _upper_identifiers(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def _identity_is_consistent(self) -> FundTopHolding:
        symbol_code, symbol_exchange = self.symbol.split(".", 1)
        if symbol_code != self.code or symbol_exchange != self.exchange:
            raise ValueError("holding canonical symbol/code/exchange mismatch")
        return self


class FundHoldingsLineage(BaseModel):
    """One disclosure leg required to make the selected holdings visible."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal[
        "parent_holdings",
        "parent_target_allocation",
        "target_holdings",
    ]
    instrument_code: str = Field(pattern=r"^\d{6}$")
    period_end: date
    published_at: datetime
    source_id: str = Field(min_length=1, max_length=512)
    provider: str = Field(min_length=1, max_length=128)
    publication_time_basis: Literal[
        "provider_published_at",
        "immutable_first_seen_at",
        "observed_at_current_day",
    ]

    @field_validator("instrument_code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        return value.strip()

    @field_validator("published_at")
    @classmethod
    def _aware_time(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="published_at")


class FundTopHoldingsPayload(BaseModel):
    """Strict one-slot payload that expands a fund into ten stock targets.

    This is an acquisition contract only.  It deliberately contains no fund
    scoring fields and cannot be mistaken for the stock 22-slot report contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    fund_code: str = Field(pattern=r"^\d{6}$")
    fund_name: str = Field(min_length=1, max_length=256)
    fund_type: str | None = Field(default=None, max_length=128)
    asset_type: Literal["exchange_fund", "otc_fund"]
    market: Literal["CN_FUND"] = "CN_FUND"
    holding_source: Literal[
        "direct_equity_disclosure",
        "target_etf_lookthrough",
    ]
    is_fund_of_funds: bool
    requested_top_n: Literal[10] = FUND_TOP_HOLDINGS_COUNT
    analysis_as_of_date: date
    cutoff_at: datetime
    disclosure_date: date
    availability_at: datetime
    parent_allocation_weight_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    holdings: tuple[FundTopHolding, ...] = Field(
        min_length=FUND_TOP_HOLDINGS_COUNT,
        max_length=FUND_TOP_HOLDINGS_COUNT,
    )
    lineage: tuple[FundHoldingsLineage, ...] = Field(min_length=1)
    source_artifact_id: str | None = Field(default=None, max_length=128)

    @field_validator("fund_code", "market")
    @classmethod
    def _upper_identifiers(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("cutoff_at", "availability_at")
    @classmethod
    def _aware_times(cls, value: datetime, info) -> datetime:
        return require_aware_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _strict_pit_and_top_ten(self) -> FundTopHoldingsPayload:
        if self.disclosure_date > self.analysis_as_of_date:
            raise ValueError("fund disclosure date is later than analysis_as_of_date")
        if self.availability_at > self.cutoff_at:
            raise ValueError("fund holdings were not available by the requested cutoff")
        if self.availability_at != max(item.published_at for item in self.lineage):
            raise ValueError("availability_at must equal the latest required lineage publication")
        if any(item.period_end > self.analysis_as_of_date for item in self.lineage):
            raise ValueError("lineage period_end is later than analysis_as_of_date")
        if [item.rank for item in self.holdings] != list(
            range(1, FUND_TOP_HOLDINGS_COUNT + 1)
        ):
            raise ValueError("fund holding ranks must be contiguous 1-10")
        symbols = [item.symbol for item in self.holdings]
        if len(symbols) != len(set(symbols)):
            raise ValueError("fund holdings must have unique canonical symbols")
        if any(item.disclosure_date != self.disclosure_date for item in self.holdings):
            raise ValueError("holding disclosure dates must match the parent snapshot")
        if any(item.holding_source != self.holding_source for item in self.holdings):
            raise ValueError("holding sources must match the parent snapshot")
        if self.is_fund_of_funds != (
            self.holding_source == "target_etf_lookthrough"
        ):
            raise ValueError("fund-of-funds flag and holding source disagree")
        roles = {item.role for item in self.lineage}
        expected_roles = (
            {"parent_target_allocation", "target_holdings"}
            if self.is_fund_of_funds
            else {"parent_holdings"}
        )
        if roles != expected_roles:
            raise ValueError("fund holding lineage roles are incomplete or inconsistent")
        if self.is_fund_of_funds and self.parent_allocation_weight_pct is None:
            raise ValueError("lookthrough holdings require parent_allocation_weight_pct")
        if not self.is_fund_of_funds and self.parent_allocation_weight_pct is not None:
            raise ValueError("direct holdings cannot declare parent_allocation_weight_pct")
        for item in self.holdings:
            expected_weight = item.disclosed_weight_pct
            if self.parent_allocation_weight_pct is not None:
                expected_weight *= self.parent_allocation_weight_pct / 100.0
            if abs(item.fund_weight_pct - expected_weight) > 0.000001:
                raise ValueError(
                    f"holding {item.symbol} effective weight does not match disclosure lineage"
                )
        if sum(item.fund_weight_pct for item in self.holdings) > 100.000001:
            raise ValueError("top-ten effective fund weights cannot exceed 100%")
        return self


FUND_TOP_HOLDINGS_CONTRACT = DimensionContract(
    dimension=FUND_TOP_HOLDINGS_DIMENSION,
    ordinal=0,
    snapshot_type="fund_snapshot",
    label="基金前十大持仓披露",
    description="父基金在指定日 cutoff 前可见的最新披露，并规范化为十只 A 股子标的。",
    required_fields=(
        "fund_code",
        "fund_name",
        "asset_type",
        "market",
        "holding_source",
        "is_fund_of_funds",
        "requested_top_n",
        "analysis_as_of_date",
        "cutoff_at",
        "disclosure_date",
        "availability_at",
        "lineage",
        "holdings",
    ),
    freshness_seconds=43_200,
    history_requirement=(
        "仅接受 cutoff 前已发布或当时已观测的最新持仓披露；"
        "ETF 联接必须同时证明父基金目标基金披露及目标 ETF 持仓披露可见"
    ),
    min_evidence_count=1,
    verified_empty_policy="never",
    verified_empty_requirement=None,
    item_count_requirements=(
        ItemCountRequirement(path="holdings", exact=FUND_TOP_HOLDINGS_COUNT),
        ItemCountRequirement(path="lineage", minimum=1),
    ),
    item_status_requirements=(
        ItemStatusRequirement(
            path="holdings",
            status_field="status",
            allowed_values=("available",),
            id_field="symbol",
            require_unique_ids=True,
        ),
    ),
    field_query_requirements=(
        FieldQueryRequirement(
            path="holdings",
            mode="always",
            allow_empty=False,
            minimum_window_days=0,
            result_count_matches_payload=True,
        ),
        FieldQueryRequirement(
            path="lineage",
            mode="always",
            allow_empty=False,
            minimum_window_days=0,
            result_count_matches_payload=True,
        ),
    ),
    coverage_value_requirements=(
        CoverageValueRequirement(
            key="pit_visibility",
            allowed_values=(
                "observed_by_cutoff",
                "immutable_published_by_cutoff",
            ),
        ),
        CoverageValueRequirement(
            key="asset_contract",
            allowed_values=("fund_top_holdings",),
        ),
    ),
    field_value_requirements=(
        FieldValueRequirement(path="fund_code", allowed_types=("string",)),
        FieldValueRequirement(path="fund_name", allowed_types=("string",)),
        FieldValueRequirement(path="asset_type", allowed_types=("string",)),
        FieldValueRequirement(path="market", allowed_types=("string",)),
        FieldValueRequirement(path="holding_source", allowed_types=("string",)),
        FieldValueRequirement(
            path="requested_top_n",
            allowed_types=("number",),
            number_constraint="positive",
        ),
        FieldValueRequirement(path="analysis_as_of_date", allowed_types=("date",)),
        FieldValueRequirement(path="cutoff_at", allowed_types=("datetime",)),
        FieldValueRequirement(path="disclosure_date", allowed_types=("date",)),
        FieldValueRequirement(path="availability_at", allowed_types=("datetime",)),
        FieldValueRequirement(
            path="holdings",
            allowed_types=("collection",),
            item_allowed_types=("object",),
            item_required_keys=(
                "rank",
                "source_rank",
                "symbol",
                "code",
                "name",
                "asset_type",
                "market",
                "exchange",
                "status",
                "fund_weight_pct",
                "disclosed_weight_pct",
                "holding_source",
                "disclosure_date",
            ),
            item_field_types={
                "rank": ("number",),
                "source_rank": ("number",),
                "symbol": ("string",),
                "code": ("string",),
                "name": ("string",),
                "asset_type": ("string",),
                "market": ("string",),
                "exchange": ("string",),
                "status": ("string",),
                "fund_weight_pct": ("number",),
                "disclosed_weight_pct": ("number",),
                "holding_source": ("string",),
                "disclosure_date": ("date",),
            },
            item_number_constraints={
                "rank": "positive",
                "source_rank": "positive",
                "fund_weight_pct": "nonnegative",
                "disclosed_weight_pct": "nonnegative",
            },
        ),
        FieldValueRequirement(
            path="lineage",
            allowed_types=("collection",),
            item_allowed_types=("object",),
            item_required_keys=(
                "role",
                "instrument_code",
                "period_end",
                "published_at",
                "source_id",
                "provider",
                "publication_time_basis",
            ),
            item_field_types={
                "role": ("string",),
                "instrument_code": ("string",),
                "period_end": ("date",),
                "published_at": ("datetime",),
                "source_id": ("string",),
                "provider": ("string",),
                "publication_time_basis": ("string",),
            },
        ),
    ),
)

FUND_TOP_HOLDINGS_REGISTRY = DimensionRegistry(
    (FUND_TOP_HOLDINGS_CONTRACT,),
    contract_version=FUND_HOLDINGS_CONTRACT_VERSION,
)


def validate_fund_top_holdings_payload(
    payload: FundTopHoldingsPayload | dict[str, Any],
) -> FundTopHoldingsPayload:
    """Validate and canonicalize one report-ready parent-fund slot payload."""

    if isinstance(payload, FundTopHoldingsPayload):
        return payload
    return FundTopHoldingsPayload.model_validate(payload)


class FundHoldingsSemanticError(ValueError):
    """Raised when a structurally valid slot violates the fund PIT contract."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(dict.fromkeys(str(item) for item in errors if str(item)))
        super().__init__("; ".join(self.errors))


def validate_fund_top_holdings_ingest(
    item: SnapshotIngestInput | Mapping[str, Any],
) -> FundTopHoldingsPayload:
    """Validate the semantic boundary before ingestion or publication.

    Bundle publication/materialisation must call this validator again using a
    reconstructed ``SnapshotIngestInput`` (or the persisted companion below),
    because the generic DimensionContract intentionally cannot express lineage.
    """

    ingest = (
        item
        if isinstance(item, SnapshotIngestInput)
        else SnapshotIngestInput.model_validate(item)
    )
    return _validate_semantics(
        symbol=ingest.symbol,
        market=ingest.market,
        exchange_timezone=ingest.exchange_timezone,
        dimension=ingest.dimension,
        contract_version=ingest.contract_version,
        as_of_date=ingest.as_of_date,
        status=ingest.status,
        published_at=ingest.published_at,
        effective_at=ingest.effective_at,
        payload=ingest.payload,
        query_manifest=ingest.query_manifest,
        evidence=ingest.evidence,
    )


def validate_persisted_fund_top_holdings(
    snapshot: Any,
    *,
    evidence: Iterable[Any],
) -> FundTopHoldingsPayload:
    """Re-run fund semantics from persisted rows before Bundle use."""

    return _validate_semantics(
        symbol=_row_value(snapshot, "symbol"),
        market=_row_value(snapshot, "market"),
        exchange_timezone=_row_value(snapshot, "exchange_timezone"),
        dimension=_row_value(snapshot, "dimension"),
        contract_version=_row_value(snapshot, "contract_version"),
        as_of_date=_row_value(snapshot, "as_of_date"),
        status=_row_value(snapshot, "status"),
        published_at=_row_value(snapshot, "published_at"),
        effective_at=_row_value(snapshot, "effective_at"),
        payload=_row_value(snapshot, "payload_json", fallback="payload") or {},
        query_manifest=(
            _row_value(snapshot, "query_manifest_json", fallback="query_manifest") or {}
        ),
        evidence=evidence,
    )


def _validate_semantics(
    *,
    symbol: Any,
    market: Any,
    exchange_timezone: Any,
    dimension: Any,
    contract_version: Any,
    as_of_date: Any,
    status: Any,
    published_at: Any,
    effective_at: Any,
    payload: Any,
    query_manifest: Any,
    evidence: Iterable[Any],
) -> FundTopHoldingsPayload:
    errors: list[str] = []
    try:
        resolved = validate_fund_top_holdings_payload(dict(payload or {}))
    except Exception as exc:  # Pydantic exposes the complete field paths in str(exc).
        raise FundHoldingsSemanticError((str(exc),)) from exc
    normalized_status = (
        status.value if isinstance(status, SnapshotStatus) else str(status or "")
    )
    if normalized_status != SnapshotStatus.AVAILABLE.value:
        errors.append("fund top-holdings semantic validation requires available status")
    if str(symbol or "").strip().upper().split(".", 1)[0] != resolved.fund_code:
        errors.append("snapshot symbol does not match payload fund_code")
    if str(market or "").strip().upper() != "CN_FUND":
        errors.append("fund top-holdings snapshot market must be CN_FUND")
    if str(exchange_timezone or "").strip() != "Asia/Shanghai":
        errors.append("fund top-holdings snapshot must use Asia/Shanghai")
    if str(dimension or "") != FUND_TOP_HOLDINGS_DIMENSION:
        errors.append("snapshot dimension does not match fund top-holdings contract")
    if str(contract_version or "") != FUND_HOLDINGS_CONTRACT_VERSION:
        errors.append("snapshot contract version does not match fund top-holdings contract")
    if _date_value(as_of_date) != resolved.analysis_as_of_date:
        errors.append("snapshot as_of_date does not match payload analysis_as_of_date")
    cutoff_local_date = resolved.cutoff_at.astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date()
    if cutoff_local_date != resolved.analysis_as_of_date:
        errors.append("payload cutoff_at is outside analysis_as_of_date")
    if _datetime_value(published_at) != resolved.availability_at:
        errors.append("snapshot published_at must equal payload availability_at")
    if _date_value(effective_at) != resolved.disclosure_date:
        errors.append("snapshot effective_at date must equal disclosure_date")

    manifest = dict(query_manifest or {}) if isinstance(query_manifest, Mapping) else {}
    coverage = manifest.get("coverage") if isinstance(manifest.get("coverage"), Mapping) else {}
    if coverage.get("selection_rule") != "latest_disclosure_published_by_cutoff":
        errors.append("query manifest lacks the strict latest-disclosure selection rule")
    if coverage.get("holding_source") != resolved.holding_source:
        errors.append("query manifest holding_source does not match payload")
    selection = manifest.get("selection") if isinstance(manifest.get("selection"), Mapping) else {}
    if selection.get("complete") is not True:
        errors.append("query manifest candidate selection is not complete")
    if _datetime_value(selection.get("cutoff_at")) != resolved.cutoff_at:
        errors.append("query manifest selection cutoff does not match payload")
    lineage_ids = {item.source_id for item in resolved.lineage}
    selected_ids = {
        str(value).strip() for value in list(selection.get("selected_source_ids") or [])
    }
    if selected_ids != lineage_ids:
        errors.append("query manifest selected sources do not match payload lineage")

    native_refs = [
        dict(value)
        for value in list(manifest.get("native_source_refs") or [])
        if isinstance(value, Mapping)
    ]
    native_refs_by_id = {
        str(ref.get("id") or ref.get("uri") or ref.get("locator") or "").strip(): ref
        for ref in native_refs
        if str(ref.get("id") or ref.get("uri") or ref.get("locator") or "").strip()
    }

    evidence_by_source: dict[str, list[Any]] = {}
    for proof in evidence:
        metadata = _evidence_metadata(proof)
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        source_id = str(metadata.get("source_id") or "").strip()
        if source_id:
            evidence_by_source.setdefault(source_id, []).append(proof)
    for lineage in resolved.lineage:
        proofs = evidence_by_source.get(lineage.source_id, [])
        if not proofs:
            errors.append(f"lineage source {lineage.source_id} has no bound evidence")
            continue
        matching = False
        for proof in proofs:
            metadata = _evidence_metadata(proof)
            paths = {str(path) for path in list(metadata.get("field_paths") or [])}
            grade = _row_value(proof, "grade")
            grade_value = grade.value if isinstance(grade, EvidenceGrade) else str(grade or "")
            immutable = bool(_row_value(proof, "immutable_proof"))
            published = _datetime_value(_row_value(proof, "published_at"))
            has_content = bool(
                _row_value(proof, "content_hash")
                or _row_value(proof, "content_ref")
                or _row_value(proof, "inline_content")
            )
            if published != lineage.published_at or not {"holdings", "lineage"}.issubset(paths):
                continue
            if grade_value not in {EvidenceGrade.A.value, EvidenceGrade.B.value}:
                continue
            if grade_value == EvidenceGrade.B.value and not immutable:
                continue
            if not has_content or metadata.get("immutable_content") is not True:
                continue
            if grade_value == EvidenceGrade.B.value:
                native_ref = native_refs_by_id.get(lineage.source_id)
                if native_ref is None or not _official_immutable_source(native_ref):
                    continue
                native_hash = str(native_ref.get("content_hash") or "").lower()
                native_content_ref = str(native_ref.get("content_ref") or "").strip()
                proof_hash = str(_row_value(proof, "content_hash") or "").lower()
                proof_content_ref = str(_row_value(proof, "content_ref") or "").strip()
                proof_source_url = str(_row_value(proof, "source_url") or "").strip()
                if (
                    proof_hash != native_hash
                    or proof_content_ref != native_content_ref
                    or proof_source_url != native_content_ref
                ):
                    continue
                expected_binding_hash = canonical_hash(
                    {
                        "lineage": lineage.model_dump(mode="json"),
                        "holdings": [
                            holding.model_dump(mode="json")
                            for holding in resolved.holdings
                        ],
                    }
                )
                if (
                    metadata.get("normalized_holdings_binding_hash")
                    != expected_binding_hash
                ):
                    continue
            matching = True
            break
        if not matching:
            errors.append(
                f"lineage source {lineage.source_id} lacks eligible immutable field evidence"
            )
    if errors:
        raise FundHoldingsSemanticError(errors)
    return resolved


def _official_immutable_source(source: Mapping[str, Any]) -> bool:
    content_hash = str(source.get("content_hash") or "").strip()
    content_ref = str(source.get("content_ref") or "").strip()
    source_id = str(
        source.get("id") or source.get("uri") or source.get("locator") or ""
    ).strip()
    return bool(
        source.get("immutable_proof") is True
        and re.fullmatch(r"[0-9a-fA-F]{64}", content_hash)
        and source_id == content_ref
        and re.fullmatch(
            r"https://pdf\.dfcfw\.com/pdf/H2_[A-Za-z0-9_-]+_1\.pdf",
            content_ref,
        )
    )


def _row_value(row: Any, field: str, *, fallback: str | None = None) -> Any:
    if isinstance(row, Mapping):
        if field in row:
            return row.get(field)
        return row.get(fallback) if fallback else None
    value = getattr(row, field, None)
    return value if value is not None or fallback is None else getattr(row, fallback, None)


def _evidence_metadata(row: Any) -> dict[str, Any]:
    """Read Pydantic or persisted evidence without hitting SQLAlchemy MetaData.

    Declarative SQLAlchemy models expose a class-level ``metadata`` attribute,
    while the persisted JSON column is named ``metadata_json``.  Prefer the
    persisted column when present; ``EvidenceInput`` continues to use its
    ordinary ``metadata`` mapping.
    """

    if isinstance(row, Mapping):
        value = row.get("metadata_json", row.get("metadata"))
    else:
        value = getattr(row, "metadata_json", None)
        if value is None:
            value = getattr(row, "metadata", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        resolved = value
    else:
        try:
            resolved = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        # SQLite drops timezone information from DateTime(timezone=True).
        # Market-data persistence normalizes timestamps to UTC before writing,
        # so a naive value read from that boundary represents UTC rather than an
        # unknowable local time.
        resolved = resolved.replace(tzinfo=timezone.utc)
    return require_aware_utc(resolved)


def payload_as_json(payload: FundTopHoldingsPayload) -> dict[str, Any]:
    """Return the JSON-compatible representation expected by SnapshotIngestInput."""

    return payload.model_dump(mode="json", exclude_none=False)


__all__ = [
    "FUND_HOLDINGS_CONTRACT_VERSION",
    "FUND_TOP_HOLDINGS_CONTRACT",
    "FUND_TOP_HOLDINGS_COUNT",
    "FUND_TOP_HOLDINGS_DIMENSION",
    "FUND_TOP_HOLDINGS_REGISTRY",
    "FundHoldingsLineage",
    "FundHoldingsSemanticError",
    "FundTopHolding",
    "FundTopHoldingsPayload",
    "payload_as_json",
    "validate_fund_top_holdings_ingest",
    "validate_fund_top_holdings_payload",
    "validate_persisted_fund_top_holdings",
]
