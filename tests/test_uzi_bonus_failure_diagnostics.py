"""Top-level native bonus failures retain their value, but are not successes."""

from copy import deepcopy

import pytest

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _project_uzi_symbol_snapshot,
)


def project(value):
    source = {
        "ticker": "688041", "full": "688041.SH", "market": "A",
        "dimensions": {}, "similar_stocks": deepcopy(value),
        "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
    }
    before = deepcopy(source)
    result = _project_uzi_symbol_snapshot(
        query="688041.SH", requested_symbol="688041.SH",
        snapshot_type="similar_stocks", symbol_result=source,
    )
    assert source == before
    assert result["facts"]["_uzi_top_level_fields"]["similar_stocks"] == {
        "present": True, "value": value,
    }
    assert result["facts"]["similar_stocks"] == value
    return result


def test_pipeline_timeout_bonus_is_not_mislabeled_as_available():
    # Native pipeline _apply_outcome timeout shape, no invented market values.
    value = {
        "data": {}, "source": "pipeline:timeout", "fallback": True,
        "_pipeline": {
            "dim_key": "similar_stocks", "quality": "error", "data_gaps": [],
            "latency_ms": None, "fetched_at": None, "cached": False,
            "top_level_fields": {}, "error": "fetcher timeout > 30s",
        },
    }
    result = project(value)
    assert result["quality_status"] == "data_gap"
    assert result["degraded"] is True
    assert result["degrade_reason"] == value["_pipeline"]["error"]
    trace = result["facts"]["_uzi_source_trace"][0]
    assert trace["source_id"] == "pipeline:timeout"
    assert trace["status"] == "data_gap"
    assert trace["fallback"] is True


@pytest.mark.parametrize("value", [[], [{"code": "test-only", "name": "fixture"}]])
def test_native_bonus_list_semantics_remain_unchanged(value):
    result = project(value)
    assert result["quality_status"] == "available"
    assert result["degraded"] is False
    assert result["facts"]["_uzi_source_trace"][0]["fallback"] is False
