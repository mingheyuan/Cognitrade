from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from research_system.market_data.contracts import (
    CoverageResult,
    DimensionCoverage,
    EvidenceGrade,
    SnapshotStatus,
    cutoff_at_for_date,
    is_supported_uzi_contract_version,
    require_aware_utc,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionContract,
    DimensionRegistry,
)
from research_system.market_data.historical import is_safe_degradation_manifest
from research_system.market_data.ingestion import validate_persisted_snapshot_contract
from research_system.market_data.models import (
    MarketDataEvidence,
    MarketDataSeriesPoint,
    MarketDataSnapshot,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.uzi_gate import (
    evaluate_uzi_collection_gate,
    native_quality,
    native_snapshot_usable,
)


def _database_utc(value: datetime) -> datetime:
    # SQLite drops timezone offsets even for DateTime(timezone=True).  The schema
    # contract stores UTC only, so attach UTC when reading a naive SQLite value.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_non_data_disclosure(snapshot: MarketDataSnapshot) -> bool:
    payload = dict(snapshot.payload_json or {})
    return bool(
        snapshot.status == SnapshotStatus.DATA_GAP.value
        and is_safe_degradation_manifest(snapshot.query_manifest_json)
        and payload.get("availability") == "historically_unavailable"
        and payload.get("excluded_from_scoring") is True
    )


def _snapshot_pit_visibility_errors(
    repository: MarketDataRepository,
    snapshot: MarketDataSnapshot,
    cutoff_at: datetime,
    *,
    evidence: list[MarketDataEvidence] | None = None,
    series_points: list[MarketDataSeriesPoint] | None = None,
) -> list[str]:
    """Return hard PIT violations without changing UZI quality semantics.

    A snapshot collected after a historical cutoff may still be usable when it
    is a grade-B reconstruction backed by immutable, pre-cutoff evidence.  All
    other rows must have been observable by the cutoff.  These checks are a
    candidate-selection boundary, unlike the richer contract/audit checks that
    intentionally remain independent from UZI's native ``eligible`` value.
    """

    cutoff = require_aware_utc(cutoff_at, field_name="cutoff_at")
    if _safe_non_data_disclosure(snapshot):
        # This is a post-hoc statement that no recoverable data exists, not a
        # market fact.  Keeping its empty slot makes the gap auditable while
        # ``excluded_from_scoring`` prevents it from leaking into a backtest.
        return []

    evidence = (
        repository.list_snapshot_evidence(snapshot.id)
        if evidence is None
        else evidence
    )
    series_points = (
        repository.list_snapshot_series_points(snapshot.id)
        if series_points is None
        else series_points
    )
    errors: list[str] = []
    if _database_utc(snapshot.published_at) > cutoff:
        errors.append("snapshot was published after cutoff")

    reconstruction_proven = bool(evidence) and all(
        _database_utc(item.published_at) <= cutoff
        and (
            _database_utc(item.observed_at) <= cutoff
            or (
                item.grade == EvidenceGrade.B.value
                and bool(item.immutable_proof)
            )
        )
        for item in evidence
    )
    if _database_utc(snapshot.observed_at) > cutoff and not (
        snapshot.evidence_grade == EvidenceGrade.B.value
        and reconstruction_proven
    ):
        errors.append("snapshot was not observable by cutoff")

    for item in evidence:
        if _database_utc(item.published_at) > cutoff:
            errors.append("snapshot evidence was published after cutoff")
        if _database_utc(item.observed_at) > cutoff and not (
            item.grade == EvidenceGrade.B.value and bool(item.immutable_proof)
        ):
            errors.append("snapshot evidence was not observable by cutoff")

    reconstruction_snapshot = (
        snapshot.evidence_grade == EvidenceGrade.B.value
        and reconstruction_proven
    )
    for point in series_points:
        if _database_utc(point.published_at) > cutoff:
            errors.append("snapshot series point was published after cutoff")
        if (
            _database_utc(point.observed_at) > cutoff
            and not reconstruction_snapshot
        ):
            errors.append("snapshot series point was not observable by cutoff")
    return list(dict.fromkeys(errors))


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

        selected_snapshots = {
            item.dimension: self.repository.get_snapshot(item.snapshot_id)
            for item in dimensions
            if item.snapshot_id is not None
        }
        selected_snapshots = {
            dimension: snapshot
            for dimension, snapshot in selected_snapshots.items()
            if snapshot is not None
        }
        uses_uzi_gate = is_supported_uzi_contract_version(
            self.registry.contract_version
        )
        uzi_integrity_by_profile = (
            {
                profile: evaluate_uzi_collection_gate(
                    selected_snapshots,
                    profile=profile,
                    market=market,
                )
                for profile in ("lite", "medium", "deep")
            }
            if uses_uzi_gate
            else {}
        )
        uzi_integrity = dict(uzi_integrity_by_profile.get("medium") or {})

        structural_count = sum(item.snapshot_id is not None for item in dimensions)
        ready_count = sum(item.eligible for item in dimensions)
        degraded_count = sum(item.degraded for item in dimensions)
        available_count = sum(
            item.snapshot_id is not None and item.status == SnapshotStatus.AVAILABLE
            for item in dimensions
        )
        empty_count = sum(
            item.snapshot_id is not None and item.status == SnapshotStatus.VERIFIED_EMPTY
            for item in dimensions
        )
        stale_count = sum(item.stale for item in dimensions)
        conflict_count = sum(item.conflict for item in dimensions)
        gap_count = sum(
            item.snapshot_id is None or not item.eligible
            for item in dimensions
        )
        structurally_complete = structural_count == len(self.registry)
        audit_ready_count = sum(item.audit_eligible for item in dimensions)
        audit_ready = structurally_complete and audit_ready_count == len(self.registry)
        report_ready = (
            structurally_complete and bool(uzi_integrity.get("gate_passed"))
            if uses_uzi_gate
            else audit_ready
        )
        # A Bundle is the immutable input to UZI's recovery/Agent workflow.
        # Upstream does not reject raw_data at data_integrity time: unresolved
        # gaps are repaired and the complete native self-review is the final
        # HTML gate.  Therefore a structurally complete 22-slot Bundle remains
        # generatable even when this precheck still has critical data issues.
        report_generatable = structurally_complete if uses_uzi_gate else report_ready
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
            resolved_dimension_count=structural_count,
            degraded_dimension_count=degraded_count,
            report_generatable=report_generatable,
            quality=(
                "strict"
                if report_ready and ready_count == len(self.registry)
                else "degraded"
                if report_generatable
                else "blocked"
            ),
            gate_policy=(
                str(uzi_integrity.get("gate_policy") or "uzi-3.9.4-medium")
                if uses_uzi_gate
                else self.registry.contract_version
            ),
            uzi_integrity=uzi_integrity,
            uzi_integrity_by_profile=uzi_integrity_by_profile,
            audit_ready_dimension_count=audit_ready_count,
            audit_ready=audit_ready,
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
        candidates = self.repository.list_snapshots(
            symbol=symbol,
            market=market,
            dimension=contract.dimension,
            as_of_date=as_of_date,
            cutoff_at=cutoff_at,
            include_revoked=True,
            limit=200,
        )
        candidates = [
            row
            for row in candidates
            if row.contract_version == self.registry.contract_version
            and row.revoked_at is None
        ]
        candidates.sort(
            key=lambda row: (
                0 if native_snapshot_usable(row) else 1,
                int(row.source_priority),
                -_database_utc(row.published_at).timestamp(),
            )
        )
        snapshot = next(
            (
                row
                for row in candidates
                if not _snapshot_pit_visibility_errors(
                    self.repository,
                    row,
                    cutoff_at,
                )
            ),
            None,
        )
        if snapshot is None:
            # An explicit historical-unavailable disclosure is intentionally
            # created after the requested day.  It contains no market facts
            # and is excluded from scoring, so preserve that auditable empty
            # slot without weakening the SQL cutoff for ordinary snapshots.
            disclosure_candidates = self.repository.list_snapshots(
                symbol=symbol,
                market=market,
                dimension=contract.dimension,
                as_of_date=as_of_date,
                include_revoked=True,
                limit=200,
            )
            snapshot = next(
                (
                    row
                    for row in disclosure_candidates
                    if row.contract_version == self.registry.contract_version
                    and row.revoked_at is None
                    and _safe_non_data_disclosure(row)
                ),
                None,
            )
        if snapshot is not None:
            audit_error = self._evidence_error(snapshot, contract, cutoff_at)
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
            validation = dict(snapshot.validation_json or {})
            persisted_audit_errors = [
                str(item).strip()
                for item in list(
                    validation.get("audit_errors")
                    or validation.get("errors")
                    or []
                )
                if str(item).strip()
            ]
            audit_reasons = list(
                dict.fromkeys(
                    [item for item in (audit_error, *persisted_audit_errors) if item]
                )
            )
            if conflicts:
                audit_reasons.append("unresolved field-level conflict")
            if stale:
                audit_reasons.append(
                    f"snapshot is stale: age exceeds {contract.freshness_seconds} seconds"
                )
            quality = native_quality(snapshot)
            usable = native_snapshot_usable(snapshot)
            degradation = dict(
                (snapshot.query_manifest_json or {}).get("degradation") or {}
            )
            safe_degradation = bool(
                snapshot.status == SnapshotStatus.DATA_GAP.value
                and is_safe_degradation_manifest(snapshot.query_manifest_json)
            )
            native_reason = None
            if not usable:
                native_reason = str(
                    degradation.get("message")
                    or degradation.get("reason_code")
                    or f"UZI 原生质量状态：{quality}"
                )
            return DimensionCoverage(
                dimension=contract.dimension,
                ordinal=contract.ordinal,
                label=contract.label,
                status=SnapshotStatus(snapshot.status),
                snapshot_id=snapshot.id,
                content_hash=snapshot.content_hash,
                evidence_grade=EvidenceGrade(snapshot.evidence_grade),
                stale=stale,
                conflict=bool(conflicts),
                eligible=usable,
                reason=native_reason,
                published_at=_database_utc(snapshot.published_at),
                observed_at=observed_at,
                degraded=quality in {"degraded", "partial"} or not usable,
                excluded_from_scoring=safe_degradation,
                native_quality=quality,
                audit_eligible=not audit_reasons,
                audit_reason="; ".join(audit_reasons) or None,
                audit_errors=audit_reasons,
            )
        return DimensionCoverage(
            dimension=contract.dimension,
            ordinal=contract.ordinal,
            label=contract.label,
            reason="UZI 维度结果缺失",
            native_quality="missing",
            audit_reason="snapshot missing",
            audit_errors=["snapshot missing"],
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
