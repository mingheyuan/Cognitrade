"""Known historical unavailability is a disclosure, never a market observation."""
from copy import deepcopy
from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from research_system.market_data.native_projection import (
    PROJECTION_VERSION, V7_PROJECTION_VERSION, native_payload, project_native_result,
)
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.market_data.uzi_gate import evaluate_uzi_collection_gate, native_snapshot_usable
from tests.market_data.test_uzi_gate import _native_rows


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("projection must be offline"))
    monkeypatch.setenv("UZI_DEPTH", "medium")


def gap_input():
    return UziSnapshotAdapter().disclosed_gap(
        symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai",
        dimension="19_contests", as_of_date=date(2026, 8, 20),
        reason_code="historical_source_current_only",
        message="来源只提供当前组合，目标日没有归档，未进行历史取数",
        attempted_reconstruction=False,
    )


def gap_snapshot():
    value = gap_input()
    return SimpleNamespace(dimension=value.dimension, status=value.status.value,
        source_version=value.source_version, payload_json=value.payload,
        query_manifest_json=value.query_manifest, validation_json=value.validation)


def test_known_disclosure_preserves_reason_without_fabricating_source_result():
    snapshot = gap_snapshot()
    before = deepcopy(vars(snapshot))
    result = project_native_result(snapshot)
    assert set(result["data"]) == {"_note", "_historical_availability"}
    assert "非历史观测" in result["data"]["_note"]
    meta = result["data"]["_historical_availability"]
    assert meta["attempted_reconstruction"] is False
    assert meta["is_research_fact"] is False
    assert meta["excluded_from_scoring"] is True
    assert meta["requested_as_of_date"] == "2026-08-20"
    assert result["_pipeline"]["quality"] == "missing"
    assert result["source"] == snapshot.source_version
    assert not native_snapshot_usable(snapshot)
    assert not {"error", "_timeout"} & result.keys()
    assert vars(snapshot) == before
    assert project_native_result(snapshot, version=V7_PROJECTION_VERSION)["data"] == {}


@pytest.mark.parametrize("invalid", [
    "unowned", "unsafe", "future_observation", "missing_decision", "missing_date",
    "mismatched_reason", "missing_message", "unknown_attempt", "not_excluded", "actual_payload",
])
def test_unproven_or_unsafe_disclosure_cannot_become_native_notes(invalid):
    snapshot = gap_snapshot()
    manifest = snapshot.query_manifest_json
    if invalid == "unowned": snapshot.source_version = "unknown-provider"
    elif invalid == "unsafe": manifest["pit_safe"] = False
    elif invalid == "future_observation": manifest["observed_local_date"] = "2026-09-10"
    elif invalid == "missing_decision": manifest.pop("pit_mode")
    elif invalid == "missing_date": manifest.pop("requested_as_of_date")
    elif invalid == "mismatched_reason": manifest["degradation"]["reason_code"] = "different"
    elif invalid == "missing_message": snapshot.payload_json["message"] = ""
    elif invalid == "unknown_attempt": manifest["degradation"].pop("attempted_reconstruction")
    elif invalid == "not_excluded": manifest["degradation"]["excluded_from_scoring"] = False
    elif invalid == "actual_payload": snapshot.payload_json["raw"] = {"summary": {"s_tier_holders": 99}}
    result = project_native_result(snapshot)
    assert "_historical_availability" not in result["data"]
    if invalid != "actual_payload":
        assert result["data"] == {}


def test_native_gate_and_report_use_identical_notes_without_changing_features():
    rows = _native_rows()
    rows["19_contests"] = gap_snapshot()
    old = evaluate_uzi_collection_gate(rows, projection_version=V7_PROJECTION_VERSION)
    new = evaluate_uzi_collection_gate(rows)
    assert old["gate_passed"] is False and new["gate_passed"] is True
    assert new["coverage_pct"] == old["coverage_pct"]
    result = project_native_result(rows["19_contests"])
    raw = build_uzi_raw([{"symbol": "600519.SH", "snapshot_type": "contests",
                         "facts": {"_uzi_raw_result": result}}], "600519.SH")
    assert raw["dimensions"]["19_contests"]["data"] == native_payload(rows["19_contests"])
    native = import_module("lib.self_review")
    assert native.check_empty_dims({"dims": {"19_contests": result}}) == []
    before = deepcopy(raw)
    before["dimensions"]["19_contests"]["data"] = {}
    features = import_module("lib.stock_features")
    assert features.extract_features(before, {}) == features.extract_features(raw, {})


def test_all_notes_cannot_satisfy_native_financial_coverage_gate():
    rows = _native_rows()
    for dimension in rows:
        rows[dimension] = gap_snapshot()
        rows[dimension].dimension = dimension
    gate = evaluate_uzi_collection_gate(rows)
    assert gate["gate_passed"] is False
    assert gate["coverage_pct"] == 0
    assert any(row["check"] == "coverage_threshold" and row["severity"] == "critical" for row in gate["issues"])


def test_new_manifest_projects_notes_but_old_manifest_and_snapshot_hashes_stay_fixed():
    from research_system.market_data.bundles import BundlePublisher
    from research_system.market_data.dimension_contracts import DIMENSION_IDS
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.repository import MarketDataRepository
    from research_system.market_data.report_inputs import DatabaseReportInputGateway
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import _input, AS_OF

    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    try:
        with database.session() as session:
            repo = MarketDataRepository(session)
            ingestion = SnapshotIngestionService(repo)
            for dimension in DIMENSION_IDS:
                if dimension != "19_contests":
                    ingestion.ingest(_input(dimension))
            snapshot = ingestion.ingest(gap_input())
            assert snapshot.status == "data_gap"
            old_hash = snapshot.content_hash
            bundle = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY",
                as_of_date=AS_OF, now=datetime(2026, 9, 14, tzinfo=timezone.utc))
            bundle_id = bundle.id
        gateway = DatabaseReportInputGateway(database)
        args = dict(symbols=["600519.SH"], as_of_date=AS_OF, tenant_id="t", user_id="u", context={})
        old = gateway.freeze_report_manifest(**args, workflow_pins={"research_profile": "lite",
            "snapshot_projection_version": V7_PROJECTION_VERSION})
        new = gateway.freeze_report_manifest(**args, workflow_pins={"research_profile": "lite"})
        assert new["workflow_pins"]["snapshot_projection_version"] == PROJECTION_VERSION
        assert new["manifest_hash"] != old["manifest_hash"]
        loaded = gateway.load_frozen_report_manifest(manifest_id=old["manifest_id"],
            manifest_hash=old["manifest_hash"], tenant_id="t", user_id="u")
        assert loaded["items"][0]["snapshots"] == old["items"][0]["snapshots"]
        assert loaded["items"][0]["context"]["evidence_bindings"] == old["items"][0]["context"]["evidence_bindings"]
        for manifest, has_notes in ((old, False), (new, True)):
            item = manifest["items"][0]
            assert item["bundle_id"] == bundle_id
            row = next(r for r in item["snapshots"] if r["dimension_id"] == "19_contests")
            data = row["facts"]["_uzi_raw_result"]["data"]
            assert ("_historical_availability" in data) is has_notes
            assert row["content_hash"] == old_hash
    finally:
        database.close()
