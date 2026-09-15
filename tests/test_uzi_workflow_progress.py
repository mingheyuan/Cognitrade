"""Research/roleplay counts must not masquerade as near-complete reports."""
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents import uzi_agent_roleplay as roleplay
from domain_packs.finance_ashare.subagents import (
    uzi_qualitative_deep_dive as qualitative,
)
from tests.test_uzi_deep_roleplay import _panel, _RoleplayProvider
from tests.test_uzi_qualitative_deep_dive import _Provider, _rows


def test_standalone_research_progress_counts_validated_groups():
    progress = []
    result = qualitative.apply_qualitative_deep_dive({}, snapshot_rows=_rows(),
        llm_provider=_Provider(), progress_callback=progress.append)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    validated = [row for row in progress if row.get("stage_phase") == "validated"]
    assert [row["completed_stages"] for row in validated] == [1, 2, 3]
    assert all(row["progress_scope"] == "qualitative_groups" for row in validated)


def test_roleplay_reports_validated_and_cached_groups(tmp_path):
    provider = _RoleplayProvider()
    provider.settings = SimpleNamespace(workspace_root=tmp_path, uzi_llm_max_concurrency=4)
    for cache_hit in (False, True):
        progress = []
        result = roleplay.apply_deep_agent_roleplay(_panel("deep"), features={},
            llm_provider=provider, progress_callback=progress.append)
        assert result["roleplay"]["status"] == "completed"
        validated = [row for row in progress if row.get("stage_phase") == "validated"]
        assert [row["completed_stages"] for row in validated] == [1, 2, 3, 4, 5]
        assert all(row["cache_hit"] is cache_hit for row in validated)
    assert len(provider.system_prompts) == 5


def test_display_failure_cannot_change_native_roleplay_result():
    def broken_display(_):
        raise OSError("display unavailable")
    result = roleplay.apply_deep_agent_roleplay(_panel("deep"), features={},
        llm_provider=_RoleplayProvider(), progress_callback=broken_display)
    assert result["roleplay"]["status"] == "completed"
    assert result["roleplay"]["completed_group_count"] == 5


@pytest.mark.parametrize("failure,expected", [
    (TimeoutError("llm_request_timeout"), "预算"),
    (RuntimeError("frozen_citations_unavailable"), "数据中心"),
])
def test_research_rejection_does_not_promise_unconditional_retry(monkeypatch, failure, expected):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(qualitative, "_evaluate_group", fail)
    progress = []
    qualitative.apply_qualitative_deep_dive({}, snapshot_rows=_rows(),
        llm_provider=_Provider(), progress_callback=progress.append)
    rejected = [row for row in progress if row.get("stage_phase") == "failed"]
    assert len(rejected) == 3
    assert all(expected in row["next_action"] for row in rejected)
    assert all(row["completed_stages"] == 0 for row in rejected)


def test_full_workflow_phase_counts_do_not_jump_back_after_research(monkeypatch):
    from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
    from tests.test_uzi_392_analysis import _lite_snapshots, _LiteAgentProvider
    from tests.test_uzi_synthesis_qualitative_context import PASS_REVIEW, research

    def groups(count, name, result):
        def run(*args, **kwargs):
            callback = kwargs.get("progress_callback")
            assert callable(callback), f"{name} has no validated-stage progress"
            for completed in range(1, count + 1):
                callback({"stage": name, "stage_phase": "validated", "completed_stages": completed,
                          "stage_total": count, "current_action": f"{name} {completed}/{count}"})
            return result
        return run

    monkeypatch.setattr(host, "apply_qualitative_deep_dive", groups(3, "research", research()))
    monkeypatch.setattr(host, "build_native_panel_overrides", groups(5, "roleplay", {
        "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        "per_investor_override": {}}))
    monkeypatch.setattr(host, "run_native_self_review", lambda **kwargs: PASS_REVIEW)
    progress = []
    host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium", full_agent_workflow=True,
                                 llm_provider=_LiteAgentProvider(), progress_callback=progress.append)
    tracked = [row for row in progress if row.get("progress_scope") == "stock_agent_stages"]
    assert tracked
    assert [row["progress_percent"] for row in tracked] == sorted(row["progress_percent"] for row in tracked)
    research_done = next(row for row in tracked if row.get("current_action") == "research 3/3")
    assert research_done["progress_percent"] < 40
    assert research_done["workflow_completed_stages"] == 3
    assert research_done["workflow_total_stages"] > 8
    assert {row["workflow_phase"] for row in tracked} == {"qualitative", "roleplay", "analysis"}
