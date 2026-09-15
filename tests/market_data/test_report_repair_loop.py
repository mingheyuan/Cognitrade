from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.models import MarketDataEvidence
from research_system.market_data.report_inputs import DatabaseReportInputGateway, ReportInputError
from research_system.market_data.report_repairs import report_repair_plan
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, NOW, seed_strict_bundle


@pytest.fixture
def inputs():
    db = StrategyDatabase("sqlite://")
    db.create_schema()
    for symbol in ("600519.SH", "688012.SH"):
        seed_strict_bundle(db, symbol)
    gateway = DatabaseReportInputGateway(db, clock=lambda: NOW)
    original = gateway.freeze_report_manifest(
        symbols=["600519.SH", "688012.SH"], as_of_date=AS_OF, tenant_id="tenant",
        user_id="user", workflow_pins={"research_profile": "lite"}, context={},
    )
    yield db, gateway, original
    db.close()


def append_proofs(db):
    with db.session() as session:
        repo = MarketDataRepository(session)
        for symbol in ("600519.SH", "688012.SH"):
            snapshot = next(row for row in repo.list_snapshots(symbol=symbol, as_of_date=AS_OF)
                            if row.dimension == "0_basic")
            body = "测试补充原文 " + symbol
            proof = repo.add_evidence(MarketDataEvidence(
                evidence_key=canonical_hash(body), provider="fixture", source_url="https://fixture.invalid/" + symbol,
                source_kind="structured", content_hash=canonical_hash(body), inline_content=body,
                immutable_proof=True, published_at=snapshot.published_at, observed_at=snapshot.observed_at,
                grade="A",
            ))
            repo.add_snapshot_evidence(snapshot_id=snapshot.id, evidence_id=proof.id, field_path="$", purpose="supports")


def ref(original):
    return {key: original[key] for key in ("manifest_id", "manifest_hash")}


def test_revision_rebinds_only_selected_evidence_and_old_manifest_stays_exact(inputs):
    db, gateway, original = inputs
    before = deepcopy(original)
    append_proofs(db)
    new = gateway.revise_report_manifest(**ref(original), symbols=["600519"], tenant_id="tenant", user_id="user")
    assert new["manifest_hash"] != original["manifest_hash"]
    assert new["items"][0]["context"]["evidence_bindings"] != original["items"][0]["context"]["evidence_bindings"]
    assert new["items"][1]["context"] == original["items"][1]["context"]
    assert new["items"][1]["snapshots"] == original["items"][1]["snapshots"]
    assert new["items"][1]["bundle_id"] == original["items"][1]["bundle_id"]
    loaded = gateway.load_frozen_report_manifest(**ref(original), tenant_id="tenant", user_id="user")
    assert [row["snapshots"] for row in loaded["items"]] == [row["snapshots"] for row in before["items"]]
    reloaded = gateway.load_frozen_report_manifest(**ref(new), tenant_id="tenant", user_id="user")
    assert [row["context"] for row in reloaded["items"]] == [row["context"] for row in new["items"]]
    assert original == before


@pytest.mark.parametrize("symbols,user,code", [
    (["600519"], "user", "report_revision_inputs_unchanged"),
    (["000001"], "user", "report_revision_scope_invalid"),
    ([], "user", "report_revision_scope_invalid"),
    (["600519"], "another-user", "report_manifest_not_found"),
])
def test_revision_errors_are_explicit_and_do_not_rewrite_input(inputs, symbols, user, code):
    _, gateway, original = inputs
    with pytest.raises(ReportInputError) as exc:
        gateway.revise_report_manifest(**ref(original), symbols=symbols, tenant_id="tenant", user_id=user)
    assert exc.value.code == code


def test_revision_refuses_to_execute_changed_workflow_as_old_version(inputs):
    db, gateway, original = inputs
    append_proofs(db)
    gateway.runtime_settings = SimpleNamespace(default_model="different-model")
    with pytest.raises(ReportInputError) as exc:
        gateway.revise_report_manifest(**ref(original), symbols=["600519"], tenant_id="tenant", user_id="user")
    assert exc.value.code == "published_workflow_changed"


@pytest.mark.parametrize("dimension,historical,expected", [
    ("6_research", True, False), ("15_events", True, False),
    ("16_lhb", True, False), ("19_contests", True, True), ("19_contests", False, False),
])
def test_explicit_missing_collection_does_not_reuse_lost_empty_envelope(dimension, historical, expected):
    coordinator = object.__new__(MarketDataCollectionCoordinator)
    coordinator.price_evidence_collector = None
    snapshot = SimpleNamespace(status="verified_empty", payload_json={},
                               query_manifest_json={"collection_outcome": "verified_empty"})
    coverage = SimpleNamespace(eligible=True, degraded=False)
    assert coordinator._can_reuse_existing(coverage, snapshot, dimension=dimension, historical=historical,
                                           as_of_date=AS_OF, mode="missing_or_stale") == expected


def test_repair_plan_separates_history_gap_and_missing_source_bytes():
    content = {"results": [{"raw": {"dimensions": {"6_research": {"data": {},
        "_market_data_observation": {"reason_code": "native_empty_input_not_retained"}}}},
        "self_review": {"issues": [{"severity": "critical", "category": "data", "dim": dimension}
                                   for dimension in ("6_research", "19_contests")]}}]}
    original = deepcopy(content)
    plan = report_repair_plan(content, symbol="688012", as_of=date(2026, 9, 2), today=date(2026, 9, 14))
    assert plan["collection_request"]["symbols"] == ["688012.SH"]
    assert plan["collection_request"]["dimensions"] == ["6_research"]
    assert plan["collection_request"]["mode"] == "force_all"
    assert plan["collection_request"]["publish_when_complete"] is False
    assert plan["fund_holdings_recollection"] is False
    rows = {row["dimension"]: row for row in plan["dimensions"]}
    assert rows["6_research"]["reason"] == "native_empty_input_not_retained"
    assert rows["19_contests"]["retryable"] is False
    assert content == original


def test_new_retained_empty_envelope_is_reused_without_provider_call():
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter
    from tests.market_data.test_native_verified_empty_projection import _source

    coordinator = object.__new__(MarketDataCollectionCoordinator)
    coordinator.price_evidence_collector = None
    for data in ({}, {"lhb_count_30d": 0, "lhb_records": []}):
        value = UziSnapshotAdapter().adapt(_source(data), symbol="688012.SH", market="CN_EQUITY",
            exchange_timezone="Asia/Shanghai", as_of_date=date(2026, 9, 1))
        snapshot = SimpleNamespace(status=value.status.value, payload_json=value.payload,
                                   query_manifest_json=value.query_manifest)
        before = deepcopy(vars(snapshot))
        assert coordinator._can_reuse_existing(SimpleNamespace(eligible=True, degraded=False), snapshot,
            dimension="16_lhb", historical=True, as_of_date=date(2026, 9, 1), mode="missing_or_stale")
        assert vars(snapshot) == before


@pytest.mark.parametrize("dimension", ["6_research", "6_fund_holders"])
def test_legacy_false_zero_is_rechecked_once_after_adapter_fix(dimension):
    from research_system.market_data.historical import historical_provider_revision

    coordinator = object.__new__(MarketDataCollectionCoordinator)
    coordinator.price_evidence_collector = None
    payload = {"raw": {"reports": []}, "_uzi_raw_result": {"data": {"reports": []}}}
    snapshot = SimpleNamespace(status="verified_empty", payload_json=payload,
        query_manifest_json={"collection_outcome": "verified_empty"})
    arguments = dict(dimension=dimension, historical=True, as_of_date=AS_OF, mode="missing_or_stale")
    coverage = SimpleNamespace(eligible=True, degraded=False)
    assert not coordinator._can_reuse_existing(coverage, snapshot, **arguments)
    snapshot.query_manifest_json["historical_provider_revision"] = historical_provider_revision(dimension)
    assert coordinator._can_reuse_existing(coverage, snapshot, **arguments)


def test_fund_revision_keeps_parent_order_weights_and_other_nine_stocks(inputs):
    from research_system.market_data.bundles import BundlePublisher
    from research_system.market_data.fund_holdings_adapter import FundTopHoldingsSnapshotAdapter
    from research_system.market_data.fund_holdings_contracts import FUND_TOP_HOLDINGS_REGISTRY
    from research_system.market_data.ingestion import SnapshotIngestionService
    from tests.market_data.test_fund_holdings_foundation import STOCK_CODES, OBSERVED, direct_skill_output

    db, gateway, _ = inputs
    for symbol in STOCK_CODES:
        if symbol not in {"600519", "688012"}:
            seed_strict_bundle(db, symbol)
    with db.session() as session:
        repo = MarketDataRepository(session)
        value = FundTopHoldingsSnapshotAdapter().adapt(direct_skill_output(), fund_code="020464",
                                                     as_of_date=AS_OF, observed_at=OBSERVED)
        SnapshotIngestionService(repo, registry=FUND_TOP_HOLDINGS_REGISTRY).ingest(value)
        parent = BundlePublisher(repo, registry=FUND_TOP_HOLDINGS_REGISTRY).publish(
            symbol="020464", market="CN_FUND", as_of_date=AS_OF, cutoff_at=OBSERVED,
            now=OBSERVED, published_by="test",
        )
    original = gateway.freeze_fund_report_manifest(fund_code="020464", as_of_date=AS_OF,
        fund_bundle_id=parent.id, workflow_pins={"research_profile": "lite"}, context={},
        tenant_id="tenant", user_id="user")
    append_proofs(db)  # New evidence also exists for a non-selected stock.
    revised = gateway.revise_report_manifest(**ref(original), symbols=["688012"], tenant_id="tenant", user_id="user")
    assert revised["dependencies"] == original["dependencies"]
    assert [(r["symbol"], r["weight"]) for r in revised["items"]] == [(r["symbol"], r["weight"]) for r in original["items"]]
    for old, new in zip(original["items"], revised["items"], strict=True):
        if old["symbol"] != "688012.SH":
            assert old["context"] == new["context"]
            assert old["snapshots"] == new["snapshots"]
