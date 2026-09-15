from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from research_system.api.fund_holding_report_service import FundHoldingReportService
from research_system.api.routes.report_signals import ReportJobRequest
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from research_system.runtime.storage.jsonl import read_json
from research_system.strategies.backtesting.instruments import InstrumentResolver
from research_system.strategies.backtesting.market_data_gateway import MarketDataGateway
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.orchestration import frozen_analysis_input_hashes
from tests.market_data.test_pit_core import AS_OF, _input, _payload
from tests.test_fund_holding_reports import FakeAgentOS, FakeRunService
from tests.test_market_data_report_inputs import _manifest


def test_analysis_cache_tracks_frozen_evidence_not_unrelated_manifest_metadata():
    original = _manifest("600001")
    original["workflow_pins"] = {"snapshot_projection_version": "uzi-dim-result-v2", "research_profile": "medium"}
    original["items"][0]["context"] = {"evidence_bindings": [{"evidence_id": "one", "content_hash": "a" * 64}]}
    baseline = frozen_analysis_input_hashes(original, ["600001"])
    modified = deepcopy(original)
    modified["manifest_id"] = "new-random-id"
    modified["context"] = {"orchestration_job_id": "other-job"}
    assert frozen_analysis_input_hashes(modified, ["600001"]) == baseline
    modified["items"][0]["context"]["evidence_bindings"].append({"evidence_id": "two", "content_hash": "b" * 64})
    assert frozen_analysis_input_hashes(modified, ["600001"]) != baseline
    modified = deepcopy(original)
    modified["workflow_pins"]["snapshot_projection_version"] = "future-version"
    assert frozen_analysis_input_hashes(modified, ["600001"]) != baseline


def test_live_acceptance_does_not_label_system_failures_native_refusal():
    from scripts.run_uzi_report_e2e import classify_reports
    def report(status, source):
        return [{"nodes": [{"node_type": "holding_uzi", "status": status, "source": source}]}]
    assert classify_reports(report("failed", {}))[0] == "generation_failed"
    assert classify_reports(report("blocked", {"report_allowed": False, "critical_count": 1}))[0] == "blocked"
    assert classify_reports(report("cancelled", {}))[0] == "cancelled"
    assert classify_reports(report("failed", {"report_stage": "input_preflight", "report_allowed": None}))[0] == "input_blocked"


def test_publication_resolves_v2_pins_but_preserves_v1():
    from research_system.strategies.contracts import (
        StrategyDocumentV1,
        StrategyDocumentV2,
        strategy_content_hash,
    )
    from research_system.strategies.runtime_pins import resolve_publication_document
    original = StrategyDocumentV1()
    assert strategy_content_hash(resolve_publication_document(original)) == strategy_content_hash(original)
    resolved = resolve_publication_document(StrategyDocumentV2(), SimpleNamespace(default_model="configured-model", codex_reasoning_effort="high"))
    assert resolved.pins.model_id == "configured-model"
    assert resolved.pins.model_parameters["reasoning_effort"] == "high"
    assert len(resolved.pins.prompt_hash) == 64
    assert resolved.pins.uzi_source_commit == "b3665e85b8666477038e11157d8e8feb0e006b76"


def test_bundle_only_batch_does_not_read_a_legacy_fund_run(tmp_path: Path):
    agent = FakeAgentOS(tmp_path / "runs")
    runs = FakeRunService([], agent)
    symbols = [f"600{i:03d}" for i in range(1, 11)]
    manifest = _manifest(*symbols)
    for index, item in enumerate(manifest["items"]):
        item["weight"] = 10 - index
    manifest["dependencies"] = [{"dependency_kind": "fund_top_holdings", "bundle_id": "fund-bundle-1"}]
    parent = {
        "fund_bundle_id": "fund-bundle-1", "bundle_manifest_hash": "b" * 64, "snapshot_id": "fund-snapshot-1",
        "payload": {
            "fund_code": "020464", "fund_name": "测试基金", "fund_type": "股票型",
            "holding_source": "direct_equity_disclosure", "is_fund_of_funds": False,
            "disclosure_date": "2026-06-30", "availability_at": "2026-07-20T00:00:00Z",
            "cutoff_at": "2026-08-20T15:59:59Z",
            "holdings": [{"rank": i + 1, "symbol": f"{code}.SH", "code": code, "name": code,
                          "fund_weight_pct": 10 - i, "disclosed_weight_pct": 10 - i} for i, code in enumerate(symbols)],
        },
    }
    resolver = Mock(return_value=parent)
    runs.report_input_service = SimpleNamespace(
        has_fund_report_lineage=True, resolve_fund_report_parent=resolver,
        load_fund_report_manifest=lambda value, **_: value,
    )
    service = FundHoldingReportService(
        settings=SimpleNamespace(runs_root=tmp_path / "runs", fund_holding_report_concurrency=2),
        agent_os=agent, run_service=runs,
    )
    service._submit_item = Mock()
    runs.artifacts = Mock(side_effect=AssertionError("legacy run must not be read"))
    try:
        batch = service.create_batch(
            fund_code="020464", profile="medium", as_of_date="2026-08-20",
            report_manifest=manifest, confirm_stale=True,
        )
        assert batch["source_run_id"] is None
        assert batch["fund_bundle_id"] == "fund-bundle-1"
        assert [row["symbol"] for row in batch["holdings"]] == symbols
        assert [row["fund_weight_pct"] for row in batch["holdings"]] == list(range(10, 0, -1))
        assert resolver.call_args.kwargs["fund_bundle_id"] == "fund-bundle-1"
        for holding in batch["holdings"]:
            request = read_json(agent.run_store.load_run_paths(holding["child_run_id"]).request)
            assert request["constraints"]["data_source_mode"] == "bundle_only"
            assert request["constraints"]["research_profile"] == "medium"
        runs.artifacts.assert_not_called()
    finally:
        service.close()


def test_explicit_fund_bundle_requires_one_date():
    ReportJobRequest(assignment_id="assignment", as_of_date="2026-09-02", fund_bundle_id="bundle")
    with pytest.raises(ValueError, match="单日"):
        ReportJobRequest(assignment_id="assignment", start_date="2026-09-02", end_date="2026-09-04", fund_bundle_id="bundle")


@pytest.fixture
def market_database():
    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    yield database
    database.engine.dispose()


def seed_prices(database, *, adjustment="none", corporate_action=False):
    payload = _payload("2_kline")
    payload["corporate_actions"] = ([{"date": AS_OF.isoformat(), "type": "cash_dividend"}] if corporate_action else [])
    ingest = _input("2_kline", payload=payload)
    # Provenance remains truthful even for a provider returning the wrong
    # adjustment; the replay boundary, not this fixture, must reject it.
    ingest.query_manifest["coverage"]["ohlcv_adjustment"] = adjustment
    for field, proof in ingest.query_manifest["field_queries"].items():
        proof.update(query_scope="field", field=field)
    with database.session() as session:
        snapshot = SnapshotIngestionService(MarketDataRepository(session)).ingest(ingest)
        return snapshot.id


def test_report_replay_reads_only_database(market_database):
    snapshot_id = seed_prices(market_database)
    legacy = Mock()
    legacy.get_market_data.side_effect = AssertionError("network fallback forbidden")
    gateway = MarketDataGateway(legacy, database=market_database)
    series = gateway.get_frozen_series(InstrumentResolver.resolve_stock("600519"), start_date=date(2026, 8, 19), end_date=AS_OF, as_of_date=AS_OF)
    assert len(series.bars) == 2
    assert series.source["snapshot_id"] == snapshot_id
    assert series.source["adjustment"] == "raw"
    assert series.source["currency"] == "CNY"
    legacy.get_market_data.assert_not_called()


def test_missing_market_data_has_explicit_collection_scope(market_database):
    with pytest.raises(StrategyDomainError) as error:
        MarketDataGateway(database=market_database).get_frozen_series(
            InstrumentResolver.resolve_stock("600519"), start_date=AS_OF, end_date=AS_OF, as_of_date=AS_OF,
        )
    assert error.value.code == "market_history_missing_in_data_center"
    assert error.value.details["suggested_collection"]["dimensions"] == ["2_kline"]


def test_replay_rejects_corporate_actions(market_database):
    seed_prices(market_database, corporate_action=True)
    with pytest.raises(StrategyDomainError) as error:
        MarketDataGateway(database=market_database).get_frozen_series(
            InstrumentResolver.resolve_stock("600519"), start_date=AS_OF, end_date=AS_OF, as_of_date=AS_OF,
        )
    assert error.value.code == "report_replay_corporate_actions_unsupported"


def test_fixture_missing_open_is_not_fabricated():
    rows = [{"date": AS_OF.isoformat(), "close": 100, "high": 101, "low": 99, "volume": 1000}]
    series = MarketDataGateway(series_loader=lambda *_: rows).get_frozen_series(
        InstrumentResolver.resolve_stock("600519"), start_date=AS_OF, end_date=AS_OF, as_of_date=AS_OF,
    )
    assert series.bars[0]["open"] is None
    assert series.source["evidence_mode"] == "fixture"
