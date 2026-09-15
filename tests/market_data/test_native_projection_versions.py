from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from research_system.market_data.native_projection import (
    LEGACY_PROJECTION_VERSION,
    PROJECTION_VERSION,
    V2_PROJECTION_VERSION,
    native_payload,
    pin_native_workflow,
    project_native_result,
)
from research_system.market_data.uzi_gate import evaluate_uzi_collection_gate
from research_system.strategies.runtime_pins import deployed_workflow_pins
from tests.market_data.test_uzi_gate import _native_rows


def _snapshot(*, outcome="unavailable", data=None):
    # A normal no-login upstream result is nonempty even with no cube holdings.
    data = (
        data
        if data is not None
        else {
            "xueqiu_cubes": [],
            "xueqiu_meta": {"login_required": True},
            "summary": {"xueqiu_login_required": True, "xueqiu_cubes_total": 0},
            "fallback_queries": ["fixture: query public contest disclosures"],
            "_note": "登录来源未启用，未取得组合持仓",
        }
    )
    return SimpleNamespace(
        dimension="19_contests",
        status="data_gap",
        source_version="fixture-source",
        payload_json={
            "raw": deepcopy(data),
            "_uzi_raw_result": {
                "data": deepcopy(data),
                "source": "fixture",
                "fallback": True,
            },
        },
        query_manifest_json={
            "collection_outcome": outcome,
            "native_quality_status": "partial",
            "pit_safe": True,
            "degradation": {
                "reason_code": "strict_field_gap",
                "message": "持仓明细未采齐",
            },
        },
        validation_json={},
    )


@pytest.mark.parametrize("outcome", ["unavailable", "retryable_failure", "partial"])
def test_v3_retains_actual_native_metadata_independently_of_collection_gap(outcome):
    snapshot = _snapshot(outcome=outcome)
    original = deepcopy(vars(snapshot))
    result = project_native_result(snapshot)
    assert result["data"] == snapshot.payload_json["_uzi_raw_result"]["data"]
    assert result["data"]["xueqiu_cubes"] == []
    assert result["_market_data_gap"]["is_research_fact"] is False
    assert "error" not in result
    assert "_timeout" not in result
    assert vars(snapshot) == original


def test_v1_v2_projection_rules_are_not_changed_by_new_default():
    snapshot = _snapshot(outcome="retryable_failure")
    snapshot.query_manifest_json["provider_failure"] = {
        "message": "legacy-classified-failure"
    }
    expected = {
        "data": {},
        "source": "fixture",
        "fallback": True,
        "_pipeline": {
            "dim_key": "19_contests",
            "quality": "partial",
            "data_gaps": [],
            "error": None,
        },
    }
    assert (
        project_native_result(snapshot, version=LEGACY_PROJECTION_VERSION) == expected
    )
    assert project_native_result(snapshot, version=V2_PROJECTION_VERSION) == {
        **expected,
        "error": "legacy-classified-failure",
    }
    assert (
        pin_native_workflow({"snapshot_projection_version": V2_PROJECTION_VERSION})[
            "snapshot_projection_version"
        ]
        == V2_PROJECTION_VERSION
    )
    assert pin_native_workflow({})["snapshot_projection_version"] == PROJECTION_VERSION
    assert deployed_workflow_pins()["snapshot_projection_version"] == PROJECTION_VERSION


def test_original_envelope_is_restored_only_for_v3_when_raw_copy_was_empty():
    snapshot = _snapshot(outcome="partial")
    snapshot.payload_json["raw"] = {}
    assert native_payload(snapshot, version=V2_PROJECTION_VERSION) == {}
    assert native_payload(snapshot) == snapshot.payload_json["_uzi_raw_result"]["data"]
    # Actual source recovery data outside the envelope remains available too.
    snapshot.payload_json["raw"] = {
        **snapshot.payload_json["_uzi_raw_result"]["data"],
        "recovered_disclosures": [{"title": "fixture disclosure"}],
    }
    assert native_payload(snapshot)["recovered_disclosures"] == [
        {"title": "fixture disclosure"}
    ]


def test_historical_gap_only_snapshot_remains_empty_and_never_invents_contest_summary():
    snapshot = _snapshot()
    snapshot.payload_json = {
        "availability": "historically_unavailable",
        "reason_code": "historical_contest_archive_unavailable",
        "message": "没有目标日期的实盘组合归档",
        "excluded_from_scoring": True,
    }
    snapshot.query_manifest_json["degradation"] = {
        "reason_code": "historical_contest_archive_unavailable",
        "message": "没有目标日期的实盘组合归档",
    }
    result = project_native_result(snapshot)
    assert result["data"] == {}
    assert (
        result["_market_data_gap"]["reason_code"]
        == "historical_contest_archive_unavailable"
    )
    assert result["_market_data_gap"]["is_research_fact"] is False
    assert "error" not in result
    rows = _native_rows()
    rows["19_contests"] = snapshot
    gate = evaluate_uzi_collection_gate(rows, profile="medium")
    assert gate["gate_passed"] is False
    assert any(
        issue["dimension"] == "19_contests" and issue["severity"] == "critical"
        for issue in gate["issues"]
    )


@pytest.mark.parametrize(
    "pit_metadata",
    [
        {"pit_safe": False},
        {
            "pit_safe": True,
            "pit_mode": "live_observation",
            "requested_as_of_date": "2026-09-02",
            "observed_local_date": "2026-09-06",
        },
    ],
)
def test_v3_cannot_reintroduce_current_observations_into_historical_input(pit_metadata):
    snapshot = _snapshot()
    snapshot.query_manifest_json.update(pit_metadata)
    result = project_native_result(snapshot)
    assert result["data"] == {}
    assert result["_market_data_gap"]["reason_code"] == "point_in_time_input_rejected"
    assert "error" not in result


def test_collection_gate_uses_the_requested_projection_version_consistently():
    rows = _native_rows()
    rows["19_contests"] = _snapshot()
    old = evaluate_uzi_collection_gate(rows, projection_version=V2_PROJECTION_VERSION)
    current = evaluate_uzi_collection_gate(rows, projection_version=PROJECTION_VERSION)
    assert old["gate_passed"] is False
    assert any(issue["dimension"] == "19_contests" for issue in old["issues"])
    assert current["gate_passed"] is True


def test_native_errors_remain_in_their_original_locations():
    snapshot = _snapshot(data={})
    snapshot.payload_json["_uzi_raw_result"]["_pipeline"] = {
        "quality": "error",
        "error": "worker failed",
    }
    result = project_native_result(snapshot)
    assert result["_pipeline"]["error"] == "worker failed"
    assert "error" not in result
    snapshot.payload_json["_uzi_raw_result"].update(
        error="actual upstream error", _timeout=True
    )
    result = project_native_result(snapshot)
    assert result["error"] == "actual upstream error"
    assert result["_timeout"] is True
