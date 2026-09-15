from copy import deepcopy
from datetime import date, timedelta

import pandas as pd
import pytest

from domain_packs.finance_ashare.skills.uzi_source_snapshot import skill as source
from domain_packs.finance_ashare.tools.snapshot_collector import (
    _annotate_point_in_time_snapshot,
)
from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.uzi_adapter import UziSnapshotAdapter

AS_OF = date(2026, 9, 2)


@pytest.fixture(autouse=True)
def no_business_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external call")

    monkeypatch.setattr(source.ak, "futures_main_sina", forbidden)
    monkeypatch.setattr(source.UziSourceSnapshotSkill, "_industry_context", forbidden)
    monkeypatch.setattr(
        source.AkShareSectorAdapter, "get_benchmark_snapshot", forbidden
    )
    monkeypatch.setattr(source.UziSourceSnapshotSkill, "_safe_search", forbidden)
    monkeypatch.setattr(source.UziSourceSnapshotSkill, "_safe_news", forbidden)


def _annotate(snapshot):
    return _annotate_point_in_time_snapshot(
        snapshot,
        snapshot_type=snapshot["snapshot_type"],
        as_of_date=AS_OF,
        cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )


def _adapt(snapshot):
    return UziSnapshotAdapter().adapt(
        snapshot,
        symbol="688041.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )


def _context(monkeypatch, industry):
    monkeypatch.setattr(
        source.UziSourceSnapshotSkill,
        "_industry_context",
        lambda *args, **kwargs: {
            "industry": industry,
            "_source_trace": [
                {"source_id": "cninfo.industry_change", "status": "available"}
            ],
        },
    )


@pytest.mark.parametrize("industry", ["", "   ", "未适配行业"])
def test_unknown_material_mapping_does_not_match_first_industry(industry):
    assert source.MaterialsSnapshotSkill._material_contracts(industry) == ()


@pytest.mark.parametrize("kind", ["materials", "futures"])
def test_missing_mapping_never_becomes_verified_empty(monkeypatch, kind):
    _context(monkeypatch, "未适配行业")
    skill = (
        source.MaterialsSnapshotSkill()
        if kind == "materials"
        else source.FuturesSnapshotSkill()
    )
    snapshot = _annotate(skill.run(query="688041", symbol="688041", as_of_date=AS_OF))
    trace = next(
        row for row in snapshot["source_trace"] if row.get("source_id") == "akshare"
    )
    assert trace["status"] == "not_queried"
    assert trace["request_executed"] is False
    assert trace["reason_code"] == "industry_material_mapping_missing"
    assert (
        snapshot["historical_reconstruction"]["proof_basis"]
        == "no_market_observations_without_query"
    )
    ingest = _adapt(snapshot)
    assert ingest.status.value == "data_gap"
    assert ingest.query_manifest["collection_outcome"] == "unavailable"
    assert (
        ingest.query_manifest["degradation"]["reason_code"]
        == "industry_material_mapping_missing"
    )
    assert "native_empty_observation" not in ingest.query_manifest
    assert ingest.payload["raw"]["data_gap"]
    assert ingest.query_manifest["field_queries"]["prices"]["required_sources"] == []


@pytest.mark.parametrize("kind", ["materials", "futures"])
def test_legacy_generic_no_data_is_not_proof_of_bounded_futures_query(kind):
    snapshot = _annotate(
        {
            "snapshot_type": kind,
            "symbol": "688041",
            "quality_status": "data_gap",
            "facts": {
                "availability": "data_gap",
                "data_gap": ["industry_material_mapping_missing"],
            },
            "source_trace": [{"source_id": "akshare", "status": "no_data", "count": 0}],
        }
    )
    ingest = _adapt(snapshot)
    assert ingest.status.value == "data_gap"
    assert ingest.query_manifest["collection_outcome"] == "unavailable"


def test_futures_prices_filter_dates_before_tail_and_statistics(monkeypatch):
    original = pd.DataFrame(
        [
            {"日期": "2026-09-03", "收盘价": 9999},
            {"日期": "2026-09-02", "收盘价": 120},
            {"日期": "2024-01-01", "收盘价": 1},
            {"日期": "2026-09-01", "收盘价": 100},
            {"日期": "invalid", "收盘价": 7777},
        ]
    )
    frame = original.copy(deep=True)
    monkeypatch.setattr(source.ak, "futures_main_sina", lambda **kwargs: frame)
    rows = source.MaterialsSnapshotSkill._futures_rows(
        (("铜", "CU0"),), as_of_date=AS_OF
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["latest_price"] == 120
    assert row["price_history_12m"] == [100, 120]
    assert row["trend_pct_12m"] == 20
    assert row["latest_record"]["日期"] == "2026-09-02"
    assert row["price_history_dates_12m"] == ["2026-09-01", "2026-09-02"]
    assert row["latest_record_date"] == "2026-09-02"
    pd.testing.assert_frame_equal(original, frame)


@pytest.mark.parametrize(
    "records",
    [
        [{"收盘价": 100}],
        [{"日期": "unknown", "收盘价": 100}],
        [{"日期": "2026-09-02", "收盘价": "bad"}],
    ],
)
def test_unverifiable_futures_rows_are_failures_not_empty(monkeypatch, records):
    _context(monkeypatch, "白酒")
    monkeypatch.setattr(
        source.ak, "futures_main_sina", lambda **kwargs: pd.DataFrame(records)
    )
    ingest = _adapt(
        _annotate(
            source.MaterialsSnapshotSkill().run(
                query="688041",
                symbol="688041",
                as_of_date=AS_OF,
            )
        )
    )
    assert ingest.status.value == "data_gap"
    assert ingest.query_manifest["collection_outcome"] == "retryable_failure"
    assert any(
        row["status"] == "parse_failed" for row in ingest.query_manifest["source_trace"]
    )


@pytest.mark.parametrize("kind,days", [("materials", 550), ("futures", 180)])
def test_actual_successful_bounded_zero_preserves_query_proof(monkeypatch, kind, days):
    _context(monkeypatch, "白酒")
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"日期": "2026-09-03", "收盘价": 999}])

    monkeypatch.setattr(source.ak, "futures_main_sina", fetch)
    skill = (
        source.MaterialsSnapshotSkill()
        if kind == "materials"
        else source.FuturesSnapshotSkill()
    )
    snapshot = _annotate(skill.run(query="688041", symbol="688041", as_of_date=AS_OF))
    assert len(calls) == (2 if kind == "materials" else 1)
    assert snapshot["facts"]["query_window"] == {
        "start": (AS_OF - timedelta(days=days)).isoformat(),
        "end": AS_OF.isoformat(),
    }
    trace = [
        row
        for row in snapshot["source_trace"]
        if row.get("query_scope") == "futures_prices"
    ]
    assert len(trace) == len(calls)
    assert all(
        row["request_executed"] and row["bounded_query_completed"] for row in trace
    )
    assert all(
        row["result_count"] == 1 and row["in_window_count"] == 0 for row in trace
    )
    assert (
        snapshot["historical_reconstruction"]["proof_basis"]
        == "date_filtered_provider_series"
    )
    assert _adapt(snapshot).query_manifest["collection_outcome"] == "verified_empty"
    price_proof = _adapt(snapshot).query_manifest["field_queries"]["prices"]
    assert price_proof["required_sources"] == ["akshare"]
    assert all(row["request_executed"] for row in price_proof["sources"])
    assert {row["symbol"] for row in price_proof["sources"]} == {
        row["symbol"] for row in calls
    }


def test_materials_series_reaches_native_top_level_and_keeps_partial_errors(
    monkeypatch,
):
    _context(monkeypatch, "白酒")

    def fetch(**kwargs):
        if kwargs["symbol"] == "A0":
            raise ConnectionError(
                "connection refused https://host/?token=secret Authorization=private"
            )
        return pd.DataFrame([{"日期": "2026-09-02", "收盘价": 100}])

    monkeypatch.setattr(source.ak, "futures_main_sina", fetch)
    snapshot = _annotate(
        source.MaterialsSnapshotSkill().run(
            query="688041", symbol="688041", as_of_date=AS_OF
        )
    )
    assert snapshot["facts"]["price_history_12m"] == [100]
    failed = next(
        row
        for row in snapshot["source_trace"]
        if row.get("status") == "provider_unavailable"
    )
    assert failed["error_type"] == "ConnectionError"
    assert "connection refused" in failed["error_message"]
    assert "secret" not in str(failed) and "private" not in str(failed)
    assert failed["symbol"] == "A0"
    assert failed["query_window"]["end"] == AS_OF.isoformat()
    assert _adapt(snapshot).query_manifest["collection_outcome"] == "partial"


def test_macro_records_actual_thirty_day_window_and_safe_error(monkeypatch):
    calls = []

    def fail(self, **kwargs):
        calls.append(kwargs)
        raise ConnectionError(
            "connection refused https://host/?token=secret Cookie=private"
        )

    monkeypatch.setattr(source.AkShareSectorAdapter, "get_benchmark_snapshot", fail)
    snapshot = _annotate(
        source.MacroSnapshotSkill().run(
            query="688041", symbol="688041", as_of_date=AS_OF
        )
    )
    assert calls == [{"start_date": "20260803", "end_date": "20260902"}]
    trace = snapshot["source_trace"][0]
    assert trace["query_window"] == {"start": "2026-08-03", "end": "2026-09-02"}
    assert "connection refused" in trace["error_message"]
    assert "secret" not in str(trace) and "private" not in str(trace)
    assert trace["request_executed"] is True
    assert snapshot["historical_reconstruction"]["window_start"] == "2026-08-03"
    ingest = _adapt(snapshot)
    assert ingest.query_manifest["source_trace"][0] == trace
    assert ingest.query_manifest["collection_outcome"] == "retryable_failure"


def test_historical_futures_does_not_reuse_unproved_material_mapping(monkeypatch):
    _context(monkeypatch, "未适配行业")
    dependency = {"facts": {"materials_detail": [{"name": "玻璃", "code": "FG0"}]}}
    original = deepcopy(dependency)
    snapshot = source.FuturesSnapshotSkill().run(
        query="688041",
        symbol="688041",
        as_of_date=AS_OF,
        dependencies={"materials": dependency},
    )
    assert snapshot["facts"]["dependency_lineage"]["used"] is False
    assert snapshot["facts"]["contracts"] == []
    assert original == dependency


def test_historical_futures_uses_same_day_proved_material_dependency(monkeypatch):
    _context(monkeypatch, "未适配行业")
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            [
                {"日期": "2026-09-03", "收盘价": 999},
                {"日期": "2026-09-02", "收盘价": 100},
            ]
        )

    monkeypatch.setattr(source.ak, "futures_main_sina", fetch)
    dependency = {
        "facts": {
            "industry_resolved": "光学光电子",
            "materials_detail": [
                {
                    "name": "玻璃",
                    "code": "FG0",
                    "price_history_12m": [100],
                    "price_history_dates_12m": [AS_OF.isoformat()],
                    "latest_record_date": AS_OF.isoformat(),
                    "latest_record": {"日期": AS_OF.isoformat()},
                }
            ],
        },
        "historical_reconstruction": {
            "safe": True,
            "requested_as_of_date": AS_OF.isoformat(),
        },
    }
    snapshot = _annotate(
        source.FuturesSnapshotSkill().run(
            query="688041",
            symbol="688041",
            as_of_date=AS_OF,
            dependencies={"materials": dependency},
        )
    )
    assert calls == [{"symbol": "FG0"}]
    assert snapshot["facts"]["dependency_lineage"]["used"] is True
    assert snapshot["facts"]["latest_price"] == 100
    assert snapshot["facts"]["price_history_dates_60d"] == [AS_OF.isoformat()]


def test_missing_futures_interface_is_not_a_successful_empty_query(monkeypatch):
    _context(monkeypatch, "白酒")
    monkeypatch.setattr(source.ak, "futures_main_sina", None)
    monkeypatch.setattr(source.ak, "futures_zh_daily_sina", None, raising=False)
    snapshot = _annotate(
        source.MaterialsSnapshotSkill().run(
            query="688041",
            symbol="688041",
            as_of_date=AS_OF,
        )
    )
    ingest = _adapt(snapshot)
    assert ingest.query_manifest["collection_outcome"] == "unavailable"
    trace = next(
        row
        for row in snapshot["source_trace"]
        if row.get("query_scope") == "futures_prices"
    )
    assert trace["status"] == "not_configured"
    assert trace["request_executed"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "missing_dates",
        "future_dates",
        "future_latest",
        "empty_industry",
        "wrong_proof_day",
    ],
)
def test_historical_futures_rejects_old_or_inconsistent_safe_labels(
    monkeypatch, defect
):
    _context(monkeypatch, "未适配行业")
    row = {
        "name": "玻璃",
        "code": "FG0",
        "price_history_12m": [100],
        "price_history_dates_12m": [AS_OF.isoformat()],
        "latest_record_date": AS_OF.isoformat(),
        "latest_record": {"日期": AS_OF.isoformat()},
    }
    dependency = {
        "facts": {"industry_resolved": "光学光电子", "materials_detail": [row]},
        "historical_reconstruction": {
            "safe": True,
            "requested_as_of_date": AS_OF.isoformat(),
        },
    }
    if defect == "missing_dates":
        row.pop("price_history_dates_12m")
    elif defect == "future_dates":
        row["price_history_dates_12m"] = ["2026-09-03"]
    elif defect == "future_latest":
        row["latest_record"]["日期"] = "2026-09-03"
    elif defect == "empty_industry":
        dependency["facts"]["industry_resolved"] = None
    else:
        dependency["historical_reconstruction"]["requested_as_of_date"] = "2026-09-03"
    unchanged = deepcopy(dependency)
    snapshot = source.FuturesSnapshotSkill().run(
        query="688041",
        symbol="688041",
        as_of_date=AS_OF,
        dependencies={"materials": dependency},
    )
    assert snapshot["facts"]["dependency_lineage"]["used"] is False
    assert snapshot["facts"]["contracts"] == []
    assert dependency == unchanged


def test_live_futures_still_uses_latest_price_without_historical_date_contract(
    monkeypatch,
):
    monkeypatch.setattr(
        source.ak,
        "futures_main_sina",
        lambda **kwargs: pd.DataFrame(
            [
                {"收盘价": 100},
                {"收盘价": 120},
            ]
        ),
    )
    rows = source.MaterialsSnapshotSkill._futures_rows((("铜", "CU0"),))
    assert rows[0]["latest_price"] == 120
    assert rows[0]["trend_pct_12m"] == 20


def test_futures_repeated_same_frame_produces_identical_historical_result(monkeypatch):
    monkeypatch.setattr(
        source.ak,
        "futures_main_sina",
        lambda **kwargs: pd.DataFrame(
            [
                {"日期": "2026-09-02", "收盘价": 100},
                {"日期": "2026-09-03", "收盘价": 999},
            ]
        ),
    )
    traces = [[], []]
    rows = [
        source.MaterialsSnapshotSkill._futures_rows(
            (("铜", "CU0"),),
            as_of_date=AS_OF,
            diagnostics=trace,
        )
        for trace in traces
    ]
    assert rows[0] == rows[1]
    assert traces[0] == traces[1]
