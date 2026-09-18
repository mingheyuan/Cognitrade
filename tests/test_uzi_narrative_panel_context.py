"""Task3.3/4 must see actual persona reasoning and native rule messages."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from tests.test_uzi_392_analysis import _lite_snapshots
from tests.test_uzi_full_skill_closure import DebateProvider
from tests.uzi_prompt_wire import decode_evidence, decode_paths


def projection(request, marker):
    value = json.loads(request["system_prompt"].split(marker, 1)[1].split("\n", 1)[0])
    if marker == "FROZEN_STAGE_EVIDENCE=":
        return decode_evidence(value)
    if marker == "ALLOWED_LEAF_EVIDENCE_PATHS=":
        return decode_paths(value)
    return value


@pytest.mark.parametrize("pass_key,fail_key", [("pass", "fail"), ("pass_rules", "fail_rules")])
def test_methodology_prose_and_rules_survive_with_original_identity(pass_key, fail_key):
    rows = [{"investor_id": f"seat-{i}", "group": "A", "score": i,
             "reasoning": "长推理原文" * 500, "comment": "单独的逐人结论",
             pass_key: [{"name": f"rule-{j}", "msg": f"实际指标{j}和通过条件", "weight": j,
                         "actual": None, "passed": True} for j in range(40)],
             fail_key: [{"id": "roe", "msg": "实际ROE低于门槛；缺数不是零", "actual": None}]}
            for i in range(66)]
    panel = {"investors": rows, "panel_consensus": 17.2}
    before = deepcopy(panel)
    result = host._compact_panel_for_agent(panel)
    assert panel == before and result["seat_count"] == 66
    assert result["panel_consensus"] == 17.2
    for original, received in zip(rows, result["investors"], strict=True):
        for field in ("reasoning", "comment", pass_key, fail_key):
            assert received.get(field) == original[field], f"Task4 lost {field}"


def prepared_input():
    host._initialize_native_imports()
    prepared = host._prepare_native_computation(host.build_uzi_raw(_lite_snapshots(), "600000"),
                                               profile="medium", school=None)
    baseline = prepared["panel"]
    original = baseline["investors"][0]
    investor_id = original["investor_id"]
    overrides = {investor_id: {"signal": "bullish", "score": 99,
                              "reasoning": "真实逐人判断不同于规则分；请在整体评审保留分歧。",
                              "comment": "保留这段逐人评论"}}
    merged = host._native_panel_with_overrides(baseline, overrides)
    return prepared, merged, investor_id


def test_native_decision_fields_are_not_silently_removed():
    _prepared, merged, _ = prepared_input()
    projected = host._compact_panel_for_agent(merged)
    for original, row in zip(merged["investors"], projected["investors"], strict=True):
        for key in set(original) - {"avatar"}:
            assert key in row, f"Lost native decision field {key}"
            assert row[key] == original[key]
    assert any(row.get("mandate") == "short" for row in projected["investors"])


def execute(provider, prepared, merged):
    return host._generate_agent_analysis(
        prepared["raw_after_derivation"], prepared["dimensions"], merged,
        profile="medium", school=None, llm_provider=provider, recovery_tasks=[], review_feedback=[],
        full_agent_workflow=True, rule_panel=prepared["panel"],
    )


def test_actual_narrative_request_contains_merged_rules_reasoning_and_original_scores(tmp_path):
    prepared, merged, investor_id = prepared_input()
    original = deepcopy((prepared, merged))
    provider = DebateProvider(tmp_path)
    execute(provider, prepared, merged)
    assert (prepared, merged) == original
    request = next(row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload)
    received = projection(request, "EXECUTED_PANEL_METHODOLOGY_CONTEXT=")
    for source, row in zip(merged["investors"], received, strict=True):
        for key in ("reasoning", "comment", "pass", "fail", "what_would_change_my_mind"):
            if key in source:
                assert row[key] == source[key]
        assert row["investor_id"] == source["investor_id"]
    factual_panel = projection(request, "FROZEN_STAGE_EVIDENCE=")["panel"]["investors"]
    assert [row["mandate"] for row in factual_panel] == [row["mandate"] for row in merged["investors"]]
    context = projection(request, "RULE_PANEL_COMPARISON_CONTEXT=")
    assert context["rule_scores_by_investor_id"][investor_id] == prepared["panel"]["investors"][0]["score"]
    assert "差 > 30 分" in request["system_prompt"]
    assert "不是新增市场事实" in request["system_prompt"]
    outcome = projection(request, "NATIVE_SYNTHESIS_OUTCOME_CONTEXT=")
    _, expected = host._synthesize_native(
        prepared["raw_after_derivation"], prepared["dimensions"], merged,
        agent_analysis=None, profile="medium", school=None,
    )
    assert outcome["source_commit"] == host.UZI_SOURCE_COMMIT
    for key, value in outcome["synthesis"].items():
        assert value == expected[key]
    for side, participant in outcome["debate_participants"].items():
        for key, value in expected["debate"][side].items():
            assert participant[key] == value
    assert "骨架统计" in request["system_prompt"]
    assert "不能交换多空两侧" in request["system_prompt"]
    assert not request.get("tools")
    for other in provider.requests:
        if other is not request:
            assert "RULE_PANEL_COMPARISON_CONTEXT=" not in other["system_prompt"]
            assert "EXECUTED_PANEL_METHODOLOGY_CONTEXT=" not in other["system_prompt"]
            assert "NATIVE_SYNTHESIS_OUTCOME_CONTEXT=" not in other["system_prompt"]
    paths = projection(request, "ALLOWED_LEAF_EVIDENCE_PATHS=")
    assert all("reasoning" not in path and "comment" not in path for path in paths if path.startswith("panel."))
    assert "native_synthesis_outcome.synthesis.panel_consensus" in paths
    assert not any(path.startswith(("panel.panel_consensus", "panel.signal_distribution",
                                   "panel.school_scores", "panel.consensus_formula")) for path in paths)


def test_native_computation_is_citable_and_archived_but_not_a_market_price(tmp_path):
    prepared, merged, _ = prepared_input()
    before = deepcopy((prepared, merged))
    provider = DebateProvider(tmp_path)
    result = execute(provider, prepared, merged)
    request = next(row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload)
    evidence = projection(request, "FROZEN_STAGE_EVIDENCE=")
    outcome = evidence["native_synthesis_outcome"]
    paths = projection(request, "ALLOWED_LEAF_EVIDENCE_PATHS=")
    assert set(evidence["panel"]) == {"investors"}
    assert set(host._leaf_paths({"native_synthesis_outcome": outcome})).issubset(paths)
    assert result["grounding"]["panel_insights"] == [
        "native_synthesis_outcome.synthesis.panel_consensus"]
    recorded = result["grounding"]["native_synthesis_outcome"]
    assert recorded["content"] == outcome
    assert recorded["content_hash"] == sha256(host._compact_json(outcome).encode()).hexdigest()
    assert recorded["scope"] == "pinned_native_computation_not_market_observation"
    for other in provider.requests:
        if other is not request:
            assert "native_synthesis_outcome" not in other["system_prompt"]
    assert (prepared, merged) == before


def test_old_skeleton_reference_requires_narrative_repair_not_silent_relabel(tmp_path, monkeypatch):
    from domain_packs.finance_ashare.subagents import uzi_llm_support
    clock = [host.monotonic()]
    monkeypatch.setattr(host, "monotonic", lambda: clock[0])
    monkeypatch.setattr(uzi_llm_support, "monotonic", lambda: clock[0])

    class StaleFirst(DebateProvider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            if kwargs["schema"] is host.AgentNarrativeCorePayload:
                count = sum(row["schema"] is host.AgentNarrativeCorePayload for row in self.requests)
                if count == 1:
                    result["panel_evidence_paths"] = ["panel.consensus_formula.consensus_final"]
                    clock[0] += 30
            return result

    prepared, merged, _ = prepared_input()
    provider = StaleFirst(tmp_path)
    result = execute(provider, prepared, merged)
    narratives = [row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload]
    assert len(narratives) == 2
    assert narratives[1]["deadline_at"] == narratives[0]["deadline_at"]
    assert "panel.consensus_formula.consensus_final" in narratives[1]["user_prompt"]
    assert result["grounding"]["panel_insights"] == [
        "native_synthesis_outcome.synthesis.panel_consensus"]
    assert sum(row["schema"] is host.AgentDimensionPayload for row in provider.requests) == 4


def test_native_consensus_cannot_be_used_as_a_buy_price(tmp_path):
    class MisusedPrice(DebateProvider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            if kwargs["schema"] is host.AgentBuyZonePayload and "value 买入区间" in kwargs["system_prompt"]:
                result["zone"]["basis_fields"] = ["native_synthesis_outcome.synthesis.panel_consensus"]
            return result

    prepared, merged, _ = prepared_input()
    with pytest.raises(host.UziStageFailures) as caught:
        execute(MisusedPrice(tmp_path), prepared, merged)
    assert {row["stage"] for row in caught.value.failures} == {"buy_zone_value"}


def test_native_outcome_uses_renderer_selection_not_zero_score_or_stale_counts():
    prepared, merged, _ = prepared_input()
    members = merged["investors"]
    for row in members:
        row.update(signal="neutral", score=50)
    members[0].update(signal="bullish", score=99)
    members[1].update(signal="bearish", score=0)
    members[2].update(signal="bearish", score=5)
    members[3].update(signal="skip", score=1)
    # Upstream intentionally does not recompute these after roleplay.
    merged["panel_consensus"] = 98.7
    merged["signal_distribution"] = {"bullish": 1234}
    before = deepcopy((prepared, merged))
    outcome = host._native_narrative_outcome(
        prepared["raw_after_derivation"], prepared["dimensions"], merged,
        profile="medium", school=None,
    )
    assert (prepared, merged) == before
    assert outcome["debate_participants"]["bull"]["investor_id"] == members[0]["investor_id"]
    assert outcome["debate_participants"]["bear"]["investor_id"] == members[2]["investor_id"]
    assert outcome["synthesis"]["panel_consensus"] != 98.7
    assert outcome["merged_signal_counts_all_seats"] == {
        "bullish": 1, "bearish": 2, "skip": 1, "neutral": len(members) - 4,
    }
    assert "rounds" not in outcome["debate_participants"], "No script default prose as Agent output"


def test_narrative_preview_replays_same_fixed_auxiliary_input(monkeypatch):
    prepared, merged, _ = prepared_input()
    auxiliary = {"recording": {"frozen": True}, "binding": {"manifest_id": "test"}}
    original = host._synthesize_native
    received = []

    def record(raw, dimensions, panel, **kwargs):
        received.append(kwargs.pop("auxiliary_input"))
        return original(raw, dimensions, panel, **kwargs)

    monkeypatch.setattr(host, "_synthesize_native", record)
    host._native_narrative_outcome(
        prepared["raw_after_derivation"], prepared["dimensions"], merged,
        profile="medium", school=None, auxiliary_input=auxiliary,
    )
    assert received == [auxiliary]


def test_narrative_preserves_native_zero_score_fallback(tmp_path):
    prepared, merged, _ = prepared_input()
    for member in merged["investors"]:
        member.update(signal="bearish", score=0)
    before = deepcopy(merged)
    provider = DebateProvider(tmp_path)
    execute(provider, prepared, merged)
    request = next(row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload)
    outcome = projection(request, "NATIVE_SYNTHESIS_OUTCOME_CONTEXT=")
    _, native = host._synthesize_native(
        prepared["raw_after_derivation"], prepared["dimensions"], merged,
        agent_analysis=None, profile="medium", school=None,
    )
    for side in ("bull", "bear"):
        assert outcome["debate_participants"][side]["score"] == 0
        assert outcome["debate_participants"][side]["investor_id"] == native["debate"][side]["investor_id"]
    prompt = request["system_prompt"]
    assert "不得重新选择零分" not in prompt
    assert "如实使用该回退结果" in prompt
    assert "原生候选中的最高/最低分" in prompt
    assert merged == before


def test_panel_revision_invalidates_narrative_only(tmp_path):
    prepared, merged, _ = prepared_input()
    first = DebateProvider(tmp_path)
    execute(first, prepared, merged)
    unchanged = DebateProvider(tmp_path)
    execute(unchanged, prepared, merged)
    assert unchanged.requests == []
    for change in ("reasoning", "comment", "rule_message", "baseline_score"):
        changed_prepared, changed_merged = deepcopy(prepared), deepcopy(merged)
        if change == "reasoning":
            changed_merged["investors"][0]["reasoning"] += "新增逐人推理，不修改数值。"
        elif change == "comment":
            changed_merged["investors"][0]["comment"] += "不同的评委评论不能复用旧综合。"
        elif change == "rule_message":
            changed_merged["investors"][0]["pass"] = [{"name": "distinct", "msg": "不同的原始规则说明", "weight": 1}]
        else:
            changed_prepared["panel"]["investors"][0]["score"] += 1
        provider = DebateProvider(tmp_path)
        execute(provider, changed_prepared, changed_merged)
        assert [row["schema"] for row in provider.requests] == [host.AgentNarrativeCorePayload]


def test_native_task4_writing_revision_invalidates_only_narrative_cache(tmp_path, monkeypatch):
    prepared, merged, _ = prepared_input()
    current_protocol = host.native_agent_protocol

    def previous_protocol(stage):
        prompt = current_protocol(stage)
        if stage == "narrative":
            return prompt.split("NATIVE_TASK4_WRITING_GUIDANCE", 1)[0].rstrip() + "\n"
        return prompt

    with monkeypatch.context() as context:
        context.setattr(host, "native_agent_protocol", previous_protocol)
        execute(DebateProvider(tmp_path), prepared, merged)
    revised = DebateProvider(tmp_path)
    execute(revised, prepared, merged)
    assert [row["schema"] for row in revised.requests] == [host.AgentNarrativeCorePayload]
    assert "NATIVE_TASK4_WRITING_GUIDANCE" in revised.requests[0]["system_prompt"]
    repeated = DebateProvider(tmp_path)
    execute(repeated, prepared, merged)
    assert repeated.requests == []


def test_outcome_context_revision_invalidates_only_narrative_cache(tmp_path, monkeypatch):
    prepared, merged, _ = prepared_input()
    current = host._native_narrative_outcome

    def previous(*args, **kwargs):
        result = current(*args, **kwargs)
        result["version"] = "previous-test-context"
        return result

    with monkeypatch.context() as context:
        context.setattr(host, "_native_narrative_outcome", previous)
        execute(DebateProvider(tmp_path), prepared, merged)
    revised = DebateProvider(tmp_path)
    execute(revised, prepared, merged)
    assert [row["schema"] for row in revised.requests] == [host.AgentNarrativeCorePayload]
    repeated = DebateProvider(tmp_path)
    execute(repeated, prepared, merged)
    assert repeated.requests == []
