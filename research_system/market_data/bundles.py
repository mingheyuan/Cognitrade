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
    is_supported_uzi_contract_version,
    require_aware_utc,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.historical import is_safe_degradation_manifest
from research_system.market_data.ingestion import (
    snapshot_content_hash,
    validate_persisted_snapshot_contract,
)
from research_system.market_data.models import MarketDataBundle, MarketDataBundleItem
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import (
    SnapshotResolver,
    _database_utc,
    _snapshot_pit_visibility_errors,
)
from research_system.market_data.uzi_gate import native_payload, native_quality


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
        for issue in list(coverage.uzi_integrity.get("issues") or []):
            if not isinstance(issue, dict) or issue.get("severity") != "critical":
                continue
            dimension = str(issue.get("dimension") or "").strip()
            if dimension and dimension != "overall" and dimension not in self.blocked_dimensions:
                self.blocked_dimensions.append(dimension)
        details = [
            f"{item.dimension}: {item.reason or 'not ready'}"
            for item in coverage.dimensions
            if not item.eligible
        ]
        super().__init__("cannot publish an incomplete UZI day bundle; " + "; ".join(details))


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
        allow_degraded: bool = False,
        expected_manifest_hash: str | None = None,
    ) -> MarketDataBundle:
        # Bundle revisions and Snapshot revocation share this transaction lock.
        # It closes both the max(revision)+1 race and the gap between resolving
        # a candidate and persisting its immutable item rows.
        normalized_symbol = symbol.strip().upper()
        normalized_market = market.strip().upper()
        self.repository.lock_snapshot_scope_for_publication(
            symbol=normalized_symbol,
            market=normalized_market,
            as_of_date=as_of_date,
            contract_version=self.registry.contract_version,
        )
        plan = self._publication_plan(
            symbol=normalized_symbol,
            market=normalized_market,
            as_of_date=as_of_date,
            cutoff_at=cutoff_at,
            exchange_timezone=exchange_timezone,
            now=now,
            allow_degraded=allow_degraded,
        )
        symbol = plan["symbol"]
        market = plan["market"]
        cutoff = plan["cutoff_at"]
        publication_time = plan["publication_time"]
        coverage = plan["coverage"]
        rows = plan["items"]
        manifest_hash = plan["manifest_hash"]
        locked_snapshots = {
            snapshot.id: snapshot
            for snapshot in self.repository.lock_snapshots_for_update(
                row["snapshot_id"] for row in rows if row.get("snapshot_id")
            )
        }
        for row in rows:
            snapshot_id = row.get("snapshot_id")
            if snapshot_id is None:
                continue
            snapshot = locked_snapshots.get(snapshot_id)
            if snapshot is None:
                raise BundleIntegrityError(
                    f"publication candidate Snapshot disappeared: {snapshot_id}"
                )
            if snapshot.revoked_at is not None:
                raise BundleIntegrityError(
                    f"publication candidate Snapshot was revoked: {snapshot_id}"
                )
            if (
                snapshot.symbol != symbol
                or snapshot.market != market
                or snapshot.as_of_date != as_of_date
                or snapshot.contract_version != self.registry.contract_version
                or snapshot.dimension != row["dimension"]
                or snapshot.content_hash != row["content_hash"]
                or snapshot.status != row["status"]
            ):
                raise BundleIntegrityError(
                    f"publication candidate Snapshot changed before persistence: {snapshot_id}"
                )
        if expected_manifest_hash is not None and manifest_hash != expected_manifest_hash:
            raise BundleIntegrityError(
                "publication candidate changed before confirmation; refresh the proposal"
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

    def propose(
        self,
        *,
        symbol: str,
        market: str,
        as_of_date: date,
        cutoff_at: datetime | None = None,
        exchange_timezone: str = "Asia/Shanghai",
        now: datetime | None = None,
        allow_degraded: bool = False,
    ) -> dict[str, Any]:
        """Return the exact immutable manifest that ``publish`` would create."""

        plan = self._publication_plan(
            symbol=symbol,
            market=market,
            as_of_date=as_of_date,
            cutoff_at=cutoff_at,
            exchange_timezone=exchange_timezone,
            now=now,
            allow_degraded=allow_degraded,
        )
        coverage = plan["coverage"]
        degraded_dimensions = [
            {
                "dimension": item.dimension,
                "status": item.status.value if item.status else None,
                "reason": item.reason,
            }
            for item in coverage.dimensions
            if item.snapshot_id is None or not item.eligible
        ]
        uzi_issues = [
            {
                "severity": str(issue.get("severity") or "warning"),
                "check": str(issue.get("check") or ""),
                "dimension": str(issue.get("dimension") or "overall"),
                "message": str(issue.get("message") or "")[:500],
            }
            for issue in list(coverage.uzi_integrity.get("issues") or [])
            if isinstance(issue, dict)
        ]
        missing_dimensions = sorted(
            {
                *(
                    str(item["dimension"])
                    for item in degraded_dimensions
                    if item.get("dimension")
                ),
                *(
                    str(issue["dimension"])
                    for issue in uzi_issues
                    if issue.get("dimension")
                ),
            }
        )
        return {
            "symbol": plan["symbol"],
            "market": plan["market"],
            "exchange_timezone": coverage.exchange_timezone,
            "as_of_date": as_of_date.isoformat(),
            "cutoff_at": plan["cutoff_at"].isoformat(),
            "contract_version": self.registry.contract_version,
            "manifest_hash": plan["manifest_hash"],
            "expected_dimension_count": coverage.expected_dimension_count,
            "ready_dimension_count": coverage.ready_dimension_count,
            "resolved_dimension_count": coverage.resolved_dimension_count,
            "report_ready": coverage.report_ready,
            "report_generatable": coverage.report_generatable,
            "quality": coverage.quality,
            "gate_policy": coverage.gate_policy,
            "degraded": not coverage.report_ready,
            "missing_dimensions": missing_dimensions,
            "degraded_dimensions": degraded_dimensions,
            "uzi_issues": uzi_issues,
            "uzi_critical_count": int(
                coverage.uzi_integrity.get("critical_count") or 0
            ),
            "uzi_warning_count": int(
                coverage.uzi_integrity.get("warning_count") or 0
            ),
            "items": [dict(item) for item in plan["items"]],
        }

    def _publication_plan(
        self,
        *,
        symbol: str,
        market: str,
        as_of_date: date,
        cutoff_at: datetime | None,
        exchange_timezone: str,
        now: datetime | None,
        allow_degraded: bool,
    ) -> dict[str, Any]:
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
        pit_errors: list[str] = []
        for item in coverage.dimensions:
            if item.snapshot_id is None:
                continue
            snapshot = self.repository.require_snapshot(item.snapshot_id)
            pit_errors.extend(
                f"{item.dimension}: {error}"
                for error in _snapshot_pit_visibility_errors(
                    self.repository,
                    snapshot,
                    cutoff,
                )
            )
        if pit_errors:
            raise BundleIntegrityError(
                "publication candidate crosses the PIT cutoff; "
                + "; ".join(dict.fromkeys(pit_errors))
            )
        uses_uzi_gate = is_supported_uzi_contract_version(
            self.registry.contract_version
        )
        if not coverage.report_ready and not (
            coverage.report_generatable and (uses_uzi_gate or allow_degraded)
        ):
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
        return {
            "symbol": symbol,
            "market": market,
            "cutoff_at": cutoff,
            "publication_time": publication_time,
            "coverage": coverage,
            "items": rows,
            "manifest_hash": manifest_hash,
        }


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
        materialization_registry = self.registry
        if bundle.contract_version != materialization_registry.contract_version:
            if not (
                is_supported_uzi_contract_version(bundle.contract_version)
                and is_supported_uzi_contract_version(
                    materialization_registry.contract_version
                )
            ):
                raise BundleIntegrityError(
                    "bundle contract mismatch: "
                    f"{bundle.contract_version} != "
                    f"{materialization_registry.contract_version}"
                )
            # The b3665e8 upgrade did not change UZI's immutable 22-slot
            # shape.  Bind the same contracts to the Bundle's stored version
            # so old Bundles remain read-only materializable; new publications
            # still always use the current registry version.
            materialization_registry = DimensionRegistry(
                tuple(materialization_registry),
                contract_version=bundle.contract_version,
            )
        items = self.repository.list_bundle_items(bundle.id)
        if len(items) != len(materialization_registry):
            raise BundleIntegrityError(
                f"bundle has {len(items)} items; expected {len(materialization_registry)}"
            )

        manifest_rows: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        all_source_refs: list[dict[str, Any]] = []
        seen_evidence: set[str] = set()
        bundle_coverage = dict(bundle.coverage_json or {})
        uses_uzi_gate = is_supported_uzi_contract_version(
            bundle.contract_version
        )
        degraded_bundle = bool(
            bundle_coverage.get("report_generatable") is True
            and bundle_coverage.get("quality") == "degraded"
        )
        resolved_snapshots: dict[str, Any] = {}
        for contract, item in zip(materialization_registry, items, strict=True):
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
            evidence = self.repository.list_snapshot_evidence(snapshot.id)
            pit_errors = _snapshot_pit_visibility_errors(
                self.repository,
                snapshot,
                _database_utc(bundle.cutoff_at),
                evidence=evidence,
                series_points=series,
            )
            if pit_errors:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} crosses the PIT cutoff: "
                    + "; ".join(pit_errors)
                )
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
            resolved_snapshots[contract.dimension] = snapshot
            safe_degradation = bool(
                degraded_bundle
                and snapshot.status == SnapshotStatus.DATA_GAP.value
                and is_safe_degradation_manifest(snapshot.query_manifest_json)
            )
            if not uses_uzi_gate and not safe_degradation:
                contract_errors = validate_persisted_snapshot_contract(
                    snapshot,
                    contract=contract,
                    expected_contract_version=materialization_registry.contract_version,
                )
                if contract_errors:
                    raise BundleIntegrityError(
                        f"snapshot {snapshot.id} failed contract validation: "
                        + "; ".join(contract_errors)
                    )
            if snapshot.status != item.snapshot_status or (
                not uses_uzi_gate
                and not safe_degradation
                and snapshot.status not in {
                    SnapshotStatus.AVAILABLE.value,
                    SnapshotStatus.VERIFIED_EMPTY.value,
                }
            ):
                raise BundleIntegrityError(
                    f"snapshot status drift detected for {item.dimension}"
                )
            if not uses_uzi_gate and not safe_degradation and (
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
            conflicts = self.repository.list_open_conflicts(
                symbol=bundle.symbol,
                market=bundle.market,
                dimension=item.dimension,
                as_of_date=bundle.as_of_date,
                snapshot_id=snapshot.id,
            )
            if conflicts and not uses_uzi_gate:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} has an unresolved conflict"
                )

            if (
                not uses_uzi_gate
                and not safe_degradation
                and len(evidence) < contract.min_evidence_count
            ):
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} lacks required evidence"
                )
            try:
                from research_system.market_data.fund_holdings_contracts import (
                    FUND_HOLDINGS_CONTRACT_VERSION,
                    FundHoldingsSemanticError,
                    validate_persisted_fund_top_holdings,
                )

                if (
                    materialization_registry.contract_version
                    == FUND_HOLDINGS_CONTRACT_VERSION
                ):
                    validate_persisted_fund_top_holdings(snapshot, evidence=evidence)
            except FundHoldingsSemanticError as exc:
                raise BundleIntegrityError(
                    f"snapshot {snapshot.id} failed fund holdings semantic validation: "
                    + "; ".join(exc.errors)
                ) from exc
            refs: list[dict[str, Any]] = []
            for proof in evidence:
                if (
                    not uses_uzi_gate
                    and not safe_degradation
                    and proof.grade not in {EvidenceGrade.A.value, EvidenceGrade.B.value}
                ):
                    raise BundleIntegrityError(
                        f"snapshot {snapshot.id} now has grade C evidence"
                    )
                if not uses_uzi_gate and not safe_degradation and proof.grade == EvidenceGrade.B.value and not proof.immutable_proof:
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

            stored_payload = dict(snapshot.payload_json or {})
            if uses_uzi_gate:
                raw = native_payload(snapshot)
                quality = native_quality(snapshot)
                stored_native_result = stored_payload.get("_uzi_raw_result")
                native_result = (
                    dict(stored_native_result)
                    if isinstance(stored_native_result, dict)
                    else {}
                )
                native_result["data"] = raw
                native_result.setdefault(
                    "source", snapshot.source_version or "database"
                )
                native_result.setdefault(
                    "fallback",
                    quality
                    in {
                        "assumption_only",
                        "data_gap",
                        "error",
                        "failed",
                        "missing",
                        "missing_result",
                        "no_data",
                        "parse_failed",
                        "provider_unavailable",
                        "rate_limited",
                        "timeout",
                    },
                )
                native_result.setdefault(
                    "_pipeline",
                    {
                        "dim_key": contract.dimension,
                        "quality": (
                            "partial"
                            if quality in {"degraded", "partial"}
                            else "missing"
                            if quality
                            in {
                                "assumption_only",
                                "data_gap",
                                "missing",
                                "missing_result",
                                "no_data",
                            }
                            else "error"
                            if quality
                            in {
                                "error",
                                "failed",
                                "parse_failed",
                                "provider_unavailable",
                                "rate_limited",
                                "timeout",
                            }
                            else "full"
                        ),
                        "data_gaps": list(
                            (snapshot.query_manifest_json or {}).get("missing_fields")
                            or []
                        ),
                        "error": None,
                    },
                )
                facts = {
                    **raw,
                    "availability": quality,
                    "_uzi_raw_result": native_result,
                    "_market_data_audit": {
                        "normalized_payload": stored_payload,
                        "evidence_grade": snapshot.evidence_grade,
                        "validation": dict(snapshot.validation_json or {}),
                    },
                }
            else:
                facts = stored_payload
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
                    "quality_status": (
                        native_quality(snapshot) if uses_uzi_gate else snapshot.status
                    ),
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
