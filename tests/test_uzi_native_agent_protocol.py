"""Actual split requests must retain the pinned SKILL's analysis duties."""

import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest
from tests.uzi_prompt_wire import decode_evidence, decode_paths

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from tests.test_uzi_392_analysis import _lite_snapshots
from tests.test_uzi_synthesis_qualitative_context import (
    CaptureProvider,
    execute,
    research,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Protocol regression must not perform network I/O")
    monkeypatch.setattr(socket.socket, "connect", denied)


SKILL = (Path(__file__).resolve().parents[1]
         / "domain_packs/finance_ashare/vendor/uzi_3_9_2/skill/SKILL.md").read_text()


def section(start, end):
    return SKILL[SKILL.index(start):SKILL.index(end, SKILL.index(start))].strip()


ASSUMPTIONS = section("### 🧠 你的判断环节（Task 1.5 假设审查）", "### Task 2 ·")
DIMENSIONS = section("### Task 2 ·", "### Task 3 ·")
FACTCHECK = section("### ⛔ HARD-GATE-FACTCHECK", "### ⛔ HARD-GATE-DATAGAPS")
PANEL_REVIEW = section("### Step 3.4 · 整体审查", "### Task 4 ·")
SYNTHESIS = section("### Task 4 ·", "### Task 5 ·")
COPY_REVIEW = section("### 🧠 你的金句审查", "### 完成验证")
BUY_ZONES = section("**4.5 四派系买入区间**", "### 写入（v2.2 闭环机制）")
TASK4_REFERENCE = (Path(__file__).resolve().parents[1]
                  / "domain_packs/finance_ashare/vendor/uzi_3_9_2/skill/references/task4-synthesis.md").read_text()
TASK4_WRITING = TASK4_REFERENCE[
    TASK4_REFERENCE.index("**辩论的语言规则**"):TASK4_REFERENCE.index("## Step 4.3 风险清单")
].strip()


def test_real_split_requests_receive_native_duties_not_just_length_constraints(tmp_path):
    provider = CaptureProvider(tmp_path)
    execute(provider, [research()])
    for request in provider.requests:
        prompt = request["system_prompt"]
        assert FACTCHECK in prompt
        assert "报告阶段不执行联网、改写冻结数据或脚本操作" in prompt
        if request["schema"] is host.AgentDimensionPayload:
            assert DIMENSIONS in prompt
            assert ASSUMPTIONS in prompt
        elif request["schema"] is host.AgentNarrativeCorePayload:
            for protocol in (ASSUMPTIONS, PANEL_REVIEW, SYNTHESIS, COPY_REVIEW):
                assert protocol in prompt
            assert TASK4_WRITING in prompt
            assert "NATIVE_TASK4_WRITING_GUIDANCE" in prompt
            assert "Top 3 催化剂/风险及 agent_analysis.json 写回仍以 SKILL.md 为准" in prompt
            assert "前 5 条进风险清单" not in prompt
        else:
            assert BUY_ZONES in prompt
        if request["schema"] is not host.AgentNarrativeCorePayload:
            assert "NATIVE_TASK4_WRITING_GUIDANCE" not in prompt
    assert len(provider.requests) == 9, "Protocol alignment must not add a model stage"


def test_native_writing_guidance_does_not_create_new_host_length_gates():
    # Native Python validates minimum coverage, not a new host max-length
    # contract. Authoring/self-review should improve prose, not truncate it.
    debate = host.AgentGreatDivide.model_validate({
        "punchline": "原生指导不能被偷换成本地新增门禁" * 3,
        **{side: [{"text": "有来源的完整论证" * 20, "evidence_paths": ["raw.test.fact"]}
                  for _ in range(4)] for side in ("bull_say_rounds", "bear_say_rounds")},
    })
    assert len(debate.bull_say_rounds) == 4
    assert len(debate.bull_say_rounds[0].text) > 80
    schema = host.AgentGreatDivide.model_json_schema()
    assert "maxItems" not in schema["properties"]["bull_say_rounds"]
    assert "maxLength" not in schema["$defs"]["GroundedRound"]["properties"]["text"]


def test_narrative_can_review_model_assumptions_and_catalysts_without_earlier_citation(tmp_path):
    provider = CaptureProvider(tmp_path)
    execute(provider, [research()])
    request = next(row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload)
    prompt = request["system_prompt"]
    evidence = decode_evidence(json.loads(prompt.split("FROZEN_STAGE_EVIDENCE=", 1)[1].split("\n", 1)[0]))
    paths = set(decode_paths(json.loads(prompt.split("ALLOWED_LEAF_EVIDENCE_PATHS=", 1)[1].split("\n", 1)[0])))
    models = evidence.get("raw", {}).get("dimensions", {})
    assert set(models) == {"20_valuation_models", "21_research_workflow", "22_deep_methods"}
    for key in models:
        assert any(path.startswith(f"raw.dimensions.{key}.") for path in paths)
    # These are actual frozen numeric/model facts, not Task2.5 hypotheses.
    assert "987654321" not in json.dumps(models)
    assert all(path in paths for path in host._leaf_paths({"raw": {"dimensions": models}}))


@pytest.mark.parametrize("profile", ["lite", "medium", "deep"])
def test_full_agent_request_coverage_and_fixed_input_preservation(tmp_path, profile):
    class FullProvider(CaptureProvider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            if kwargs["schema"] is host.AgentNarrativeCorePayload:
                result["great_divide_override"] = {
                    "punchline": "冻结事实支持分歧，但模拟测试不构成投资结论。",
                    **{side: [{
                        "text": "基于冻结价格及原生规则结果审查分歧，本条仅用于离线契约测试。",
                        "evidence_paths": ["raw.dimensions.0_basic.data.price"],
                    } for _ in range(3)] for side in ("bull_say_rounds", "bear_say_rounds")},
                }
            return result
    provider = FullProvider(tmp_path)
    host._initialize_native_imports()
    prepared = host._prepare_native_computation(
        host.build_uzi_raw(_lite_snapshots(), "600000"), profile=profile, school=None,
    )
    original = deepcopy(prepared)
    # Native computation and actual request assembly; only the model is a fixture.
    result = host._generate_agent_analysis(
        prepared["raw_after_derivation"], prepared["dimensions"], prepared["panel"],
        profile=profile, school=None, llm_provider=provider, recovery_tasks=[], review_feedback=[],
        full_agent_workflow=True, qualitative_research=research(),
    )
    assert prepared == original
    assert set(result["dim_commentary"]) == set(host._workflow_dimension_keys(profile, full_agent_workflow=True))
    assert len(provider.requests) == (len(result["dim_commentary"]) + 4) // 5 + 5
    assert all(FACTCHECK in row["system_prompt"] for row in provider.requests)
    assert not any(row.get("tools") for row in provider.requests)


def test_narrative_model_projection_preserves_null_errors_and_actual_paths():
    raw = {"dimensions": {
        "20_valuation_models": {"data": {"dcf": {"assumptions": {"beta": 1.3}, "intrinsic_per_share": None,
                                                 "projected_fcf_yi": [1, 2]}, "lbo": False},
                                "error": "recorded failure", "_timeout": True, "source": "recorded"},
        "21_research_workflow": None,
    }}
    before = deepcopy(raw)
    result = host._narrative_model_evidence(raw)
    assert raw == before
    assert result["dimensions"]["21_research_workflow"] is None
    assert "22_deep_methods" not in result["dimensions"]
    row = result["dimensions"]["20_valuation_models"]
    assert row["error"] == "recorded failure" and row["_timeout"] is True and row["source"] == "recorded"
    assert row["data"] == {"dcf": {"assumptions": {"beta": 1.3}, "intrinsic_per_share": None}, "lbo": False}
    for path in host._leaf_paths({"raw": result}):
        assert host._path_value({"raw": result}, path) == host._path_value({"raw": raw}, path)
