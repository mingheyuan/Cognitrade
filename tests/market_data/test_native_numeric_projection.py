"""Regression for the real E2E's zero shares / yuan-as-亿 valuation inputs."""
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
    _initialize_native_imports, build_uzi_raw,
)
from domain_packs.finance_ashare.tools.snapshot_collector import _point_in_time_uzi_projection
from research_system.market_data.native_numbers import project_host_numbers
from research_system.market_data.native_projection import (
    PROJECTION_VERSION, PROJECTION_VERSIONS, project_native_result,
)
from research_system.market_data.uzi_gate import _native_result

HEALTH = {
    "cash": 9768212270.46, "total_debt": 7446753872.34,
    "equity": 30037754322.85, "total_assets": 37736913182.75,
    "total_liabilities": 7446753872.34,
    "debt_ratio": 19.73, "total_debt_basis": "total_liabilities_proxy",
    "report_date": "2026-06-30 00:00:00",
    "source": "akshare.stock_balance_sheet_by_report_em",
}
BASIC = {"price": 343.88, "market_cap_raw": 329382826630.24,
         "circulating_cap_raw": 324002717427.44, "total_shares": 957842348.0}


def snapshot(dimension, data):
    provider = "akshare_market" if dimension == "0_basic" else "akshare_fundamental"
    return SimpleNamespace(dimension=dimension, status="available", source_version="uzi-3.9.4-b3665e8",
        payload_json={"raw": deepcopy(data), "_uzi_raw_result": {
            "data": deepcopy(data), "source": provider, "fallback": False}},
        query_manifest_json={"pit_safe": True, "pit_mode": "historical_reconstruction",
                             "provider": provider}, validation_json={})


@pytest.mark.parametrize("version", sorted(PROJECTION_VERSIONS - {PROJECTION_VERSION}))
@pytest.mark.parametrize("dimension,data", [("0_basic", BASIC), ("1_financials", {"financial_health": HEALTH})])
def test_every_older_projection_keeps_exact_frozen_data(version, dimension, data):
    row = snapshot(dimension, data)
    before = deepcopy(vars(row))
    expected = project_host_numbers(data, dimension) if version == "uzi-dim-result-v9" else data
    assert project_native_result(row, version=version)["data"] == expected
    assert vars(row) == before


def test_actual_numeric_regression_feeds_unmodified_native_features_and_dcf():
    rows = [snapshot("0_basic", BASIC), snapshot("1_financials", {"financial_health": HEALTH})]
    before = deepcopy([vars(row) for row in rows])
    projected = [project_native_result(row) for row in rows]
    assert projected == [_native_result(row) for row in rows]
    basic, financial = [value["data"] for value in projected]
    assert basic["market_cap_yi"] == BASIC["market_cap_raw"] / 1e8
    assert basic["circulating_cap_yi"] == BASIC["circulating_cap_raw"] / 1e8
    assert basic["market_cap"] == "3293.8亿"
    assert basic["circulating_cap"] == "3240.0亿"
    health = financial["financial_health"]
    assert health["cash"] == 97.68
    assert health["total_debt"] == 74.47
    assert health["equity"] == 300.38
    for field in ("source", "report_date", "debt_ratio", "total_debt_basis"):
        assert health[field] == HEALTH[field]
    materialized = [{"symbol": "688012.SH", "snapshot_type": kind,
                     "facts": {**value["data"], "_uzi_raw_result": value}}
                    for kind, value in zip(("market", "fundamental"), projected)]
    raw = build_uzi_raw(materialized, "688012.SH")
    _initialize_native_imports()
    features = import_module("lib.stock_features").extract_features(raw, {})
    integrity = import_module("lib.data_integrity").validate(raw)
    assert not any(r["path"] == "market_cap" for r in integrity["missing_critical"])
    assert features["shares_outstanding_yi"] == 9.578
    assert features["cash_yi"] == 97.68
    assert features["total_debt_yi"] == 74.47
    # Deterministic model input, not a fabricated real-report valuation.
    features["fcf_latest_yi"] = 16.89
    dcf = import_module("lib.fin_models").compute_dcf(features)
    assert dcf["net_debt_yi"] == -23.21
    assert dcf["shares_yi"] == 9.578
    assert dcf["intrinsic_per_share"] > 0
    assert [vars(row) for row in rows] == before


def test_new_collection_uses_same_units_and_is_never_converted_twice():
    facts = {"financial_health": deepcopy(HEALTH), "financial_abstract_history": []}
    before = deepcopy(facts)
    data = _point_in_time_uzi_projection("fundamental", facts)
    assert data["financial_health"]["cash"] == 97.68
    assert project_native_result(snapshot("1_financials", data))["data"] == data
    assert facts == before
    market = _point_in_time_uzi_projection("market", {"market_overview": {
        "market_cap": BASIC["market_cap_raw"], "latest_price": BASIC["price"]}})
    assert market["market_cap_yi"] == BASIC["market_cap_raw"] / 1e8
    assert project_native_result(snapshot("0_basic", market))["data"] == market


@pytest.mark.parametrize("mutation", ["no_provider", "native_source", "not_historical", "pit_rejected"])
def test_no_unit_guessing_for_native_unknown_or_unsafe_snapshot(mutation):
    row = snapshot("1_financials", {"financial_health": HEALTH})
    if mutation == "no_provider":
        row.query_manifest_json.pop("provider")
    elif mutation == "native_source":
        row.payload_json["_uzi_raw_result"]["source"] = "akshare:financial_abstract"
    elif mutation == "not_historical":
        row.query_manifest_json["pit_mode"] = "live_observation"
    else:
        row.query_manifest_json["pit_safe"] = False
    expected = {} if mutation == "pit_rejected" else {"financial_health": HEALTH}
    assert project_native_result(row)["data"] == expected


@pytest.mark.parametrize("unit", ["yi", "unknown"])
def test_explicit_units_and_unidentified_health_remain_untouched(unit):
    health = {**HEALTH, "_monetary_unit": unit}
    assert project_host_numbers({"financial_health": health}, "1_financials") == {"financial_health": health}
    health.pop("source")
    health.pop("_monetary_unit")
    assert project_host_numbers({"financial_health": health}, "1_financials") == {"financial_health": health}


@pytest.mark.parametrize("value", [None, "", True, float("inf"), float("nan"), -100])
def test_invalid_capitalization_is_not_invented(value):
    assert "market_cap_yi" not in project_host_numbers({"market_cap_raw": value}, "0_basic")


def test_existing_native_aliases_zero_values_and_small_base_units_are_preserved():
    data = {**BASIC, "market_cap": "3293.83亿", "circulating_cap_yi": 3240.03}
    assert project_host_numbers(data, "0_basic") == data
    assert project_host_numbers({"market_cap_raw": 100}, "0_basic")["market_cap_yi"] == 1e-6
    health = {**HEALTH, "cash": 0, "total_debt": 100}
    fixed = project_host_numbers({"financial_health": health}, "1_financials")["financial_health"]
    assert fixed["cash"] == 0
    assert fixed["total_debt"] == round(100 / 1e8, 2)


def test_one_frozen_bundle_materializes_both_versions_without_changing_old_manifest():
    from datetime import date, datetime, timezone
    from research_system.market_data.bundles import BundlePublisher
    from research_system.market_data.contracts import canonical_hash
    from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.report_inputs import DatabaseReportInputGateway
    from research_system.market_data.repository import MarketDataRepository
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import _input

    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    try:
        before = {}
        with database.session() as session:
            repo = MarketDataRepository(session)
            for contract in DEFAULT_DIMENSION_REGISTRY:
                request = _input(contract.dimension)
                if contract.dimension in {"0_basic", "1_financials"}:
                    data = deepcopy(request.payload["raw"])
                    data.update(BASIC if contract.dimension == "0_basic" else {"financial_health": HEALTH})
                    if contract.dimension == "0_basic":
                        data.pop("market_cap", None)  # Reproduce the host's missing native alias.
                    row = snapshot(contract.dimension, data)
                    request = request.model_copy(update={
                        "payload": {**request.payload, **row.payload_json},
                        "query_manifest": {**request.query_manifest, **row.query_manifest_json}})
                saved = SnapshotIngestionService(repo).ingest(request)
                before[saved.id] = canonical_hash({"hash": saved.content_hash, "payload": saved.payload_json})
            BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY",
                as_of_date=date(2026, 8, 20), now=datetime(2026, 8, 21, 1, tzinfo=timezone.utc))
        gateway = DatabaseReportInputGateway(database,
            clock=lambda: datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
            runtime_settings=SimpleNamespace(default_model="fixture-model", provider_name="fixture"))
        manifests = {}
        for version in ("uzi-dim-result-v8", PROJECTION_VERSION):
            manifests[version] = gateway.freeze_report_manifest(symbols=["600519.SH"],
                as_of_date=date(2026, 8, 20), tenant_id="tenant", user_id="user", context={},
                workflow_pins={"research_profile": "lite", "snapshot_projection_version": version})
        old, new = manifests.values()
        assert old["manifest_hash"] != new["manifest_hash"]
        for manifest in manifests.values():
            version = manifest["workflow_pins"]["snapshot_projection_version"]
            loaded = gateway.load_frozen_report_manifest(manifest_id=manifest["manifest_id"],
                manifest_hash=manifest["manifest_hash"], tenant_id="tenant", user_id="user")
            assert loaded["items"][0]["snapshots"] == manifest["items"][0]["snapshots"]
            with database.session() as session:
                repo = MarketDataRepository(session)
                for materialized in loaded["items"][0]["snapshots"]:
                    row = repo.require_snapshot(materialized["snapshot_id"])
                    assert materialized["facts"]["_uzi_raw_result"] == _native_result(row, version=version)
                    assert canonical_hash({"hash": row.content_hash, "payload": row.payload_json}) == before[row.id]
            market = next(r for r in loaded["items"][0]["snapshots"] if r["snapshot_type"] == "market")
            financial = next(r for r in loaded["items"][0]["snapshots"] if r["snapshot_type"] == "fundamental")
            assert ("market_cap_yi" in market["facts"]) is (version == PROJECTION_VERSION)
            assert financial["facts"]["financial_health"]["cash"] == (
                97.68 if version == PROJECTION_VERSION else HEALTH["cash"])
    finally:
        database.close()
