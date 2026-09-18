"""Bounded zero-query status is not a substitute for native source fields."""

from copy import deepcopy
from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
    _initialize_native_imports,
)
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.market_data.uzi_gate import evaluate_uzi_collection_gate
from tests.market_data.test_uzi_gate import _native_rows


@pytest.fixture(autouse=True)
def no_business_network(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *args, **kwargs: pytest.fail("offline projection"))
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: pytest.fail("offline projection"))


def _legacy_snapshot():
    return SimpleNamespace(
        dimension="16_lhb", status="verified_empty", source_version="recorded-history",
        payload_json={}, validation_json={}, query_manifest_json={
            "pit_safe": True, "collection_outcome": "verified_empty",
            "native_empty_observation": {
                "observation_status": "verified_empty",
                "query_status": {"status": "success", "result_count": 0},
            },
        },
    )


@pytest.mark.parametrize("with_observation", [False, True])
def test_old_empty_snapshot_does_not_manufacture_native_nonempty_data(with_observation):
    snapshot = _legacy_snapshot()
    if not with_observation:
        snapshot.query_manifest_json.pop("native_empty_observation")
    before = deepcopy(vars(snapshot))
    result = project_native_result(snapshot)
    assert result["data"] == {}
    assert "error" not in result and "_timeout" not in result
    assert vars(snapshot) == before
    assert project_native_result(snapshot, version="uzi-dim-result-v6")["data"] == (
        before["query_manifest_json"].get("native_empty_observation")
        or {"observation_status": "verified_empty"}
    )


def test_every_dimension_keeps_source_types_and_error_envelope():
    from research_system.market_data.dimension_contracts import DIMENSION_IDS

    data = {"null": None, "zero": 0, "false": False, "empty_string": "", "empty_list": [],
            "records": [{"value": 0.0, "known": False}], "nested": {"missing": None}}
    envelope = {"data": data, "source": "recorded-provider", "fallback": False,
                "error": None, "_timeout": False, "_pipeline": {"quality": "partial", "error": "original-inner-error"}}
    for dimension in DIMENSION_IDS:
        snapshot = _legacy_snapshot()
        snapshot.dimension = dimension
        snapshot.query_manifest_json = {"pit_safe": True, "collection_outcome": "partial"}
        snapshot.payload_json = {"raw": deepcopy(data), "_uzi_raw_result": deepcopy(envelope)}
        before = deepcopy(vars(snapshot))
        projected = project_native_result(snapshot)
        assert {k: projected[k] for k in envelope} == envelope, dimension
        assert type(projected["data"]["zero"]) is int
        assert type(projected["data"]["false"]) is bool
        assert type(projected["data"]["records"][0]["value"]) is float
        assert vars(snapshot) == before


def test_verified_empty_collection_gate_matches_original_native_empty_check(monkeypatch):
    _initialize_native_imports()
    monkeypatch.setenv("UZI_DEPTH", "medium")
    monkeypatch.delenv("UZI_CLI_ONLY", raising=False)
    monkeypatch.delenv("CI", raising=False)
    original = {"data": {}, "source": "recorded-history"}
    native = import_module("lib.self_review").check_empty_dims({"dims": {"16_lhb": original}})
    assert [(row.dim, row.severity) for row in native] == [("16_lhb", "critical")]
    rows = _native_rows()
    rows["16_lhb"] = _legacy_snapshot()
    gate = evaluate_uzi_collection_gate(rows, profile="medium")
    assert [(row["dimension"], row["severity"]) for row in gate["issues"]
            if row["check"] == "empty_dims"] == [("16_lhb", "critical")]


def _source(data):
    return {
        "snapshot_type": "lhb", "symbol": "688012.SH",
        "retrieved_at": "2026-09-03T02:00:00+00:00", "quality_status": "available",
        "facts": {"availability": "available", "_uzi_raw_result": {
            "data": deepcopy(data), "source": "recorded-history", "fallback": False,
            "error": None, "_timeout": False,
            "_pipeline": {"quality": "partial", "error": None},
        }},
        "source_trace": [{"source_id": "akshare_lhb", "status": "available"}],
        "historical_reconstruction": {
            "safe": True, "strategy": "dated_series", "requested_as_of_date": "2026-09-01",
            "window_start": "2026-08-02", "window_end": "2026-09-01",
        },
    }


@pytest.mark.parametrize("data", [{}, {"observation_status": "verified_empty", "lhb_count_30d": 0, "lhb_records": []}])
def test_adapter_preserves_actual_empty_source_without_promoting_status_to_fact(data):
    original = _source(data)
    before = deepcopy(original)
    ingest = UziSnapshotAdapter().adapt(original, symbol="688012.SH", market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai", as_of_date=date(2026, 9, 1))
    assert ingest.status.value == "verified_empty"
    assert ingest.payload == {}  # Keep the strict bounded-zero storage contract.
    snapshot = SimpleNamespace(dimension=ingest.dimension, status=ingest.status.value,
        source_version=ingest.source_version, payload_json=ingest.payload,
        query_manifest_json=ingest.query_manifest, validation_json={})
    projected = project_native_result(snapshot)
    for key, value in original["facts"]["_uzi_raw_result"].items():
        assert projected[key] == value, key
    assert original == before


def test_native_empty_input_hash_is_verified_and_cannot_bypass_pit():
    ingest = UziSnapshotAdapter().adapt(_source({"observation_status": "verified_empty", "lhb_count_30d": 0}),
        symbol="688012.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=date(2026, 9, 1))
    snapshot = SimpleNamespace(dimension=ingest.dimension, status=ingest.status.value,
        source_version=ingest.source_version, payload_json=ingest.payload,
        query_manifest_json=deepcopy(ingest.query_manifest), validation_json={})
    snapshot.query_manifest_json["pit_safe"] = False
    assert project_native_result(snapshot)["data"] == {}
    snapshot.query_manifest_json["native_empty_input"]["payload"]["raw"]["lhb_count_30d"] = 99
    with pytest.raises(ValueError, match="binding mismatch"):
        project_native_result(snapshot)
    # Legacy readers do not adopt the new transport, including its metadata.
    assert project_native_result(snapshot, version="uzi-dim-result-v6")["data"] == {}


@pytest.mark.parametrize("data", [{}, {"observation_status": "verified_empty", "lhb_count_30d": 0, "lhb_records": []}])
def test_zero_query_source_survives_database_and_bundle_without_changing_old_manifest(data):
    from research_system.market_data.bundles import BundleMaterializer, BundlePublisher
    from research_system.market_data.dimension_contracts import DIMENSION_IDS
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.report_inputs import DatabaseReportInputGateway
    from research_system.market_data.repository import MarketDataRepository
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import AS_OF, _input

    source = _source(data)
    source["symbol"] = "600519.SH"
    source["historical_reconstruction"].update(requested_as_of_date=AS_OF.isoformat(),
        window_start="2026-07-20", window_end=AS_OF.isoformat())
    ingest = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    evaluation_at = datetime(2026, 9, 4, tzinfo=timezone.utc)
    try:
        with database.session() as session:
            repo = MarketDataRepository(session)
            ingestion = SnapshotIngestionService(repo)
            for dimension in DIMENSION_IDS:
                if dimension != "16_lhb":
                    ingestion.ingest(_input(dimension))
            snapshot = ingestion.ingest(ingest)
            assert snapshot.status == "verified_empty", snapshot.validation_json
            assert snapshot.payload_json == {}
            original_hash = snapshot.content_hash
            bundle = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=evaluation_at)
            rows = BundleMaterializer(repo).materialize(bundle.id).snapshots
            target = next(row for row in rows if row["snapshot_id"] == snapshot.id)
            for key, value in source["facts"]["_uzi_raw_result"].items():
                assert target["facts"]["_uzi_raw_result"][key] == value
            assert snapshot.content_hash == original_hash
        gateway = DatabaseReportInputGateway(database, clock=lambda: evaluation_at)
        args = {"symbols": ["600519.SH"], "as_of_date": AS_OF, "tenant_id": "t", "user_id": "u", "context": {}}
        old = gateway.freeze_report_manifest(**args, workflow_pins={"snapshot_projection_version": "uzi-dim-result-v6", "research_profile": "lite"})
        new = gateway.freeze_report_manifest(**args, workflow_pins={"research_profile": "lite"})
        assert new["workflow_pins"]["snapshot_projection_version"] == "uzi-dim-result-v10"
        loaded = gateway.load_frozen_report_manifest(manifest_id=old["manifest_id"], manifest_hash=old["manifest_hash"], tenant_id="t", user_id="u")
        assert loaded["items"][0]["snapshots"] == old["items"][0]["snapshots"]
        assert new["manifest_hash"] != old["manifest_hash"]
    finally:
        database.close()
