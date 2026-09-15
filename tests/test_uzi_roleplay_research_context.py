"""Native Task2.5 precedes and informs Task3; research is not raw data."""

import ast
import json
import socket
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from domain_packs.finance_ashare.subagents import uzi_agent_roleplay as roleplay
from tests.test_uzi_392_analysis import _lite_snapshots, _LiteAgentProvider
from tests.test_uzi_deep_roleplay import _panel, _RoleplayProvider
from tests.test_uzi_synthesis_qualitative_context import PASS_REVIEW, research

MARKER = "EXECUTED_QUALITATIVE_RESEARCH_CONTEXT="


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Roleplay regression must not fetch business data")
    monkeypatch.setattr(socket.socket, "connect", denied)


def execute(research_outputs, reviews):
    order, received = [], []
    output_iter = iter(research_outputs)

    def qualitative(*args, **kwargs):
        result = next(output_iter)
        order.append("research_completed")
        return deepcopy(result)

    def panel(*args, **kwargs):
        order.append("roleplay_started")
        received.append(deepcopy(kwargs.get("research_context")))
        assert "987654321" not in json.dumps(kwargs["features"])
        return {"roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
                "per_investor_override": {}}

    rows = _lite_snapshots()
    before = deepcopy(rows)
    with (patch.object(host, "apply_qualitative_deep_dive", side_effect=qualitative),
          patch.object(host, "build_native_panel_overrides", side_effect=panel),
          patch.object(host, "run_native_self_review", side_effect=reviews),
          patch.object(host, "_validate_agent_analysis", return_value=[])):
        result = host.execute_native_analysis(rows, ["600000"], profile="lite", full_agent_workflow=True,
                                              llm_provider=_LiteAgentProvider())
    assert rows == before
    return result, order, received


def test_actual_full_workflow_finishes_research_before_passing_it_to_roleplay():
    output = research()
    _, order, received = execute([output], [PASS_REVIEW])
    assert order == ["research_completed", "roleplay_started"]
    assert received == [host._qualitative_research_context(output)]


def test_repaired_research_does_not_reuse_stale_in_memory_roleplay():
    initial, revised = research(status="incomplete"), research()
    initial["qualitative_agent_execution"]["failure_records"] = [
        {"stage": "qualitative_A", "kind": "agent_content_invalid", "error_type": "StructuredOutputError"}]
    issue = {"severity": "critical", "category": "agent-os-boundary", "dim": "qualitative_deep_dive",
             "issue": "六维定性研究未满足 UZI 证据门", "evidence": "status=incomplete", "suggested_fix": "完成本组"}
    _, order, received = execute([initial, revised], [
        {"issues": [issue], "critical_count": 1, "warning_count": 0, "info_count": 0}, PASS_REVIEW,
    ])
    assert order == ["research_completed", "research_completed", "roleplay_started"]
    assert received == [host._qualitative_research_context(revised)]


def test_roleplay_requests_and_cache_use_exact_research_as_non_fact_context(tmp_path):
    provider = _RoleplayProvider()
    provider.settings = SimpleNamespace(workspace_root=tmp_path, uzi_llm_max_concurrency=2)
    features = {"price": 10.0, "industry": "semiconductor"}
    original = deepcopy(features)
    context = host._qualitative_research_context(research())
    original_context = deepcopy(context)
    for _ in range(2):
        result = roleplay.apply_deep_agent_roleplay(_panel("deep"), features=features,
            llm_provider=provider, required=True, research_context=context)
        assert result["roleplay"]["status"] == "completed"
    assert len(provider.system_prompts) == 5, "Identical research did not reuse completed roleplay groups"
    task3 = (Path(__file__).resolve().parents[1]
             / "domain_packs/finance_ashare/vendor/uzi_3_9_2/skill/references/task3-agent-evaluation.md").read_text()
    for prompt in provider.system_prompts:
        assert task3 in prompt
        assert json.loads(prompt.split(MARKER, 1)[1].split("\n", 1)[0]) == context
        actual_features = json.loads(prompt.split("FROZEN_FEATURE_SNAPSHOT=", 1)[1])
        assert "987654321" not in json.dumps(actual_features)
        assert "研究推断，不是新增原始事实" in prompt
    revised = host._qualitative_research_context(research(revision="updated"))
    roleplay.apply_deep_agent_roleplay(_panel("deep"), features=features,
        llm_provider=provider, required=True, research_context=revised)
    assert len(provider.system_prompts) == 10, "Revised research reused stale roleplay"
    assert features == original and context == original_context


def test_narrative_only_repair_keeps_completed_research_and_roleplay():
    issue = {"severity": "critical", "category": "agent-os-boundary", "dim": "synthesis",
             "issue": "综合结论需要输出修复", "suggested_fix": "修复主结论"}
    _, order, received = execute([research()], [
        {"issues": [issue], "critical_count": 1, "warning_count": 0, "info_count": 0}, PASS_REVIEW,
    ])
    assert order == ["research_completed", "roleplay_started"]
    assert len(received) == 1


@pytest.mark.parametrize("failure", [TimeoutError("llm_request_timeout: 5s"),
                                     RuntimeError("input_budget_exceeded: frozen research too large")])
def test_fatal_research_does_not_start_downstream_model_wave(failure):
    provider = _LiteAgentProvider()
    with (patch.object(host, "apply_qualitative_deep_dive", side_effect=failure),
          patch.object(host, "build_native_panel_overrides") as panel,
          patch.object(host, "_generate_agent_analysis") as narrative):
        result = host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                                               full_agent_workflow=True, llm_provider=provider)
    panel.assert_not_called()
    narrative.assert_not_called()
    assert result["report_allowed"] is False


@pytest.mark.parametrize("control", ["cancel", "deadline"])
def test_global_control_after_research_prevents_roleplay(control):
    from domain_packs.finance_ashare.subagents.uzi_llm_support import (
        UziRequestCancelled,
    )
    clock, cancellation = [0.0], Event()

    def complete(*args, **kwargs):
        if control == "cancel":
            cancellation.set()
        else:
            clock[0] = 20.0
        return research()

    with (patch.object(host, "apply_qualitative_deep_dive", side_effect=complete) as qualitative,
          patch.object(host, "build_native_panel_overrides") as panel,
          patch.object(host, "run_native_self_review") as review,
          patch.object(host, "monotonic", side_effect=lambda: clock[0]),
          patch("domain_packs.finance_ashare.subagents.uzi_llm_support.monotonic", side_effect=lambda: clock[0])):
        if control == "cancel":
            with pytest.raises(UziRequestCancelled):
                host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                    full_agent_workflow=True, llm_provider=_LiteAgentProvider(), cancellation=cancellation)
        else:
            result = host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                full_agent_workflow=True, llm_provider=_LiteAgentProvider(), deadline_at=10.0)
            assert result["report_allowed"] is False
    panel.assert_not_called()
    review.assert_not_called()
    qualitative.assert_called_once()


def native_constants(filename):
    source = roleplay.PERSONAS_DIR.parent.parent / "scripts/lib" / filename
    return {node.target.id: ast.literal_eval(node.value)
            for node in ast.parse(source.read_text()).body if isinstance(node, ast.AnnAssign)}


def test_actual_group_requests_keep_native_yaml_voice_and_knowledge():
    templates = native_constants("investor_personas.py")["PERSONAS"]
    knowledge = native_constants("investor_knowledge.py")
    votes = _panel("deep")["votes"]
    for label, rows in roleplay._nonempty_batches(votes):
        prompt = roleplay._batch_user_prompt(label, rows)
        context = json.loads(prompt.split("PINNED_PERSONA_CONTEXT=", 1)[1].split("\n", 1)[0])
        for vote in rows:
            investor_id = vote["investor_id"]
            entry = context[investor_id]
            path = roleplay.PERSONAS_DIR / f"{investor_id}.yaml"
            assert entry["yaml"] == (path.read_text() if path.is_file() else None)
            assert entry["voice_templates"] == templates.get(investor_id)
            assert entry["market_scope"] == knowledge["MARKET_SCOPE"].get(investor_id)
            assert entry["industry_affinity"] == knowledge["INDUSTRY_AFFINITY"].get(investor_id)
            assert entry["historical_knowledge"] == json.loads(json.dumps(
                knowledge["KNOWN_HOLDINGS"].get(investor_id)))
        assert "不能证明当前或目标历史日持仓" in prompt


def test_native_roleplay_receives_frozen_raw_summary_and_tracks_changes(tmp_path):
    provider = _RoleplayProvider()
    provider.settings = SimpleNamespace(workspace_root=tmp_path, uzi_llm_max_concurrency=2)
    raw = {"dimensions": {"8_materials": {"data": {"cost_driver": "上游成本说明", "estimate": None},
                                            "error": "source_partial", "_timeout": False, "source": "frozen"}},
           "fund_managers": [{"name": str(index)} for index in range(50)]}
    original = deepcopy(raw)
    result = roleplay.apply_deep_agent_roleplay(_panel("deep"), features={"price": 10},
        llm_provider=provider, required=True, raw=raw)
    assert result["roleplay"]["status"] == "completed"
    for prompt in provider.system_prompts:
        view = json.loads(prompt.split("FROZEN_RAW_SUMMARY=", 1)[1].split("\n", 1)[0])
        assert view["raw"]["dimensions"] == raw["dimensions"]
        assert view["projection"]["truncations"]
    raw["dimensions"]["8_materials"]["data"]["cost_driver"] = "已修订成本说明"
    roleplay.apply_deep_agent_roleplay(_panel("deep"), features={"price": 10},
        llm_provider=provider, required=True, raw=raw)
    assert len(provider.system_prompts) == 10
    raw["dimensions"]["8_materials"]["data"]["cost_driver"] = "上游成本说明"
    assert raw == original
