from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _point_in_time_uzi_projection,
)
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from tests.market_data import test_uzi_snapshot_adapter as adapter_fixtures
from tests.market_data.test_pit_core import _query_proof


def _adapt(raw, *, historical=False):
    snapshot = adapter_fixtures.UziSnapshotAdapterTests._snapshot("kline", raw)
    snapshot["source_trace"] = [
        {"provider_id": "raw_kline", "status": "available", "count": 0}
    ]
    as_of = date(2026, 8, 20)
    if historical:
        as_of = date(2026, 8, 19)
        snapshot["historical_reconstruction"] = {
            "safe": True,
            "requested_as_of_date": as_of.isoformat(),
            "strategy": "dated_series",
            "window_start": "2026-01-01",
        }
    return UziSnapshotAdapter().adapt(
        snapshot,
        symbol="600519.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=as_of,
    )


@pytest.mark.parametrize("historical", [True, False])
def test_kline_success_or_generic_empty_query_does_not_prove_actions_or_factors(
    historical,
):
    raw = {
        "ohlcv": [{"date": "2026-08-19", "close": 100}],
        "ohlcv_adjustment": "none",
        "query_status": {"status": "success"},
        "source_coverage": {
            "corporate_actions": "available",
            "adjustment_factors": "available",
        },
    }
    result = _adapt(raw, historical=historical)
    queries = result.query_manifest.get("field_queries", {})
    assert "corporate_actions" not in queries
    assert "adjustment_factors" not in queries
    assert result.payload["corporate_actions"] == []  # report compatibility only
    assert result.payload["adjustment_factors"] == []


@pytest.mark.parametrize("historical", [True, False])
@pytest.mark.parametrize("status", ["success", "timeout"])
def test_only_real_field_scoped_query_results_are_preserved(historical, status):
    proof = _query_proof("corporate_actions", 0)
    proof["sources"][0]["status"] = status
    raw = {
        "ohlcv": [{"date": "2026-08-19", "close": 100}],
        "ohlcv_adjustment": "none",
        "corporate_actions": [],
        "field_queries": {"corporate_actions": proof},
    }
    before = deepcopy(raw)
    result = _adapt(raw, historical=historical)
    actual = result.query_manifest["field_queries"]["corporate_actions"]
    assert actual == {**proof, "query_scope": "field", "field": "corporate_actions"}
    assert "adjustment_factors" not in result.query_manifest["field_queries"]
    assert raw == before


@pytest.mark.parametrize(
    "history,expected",
    [
        ({}, None),
        ({"adjust": None}, None),
        ({"adjust": ""}, "none"),
        ({"adjust": "qfq"}, "qfq"),
    ],
)
def test_missing_adjust_metadata_is_not_declared_unadjusted(history, expected):
    result = _point_in_time_uzi_projection("kline", {"history": history})
    assert result["ohlcv_adjustment"] == expected
