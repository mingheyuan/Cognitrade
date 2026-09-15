from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest

from research_system.market_data.research_readiness import (
    PREFLIGHT_POLICY_VERSION,
    report_input_readiness,
    report_start_preflight,
)


def snapshot(dimension, refs=()):
    return {
        "dimension_id": dimension,
        "facts": {"_uzi_raw_result": {"data": {}}},
        "query_manifest": {"pit_mode": "historical_reconstruction"},
        "source_refs": list(refs),
    }


def native_finding():
    return {
        "gate_passed": False,
        "issues": [
            {"severity": "critical", "dimension": "19_contests", "check": "empty_dims"},
        ],
    }


def test_data_criticals_and_missing_research_are_agent_work_not_start_blockers():
    item = {"symbol": "688012.SH", "snapshots": [snapshot("19_contests")]}
    with patch(
        "research_system.market_data.research_readiness.evaluate_uzi_collection_gate",
        return_value=native_finding(),
    ):
        result = report_input_readiness([item], {"research_profile": "medium"})
    assert result["policy_version"] == PREFLIGHT_POLICY_VERSION
    assert result["can_start_analysis"] is True
    stock = result["stocks"][0]
    assert stock["data_check_passed"] is False
    assert stock["data_issues"][0]["severity"] == "critical"
    assert len(stock["evidence_gaps"]) == 6
    assert all(gap["blocking"] is False for gap in stock["evidence_gaps"])
    assert stock["input_blockers"] == []
    assert stock["evidence_check_scope"] == "frozen_input_source_inventory"
    assert stock["final_report_gate"] == "pending_agent_self_review"


def test_one_archived_url_is_not_a_two_source_start_gate():
    ref = {
        "url": "https://archive.example/policy",
        "source_kind": "research_document",
        "evidence_mode": "public_archive_reconstruction",
        "content": "Two usable facts in one document.",
        "content_hash": "a" * 64,
        "content_ref": "archive/body",
    }
    item = {"symbol": "688012.SH", "snapshots": [snapshot("13_policy", [ref])]}
    with patch(
        "research_system.market_data.research_readiness.evaluate_uzi_collection_gate",
        return_value=native_finding(),
    ):
        result = report_input_readiness([item], {"research_profile": "medium"})
    assert result["can_start_analysis"] is True
    assert not any(
        gap["dimension"] == "13_policy" for gap in result["stocks"][0]["evidence_gaps"]
    )


def test_recorded_legacy_research_rejection_is_reclassified_without_input_mutation():
    recorded = {
        "can_start_analysis": False,
        "symbol": "688012.SH",
        "evidence_gaps": [{"reason_code": "requires_two_cited_sources"}],
        "data_issues": native_finding()["issues"],
    }
    before = deepcopy(recorded)
    current = report_start_preflight(recorded)
    assert recorded == before
    assert current["recorded_can_start_analysis"] is False
    assert current["can_start_analysis"] is True
    assert current["data_issues"] == recorded["data_issues"]
    assert current["final_report_gate"] == "pending_agent_self_review"


@pytest.mark.parametrize(
    "item,reason",
    [
        ({"symbol": "688012.SH", "snapshots": []}, "frozen_snapshots_missing"),
        ({"snapshots": [snapshot("0_basic")]}, "frozen_stock_identity_missing"),
    ],
)
def test_missing_frozen_input_structure_remains_blocked(item, reason):
    with patch(
        "research_system.market_data.research_readiness.evaluate_uzi_collection_gate",
        return_value=native_finding(),
    ):
        result = report_input_readiness([item], {})
    assert result["can_start_analysis"] is False
    assert reason in [
        row["reason_code"] for row in result["stocks"][0]["input_blockers"]
    ]


def test_unknown_preflight_policy_and_explicit_input_blocker_fail_closed():
    for recorded in (
        {"policy_version": "future-policy"},
        {"input_blockers": [{"reason_code": "unsafe_input"}]},
    ):
        assert report_start_preflight(recorded)["can_start_analysis"] is False
    assert report_input_readiness([], {})["can_start_analysis"] is False
