from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from domain_packs.finance_ashare.skills.policy_snapshot.skill import PolicySnapshotSkill
from domain_packs.finance_ashare.tools import snapshot_collector as collector_module
from research_system.adapters.vendors.akshare_policy_adapter import AkSharePolicyAdapter
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.historical import reconstruct_historical_snapshot
from research_system.market_data.models import (
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
)
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_collection_coordinator import _HistoricalOutcomeCollector

AS_OF = date(2026, 9, 2)


@pytest.mark.parametrize("outcome", ["partial", "retryable_failure"])
def test_historical_eligible_gap_is_requested_before_it_is_processed(outcome):
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    provider = _HistoricalOutcomeCollector(outcome)
    if outcome == "retryable_failure":
        original_call = provider.__call__

        class MetadataFailureCollector:
            calls = provider.calls

            def __call__(self, **kwargs):
                rows = original_call(**kwargs)
                for row in rows:
                    row["facts"]["_uzi_raw_result"]["data"] = {
                        "provider_note": "failed query metadata"
                    }
                return rows

        provider = MetadataFailureCollector()
    coordinator = MarketDataCollectionCoordinator(database, collector=provider)
    historical = datetime.now(timezone.utc).date() - timedelta(days=30)
    try:
        for mode in ("force_all", "missing_or_stale"):
            job = coordinator.create_job(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                symbols=["600519.SH"],
                start_date=historical,
                end_date=historical,
                dimensions=["10_valuation"],
                mode=mode,
                publish_when_complete=False,
                submit=False,
            )
            coordinator.run_job_now(job["id"])
        result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        assert len(provider.calls) == 2
        assert provider.calls[-1]["snapshot_types"] == ["valuation"]
        assert result["tasks"][0]["collection_outcome"] == outcome
        assert result["tasks"][0]["error_code"] != "collector_result_missing"
    finally:
        coordinator.close()
        database.close()


def _failure(kind):
    kwargs = {
        "query": "historical test",
        "symbol": "002371.SZ",
        "snapshot_type": "policy",
    }
    if kind == "timeout":
        return collector_module._fetcher_timeout_snapshot(**kwargs, timeout_seconds=45)
    return collector_module._fetcher_failure_snapshot(
        **kwargs, exc=ConnectionError("offline")
    )


@pytest.mark.parametrize("kind", ["timeout", "exception"])
def test_historical_error_envelope_survives_without_business_facts(kind):
    original = _failure(kind)
    unchanged = deepcopy(original)
    restored = reconstruct_historical_snapshot(
        original, dimension="13_policy", as_of_date=AS_OF
    )
    assert original == unchanged
    assert restored is not None
    assert restored["snapshot_date"] == AS_OF.isoformat()
    assert (
        restored["historical_reconstruction"]["requested_as_of_date"]
        == AS_OF.isoformat()
    )
    assert restored["facts"]["_uzi_raw_result"] == original["facts"]["_uzi_raw_result"]
    assert (
        restored["source_trace"][: len(original["source_trace"])]
        == original["source_trace"]
    )
    assert restored["facts"]["_uzi_raw_result"]["data"] == {}


def test_failure_does_not_make_current_facts_historically_safe():
    original = _failure("timeout")
    original["facts"]["_uzi_raw_result"]["data"] = {"policies": [{"title": "today"}]}
    assert (
        reconstruct_historical_snapshot(
            original, dimension="13_policy", as_of_date=AS_OF
        )
        is None
    )
    original = _failure("timeout")
    original["facts"]["current_price"] = 100
    assert (
        reconstruct_historical_snapshot(
            original, dimension="13_policy", as_of_date=AS_OF
        )
        is None
    )


@pytest.mark.parametrize("kind", ["timeout", "exception"])
def test_historical_force_collection_persists_error_not_missing_result(kind):
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return [_failure(kind)]

    coordinator = MarketDataCollectionCoordinator(database, collector=provider)
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["002371.SZ"],
            start_date=AS_OF,
            end_date=AS_OF,
            dimensions=["13_policy"],
            mode="force_all",
            publish_when_complete=False,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        task = result["tasks"][0]
        assert len(calls) == 1
        assert task["retryable"] is True
        assert task["error_code"] == "historical_provider_failure"
        with database.session() as session:
            stored_result = session.scalar(
                select(MarketDataCollectionTaskResult).where(
                    MarketDataCollectionTaskResult.task_id == task["id"]
                )
            )
            snapshot = session.get(MarketDataSnapshot, stored_result.snapshot_id)
            raw = snapshot.payload_json["_uzi_raw_result"]
            assert raw["data"] == {}
            assert raw["error"]
            assert bool(raw.get("_timeout")) == (kind == "timeout")
            assert snapshot.as_of_date == AS_OF
    finally:
        coordinator.close()
        database.close()


@pytest.mark.parametrize("proved", [True, False])
def test_historical_policy_uses_only_proved_industry_or_stock_scope(proved):
    skill = PolicySnapshotSkill()
    basic = {"data": {"industry": "半导体", "name": "北方华创", "code": "002371"}}
    if proved:
        basic["_pit_context"] = {
            "safe": True,
            "requested_as_of_date": AS_OF.isoformat(),
        }
    calls = []
    skill.adapter.get_policy_snapshot = lambda **kwargs: (
        calls.append(kwargs)
        or {
            "policy_signals": [],
            "regulatory_signals": [],
            "policy_query_status": "empty",
            "query_window": {"start": "2026-08-03", "end": "2026-09-02"},
        }
    )
    result = skill.run(
        query="历史 Point-in-Time 自动重建，截至 2026-09-02",
        symbol="002371.SZ",
        as_of_date=AS_OF,
        dependencies={"0_basic": basic},
    )
    assert calls[0]["theme"] == ("半导体" if proved else "")
    assert calls[0]["constituents"] == [
        {"code": "002371", "name": "北方华创" if proved else ""}
    ]
    assert result["source_trace"][0]["status"] == "no_data"
    assert result["facts"]["policy_query_status"] == "empty"


def test_policy_query_window_and_empty_outcome_are_truthful():
    adapter = AkSharePolicyAdapter()
    received = []
    adapter.catalyst = SimpleNamespace(
        get_policy_signals=lambda **kwargs: pytest.fail(
            "current feed must stay disabled"
        ),
        get_regulatory_signals=lambda **kwargs: received.append(kwargs) or [],
        build_catalyst_digest=lambda **kwargs: {},
    )
    result = adapter.get_policy_snapshot(
        theme="半导体", constituents=[{"code": "002371"}], as_of_date=AS_OF
    )
    assert result["policy_query_status"] == "empty"
    assert result["query_window"] == {"start": "2026-08-03", "end": "2026-09-02"}
    snapshot = collector_module._annotate_point_in_time_snapshot(
        {"facts": result, "source": {"provider_id": "policy"}, "source_trace": []},
        snapshot_type="policy",
        as_of_date=AS_OF,
        cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )
    assert snapshot["historical_reconstruction"]["window_start"] == "2026-08-03"


def test_policy_diagnostics_distinguish_transport_failure_from_empty(monkeypatch):
    from research_system.adapters.vendors.akshare_catalyst_adapter import (
        AkShareCatalystAdapter,
    )

    catalyst = AkShareCatalystAdapter()
    monkeypatch.setattr(catalyst, "_disclosure_keywords", lambda theme: ["半导体"])
    monkeypatch.setattr(
        "research_system.adapters.vendors.akshare_catalyst_adapter.call_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ConnectionError("https://source.test?token=SECRET unavailable")
        ),
    )
    diagnostics = []
    rows = catalyst.get_regulatory_signals(
        theme="半导体",
        constituents=[{"code": "002371"}],
        as_of_date=AS_OF,
        diagnostics=diagnostics,
    )
    assert rows == []
    assert diagnostics[0]["status"] == "provider_unavailable"
    assert diagnostics[0]["error_type"] == "ConnectionError"
    assert "SECRET" not in str(diagnostics)
    assert diagnostics[0]["query_window"] == {
        "start": "2026-08-03",
        "end": "2026-09-02",
    }


def test_timeout_wrapper_binds_request_day_and_keeps_original_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise collector_module.FutureTimeoutError()

    monkeypatch.setattr(collector_module, "_run_snapshot_skill", fail)
    snapshot = collector_module._run_snapshot_skill_with_deadline(
        query="historical request",
        symbol="002371.SZ",
        snapshot_type="policy",
        provider_registry=None,
        dependencies={},
        timeout_seconds=45,
        as_of_date=AS_OF,
        cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )
    assert snapshot["facts"]["_uzi_raw_result"]["_timeout"] is True
    assert snapshot["snapshot_date"] == AS_OF.isoformat()
    assert (
        snapshot["historical_reconstruction"]["cutoff_at"]
        == cutoff_at_for_date(AS_OF, "Asia/Shanghai").isoformat()
    )


def test_policy_historical_route_passes_pit_dependency_and_does_not_fetch_live(
    monkeypatch,
):
    calls = []

    def collect(self, **kwargs):
        calls.append(kwargs)
        return {
            "policy_signals": [],
            "regulatory_signals": [],
            "policy_query_status": "empty",
            "query_window": {"start": "2026-08-03", "end": "2026-09-02"},
        }

    monkeypatch.setattr(AkSharePolicyAdapter, "get_policy_snapshot", collect)
    snapshot = collector_module._run_snapshot_skill(
        "历史 Point-in-Time 自动重建，截至 2026-09-02",
        "002371.SZ",
        "policy",
        dependencies={
            "0_basic": {
                "data": {"industry": "半导体", "name": "北方华创"},
                "_pit_context": {
                    "safe": True,
                    "requested_as_of_date": AS_OF.isoformat(),
                },
            }
        },
        as_of_date=AS_OF,
        cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )
    assert len(calls) == 1
    assert calls[0]["theme"] == "半导体"
    assert calls[0]["constituents"][0]["code"] == "002371"
    assert snapshot["historical_reconstruction"]["window_start"] == "2026-08-03"
    assert [row["status"] for row in snapshot["source_trace"]][-2:] == [
        "not_queried",
        "not_queried",
    ]
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter

    ingested = UziSnapshotAdapter().adapt(
        snapshot,
        symbol="002371.SZ",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )
    assert ingested.status.value == "data_gap"
    assert ingested.query_manifest["collection_outcome"] == "unavailable"
    assert (
        ingested.query_manifest["degradation"]["reason_code"] == "bounded_archive_gap"
    )
    assert "provider_failure" not in ingested.query_manifest
    assert ingested.payload["policies"] == []


def test_historical_policy_disclosure_query_is_stock_bounded(monkeypatch):
    import pandas as pd

    from research_system.adapters.vendors.akshare_catalyst_adapter import (
        AkShareCatalystAdapter,
    )

    calls = []
    monkeypatch.setattr(
        "research_system.adapters.vendors.akshare_catalyst_adapter.ak.stock_zh_a_disclosure_report_cninfo",
        lambda **kwargs: calls.append(kwargs) or pd.DataFrame(),
    )
    diagnostics = []
    AkShareCatalystAdapter().get_regulatory_signals(
        theme="半导体",
        constituents=[{"code": "002371"}],
        as_of_date=AS_OF,
        diagnostics=diagnostics,
    )
    assert len(calls) == 1
    assert calls[0]["symbol"] == "002371"
    assert calls[0]["keyword"] == ""
    assert calls[0]["start_date"] == "20260803"
    assert calls[0]["end_date"] == "20260902"
    assert diagnostics[0]["status"] == "no_data"
