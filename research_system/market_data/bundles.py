from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from research_system.market_data.contracts import (
    BundleStatus,
    CoverageResult,
    EvidenceGrade,
    MaterializedBundle,
    SnapshotStatus,
    canonical_hash,
    cutoff_at_for_date,
    require_aware_utc,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.ingestion import (
    snapshot_content_hash,
    validate_persisted_snapshot_contract,
)
from research_system.market_data.models import MarketDataBundle, MarketDataBundleItem
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver, _database_utc


class BundlePublicationError(RuntimeError):
    pass


class IncompleteBundleError(BundlePublicationError):
    def __init__(self, coverage: CoverageResult) -> None:
        self.coverage = coverage
        self.missing_dimensions = [
            item.dimension for item in coverage.dimensions if item.snapshot_id is None
        ]
        self.blocked_dimensions = [
            item.dimension
            for item in coverage.dimensions
            if item.snapshot_id is not None and not item.eligible
        ]
        details = [
            f"{item.dimension}: {item.reason or 'not ready'}"
            for item in coverage.dimensions
            if not item.eligible
        ]
        super().__init__("cannot publish incomplete PIT bundle; " + "; ".join(details))


class BundleIntegrityError(RuntimeError):
    pass


def _manifest_payload(
    *,
    symbol: str,
    market: str,
    as_of_date: date,
    cutoff_at: datetime,
    contract_version: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "market": market,
        "as_of_date": as_of_date,
        "cutoff_at": cutoff_at,
        "contract_version": contract_version,
        "items": [
            {
                "dimension": item["dimension"],
                "ordinal": item["ordinal"],
                "snapshot_id": item["snapshot_id"],
                "content_hash": item["content_hash"],
                "status": item["status"],
                "evidence_grade": item["evidence_grade"],
            }
            for item in sorted(items, key=lambda row: int(row["ordinal"]))
        ],
    }


def bundle_manifest_hash(**kwargs: Any) -> str:
    return canonical_hash(_manifest_payload(**kwargs))


class BundlePublisher:
    def __init__(
        self,
        repository: MarketDataRepository,
        *,
        registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
        resolver: SnapshotResolver | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.resolver = resolver or SnapshotResolver(repository, registry=registry)

    def publish(
        self,
        *,
        symbol: str,
        market: str,
        as_of_date: date,
        cutoff_at: datetime | None = None,
        exchange_timezone: str = "Asia/Shanghai",
        published_by: str = "system",
        now: datetime | None = None,
    ) -> MarketDataBundle:
        symbol = symbol.strip().upper()
        market = market.strip().upper()
        cutoff = (
            require_aware_utc(cutoff_at, field_name="cutoff_at")
            if cutoff_at is not None
            else cutoff_at_for_date(as_of_date, exchange_timezone)
        )
        publication_time = require_aware_utc(
            now or datetime.now(timezone.utc), field_name="now"
        )
        coverage = self.resolver.coverage(
            symbol,
            market,
            as_of_date,
            cutoff,
            exchange_timezone=exchange_timezone,
            now=publication_time,
        )
        if not coverage.report_ready:
            raise IncompleteBundleError(coverage)

        rows = [
            {
                "dimension": item.dimension,
                "ordinal": item.ordinal,
                "snapshot_id": item.snapshot_id,
                "content_hash": item.content_hash,
                "status": item.status.value if item.status else None,
                "evidence_grade": item.evidence_grade.value
                if item.evidence_grade
                else None,
            }
            for item in coverage.dimensions
        ]
        manifest_hash = bundle_manifest_hash(
            symbol=symbol,
            market=market,
            as_of_date=as_of_date,
            cutoff_at=cutoff,
            contract_version=self.registry.contract_version,
            items=rows,
        )
        existing = self.repository.find_bundle_by_hash(manifest_hash)
        if existing is not None:
            if existing.invalidated_at is not None:
                raise BundleIntegrityError(
                    "the identical manifest was previously invalidated; acquire corrected snapshots"
                )
            return existing

        previous = self.repository.latest_bundle(
            symbol=symbol,
            market=market,
            as_of_date=as_of_date,
            contract_version=self.registry.contract_version,
        )
        bundle = self.repository.add_bundle(
            MarketDataBundle(
                symbol=symbol,
                market=market,
                exchange_timezone=exchange_timezone,
                as_of_date=as_of_date,
                cutoff_at=cutoff,
                contract_version=self.registry.contract_version,
                status=BundleStatus.PUBLISHED.value,
                revision=self.repository.next_bundle_revision(
                    symbol=symbol,
                    market=market,
                    as_of_date=as_of_date,
                    contract_version=self.registry.contract_version,
                ),
                expected_dimension_count=coverage.expected_dimension_count,
                ready_dimension_count=coverage.ready_dimension_count,
                available_count=coverage.available_count,
                verified_empty_count=coverage.verified_empty_count,
                coverage_json=coverage.model_dump(mode="json"),
                manifest_hash=manifest_hash,
                supersedes_bundle_id=previous.id if previous else None,
                published_by=published_by,
                published_at=publication_time,
            )
        )
        for row in rows:
            self.repository.add_bundle_item(
                MarketDataBundleItem(
                    bundle_id=bundle.id,
                    dimension=row["dimension"],
                    ordinal=row["ordinal"],
                    snapshot_id=row["snapshot_id"],
                    snapshot_content_hash=row["content_hash"],
                    snapshot_status=row["status"],
                    evidence_grade=row["evidence_grade"],
                )
            )
        return bundle


class BundleMaterializer:
    def __init__(
        self,
        repository: MarketDataRepository,
        *,
        registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
    ) -> None:
        self.repository = repository
        self.registry = registry

    def materialize(self, bundle_id: str) -> MaterializedBundle:
        bundle = self.repository.require_bundle(bundle_id)
        if (
            bundle.status != BundleStatus.PUBLISHED.value
            or bundle.invalidated_at is not None
        ):
            raise BundleIntegrityError(f"bundle {bundle_id} is invalidated")
        if bundle.contract_version != self.registry.contract_version:
            raise BundleIntegrityError(
                f"bundle contract mismatch: {bundle.contract_version} != {self.registry.contract_version}"
            )
        items = self.repository.list_bundle_items(bundle.id)
        if len(items) != len(self.registry):
            raise BundleIntegrityError(
                f"bundle has {len(items)} items; expected {len(self.registry)}"
            )

        manifest_rows: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        all_source_refs: list[dict[str, Any]] = []
        seen_evidence: set[str] = set()
        for contract, item in zip(self.registry, items, strict=True):
            if item.ordinal != contract.ordinal or item.dimension != contract.dimension:
                raise BundleIntegrityError(
                    "bundle item order does not match the 22-slot contract"
                )
            snapshot = self.repository.require_snapshot(item.snapshot_id)
            if snapshot.symbol != bundle.symbol:
                raise BundleIntegrityError(
                    f"bundle item {item.dimension} points at another symbol"
                )
            if snapshot.market != bundle.market:
                raise BundleIntegrityError(
                    f"bundle item {item.dimension} points at another market"
                )
            if snapshot.as_of_date != bundle.as_of_date:
                raise BundleIntegrityError(
                    f"bundle item {item.dimension} points at another as-of date"
                )
            if snapshot.exchange_timezone != bundle.exchange_timezone:
                raise BundleIntegrityError(
                    f"bundle item {item.dimension} uses another exchange timezone"
                )
            if snapshot.dimension != item.dimension:
                raise BundleIntegrityError(
                    f"bundle item {item.dimension} points at another dimension"
                )
            if snapshot.contract_version != bundle.contract_version:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} has a different contract version"
                )
            if snapshot.content_hash != item.snapshot_content_hash:
                raise BundleIntegrityError(
                    f"snapshot hash drift detected for {item.dimension}"
                )
            series = self.repository.list_snapshot_series_points(snapshot.id)
            calculated_snapshot_hash = snapshot_content_hash(
                symbol=snapshot.symbol,
                market=snapshot.market,
                exchange_timezone=snapshot.exchange_timezone,
                dimension=snapshot.dimension,
                contract_version=snapshot.contract_version,
                as_of_date=snapshot.as_of_date,
                status=snapshot.status,
                evidence_grade=snapshot.evidence_grade,
                payload=dict(snapshot.payload_json or {}),
                effective_at=_database_utc(snapshot.effective_at),
                published_at=_database_utc(snapshot.published_at),
                source_version=snapshot.source_version,
                source_priority=snapshot.source_priority,
                query_manifest=dict(snapshot.query_manifest_json or {}),
                field_sources=dict(snapshot.field_sources_json or {}),
                series_hashes=[point.content_hash for point in series],
            )
            if calculated_snapshot_hash != snapshot.content_hash:
                raise BundleIntegrityError(
                    f"snapshot payload hash verification failed for {item.dimension}"
                )
            contract_errors = validate_persisted_snapshot_contract(
                snapshot,
                contract=contract,
                expected_contract_version=self.registry.contract_version,
            )
            if contract_errors:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} failed contract validation: "
                    + "; ".join(contract_errors)
                )
            if snapshot.status != item.snapshot_status or snapshot.status not in {
                SnapshotStatus.AVAILABLE.value,
                SnapshotStatus.VERIFIED_EMPTY.value,
            }:
                raise BundleIntegrityError(
                    f"snapshot status drift detected for {item.dimension}"
                )
            if (
                snapshot.evidence_grade != item.evidence_grade
                or snapshot.evidence_grade
                not in {
                    EvidenceGrade.A.value,
                    EvidenceGrade.B.value,
                }
            ):
                raise BundleIntegrityError(
                    f"snapshot evidence grade is invalid for {item.dimension}"
                )
            if snapshot.revoked_at is not None:
                raise BundleIntegrityError(f"snapshot {snapshot.id} was revoked")
            if _database_utc(snapshot.published_at) > _database_utc(bundle.cutoff_at):
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} crosses the PIT cutoff"
                )
            conflicts = self.repository.list_open_conflicts(
                symbol=bundle.symbol,
                market=bundle.market,
                dimension=item.dimension,
                as_of_date=bundle.as_of_date,
                snapshot_id=snapshot.id,
            )
            if conflicts:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} has an unresolved conflict"
                )

            evidence = self.repository.list_snapshot_evidence(snapshot.id)
            if len(evidence) < contract.min_evidence_count:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} lacks required evidence"
                )
            try:
                from research_system.market_data.fund_holdings_contracts import (
                    FUND_HOLDINGS_CONTRACT_VERSION,
                    FundHoldingsSemanticError,
                    validate_persisted_fund_top_holdings,
                )

                if self.registry.contract_version == FUND_HOLDINGS_CONTRACT_VERSION:
                    validate_persisted_fund_top_holdings(snapshot, evidence=evidence)
            except FundHoldingsSemanticError as exc:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} failed fund holdings semantic validation: "
                    + "; ".join(exc.errors)
                ) from exc
            refs: list[dict[str, Any]] = []
            for proof in evidence:
                if proof.grade not in {EvidenceGrade.A.value, EvidenceGrade.B.value}:
                    raise BundleIntegrityError(
                        f"snapshot {snapshot.id} now has grade C evidence"
                    )
                if _database_utc(proof.published_at) > _database_utc(bundle.cutoff_at):
                    raise BundleIntegrityError(
                        f"evidence for {snapshot.id} crosses the cutoff"
                    )
                if proof.grade == EvidenceGrade.A.value and _database_utc(
                    proof.observed_at
                ) > _database_utc(bundle.cutoff_at):
                    raise BundleIntegrityError(
                        f"grade A evidence for {snapshot.id} was observed after cutoff"
                    )
                if proof.grade == EvidenceGrade.B.value and not proof.immutable_proof:
                    raise BundleIntegrityError(
                        f"grade B evidence for {snapshot.id} has no immutable proof"
                    )
                ref = {
                    "type": "market_data_evidence",
                    "id": proof.id,
                    "provider": proof.provider,
                    "url": proof.source_url,
                    "content_hash": proof.content_hash,
                    "grade": proof.grade,
                    "published_at": _database_utc(proof.published_at).isoformat(),
                }
                refs.append(ref)
                if proof.id not in seen_evidence:
                    seen_evidence.add(proof.id)
                    all_source_refs.append(ref)

            facts = dict(snapshot.payload_json or {})
            if series:
                facts["_series_points"] = [
                    {
                        "series_key": point.series_key,
                        "point_at": _database_utc(point.point_at).isoformat(),
                        "effective_at": _database_utc(point.effective_at).isoformat(),
                        "published_at": _database_utc(point.published_at).isoformat(),
                        "value": point.value_json,
                        "content_hash": point.content_hash,
                    }
                    for point in series
                ]
            snapshots.append(
                {
                    "snapshot_type": contract.snapshot_type,
                    "dimension": contract.dimension,
                    "dimension_id": contract.dimension,
                    "snapshot_id": snapshot.id,
                    "symbol": snapshot.symbol,
                    "query": snapshot.symbol,
                    "snapshot_date": bundle.as_of_date.isoformat(),
                    "facts": facts,
                    "availability_status": snapshot.status,
                    "quality_status": snapshot.status,
                    "evidence_grade": snapshot.evidence_grade,
                    "content_hash": snapshot.content_hash,
                    "contract_version": snapshot.contract_version,
                    "effective_at": _database_utc(snapshot.effective_at).isoformat(),
                    "published_at": _database_utc(snapshot.published_at).isoformat(),
                    "observed_at": _database_utc(snapshot.observed_at).isoformat(),
                    "field_sources": snapshot.field_sources_json,
                    "query_manifest": snapshot.query_manifest_json,
                    "source_refs": refs,
                    "source": {
                        "skill": "market_data_bundle",
                        "vendor": refs[0]["provider"] if refs else "database",
                        "from_cache": False,
                        "source_id": snapshot.id,
                        "dim": contract.dimension,
                    },
                }
            )
            manifest_rows.append(
                {
                    "dimension": item.dimension,
                    "ordinal": item.ordinal,
                    "snapshot_id": item.snapshot_id,
                    "content_hash": item.snapshot_content_hash,
                    "status": item.snapshot_status,
                    "evidence_grade": item.evidence_grade,
                }
            )

        calculated_hash = bundle_manifest_hash(
            symbol=bundle.symbol,
            market=bundle.market,
            as_of_date=bundle.as_of_date,
            cutoff_at=_database_utc(bundle.cutoff_at),
            contract_version=bundle.contract_version,
            items=manifest_rows,
        )
        if calculated_hash != bundle.manifest_hash:
            raise BundleIntegrityError("bundle manifest hash verification failed")
        return MaterializedBundle(
            bundle_id=bundle.id,
            symbol=bundle.symbol,
            market=bundle.market,
            as_of_date=bundle.as_of_date,
            cutoff_at=_database_utc(bundle.cutoff_at),
            contract_version=bundle.contract_version,
            manifest_hash=bundle.manifest_hash,
            snapshots=snapshots,
            source_refs=all_source_refs,
        )


__all__ = [
    "BundleIntegrityError",
    "BundleMaterializer",
    "BundlePublicationError",
    "BundlePublisher",
    "IncompleteBundleError",
    "bundle_manifest_hash",
]
