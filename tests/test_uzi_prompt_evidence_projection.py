"""Frozen prompt views retain facts and indexes; omissions are never data gaps."""

from copy import deepcopy

import pytest

from domain_packs.finance_ashare.subagents.uzi_392_analysis import _compact, _leaf_paths, _path_value
from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import _compact_mapping, _prompt_features
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import _compact as qualitative_compact


def actual_raw_shapes():
    # Shapes from 688041 recorded raw: recent_reports and business breakdown
    # rows used to become None at depth 5, despite existing primitive facts.
    return {"dimensions": {
        "6_research": {"data": {"recent_reports": [{
            "date": "2026-08-31", "title": "半年度研究", "broker": "研究机构", "rating": "增持",
        }]}},
        "5_chain": {"data": {"main_business_breakdown": [{"name": "处理器", "value": "95.4%"}]}},
        "22_deep_methods": {"data": {"unit_economics": {"metrics": {"gross_margin": {"value": 64.8}}}}},
    }}


def test_main_view_preserves_deep_real_rows_values_and_paths():
    raw = actual_raw_shapes()
    before = deepcopy(raw)
    view = _compact(raw)
    for path in (
        "dimensions.6_research.data.recent_reports[0].rating",
        "dimensions.5_chain.data.main_business_breakdown[0].value",
        "dimensions.22_deep_methods.data.unit_economics.metrics.gross_margin.value",
    ):
        assert path in _leaf_paths(view)
        assert _path_value(view, path) == _path_value(raw, path)
    assert raw == before


def test_long_time_series_keeps_latest_original_index_and_discloses_sampling():
    raw = {"history": [{"date": f"day-{i}", "price": i + 0.5} for i in range(1214)]}
    view = _compact(raw)
    assert len(view["history"]) == 1214
    assert _path_value(view, "history[1213].price") == 1213.5
    assert _path_value(view, "history[0].price") == 0.5
    paths = _leaf_paths(view)
    assert "history[1213].price" in paths
    assert not any(path.startswith("history[500]") for path in paths)


def test_large_mapping_does_not_drop_late_native_source_or_error_fields():
    data = {f"field_{i}": i for i in range(100)}
    data.update({"source": "official_archive", "error": "historical_provider_failure", "_timeout": True})
    assert _compact(data) == data


def test_qualitative_view_preserves_nested_facts_without_false_none():
    facts = {"cost": {"disclosure": {"rows": [{"share": 43.08, "hedged": False}]}}}
    assert qualitative_compact(facts) == facts


def test_roleplay_nested_mapping_preserves_values_and_scalar_types():
    facts = {"history": [{"flows": {"values": [1.5, None, False], "date": "2026-09-02"}}]}
    assert _compact_mapping(facts) == facts
    features = {"capital_flow": facts, "pe_ttm": 42.0}
    assert _prompt_features(features)["capital_flow"] == facts


def test_explicit_omission_metadata_is_not_a_citable_fact():
    from domain_packs.finance_ashare.subagents.uzi_input_views import compact_evidence_view

    metadata = []
    original = {"history": [{"x": i} for i in range(100)], "text": "正文" * 1000, "missing": None}
    result = compact_evidence_view(original, path="raw", truncations=metadata)
    assert result["missing"] is None
    assert {row["reason"] for row in metadata} == {"sequence_sample", "text_prefix"}
    history = next(row for row in metadata if row["path"] == "raw.history")
    assert history["original_length"] == 100
    assert history["retained_index_ranges"] == [[0, 14], [85, 99]]
    assert len(result["text"]) == 1200
    assert not any("prompt_omitted" in path for path in _leaf_paths(result))


def test_budget_exhaustion_is_explicit_host_input_error_not_fabricated_missing():
    from domain_packs.finance_ashare.subagents.uzi_input_views import (
        UziInputBudgetExceeded, compact_evidence_view,
    )

    raw = {"research": "事实" * 1000}
    before = deepcopy(raw)
    with pytest.raises(UziInputBudgetExceeded) as caught:
        compact_evidence_view(raw, max_characters=100)
    assert caught.value.code == "input_budget_exceeded"
    assert raw == before


def test_input_budget_is_not_a_model_output_repair_or_model_deadline():
    from types import SimpleNamespace
    from domain_packs.finance_ashare.subagents.uzi_input_views import UziInputBudgetExceeded
    from domain_packs.finance_ashare.subagents.uzi_llm_support import model_failure_record, UziStageFailures, model_failure_kind
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import _agent_repairable
    from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery, repairable_output_failures

    record = model_failure_record(UziInputBudgetExceeded("input_budget_exceeded"), stage="dimensions_1_5")
    assert record["kind"] == "pipeline_failure"
    assert "provider_error_code" not in record
    assert model_failure_kind(UziStageFailures([record])) == "pipeline_failure"
    output = {"stage": "dimensions_1_5", "kind": "agent_content_invalid"}
    assert repairable_output_failures([record, output]) == []
    issue = {"severity": "critical", "category": "agent-os-boundary", "dim": "agent_analysis", "failure_records": [record]}
    assert _agent_repairable([issue], SimpleNamespace(available=True)) is False
    recovery = native_recovery({"results": [{"self_review": {"issues": [issue]}}]})
    assert recovery["kind"] == "pipeline_failure" and recovery["action"] == "inspect"
    assert recovery["exhausted_stages"] == []
