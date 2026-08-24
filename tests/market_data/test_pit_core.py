from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from research_system.market_data import (
    DIMENSION_IDS,
    EXPECTED_DIMENSION_COUNT,
    BundleIntegrityError,
    BundleMaterializer,
    BundlePublisher,
    EvidenceGrade,
    EvidenceInput,
    IncompleteBundleError,
    MarketDataConflict,
    MarketDataEvidence,
    MarketDataObservation,
    MarketDataRepository,
    MarketDataSnapshot,
    SeriesPointInput,
    SnapshotIngestInput,
    SnapshotIngestionService,
    SnapshotResolver,
    SnapshotStatus,
    cutoff_at_for_date,
    registry,
)
from research_system.market_data.bundles import bundle_manifest_hash
from research_system.market_data.ingestion import snapshot_content_hash
from research_system.market_data.models import (
    MarketDataBundle,
    MarketDataBundleItem,
    ReportDataManifest,
    ReportDataManifestItem,
)
from research_system.market_data.resolver import _database_utc
from research_system.strategies.models import Base

AS_OF = date(2026, 8, 20)
NOW = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
PUBLISHED = datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 8, 20, 2, 55, tzinfo=timezone.utc)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    engine.dispose()


def _put_path(payload: dict, path: str, value) -> None:
    current = payload
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _get_path(payload: dict, path: str):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _payload(dimension: str) -> dict:
    contract = registry.get(dimension)
    payload: dict = {}
    for path in contract.required_fields:
        _put_path(
            payload, path, [{"value": 1}] if path.split(".")[-1].endswith("s") else 1
        )
    if dimension == "0_basic":
        payload.update(
            symbol="600519.SH",
            market="CN_EQUITY",
            industry="白酒",
            currency="CNY",
            price=100.0,
            market_cap=1_000_000.0,
            shares_outstanding=10_000.0,
            observed_at="2026-08-20T02:55:00Z",
        )
    elif dimension == "1_financials":
        payload["periods"] = [
            {"period": str(year), "revenue": year, "net_profit": year / 10}
            for year in range(2020, 2026)
        ]
    elif dimension == "2_kline":
        payload.update(
            ohlcv=[
                {
                    "date": (AS_OF - timedelta(days=offset)).isoformat(),
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "volume": 10_000,
                }
                for offset in range(259, -1, -1)
            ],
            corporate_actions=[{"date": "2026-06-30", "type": "cash_dividend"}],
            adjustment_factors=[
                {
                    "date": (AS_OF - timedelta(days=offset)).isoformat(),
                    "factor": 1.0,
                }
                for offset in range(259, -1, -1)
            ],
        )
    elif dimension == "4_peers":
        payload["target"] = {"symbol": "600519.SH"}
        payload["peers"] = [
            {"symbol": f"peer-{index}", "currency": "CNY", "period": "2025"}
            for index in range(3)
        ]
        payload["classification_version"] = "fixture-industry-v1"
    elif dimension == "5_chain":
        payload.update(
            segments=[{"name": "fixture segment"}],
            upstream={"items": ["grain"]},
            downstream={"items": ["distribution"]},
            concentration={"customer": 0.1, "supplier": 0.2},
        )
    elif dimension == "6_research":
        payload["reports"] = [{"date": "2026-08-01", "source": "fixture"}]
        payload["query_status"] = {"status": "success"}
    elif dimension == "7_industry":
        payload.update(classification="fixture", cycle="growth")
    elif dimension == "8_materials":
        payload["exposure"] = {"core_material": "fixture"}
        payload["prices"] = [
            {"index": index, "price": 100 + index} for index in range(250)
        ]
    elif dimension == "9_futures":
        payload["contract_mapping"] = {"contract": "fixture"}
        payload["prices"] = [
            {"index": index, "price": 100 + index} for index in range(60)
        ]
    elif dimension == "10_valuation":
        payload.update(
            pe=20.0,
            pb=5.0,
            history=[
                {"date": f"{year}-08-20", "pe": 20 + year - 2021}
                for year in range(2021, 2027)
            ],
            percentiles={"pe": 0.5, "pb": 0.5},
            industry_pe=18.0,
            denominators={"earnings": 50.0, "book_value": 200.0},
            shares_outstanding=10_000.0,
        )
    elif dimension == "11_governance":
        payload.update(
            controller={"name": "fixture controller"},
            pledges=[{"date": "2026-01-01", "ratio": 0.0}],
            ownership_changes=[{"date": "2026-02-01", "change": 1}],
            penalties=[{"date": "2026-03-01", "status": "none"}],
        )
    elif dimension == "12_capital_flow":
        payload.update(
            money_flow=[{"day": index, "value": index} for index in range(20)],
            northbound=[{"day": index, "value": index} for index in range(20)],
            margin=[{"day": index, "value": index} for index in range(20)],
            shareholder_counts=[{"period": index, "count": 1000} for index in range(8)],
            institutions=[{"quarter": index, "count": 10} for index in range(8)],
            events=[{"date": "2026-08-01", "type": "fixture"}],
        )
    elif dimension == "15_events":
        payload["events"] = [{"date": "2026-08-01", "type": "notice"}]
        payload["query_status"] = {"status": "success"}
        payload["truncation"] = {"truncated": False}
    elif dimension == "16_lhb":
        payload["records"] = [{"date": "2026-08-01", "seat": "fixture"}]
        payload["query_status"] = {"status": "success"}
    elif dimension == "17_sentiment":
        payload["items"] = [{"date": "2026-08-01", "source": "fixture"}]
        payload["query_status"] = {"status": "success"}
    elif dimension == "18_trap":
        payload["signals"] = [
            {"id": index, "status": "not_hit", "evidence": "fixture"}
            for index in range(1, 9)
        ]
    elif dimension == "19_contests":
        payload["results"] = [{"date": "2026-08-01", "source": "fixture"}]
        payload["authorization_status"] = "authorized"
        payload["query_status"] = {"status": "success"}
    elif dimension == "14_moat":
        payload["factors"] = {"scale": 1, "network": 1}
    elif dimension == "6_fund_holders":
        holders = [{"rank": index, "fund": f"fund-{index}"} for index in range(1, 101)]
        payload.update(
            reporting_period="2026Q2",
            funds=holders,
            top_holders=holders,
            nav_history=[
                {"date": f"{year}-08-20", "return": year - 2021}
                for year in range(2021, 2027)
            ],
        )
    elif dimension == "similar_stocks":
        payload["classification_version"] = "fixture-industry-v1"
        payload["stocks"] = [
            {"symbol": f"peer-{index}", "price": 10, "pe": 20, "market_cap": 100}
            for index in range(4)
        ]
    payload["fixture_dimension"] = dimension
    return payload


def _query_proof(path: str, count: int, *, window_days: int = 365) -> dict:
    source = f"fixture-{path}"
    return {
        "query_window": {
            "start": (AS_OF - timedelta(days=window_days)).isoformat(),
            "end": AS_OF.isoformat(),
        },
        "required_sources": [source],
        "sources": [{"source": source, "status": "success", "result_count": count}],
        "result_count": count,
    }


def _query_manifest(dimension: str, payload: dict) -> dict:
    contract = registry.get(dimension)
    manifest: dict = {}
    if dimension == "2_kline":
        manifest["coverage"] = {"ohlcv_adjustment": "unadjusted"}
    field_queries: dict[str, dict] = {}
    for requirement in contract.field_query_requirements:
        value = _get_path(payload, requirement.path)
        if isinstance(value, (list, tuple, set, dict)):
            result_count = len(value)
        else:
            result_count = 1 if value is not None else 0
        field_queries[requirement.path] = _query_proof(
            requirement.path,
            result_count,
            window_days=max(1, requirement.minimum_window_days),
        )
    if field_queries:
        manifest["field_queries"] = field_queries
    return manifest


def _evidence(
    dimension: str,
    *,
    count: int | None = None,
    grade: EvidenceGrade = EvidenceGrade.A,
    published_at: datetime = PUBLISHED,
    observed_at: datetime = OBSERVED,
    manual: bool = False,
) -> list[EvidenceInput]:
    required = (
        count if count is not None else registry.get(dimension).min_evidence_count
    )
    return [
        EvidenceInput(
            provider=f"fixture-{dimension}-{index}",
            source_url=f"https://example.test/{dimension}/{index}",
            inline_content=f"immutable evidence {dimension} {index}",
            published_at=published_at,
            observed_at=observed_at,
            grade=grade,
            immutable_proof=grade == EvidenceGrade.B,
            is_manual=manual,
        )
        for index in range(required)
    ]


def _input(
    dimension: str,
    *,
    payload: dict | None = None,
    status: SnapshotStatus = SnapshotStatus.AVAILABLE,
    evidence: list[EvidenceInput] | None = None,
    published_at: datetime = PUBLISHED,
    observed_at: datetime = OBSERVED,
    ingested_at: datetime = NOW,
    query_manifest: dict | None = None,
    origin: str = "provider",
    series_points: list[SeriesPointInput] | None = None,
) -> SnapshotIngestInput:
    resolved_payload = _payload(dimension) if payload is None else payload
    return SnapshotIngestInput(
        symbol="600519.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        dimension=dimension,
        as_of_date=AS_OF,
        status=status,
        payload=resolved_payload,
        effective_at=published_at,
        published_at=published_at,
        observed_at=observed_at,
        ingested_at=ingested_at,
        source_version="fixture-v1",
        source_priority=10,
        origin=origin,
        query_manifest=(
            _query_manifest(dimension, resolved_payload)
            if query_manifest is None
            else query_manifest
        ),
        field_sources={"$": "fixture evidence"},
        evidence=evidence
        if evidence is not None
        else _evidence(dimension, published_at=published_at, observed_at=observed_at),
        series_points=series_points or [],
    )


def _ingest_complete_set(
    session: Session,
) -> tuple[MarketDataRepository, SnapshotIngestionService]:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    for dimension in DIMENSION_IDS:
        ingestion.ingest(_input(dimension))
    return repository, ingestion


def seed_strict_bundle(database, symbol: str = "600519.SH") -> date:
    """Seed one production-valid 22-slot Bundle for API integration tests."""

    normalized = str(symbol or "").strip().upper()
    if "." not in normalized and normalized.isdigit() and len(normalized) == 6:
        suffix = "BJ" if normalized.startswith(("4", "8")) else (
            "SH" if normalized.startswith(("5", "6", "9")) else "SZ"
        )
        normalized = f"{normalized}.{suffix}"
    with database.session() as session:
        repository = MarketDataRepository(session)
        ingestion = SnapshotIngestionService(repository)
        for dimension in DIMENSION_IDS:
            value = _input(dimension)
            payload = dict(value.payload)
            if dimension == "0_basic":
                payload["symbol"] = normalized
            ingestion.ingest(
                value.model_copy(update={"symbol": normalized, "payload": payload})
            )
        BundlePublisher(repository).publish(
            symbol=normalized,
            market="CN_EQUITY",
            as_of_date=AS_OF,
            cutoff_at=NOW,
            published_by="api-test-fixture",
            now=NOW,
        )
    return AS_OF


def test_backend_registry_owns_exact_uzi_22_slot_order() -> None:
    assert EXPECTED_DIMENSION_COUNT == 22
    assert len(registry.contract_payload()["dimensions"]) == 22
    assert DIMENSION_IDS[:4] == ("0_basic", "1_financials", "2_kline", "3_macro")
    assert DIMENSION_IDS[-2:] == ("6_fund_holders", "similar_stocks")
    assert "20_valuation_models" not in DIMENSION_IDS
    assert [
        item["ordinal"] for item in registry.contract_payload()["dimensions"]
    ] == list(range(22))
    contracts = {
        item["dimension"]: item for item in registry.contract_payload()["dimensions"]
    }
    assert contracts["2_kline"]["item_count_requirements"][0]["minimum"] == 260
    assert contracts["18_trap"]["item_status_requirements"][0]["allowed_values"] == [
        "hit",
        "not_hit",
    ]
    for contract in registry:
        assert {rule.path for rule in contract.field_value_requirements} == set(
            contract.required_fields
        )


@pytest.mark.parametrize(
    "dimension",
    [
        "3_macro",
        "6_research",
        "11_governance",
        "13_policy",
        "15_events",
        "16_lhb",
        "17_sentiment",
        "19_contests",
    ],
)
def test_nonempty_bounded_collectors_still_require_complete_source_query_proof(
    session: Session, dimension: str
) -> None:
    row = SnapshotIngestionService(MarketDataRepository(session)).ingest(
        _input(dimension, query_manifest={})
    )

    assert row.status == SnapshotStatus.DATA_GAP.value
    assert any("query proof object" in error for error in row.validation_json["errors"])


@pytest.mark.parametrize(
    ("dimension", "mutate_path", "invalid_value", "message"),
    [
        ("0_basic", "symbol", True, "field symbol must be"),
        ("0_basic", "price", True, "field price must be"),
        ("0_basic", "observed_at", "2026-08-20", "field observed_at must be"),
        ("1_financials", "periods.0.period", True, "periods[0].period"),
        ("2_kline", "ohlcv.0.close", "101", "ohlcv[0].close"),
        ("4_peers", "peers.0.currency", True, "peers[0].currency"),
        ("similar_stocks", "stocks.0.price", True, "stocks[0].price"),
    ],
)
def test_field_type_and_critical_row_rules_reject_truthy_placeholders(
    session: Session,
    dimension: str,
    mutate_path: str,
    invalid_value,
    message: str,
) -> None:
    payload = _payload(dimension)
    target = payload
    tokens = mutate_path.split(".")
    for token in tokens[:-1]:
        target = target[int(token)] if isinstance(target, list) else target[token]
    if isinstance(target, list):
        target[int(tokens[-1])] = invalid_value
    else:
        target[tokens[-1]] = invalid_value
    row = SnapshotIngestionService(MarketDataRepository(session)).ingest(
        _input(dimension, payload=payload)
    )

    assert row.status == SnapshotStatus.DATA_GAP.value
    assert any(message in error for error in row.validation_json["errors"])


@pytest.mark.parametrize(
    ("dimension", "path", "replacement", "message"),
    [
        (
            "1_financials",
            "periods",
            [{"period": str(year)} for year in range(2021, 2026)],
            "at least 6",
        ),
        ("2_kline", "ohlcv", [{"date": "2026-08-20"}] * 259, "at least 260"),
        ("4_peers", "peers", [{"symbol": "a"}, {"symbol": "b"}], "at least 3"),
        ("8_materials", "prices", list(range(249)), "at least 250"),
        ("9_futures", "prices", list(range(59)), "at least 60"),
        ("12_capital_flow", "money_flow", list(range(19)), "at least 20"),
        ("12_capital_flow", "shareholder_counts", list(range(7)), "at least 8"),
        ("12_capital_flow", "institutions", list(range(7)), "at least 8"),
        (
            "18_trap",
            "signals",
            [{"id": index, "status": "not_hit"} for index in range(1, 8)],
            "exactly 8",
        ),
        ("6_fund_holders", "top_holders", list(range(99)), "at least 100"),
        ("similar_stocks", "stocks", list(range(3)), "exactly 4"),
    ],
)
def test_strict_cardinality_rules_reject_placeholder_collections(
    session: Session,
    dimension: str,
    path: str,
    replacement,
    message: str,
) -> None:
    payload = _payload(dimension)
    _put_path(payload, path, replacement)
    row = SnapshotIngestionService(MarketDataRepository(session)).ingest(
        _input(dimension, payload=payload)
    )

    assert row.status == SnapshotStatus.DATA_GAP.value
    assert any(message in error for error in row.validation_json["errors"])


def test_financials_accept_short_listing_history_only_with_explicit_complete_claim(
    session: Session,
) -> None:
    payload = _payload("1_financials")
    payload["periods"] = payload["periods"][:3]
    valid_claim = {
        "coverage": {
            "financials_all_public_periods": {
                "complete": True,
                "result_count": 3,
                "basis": "issuer listing history",
            }
        }
    }
    service = SnapshotIngestionService(MarketDataRepository(session))

    accepted = service.ingest(
        _input("1_financials", payload=payload, query_manifest=valid_claim)
    )
    assert accepted.status == SnapshotStatus.AVAILABLE.value

    rejected = service.ingest(
        _input(
            "1_financials",
            payload=payload,
            query_manifest={
                "coverage": {
                    "financials_all_public_periods": {
                        "complete": True,
                        "result_count": 3,
                    }
                }
            },
        )
    )
    assert rejected.status == SnapshotStatus.DATA_GAP.value


def test_kline_requires_unadjusted_claim_and_independent_query_proofs(
    session: Session,
) -> None:
    payload = _payload("2_kline")
    payload["corporate_actions"] = []
    payload["adjustment_factors"] = []
    proven_empty = {
        "coverage": {"ohlcv_adjustment": "unadjusted"},
        "field_queries": {
            "corporate_actions": _query_proof("corporate-actions", 0),
            "adjustment_factors": _query_proof("adjustment-factors", 0),
        },
    }
    service = SnapshotIngestionService(MarketDataRepository(session))

    accepted = service.ingest(
        _input("2_kline", payload=payload, query_manifest=proven_empty)
    )
    assert accepted.status == SnapshotStatus.AVAILABLE.value

    missing_proof = service.ingest(
        _input(
            "2_kline",
            payload=payload,
            query_manifest={"coverage": {"ohlcv_adjustment": "qfq"}},
        )
    )
    assert missing_proof.status == SnapshotStatus.DATA_GAP.value
    assert any(
        "query proof" in error or "ohlcv_adjustment" in error
        for error in missing_proof.validation_json["errors"]
    )


def test_capital_flow_allows_zero_subfield_only_with_bounded_success_proof(
    session: Session,
) -> None:
    payload = _payload("12_capital_flow")
    payload["northbound"] = []
    payload["events"] = []
    manifest = {
        "field_queries": {
            "northbound": _query_proof("northbound", 0, window_days=20),
            "events": _query_proof("events", 0, window_days=365),
        }
    }
    service = SnapshotIngestionService(MarketDataRepository(session))

    accepted = service.ingest(
        _input("12_capital_flow", payload=payload, query_manifest=manifest)
    )
    assert accepted.status == SnapshotStatus.AVAILABLE.value

    manifest["field_queries"]["northbound"]["sources"][0]["status"] = "timeout"
    rejected = service.ingest(
        _input("12_capital_flow", payload=payload, query_manifest=manifest)
    )
    assert rejected.status == SnapshotStatus.DATA_GAP.value
    assert any(
        "did not succeed" in error for error in rejected.validation_json["errors"]
    )


def test_trap_requires_exactly_eight_resolved_unique_signals(session: Session) -> None:
    payload = _payload("18_trap")
    payload["signals"][3]["status"] = "insufficient"
    payload["signals"][7]["id"] = 1

    row = SnapshotIngestionService(MarketDataRepository(session)).ingest(
        _input("18_trap", payload=payload)
    )

    assert row.status == SnapshotStatus.DATA_GAP.value
    errors = " ".join(row.validation_json["errors"])
    assert "hit, not_hit" in errors
    assert "duplicate signal IDs" in errors


def test_fund_holder_nav_history_must_cover_five_years(session: Session) -> None:
    payload = _payload("6_fund_holders")
    payload["nav_history"] = [
        {"date": f"{year}-08-20", "return": year - 2023} for year in range(2023, 2027)
    ]

    row = SnapshotIngestionService(MarketDataRepository(session)).ingest(
        _input("6_fund_holders", payload=payload)
    )

    assert row.status == SnapshotStatus.DATA_GAP.value
    assert any("1825 days" in error for error in row.validation_json["errors"])


def test_ingestion_deduplicates_immutable_content_but_appends_observations(
    session: Session,
) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    first = ingestion.ingest(_input("0_basic"))
    second = ingestion.ingest(_input("0_basic"))

    assert second.id == first.id
    assert len(repository.list_observations(first.id)) == 2
    assert session.scalar(select(func.count()).select_from(MarketDataObservation)) == 2

    changed_payload = _payload("0_basic")
    changed_payload["price"] = 1688.0
    changed = ingestion.ingest(_input("0_basic", payload=changed_payload))
    assert changed.id != first.id
    assert changed.content_hash != first.content_hash


def test_identical_payload_on_another_logical_day_creates_another_pit_snapshot(
    session: Session,
) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    base = _input("0_basic")
    first = ingestion.ingest(base)
    next_day = ingestion.ingest(
        base.model_copy(update={"as_of_date": AS_OF + timedelta(days=1)})
    )
    assert next_day.id != first.id
    assert next_day.content_hash != first.content_hash
    assert next_day.as_of_date == AS_OF + timedelta(days=1)


def test_resolver_never_reuses_a_snapshot_across_logical_days(session: Session) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    first = ingestion.ingest(_input("0_basic"))

    missing_next_day = SnapshotResolver(repository).coverage(
        "600519.SH",
        "CN_EQUITY",
        AS_OF + timedelta(days=1),
        now=NOW + timedelta(days=1),
    )
    basic = next(
        item for item in missing_next_day.dimensions if item.dimension == "0_basic"
    )
    assert basic.snapshot_id is None
    assert basic.reason == "no A/B snapshot was published by the cutoff"

    next_day = ingestion.ingest(
        _input("0_basic").model_copy(
            update={
                "as_of_date": AS_OF + timedelta(days=1),
                "effective_at": PUBLISHED + timedelta(days=1),
                "published_at": PUBLISHED + timedelta(days=1),
                "observed_at": OBSERVED + timedelta(days=1),
                "ingested_at": NOW + timedelta(days=1),
                "evidence": _evidence(
                    "0_basic",
                    published_at=PUBLISHED + timedelta(days=1),
                    observed_at=OBSERVED + timedelta(days=1),
                ),
            }
        )
    )
    resolved_first = SnapshotResolver(repository).resolve_snapshot(
        symbol="600519.SH",
        market="CN_EQUITY",
        dimension="0_basic",
        as_of_date=AS_OF,
        now=NOW,
    )
    resolved_next = SnapshotResolver(repository).resolve_snapshot(
        symbol="600519.SH",
        market="CN_EQUITY",
        dimension="0_basic",
        as_of_date=AS_OF + timedelta(days=1),
        now=NOW + timedelta(days=1),
    )
    assert resolved_first is not None and resolved_first.id == first.id
    assert resolved_next is not None and resolved_next.id == next_day.id


def test_future_or_grade_c_inputs_are_persisted_as_data_gaps(session: Session) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    future = datetime(2026, 8, 20, 16, 1, tzinfo=timezone.utc)
    future_row = ingestion.ingest(
        _input(
            "0_basic",
            published_at=future,
            observed_at=future,
            ingested_at=future + timedelta(minutes=1),
        )
    )
    assert future_row.status == SnapshotStatus.DATA_GAP.value
    assert any("cutoff" in error for error in future_row.validation_json["errors"])

    c_row = ingestion.ingest(
        _input(
            "1_financials", evidence=_evidence("1_financials", grade=EvidenceGrade.C)
        )
    )
    assert c_row.status == SnapshotStatus.DATA_GAP.value
    assert c_row.evidence_grade == EvidenceGrade.C.value

    coverage = SnapshotResolver(repository).coverage(
        "600519.SH", "CN_EQUITY", AS_OF, now=NOW
    )
    basic = next(item for item in coverage.dimensions if item.dimension == "0_basic")
    assert basic.snapshot_id is None
    assert basic.reason and basic.reason.startswith("data_gap")


def test_grade_b_reconstruction_is_eligible_only_with_immutable_proof(
    session: Session,
) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    reconstructed_at = datetime(2026, 8, 22, 1, tzinfo=timezone.utc)
    unverified = ingestion.ingest(
        _input(
            "0_basic",
            observed_at=reconstructed_at,
            ingested_at=reconstructed_at + timedelta(minutes=1),
            evidence=_evidence(
                "0_basic",
                grade=EvidenceGrade.C,
                published_at=PUBLISHED,
                observed_at=reconstructed_at,
                manual=True,
            ),
            origin="manual",
        )
    )
    assert unverified.status == SnapshotStatus.DATA_GAP.value
    row = ingestion.ingest(
        _input(
            "0_basic",
            observed_at=reconstructed_at,
            ingested_at=reconstructed_at + timedelta(minutes=1),
            evidence=_evidence(
                "0_basic",
                grade=EvidenceGrade.B,
                published_at=PUBLISHED,
                observed_at=reconstructed_at,
                manual=True,
            ),
            origin="manual",
        )
    )
    assert row.status == SnapshotStatus.AVAILABLE.value
    assert row.evidence_grade == EvidenceGrade.B.value
    assert session.scalar(select(func.count()).select_from(MarketDataEvidence)) == 2
    basic = (
        SnapshotResolver(repository)
        .coverage(
            "600519.SH",
            "CN_EQUITY",
            AS_OF,
            now=datetime(2026, 8, 25, tzinfo=timezone.utc),
        )
        .dimensions[0]
    )
    assert basic.eligible is True

    with pytest.raises(ValueError, match="immutable_proof"):
        EvidenceInput(
            provider="manual",
            source_url="https://example.test/proof",
            published_at=PUBLISHED,
            observed_at=reconstructed_at,
            grade=EvidenceGrade.B,
            immutable_proof=False,
            is_manual=True,
        )


def test_verified_empty_requires_bounded_success_not_timeout(session: Session) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    query_manifest = {
        "query_window": {"start": "2026-07-21", "end": "2026-08-20"},
        "required_sources": ["official-lhb"],
        "sources": [{"source": "official-lhb", "status": "success", "result_count": 0}],
        "result_count": 0,
    }
    empty = ingestion.ingest(
        _input(
            "16_lhb",
            payload={},
            status=SnapshotStatus.VERIFIED_EMPTY,
            query_manifest=query_manifest,
        )
    )
    assert empty.status == SnapshotStatus.VERIFIED_EMPTY.value

    timed_out = {
        **query_manifest,
        "sources": [{"source": "official-lhb", "status": "timeout"}],
    }
    gap = ingestion.ingest(
        _input(
            "16_lhb",
            payload={},
            status=SnapshotStatus.VERIFIED_EMPTY,
            query_manifest=timed_out,
        )
    )
    assert gap.status == SnapshotStatus.DATA_GAP.value
    assert any("did not succeed" in error for error in gap.validation_json["errors"])

    too_short = {
        **query_manifest,
        "query_window": {"start": "2026-07-22", "end": "2026-08-20"},
    }
    short_window = ingestion.ingest(
        _input(
            "16_lhb",
            payload={},
            status=SnapshotStatus.VERIFIED_EMPTY,
            query_manifest=too_short,
        )
    )
    assert short_window.status == SnapshotStatus.DATA_GAP.value
    assert any(
        "at least 30" in error for error in short_window.validation_json["errors"]
    )


def test_bounded_zero_collector_must_use_verified_empty_status(
    session: Session,
) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    manifest = {
        "query_window": {"start": "2025-08-20", "end": "2026-08-20"},
        "required_sources": ["fixed-research-source"],
        "sources": [
            {
                "source": "fixed-research-source",
                "status": "success",
                "result_count": 0,
            }
        ],
        "result_count": 0,
    }
    available_payload = _payload("6_research")
    available_payload["reports"] = []
    disguised_empty = ingestion.ingest(
        _input(
            "6_research",
            payload=available_payload,
            status=SnapshotStatus.AVAILABLE,
            query_manifest=manifest,
        )
    )
    assert disguised_empty.status == SnapshotStatus.DATA_GAP.value
    assert any(
        "use verified_empty" in error
        for error in disguised_empty.validation_json["errors"]
    )

    explicit_empty = ingestion.ingest(
        _input(
            "6_research",
            payload={},
            status=SnapshotStatus.VERIFIED_EMPTY,
            query_manifest=manifest,
        )
    )
    assert explicit_empty.status == SnapshotStatus.VERIFIED_EMPTY.value


def test_coverage_publish_and_materialize_freeze_all_22_slots(session: Session) -> None:
    repository, _ = _ingest_complete_set(session)
    resolver = SnapshotResolver(repository)
    coverage = resolver.coverage("600519.SH", "CN_EQUITY", AS_OF, now=NOW)
    assert coverage.structurally_complete is True
    assert coverage.report_ready is True
    assert coverage.ready_dimension_count == 22
    assert coverage.available_count == 22

    stale = resolver.coverage(
        "600519.SH",
        "CN_EQUITY",
        AS_OF,
        now=datetime(2026, 8, 20, 15, 30, tzinfo=timezone.utc),
    )
    assert stale.structurally_complete is True
    assert stale.report_ready is False
    assert stale.stale_count > 0

    publisher = BundlePublisher(repository, resolver=resolver)
    bundle = publisher.publish(
        symbol="600519.SH",
        market="CN_EQUITY",
        as_of_date=AS_OF,
        published_by="tester",
        now=NOW,
    )
    duplicate = publisher.publish(
        symbol="600519.SH",
        market="CN_EQUITY",
        as_of_date=AS_OF,
        published_by="tester",
        now=NOW,
    )
    assert duplicate.id == bundle.id

    materialized = BundleMaterializer(repository).materialize(bundle.id)
    assert len(materialized.snapshots) == 22
    assert [row["dimension_id"] for row in materialized.snapshots] == list(
        DIMENSION_IDS
    )
    assert all(
        row["content_hash"] and row["evidence_grade"] == "A"
        for row in materialized.snapshots
    )
    assert all(
        row["availability_status"] == "available" for row in materialized.snapshots
    )
    assert materialized.manifest_hash == bundle.manifest_hash


def test_missing_conflict_and_revocation_each_block_formal_bundle(
    session: Session,
) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    first = ingestion.ingest(_input("0_basic"))
    publisher = BundlePublisher(repository)
    with pytest.raises(IncompleteBundleError) as missing:
        publisher.publish(
            symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW
        )
    assert "1_financials" in missing.value.missing_dimensions

    for dimension in DIMENSION_IDS[1:]:
        ingestion.ingest(_input(dimension))
    conflict = repository.add_conflict(
        MarketDataConflict(
            symbol="600519.SH",
            market="CN_EQUITY",
            dimension="0_basic",
            as_of_date=AS_OF,
            cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
            snapshot_id=first.id,
            field_path="price",
            candidate_values_json=[1688, 1699],
            candidate_evidence_json=[],
        )
    )
    with pytest.raises(IncompleteBundleError) as blocked:
        publisher.publish(
            symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW
        )
    assert blocked.value.blocked_dimensions == ["0_basic"]
    assert blocked.value.coverage.structurally_complete is False

    repository.resolve_conflict(
        conflict.id,
        resolution={"selected": 1688},
        resolved_by="tester",
        resolved_at=NOW,
    )
    bundle = publisher.publish(
        symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW
    )
    repository.revoke_snapshot(
        first.id, reason="fixture correction", revoked_by="tester", revoked_at=NOW
    )
    assert bundle.status == "invalidated"
    assert bundle.invalidation_reason and first.id in bundle.invalidation_reason
    with pytest.raises(BundleIntegrityError, match="invalidated|revoked"):
        BundleMaterializer(repository).materialize(bundle.id)


def test_direct_database_snapshot_cannot_bypass_contract_validation_on_publish(
    session: Session,
) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    for dimension in DIMENSION_IDS[1:]:
        ingestion.ingest(_input(dimension))
    payload = _payload("0_basic")
    payload["price"] = True
    content_hash = snapshot_content_hash(
        symbol="600519.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        dimension="0_basic",
        contract_version=registry.contract_version,
        as_of_date=AS_OF,
        status=SnapshotStatus.AVAILABLE,
        evidence_grade=EvidenceGrade.A,
        payload=payload,
        effective_at=PUBLISHED,
        published_at=PUBLISHED,
        source_version="direct-fixture-v1",
        source_priority=10,
        query_manifest={},
        field_sources={"$": "direct fixture"},
        series_hashes=[],
    )
    snapshot = repository.add_snapshot(
        MarketDataSnapshot(
            symbol="600519.SH",
            market="CN_EQUITY",
            exchange_timezone="Asia/Shanghai",
            dimension="0_basic",
            contract_version=registry.contract_version,
            as_of_date=AS_OF,
            status=SnapshotStatus.AVAILABLE.value,
            evidence_grade=EvidenceGrade.A.value,
            payload_json=payload,
            effective_at=PUBLISHED,
            published_at=PUBLISHED,
            observed_at=OBSERVED,
            ingested_at=NOW,
            source_version="direct-fixture-v1",
            source_priority=10,
            origin="provider",
            query_manifest_json={},
            validation_json={
                "passed": True,
                "final_status": SnapshotStatus.AVAILABLE.value,
                "contract_version": registry.contract_version,
                "contract_dimension": "0_basic",
            },
            field_sources_json={"$": "direct fixture"},
            content_hash=content_hash,
        )
    )
    proof = repository.add_evidence(
        MarketDataEvidence(
            evidence_key="direct-invalid-basic-proof",
            provider="direct-fixture",
            source_url="https://example.test/direct-invalid-basic",
            source_kind="structured",
            content_hash="d" * 64,
            published_at=PUBLISHED,
            observed_at=OBSERVED,
            grade=EvidenceGrade.A.value,
            immutable_proof=True,
        )
    )
    repository.add_snapshot_evidence(snapshot_id=snapshot.id, evidence_id=proof.id)

    with pytest.raises(IncompleteBundleError) as blocked:
        BundlePublisher(repository).publish(
            symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW
        )
    basic = blocked.value.coverage.dimensions[0]
    assert basic.snapshot_id == snapshot.id
    assert basic.reason and "field price must be" in basic.reason


def test_materializer_revalidates_hash_correct_but_semantically_invalid_snapshot(
    session: Session,
) -> None:
    repository, _ = _ingest_complete_set(session)
    bundle = BundlePublisher(repository).publish(
        symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW
    )
    items = repository.list_bundle_items(bundle.id)
    basic_item = items[0]
    basic = repository.require_snapshot(basic_item.snapshot_id)
    bad_payload = dict(basic.payload_json)
    bad_payload["price"] = True
    bad_hash = snapshot_content_hash(
        symbol=basic.symbol,
        market=basic.market,
        exchange_timezone=basic.exchange_timezone,
        dimension=basic.dimension,
        contract_version=basic.contract_version,
        as_of_date=basic.as_of_date,
        status=basic.status,
        evidence_grade=basic.evidence_grade,
        payload=bad_payload,
        effective_at=_database_utc(basic.effective_at),
        published_at=_database_utc(basic.published_at),
        source_version=basic.source_version,
        source_priority=basic.source_priority,
        query_manifest=dict(basic.query_manifest_json),
        field_sources=dict(basic.field_sources_json),
        series_hashes=[],
    )
    session.execute(
        update(MarketDataSnapshot)
        .where(MarketDataSnapshot.id == basic.id)
        .values(payload_json=bad_payload, content_hash=bad_hash)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(MarketDataBundleItem)
        .where(MarketDataBundleItem.id == basic_item.id)
        .values(snapshot_content_hash=bad_hash)
        .execution_options(synchronize_session=False)
    )
    manifest_rows = [
        {
            "dimension": item.dimension,
            "ordinal": item.ordinal,
            "snapshot_id": item.snapshot_id,
            "content_hash": bad_hash
            if item.id == basic_item.id
            else item.snapshot_content_hash,
            "status": item.snapshot_status,
            "evidence_grade": item.evidence_grade,
        }
        for item in items
    ]
    new_manifest_hash = bundle_manifest_hash(
        symbol=bundle.symbol,
        market=bundle.market,
        as_of_date=bundle.as_of_date,
        cutoff_at=_database_utc(bundle.cutoff_at),
        contract_version=bundle.contract_version,
        items=manifest_rows,
    )
    session.execute(
        update(MarketDataBundle)
        .where(MarketDataBundle.id == bundle.id)
        .values(manifest_hash=new_manifest_hash)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    session.expire_all()

    with pytest.raises(BundleIntegrityError, match="failed contract validation"):
        BundleMaterializer(repository).materialize(bundle.id)


def test_series_points_are_content_addressed_and_reused(session: Session) -> None:
    repository = MarketDataRepository(session)
    ingestion = SnapshotIngestionService(repository)
    point = SeriesPointInput(
        series_key="unadjusted_ohlcv",
        point_at=datetime(2026, 8, 19, 7, tzinfo=timezone.utc),
        effective_at=datetime(2026, 8, 19, 7, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 19, 8, tzinfo=timezone.utc),
        observed_at=OBSERVED,
        value={"open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
        source_version="fixture-v1",
    )
    first = ingestion.ingest(_input("2_kline", series_points=[point]))
    second = ingestion.ingest(_input("2_kline", series_points=[point]))
    assert second.id == first.id
    assert len(repository.list_snapshot_series_points(first.id)) == 1


def test_bundle_core_fields_cannot_be_mutated_after_publication(
    session: Session,
) -> None:
    repository, _ = _ingest_complete_set(session)
    bundle = BundlePublisher(repository).publish(
        symbol="600519.SH",
        market="CN_EQUITY",
        as_of_date=AS_OF,
        published_by="tester",
        now=NOW,
    )
    bundle.manifest_hash = "0" * 64
    with pytest.raises(ValueError, match="immutable"):
        session.flush()


def test_report_manifest_and_items_are_append_only_except_explicit_invalidation(
    session: Session,
) -> None:
    manifest = ReportDataManifest(
        tenant_id="tenant",
        user_id="user",
        report_kind="single_stock",
        status="frozen",
        contract_version=registry.contract_version,
        evaluation_at=NOW,
        as_of_date=AS_OF,
        workflow_pins_json={"workflow_commit": "abc"},
        input_context_json={"research_profile": "lite"},
        manifest_hash="a" * 64,
        frozen_by="tester",
        frozen_at=NOW,
    )
    session.add(manifest)
    session.flush()
    item = ReportDataManifestItem(
        manifest_id=manifest.id,
        ordinal=0,
        symbol="600519.SH",
        market="CN_EQUITY",
        bundle_id="fixture-bundle",
        bundle_manifest_hash="b" * 64,
        context_json={},
    )
    session.add(item)
    session.commit()
    manifest_id = manifest.id
    item_id = item.id

    manifest = session.get(ReportDataManifest, manifest_id)
    assert manifest is not None
    manifest.manifest_hash = "c" * 64
    with pytest.raises(ValueError, match="report-data manifest is immutable"):
        session.flush()
    session.rollback()

    item = session.get(ReportDataManifestItem, item_id)
    assert item is not None
    session.delete(item)
    with pytest.raises(ValueError, match="append-only"):
        session.flush()
    session.rollback()

    manifest = session.get(ReportDataManifest, manifest_id)
    assert manifest is not None
    manifest.status = "invalidated"
    manifest.invalidated_at = NOW + timedelta(minutes=1)
    manifest.invalidation_reason = "upstream Bundle revoked"
    session.commit()

    manifest = session.get(ReportDataManifest, manifest_id)
    assert manifest is not None
    manifest.invalidation_reason = "silently rewritten"
    with pytest.raises(ValueError, match="only change during invalidation"):
        session.flush()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("symbol", "000858.SZ", "another symbol"),
        ("market", "HK_EQUITY", "another market"),
        ("as_of_date", AS_OF - timedelta(days=1), "another as-of date"),
        ("exchange_timezone", "UTC", "another exchange timezone"),
    ],
)
def test_materializer_rejects_snapshot_outside_bundle_pit_scope(
    session: Session, field: str, value, message: str
) -> None:
    repository, _ = _ingest_complete_set(session)
    bundle = BundlePublisher(repository).publish(
        symbol="600519.SH",
        market="CN_EQUITY",
        as_of_date=AS_OF,
        published_by="tester",
        now=NOW,
    )
    basic_id = repository.list_bundle_items(bundle.id)[0].snapshot_id
    session.execute(
        update(MarketDataSnapshot)
        .where(MarketDataSnapshot.id == basic_id)
        .values({field: value})
        .execution_options(synchronize_session=False)
    )
    session.flush()
    session.expire_all()

    with pytest.raises(BundleIntegrityError, match=message):
        BundleMaterializer(repository).materialize(bundle.id)


def test_materializer_recomputes_snapshot_hash_and_detects_database_tampering(
    session: Session,
) -> None:
    repository, _ = _ingest_complete_set(session)
    bundle = BundlePublisher(repository).publish(
        symbol="600519.SH",
        market="CN_EQUITY",
        as_of_date=AS_OF,
        published_by="tester",
        now=NOW,
    )
    basic_id = repository.list_bundle_items(bundle.id)[0].snapshot_id
    session.execute(
        update(MarketDataSnapshot)
        .where(MarketDataSnapshot.id == basic_id)
        .values(payload_json={"tampered": True})
    )
    session.flush()
    with pytest.raises(BundleIntegrityError, match="payload hash"):
        BundleMaterializer(repository).materialize(bundle.id)
