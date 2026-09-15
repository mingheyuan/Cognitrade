"""Replay the observed partial-research -> premature narrative failure chain."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from domain_packs.finance_ashare.subagents.uzi_llm_support import UziStageFailures
from tests.test_uzi_392_analysis import _lite_snapshots, _LiteAgentProvider
from tests.test_uzi_model_failure_classification import _scoped_failure_review


def failure(stage, kind="agent_content_invalid"):
    return {"stage": stage, "kind": kind, "error_type": "ReplayFailure"}


def test_repair_research_then_repair_panel_then_generate_once():
    order = []
    qual_count = role_count = 0

    def research(*args, **kwargs):
        nonlocal qual_count
        qual_count += 1
        ok = qual_count == 2
        order.append("research_ok" if ok else "research_invalid")
        return {"qualitative_agent_execution": {"status": "completed" if ok else "incomplete",
            "agent_reviewed": ok,
            "failure_records": [] if ok else [failure("qualitative_A")],
            "errors": [] if ok else ["A · Macro-Policy:retrieved_at missing"]}}

    def panel(*args, **kwargs):
        nonlocal role_count
        assert qual_count == 2
        role_count += 1
        ok = role_count == 2
        order.append("panel_ok" if ok else "panel_invalid")
        return {"roleplay": {"status": "completed" if ok else "incomplete", "agent_reviewed": ok,
                            "failure_records": [] if ok else [failure("roleplay_G+H+I")]}}

    def narrative(*args, **kwargs):
        assert role_count == 2
        order.append("narrative")
        return {"agent_reviewed": True}

    rows = _lite_snapshots()
    original = deepcopy(rows)
    with (patch.object(host, "apply_qualitative_deep_dive", side_effect=research),
          patch.object(host, "build_native_panel_overrides", side_effect=panel),
          patch.object(host, "_generate_agent_analysis", side_effect=narrative),
          patch.object(host, "_validate_agent_analysis", return_value=[]),
          patch.object(host, "run_native_self_review", side_effect=_scoped_failure_review)):
        result = host.execute_native_analysis(rows, ["600000"], profile="medium",
            full_agent_workflow=True, llm_provider=_LiteAgentProvider())
    assert order == ["research_invalid", "research_ok", "panel_invalid", "panel_ok", "narrative"]
    assert result["report_allowed"] and rows == original


@pytest.mark.parametrize("failed_stage", ["qualitative_A", "roleplay_G+H+I", "narrative_core"])
def test_exhausted_dependency_prevents_futile_new_wave(failed_stage):
    order = []
    terminal = failure(failed_stage, "llm_transport_timeout")
    def research(*args, **kwargs):
        order.append("research")
        failed = failed_stage.startswith("qualitative")
        return {"qualitative_agent_execution": {"status": "incomplete" if failed else "completed",
            "agent_reviewed": not failed,
            "failure_records": [terminal, failure("qualitative_B")] if failed else []}}
    def panel(*args, **kwargs):
        order.append("panel")
        failed = failed_stage.startswith("roleplay")
        return {"roleplay": {"status": "incomplete" if failed else "completed", "agent_reviewed": not failed,
                             "failure_records": [terminal, failure("roleplay_A+B")] if failed else []}}
    def narrative(*args, **kwargs):
        order.append("narrative")
        raise UziStageFailures([terminal])
    with (patch.object(host, "apply_qualitative_deep_dive", side_effect=research),
          patch.object(host, "build_native_panel_overrides", side_effect=panel),
          patch.object(host, "_generate_agent_analysis", side_effect=narrative),
          patch.object(host, "run_native_self_review", side_effect=_scoped_failure_review)):
        result = host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
            full_agent_workflow=True, llm_provider=_LiteAgentProvider())
    expected = ["research"] if failed_stage.startswith("qualitative") else (
        ["research", "panel"] if failed_stage.startswith("roleplay") else ["research", "panel", "narrative"])
    assert order == expected
    assert terminal in result["recovery"]["failure_records"]
    assert not result["report_allowed"]
