"""Lightweight live checks for already-verified immutable report references.

This deliberately does not project native facts or rehash large payload JSON.
Full Bundle materialization remains mandatory at initial input consumption.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import load_only

from research_system.market_data.bundles import bundle_manifest_hash
from research_system.market_data.contracts import is_supported_uzi_contract_version
from research_system.market_data.models import (
    MarketDataEvidence,
    MarketDataSeriesPoint,
    MarketDataSnapshot,
    MarketDataSnapshotEvidence,
    MarketDataSnapshotSeriesPoint,
)
from research_system.market_data.resolver import (
    _database_utc,
    _snapshot_pit_visibility_errors,
)


def validate_bundle_reference(
    repository, bundle, *, evidence_bindings=None, known_at=None
):
    from research_system.market_data.report_inputs import ReportInputError

    def fail(message):
        raise ReportInputError("bundle_reference_invalid", message)

    if bundle.status != "published" or bundle.invalidated_at is not None:
        fail("冻结 Bundle 已失效")
    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )
    from research_system.market_data.fund_holdings_contracts import (
        FUND_HOLDINGS_CONTRACT_VERSION,
        FUND_TOP_HOLDINGS_REGISTRY,
    )

    registry = (
        FUND_TOP_HOLDINGS_REGISTRY
        if bundle.contract_version == FUND_HOLDINGS_CONTRACT_VERSION
        else DEFAULT_DIMENSION_REGISTRY
    )
    if (
        bundle.contract_version != FUND_HOLDINGS_CONTRACT_VERSION
        and not is_supported_uzi_contract_version(bundle.contract_version)
    ):
        fail("冻结 Bundle 契约不受当前运行时支持")
    items = repository.list_bundle_items(bundle.id)
    if (
        not items
        or len(items) != bundle.expected_dimension_count
        or len(items) != len(registry)
    ):
        fail("冻结 Bundle 槽位引用不完整")
    if any(
        item.dimension != contract.dimension or item.ordinal != contract.ordinal
        for item, contract in zip(items, registry, strict=True)
    ):
        fail("冻结 Bundle 槽位顺序不符合契约")
    calculated = bundle_manifest_hash(
        symbol=bundle.symbol,
        market=bundle.market,
        as_of_date=bundle.as_of_date,
        cutoff_at=_database_utc(bundle.cutoff_at),
        contract_version=bundle.contract_version,
        items=[
            {
                "dimension": item.dimension,
                "ordinal": item.ordinal,
                "snapshot_id": item.snapshot_id,
                "content_hash": item.snapshot_content_hash,
                "status": item.snapshot_status,
                "evidence_grade": item.evidence_grade,
            }
            for item in items
        ],
    )
    if calculated != bundle.manifest_hash:
        fail("冻结 Bundle 引用未通过内容哈希校验")
    ids = [item.snapshot_id for item in items]
    snapshots = {
        row.id: row
        for row in repository.session.scalars(
            select(MarketDataSnapshot)
            .where(MarketDataSnapshot.id.in_(ids))
            .options(
                load_only(
                    MarketDataSnapshot.id,
                    MarketDataSnapshot.symbol,
                    MarketDataSnapshot.market,
                    MarketDataSnapshot.exchange_timezone,
                    MarketDataSnapshot.dimension,
                    MarketDataSnapshot.contract_version,
                    MarketDataSnapshot.as_of_date,
                    MarketDataSnapshot.content_hash,
                    MarketDataSnapshot.status,
                    MarketDataSnapshot.evidence_grade,
                    MarketDataSnapshot.published_at,
                    MarketDataSnapshot.observed_at,
                    MarketDataSnapshot.revoked_at,
                )
            )
        )
    }
    # Most raw bodies remain deferred. Commodity observations need their
    # actual dated facts checked even when a legacy envelope says available;
    # fetch these and small gap disclosures in one bounded query, not N+1.
    payload_ids = [
        row.id for row in snapshots.values()
        if row.status == "data_gap" or row.dimension in {"8_materials", "9_futures"}
    ]
    safety_payloads = {
        row.id: row for row in repository.session.execute(
            select(MarketDataSnapshot.id, MarketDataSnapshot.payload_json, MarketDataSnapshot.query_manifest_json)
            .where(MarketDataSnapshot.id.in_(payload_ids))
        )
    } if payload_ids else {}
    evidence_rows = (
        repository.session.execute(
            select(
                MarketDataSnapshotEvidence.snapshot_id,
                MarketDataSnapshotEvidence.id.label("relation_id"),
                MarketDataSnapshotEvidence.field_path,
                MarketDataSnapshotEvidence.purpose,
                MarketDataSnapshotEvidence.created_at.label("relation_created_at"),
                MarketDataEvidence.id.label("evidence_id"),
                MarketDataEvidence.content_hash,
                MarketDataEvidence.published_at,
                MarketDataEvidence.observed_at,
                MarketDataEvidence.grade,
                MarketDataEvidence.immutable_proof,
                MarketDataEvidence.created_at.label("evidence_created_at"),
            )
            .join(
                MarketDataEvidence,
                MarketDataEvidence.id == MarketDataSnapshotEvidence.evidence_id,
            )
            .where(MarketDataSnapshotEvidence.snapshot_id.in_(ids))
        )
        .mappings()
        .all()
    )
    if evidence_bindings is None and known_at is not None:
        evidence_rows = [
            row
            for row in evidence_rows
            if _database_utc(row["relation_created_at"]) <= _database_utc(known_at)
            and _database_utc(row["evidence_created_at"]) <= _database_utc(known_at)
        ]
    if evidence_bindings is not None:
        by_relation = {row["relation_id"]: row for row in evidence_rows}
        selected = []
        for binding in evidence_bindings:
            row = by_relation.get(binding.get("relation_id"))
            if row is None or any(
                row[key] != binding.get(key)
                for key in (
                    "snapshot_id",
                    "evidence_id",
                    "content_hash",
                    "field_path",
                    "purpose",
                )
            ):
                fail("冻结证据引用或内容哈希已漂移")
            selected.append(row)
        evidence_rows = selected
    series_rows = (
        repository.session.execute(
            select(
                MarketDataSnapshotSeriesPoint.snapshot_id,
                MarketDataSeriesPoint.published_at,
                MarketDataSeriesPoint.observed_at,
                MarketDataSeriesPoint.revoked_at,
            )
            .join(
                MarketDataSeriesPoint,
                MarketDataSeriesPoint.id
                == MarketDataSnapshotSeriesPoint.series_point_id,
            )
            .where(MarketDataSnapshotSeriesPoint.snapshot_id.in_(ids))
        )
        .mappings()
        .all()
    )
    for item in items:
        row = snapshots.get(item.snapshot_id)
        if row is None or row.revoked_at is not None:
            fail("冻结 Snapshot 不存在或已撤销")
        if (
            row.symbol != bundle.symbol
            or row.market != bundle.market
            or row.exchange_timezone != bundle.exchange_timezone
            or row.as_of_date != bundle.as_of_date
            or row.contract_version != bundle.contract_version
            or row.dimension != item.dimension
            or row.content_hash != item.snapshot_content_hash
            or row.status != item.snapshot_status
            or row.evidence_grade != item.evidence_grade
        ):
            fail("冻结 Snapshot 作用域或内容哈希已漂移")
        safety = safety_payloads.get(row.id)
        snapshot = SimpleNamespace(
            id=row.id,
            dimension=row.dimension,
            as_of_date=row.as_of_date,
            exchange_timezone=row.exchange_timezone,
            status=row.status,
            evidence_grade=row.evidence_grade,
            published_at=row.published_at,
            observed_at=row.observed_at,
            payload_json=safety.payload_json if safety is not None else {},
            query_manifest_json=safety.query_manifest_json if safety is not None else {},
        )
        evidence = [
            SimpleNamespace(**proof)
            for proof in evidence_rows
            if proof["snapshot_id"] == row.id
        ]
        series = [
            SimpleNamespace(**point)
            for point in series_rows
            if point["snapshot_id"] == row.id
        ]
        if any(point.revoked_at is not None for point in series):
            fail("冻结 Snapshot 关联行情序列已撤销")
        if _snapshot_pit_visibility_errors(
            repository,
            snapshot,
            _database_utc(bundle.cutoff_at),
            evidence=evidence,
            series_points=series,
        ):
            fail("冻结 Snapshot 或证据越过 PIT 截止日")
        if not is_supported_uzi_contract_version(
            bundle.contract_version
        ) and repository.list_open_conflicts(
            symbol=bundle.symbol,
            market=bundle.market,
            dimension=item.dimension,
            as_of_date=bundle.as_of_date,
            snapshot_id=row.id,
        ):
            fail("冻结 Snapshot 出现未解决的数据冲突")
