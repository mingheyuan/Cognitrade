"""Offline macro boundary: an index response is not native macro research.

These fixtures are transport-shape instrumentation, not historic market facts.
The classification regression was captured RED before its bounded repair;
genuine source failures must still remain separately retryable.
"""

from __future__ import annotations

import importlib
import socket
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import select

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _annotate_point_in_time_snapshot,
)
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import SnapshotStatus, cutoff_at_for_date
from research_system.market_data.models import MarketDataCollectionTaskResult
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.database import StrategyDatabase

AS_OF = date(2026, 9, 2)
SYMBOL = "688041.SH"
SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts"
)
NATIVE_MACRO_SHA256 = "213ed66a2141d7040c2e5157ab8d763f39d034124b877d3fd65e11eb2e82f55b"
SKILLS = importlib.import_module(
    "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill"
)


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    guards = []
    for owner, attribute in (
        (requests.sessions.Session, "request"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
        (SKILLS.UziSourceSnapshotSkill, "_safe_search"),
        (SKILLS.UziSourceSnapshotSkill, "_safe_news"),
    ):
        guard = Mock(side_effect=AssertionError("macro test must remain offline"))
        monkeypatch.setattr(owner, attribute, guard)
        guards.append(guard)
    yield
    for guard in guards:
        guard.assert_not_called()


@pytest.fixture
def historical_macro(monkeypatch):
    # This is the actual adapter's empty-DataFrame result shape. It deliberately
    # includes no invented prices, macro values or claimed historical response.
    benchmark = {
        "benchmark_code": "000300",
        "benchmark_name": "HS300",
        "start_date": "20260803",
        "end_date": "20260902",
        "latest_hist": {},
        "recent_hist": [],
    }
    fetch = Mock(return_value=deepcopy(benchmark))
    monkeypatch.setattr(SKILLS.AkShareSectorAdapter, "get_benchmark_snapshot", fetch)
    snapshot = SKILLS.MacroSnapshotSkill().run(
        query="海光信息", symbol=SYMBOL, as_of_date=AS_OF
    )
    annotated = _annotate_point_in_time_snapshot(
        snapshot,
        snapshot_type="macro",
        as_of_date=AS_OF,
        cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )
    original = deepcopy(annotated)
    ingest = UziSnapshotAdapter().adapt(
        annotated,
        symbol=SYMBOL,
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )
    assert annotated == original
    fetch.assert_called_once_with(start_date="20260803", end_date="20260902")
    return ingest, benchmark, annotated


def test_benchmark_response_cannot_be_normalized_as_macro_observations(historical_macro):
    ingest, benchmark, _ = historical_macro
    assert ingest.status == SnapshotStatus.DATA_GAP
    assert ingest.payload["raw"]["benchmark"] == benchmark
    assert ingest.payload["observations"] == []
    assert ingest.query_manifest["field_coverage"]["present_fields"] == []
    assert ingest.query_manifest["field_coverage"]["missing_fields"] == ["observations"]
    assert not any(
        ingest.payload["raw"].get(key)
        for key in ("rate_cycle", "fx_trend", "geo_risk", "commodity", "web_search_snippets")
    )


def test_pristine_native_macro_needs_six_query_groups_not_an_index(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    native = importlib.import_module("fetch_macro")
    assert Path(native.__file__).resolve() == SCRIPTS / "fetch_macro.py"
    assert sha256((SCRIPTS / "fetch_macro.py").read_bytes()).hexdigest() == NATIVE_MACRO_SHA256

    class FixedClock:
        @staticmethod
        def now():
            return datetime(2026, 9, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(native, "datetime", FixedClock)
    trusted, generic = Mock(return_value=[]), Mock(return_value=[])
    monkeypatch.setattr(native, "search_trusted", trusted)
    monkeypatch.setattr(native, "search", generic)
    result = native.main("离线测试行业")
    assert trusted.call_count == generic.call_count == 3
    assert [call.args[0] for call in trusted.call_args_list] == [
        "2026 中国 利率 货币政策 降息 最新",
        "2026 美联储 利率周期 最新",
        "2026 人民币 汇率 走势",
    ]
    assert [call.args[0] for call in generic.call_args_list] == [
        "2026 中美关系 贸易 制裁 离线测试行业",
        "2026 大宗商品 周期 CRB指数",
        "2026 离线测试行业 宏观 政策 利好 利空",
    ]
    assert all(call.kwargs == {"dim_key": "3_macro", "max_results": 4}
               for call in trusted.call_args_list)
    assert all(call.kwargs == {"max_results": 4} for call in generic.call_args_list)
    assert result == {
        "data": {
            "rate_cycle": "中性（2026 货币政策）",
            "fx_trend": "中性（人民币走势）",
            "geo_risk": "中性（地缘风险）",
            "commodity": "中性（大宗周期）",
            "industry_macro_impact": "中性",
            "web_search_snippets": {
                key: [] for key in
                ("rate_cycle", "us_rate", "fx_trend", "geo_risk", "commodity", "industry_macro")
            },
            "year": 2026,
            "industry": "离线测试行业",
        },
        "source": "web_search:ddgs + heuristic sentiment",
        "fallback": False,
    }
    # This is exact native empty-input behavior, not evidence of neutral macro
    # conditions. Any future PIT path must preserve native output separately
    # from disclosure of the six unobserved evidence groups.


def test_missing_macro_derivation_is_not_a_blindly_retryable_provider_outage(
    historical_macro,
):
    ingest, _, _ = historical_macro
    manifest = ingest.query_manifest
    assert manifest["source_trace"][0]["status"] == "available"
    assert manifest["field_coverage"]["missing_fields"] == ["observations"]
    # Originally RED: a successful index transport entered retryable_failure
    # only because capability said implemented. Do not fix this by mapping an
    # index benchmark into macro research observations.
    assert manifest["collection_outcome"] == "unavailable"
    assert "provider_failure" not in manifest
    assert manifest["degradation"]["reason_code"] == "normalization_or_derivation_missing"
    assert manifest["degradation"]["retryable"] is False
    assert manifest["historical_capability"]["implementation_status"] == "inputs_only"
    assert manifest["historical_capability"]["native_derivation"] == "not_implemented"


@pytest.mark.parametrize("source_status", ["provider_unavailable", "timeout", "rate_limited", "parse_failed"])
def test_successful_benchmark_does_not_hide_another_requested_source_failure(
    historical_macro, source_status,
):
    _, _, annotated = historical_macro
    mixed = deepcopy(annotated)
    mixed["source_trace"].append({
        "source_id": "offline.other_requested_source",
        "request_executed": True,
        "status": source_status,
    })
    before = deepcopy(mixed)
    ingest = UziSnapshotAdapter().adapt(
        mixed, symbol=SYMBOL, market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai", as_of_date=AS_OF,
    )
    assert mixed == before
    assert ingest.query_manifest["collection_outcome"] == "retryable_failure"
    assert ingest.query_manifest["provider_failure"]["reason_code"] == "historical_provider_failure"
    assert ingest.query_manifest["provider_failure"]["retryable"] is True
    assert not ingest.query_manifest.get("degradation")
    assert ingest.payload["observations"] == []


@pytest.mark.parametrize("source_status", ["available", "success", "succeeded"])
def test_all_requested_sources_successful_with_only_benchmark_is_local_gap(
    historical_macro, source_status,
):
    _, _, annotated = historical_macro
    successful = deepcopy(annotated)
    successful["source_trace"].append({
        "source_id": "offline.other_requested_source",
        "request_executed": True,
        "status": source_status,
    })
    ingest = UziSnapshotAdapter().adapt(
        successful, symbol=SYMBOL, market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai", as_of_date=AS_OF,
    )
    assert ingest.query_manifest["collection_outcome"] == "unavailable"
    assert "provider_failure" not in ingest.query_manifest
    assert ingest.query_manifest["degradation"]["retryable"] is False
    assert ingest.payload["observations"] == []


def test_explicit_macro_task_without_pit_inputs_reports_dependency_without_research_opt_in(
    historical_macro, monkeypatch,
):
    _, _, annotated = historical_macro
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    collector = Mock(side_effect=lambda **_: [deepcopy(annotated)])
    forbidden = Mock(side_effect=AssertionError("partial task did not authorize research/model/aux"))
    monkeypatch.setattr("research_system.market_data.collection.recover_research_documents", forbidden)
    monkeypatch.setattr("research_system.market_data.research_enrichment.run_research_enrichment", forbidden)
    monkeypatch.setattr("research_system.market_data.quant_auxiliary.collect_job_quant_auxiliary_inputs", forbidden)
    coordinator = MarketDataCollectionCoordinator(database, collector=collector, research_provider=forbidden)
    try:
        job = coordinator.create_job(
            tenant_id="tenant", user_id="user", actor_id="actor",
            symbols=[SYMBOL], start_date=AS_OF, end_date=AS_OF,
            dimensions=["3_macro"], mode="missing_or_stale", submit=False,
        )
        coordinator.run_job_now(job["id"])
        result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        task = result["tasks"][0]
        assert task["collection_outcome"] == "unavailable"
        # After native derivation is wired, missing PIT context must be shown
        # before calling the old benchmark-only provider. Adapter-level tests
        # above retain the exact diagnostic contract for saved old responses.
        assert task["reason_code"] == "macro_basic_dependency_missing"
        assert task["failure_kind"] == "missing_dependency"
        assert task["retryable"] is False
        assert task["raw_data_present"] is False
        with database.session() as session:
            row = session.scalar(select(MarketDataCollectionTaskResult))
            assert row.error_code == "macro_basic_dependency_missing"
            assert row.retryable is False
            assert row.metrics_json["collection_outcome"] == "unavailable"
        collector.assert_not_called()
        forbidden.assert_not_called()
    finally:
        coordinator.close()
        database.close()
