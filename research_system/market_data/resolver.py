from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from research_system.market_data.contracts import (
    CoverageResult,
    DimensionCoverage,
    EvidenceGrade,
    SnapshotStatus,
    cutoff_at_for_date,
    require_aware_utc,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionContract,
    DimensionRegistry,
)
from research_system.market_data.ingestion import validate_persisted_snapshot_contract
from research_system.market_data.models import MarketDataSnapshot
from research_system.market_data.repository import MarketDataRepository


def _database_utc(value: datetime) -> datetime:
    # SQLite drops timezone offsets even for DateTime(timezone=True).  The schema
    # contract stores UTC only, so attach UTC when reading a naive SQLite value.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SnapshotResolver:
    def __init__(
        self,
        repository: MarketDataRepository,
        *,
        registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
    ) -> None:
        self.repository = repository
        self.registry = registry

    def coverage(
        self,
        symbol: str,
        market: str,
        as_of_date: date,
        cutoff_at: datetime | None = None,
        *,
        exchange_timezone: str = "Asia/Shanghai",
        now: datetime | None = None,
    ) -> CoverageResult:
        symbol = symbol.strip().upper()
        market = market.strip().upper()
        cutoff = (
            require_aware_utc(cutoff_at, field_name="cutoff_at")
            if cutoff_at is not None
            else cutoff_at_for_date(as_of_date, exchange_timezone)
        )
        current = require_aware_utc(now or datetime.now(timezone.utc), field_name="now")
        is_current_local_date = (
            current.astimezone(ZoneInfo(exchange_timezone)).date() == as_of_date
        )

        dimensions = [
            self._resolve_dimension(
                symbol=symbol,
                market=market,
                as_of_date=as_of_date,
                cutoff_at=cutoff,
                now=current,
                current_day=is_current_local_date,
                contract=contract,
            )
            for contract in self.registry
        ]

        def structurally_usable(item: DimensionCoverage) -> bool:
            return bool(
                item.snapshot_id is not None
                and not item.conflict
                and (
                    item.eligible
                    or (
                        item.stale
                        and str(item.reason or "").startswith("snapshot is stale")
                    )
                )
            )

        structural_count = sum(structurally_usable(item) for item in dimensions)
        ready_count = sum(item.eligible for item in dimensions)
        available_count = sum(
            structurally_usable(item) and item.status == SnapshotStatus.AVAILABLE
            for item in dimensions
        )
        empty_count = sum(
            structurally_usable(item) and item.status == SnapshotStatus.VERIFIED_EMPTY
            for item in dimensions
        )
        stale_count = sum(item.stale for item in dimensions)
        conflict_count = sum(item.conflict for item in dimensions)
        gap_count = sum(item.snapshot_id is None for item in dimensions)
        structurally_complete = structural_count == len(self.registry)
        report_ready = structurally_complete and ready_count == len(self.registry)
        return CoverageResult(
            symbol=symbol,
            market=market,
            exchange_timezone=exchange_timezone,
            as_of_date=as_of_date,
            cutoff_at=cutoff,
            contract_version=self.registry.contract_version,
            expected_dimension_count=len(self.registry),
            ready_dimension_count=ready_count,
            available_count=available_count,
            verified_empty_count=empty_count,
            gap_count=gap_count,
            stale_count=stale_count,
            conflict_count=conflict_count,
            structurally_complete=structurally_complete,
            report_ready=report_ready,
            dimensions=dimensions,
        )

    def resolve_snapshot(
        self,
        *,
        symbol: str,
        market: str,
        dimension: str,
        as_of_date: date,
        cutoff_at: datetime | None = None,
        exchange_timezone: str = "Asia/Shanghai",
        now: datetime | None = None,
    ) -> MarketDataSnapshot | None:
        coverage = self.coverage(
            symbol,
            market,
            as_of_date,
            cutoff_at,
            exchange_timezone=exchange_timezone,
            now=now,
        )
        item = next(row for row in coverage.dimensions if row.dimension == dimension)
        if not item.eligible or not item.snapshot_id:
            return None
        return self.repository.get_snapshot(item.snapshot_id)

    def _resolve_dimension(
        self,
        *,
        symbol: str,
        market: str,
        as_of_date: date,
        cutoff_at: datetime,
        now: datetime,
        current_day: bool,
        contract: DimensionContract,
    ) -> DimensionCoverage:
        candidates = self.repository.list_candidate_snapshots(
            symbol=symbol,
            market=market,
            dimension=contract.dimension,
            as_of_date=as_of_date,
            contract_version=self.registry.contract_version,
            cutoff_at=cutoff_at,
        )
        fallback: DimensionCoverage | None = None
        for snapshot in candidates:
            evidence_error = self._evidence_error(snapshot, contract, cutoff_at)
            conflicts = self.repository.list_open_conflicts(
                symbol=symbol,
                market=market,
                dimension=contract.dimension,
                as_of_date=as_of_date,
                snapshot_id=snapshot.id,
            )
            observed_at = _database_utc(snapshot.observed_at)
            stale = (
                current_day
                and (now - observed_at).total_seconds() > contract.freshness_seconds
            )
            reason = evidence_error
            if conflicts:
                reason = "unresolved field-level conflict"
            elif stale:
                reason = f"snapshot is stale: age exceeds {contract.freshness_seconds} seconds"
            eligible = not evidence_error and not conflicts and not stale
            item = DimensionCoverage(
                dimension=contract.dimension,
                ordinal=contract.ordinal,
                label=contract.label,
                status=SnapshotStatus(snapshot.status),
                snapshot_id=snapshot.id,
                content_hash=snapshot.content_hash,
                evidence_grade=EvidenceGrade(snapshot.evidence_grade),
                stale=stale,
                conflict=bool(conflicts),
                eligible=eligible,
                reason=reason,
                published_at=_database_utc(snapshot.published_at),
                observed_at=observed_at,
            )
            if eligible:
                return item
            if fallback is None:
                fallback = item
        if fallback is not None:
            return fallback

        latest = self.repository.list_snapshots(
            symbol=symbol,
            market=market,
            dimension=contract.dimension,
            as_of_date=as_of_date,
            include_revoked=True,
            limit=1,
        )
        reason = "no A/B snapshot was published by the cutoff"
        if latest:
            row = latest[0]
            if row.revoked_at is not None:
                reason = "latest snapshot was revoked"
            elif row.status == SnapshotStatus.DATA_GAP.value:
                errors = list((row.validation_json or {}).get("errors") or [])
                reason = f"data_gap: {'; '.join(errors)}" if errors else "data_gap"
            elif row.evidence_grade == EvidenceGrade.C.value:
                reason = "latest snapshot only has grade C evidence"
            elif _database_utc(row.published_at) > cutoff_at:
                reason = "latest snapshot was published after the cutoff"
        return DimensionCoverage(
            dimension=contract.dimension,
            ordinal=contract.ordinal,
            label=contract.label,
            reason=reason,
        )

    def _evidence_error(
        self,
        snapshot: MarketDataSnapshot,
        contract: DimensionContract,
        cutoff_at: datetime,
    ) -> str | None:
        if snapshot.status not in {
            SnapshotStatus.AVAILABLE.value,
            SnapshotStatus.VERIFIED_EMPTY.value,
        }:
            return f"snapshot status {snapshot.status} is not complete"
        if snapshot.revoked_at is not None:
            return "snapshot was revoked"
        if snapshot.evidence_grade not in {
            EvidenceGrade.A.value,
            EvidenceGrade.B.value,
        }:
            return "snapshot evidence grade is not A/B"
        if _database_utc(snapshot.published_at) > cutoff_at:
            return "snapshot was published after cutoff"
        contract_errors = validate_persisted_snapshot_contract(
            snapshot,
            contract=contract,
            expected_contract_version=self.registry.contract_version,
        )
        if contract_errors:
            return "snapshot contract validation failed: " + "; ".join(contract_errors)
        evidence = self.repository.list_snapshot_evidence(snapshot.id)
        if len(evidence) < contract.min_evidence_count:
            return "snapshot no longer has the minimum evidence set"
        try:
            from research_system.market_data.fund_holdings_contracts import (
                FUND_HOLDINGS_CONTRACT_VERSION,
                FundHoldingsSemanticError,
                validate_persisted_fund_top_holdings,
            )

            if self.registry.contract_version == FUND_HOLDINGS_CONTRACT_VERSION:
                validate_persisted_fund_top_holdings(snapshot, evidence=evidence)
        except FundHoldingsSemanticError as exc:
            return "fund holdings semantic validation failed: " + "; ".join(exc.errors)
        for item in evidence:
            if item.grade not in {EvidenceGrade.A.value, EvidenceGrade.B.value}:
                return "snapshot has grade C evidence"
            if _database_utc(item.published_at) > cutoff_at:
                return "snapshot evidence was published after cutoff"
            if (
                item.grade == EvidenceGrade.A.value
                and _database_utc(item.observed_at) > cutoff_at
            ):
                return "grade A evidence was observed after cutoff"
            if item.grade == EvidenceGrade.B.value and not item.immutable_proof:
                return "grade B evidence lacks immutable proof"
        return None


__all__ = ["SnapshotResolver"]
