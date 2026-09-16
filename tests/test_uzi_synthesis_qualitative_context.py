"""Offline prompt/caching regression: Task2.5 is research, not numeric truth."""

from copy import deepcopy
import json
import re
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots


MARKER = "EXECUTED_QUALITATIVE_RESEARCH_CONTEXT="
PASS_REVIEW = {"issues": [], "critical_count": 0, "warning_count": 0, "info_count": 0}


def research(*, status="completed", revision="original"):
    dimensions = {}
    for key in sorted(host._QUALITATIVE_KEYS):
        dimensions[key] = {
            "conclusion": f"{key} 已执行研究结论，保留事实缺口与适用范围。{revision if key == '13_policy' else 'unchanged'}",
            "evidence": [{
                "source": "offline fixture", "url": f"https://example.test/{key}/source",
                "retrieved_at": "2026-09-01", "finding": f"{key} 的独立冻结来源事实，不能等同于关联推断。",
            }],
            "disclosures": [{"source": "unknown", "finding": "公司定量敞口未知，不能补造影响数值。"}],
            "associations": [{
                "link_to": "10_valuation", "chain_id": f"{key}-valuation",
                "causal_chain": f"{key} 外部变化可能传导至估值，属于待核验的研究推断。",
                "estimated_impact": "假设影响 987654321%，绝非原始事实或价格基准。",
            }],
        }
    if status != "completed":
        dimensions = {key: value for key, value in dimensions.items() if key in {"3_macro", "13_policy"}}
    return {
        "qualitative_deep_dive": dimensions,
        "qualitative_agent_execution": {
            "required": True, "status": status, "agent_reviewed": status == "completed",
            "completed_group_count": 3 if status == "completed" else 1,
        },
    }


class CaptureProvider(_LiteAgentProvider):
    def __init__(self, workspace):
        super().__init__()
        self.settings.workspace_root = workspace
        self.requests = []

    def parse(self, **kwargs):
        self.requests.append(kwargs)
        return super().parse(**kwargs)


def execute(provider, outputs, *, reviews=None):
    raw_checks = []
    generate = host._generate_agent_analysis

    def capture(*args, **kwargs):
        before = deepcopy(args[0])
        result = generate(*args, **kwargs)
        raw_checks.append((before, deepcopy(args[0])))
        return result

    snapshots = _lite_snapshots()
    original = deepcopy(snapshots)
    with (
        # Native signature copy uses random.choice. Cache tests require the
        # same recorded input, now including the formerly omitted comments.
        patch("random.choice", side_effect=lambda choices: choices[0]),
        patch.object(host, "apply_qualitative_deep_dive", side_effect=deepcopy(outputs)),
        patch.object(host, "run_native_self_review", side_effect=reviews or [PASS_REVIEW]),
        patch.object(host, "_validate_agent_analysis", return_value=[]),
        patch.object(host, "_generate_agent_analysis", side_effect=capture),
    ):
        result = host.execute_native_analysis(snapshots, ["600000"], profile="medium", llm_provider=provider)
    assert snapshots == original
    assert raw_checks and all(before == after for before, after in raw_checks)
    assert all("qualitative_deep_dive" not in before for before, _ in raw_checks)
    return result


def context(request):
    prompt = request["system_prompt"]
    assert MARKER in prompt, "Actual model request omitted completed Task2.5 research"
    return json.loads(prompt.split(MARKER, 1)[1].split("\n", 1)[0])


def stage(request):
    if request["schema"] is host.AgentDimensionPayload:
        return tuple(re.search(r"必须且只能是：([^；]+)；", request["user_prompt"])[1].split(","))
    if request["schema"] is host.AgentNarrativeCorePayload:
        return "narrative_core"
    return next(f"buy_zone_{name}" for name in ("value", "growth", "technical", "youzi")
                if f" {name} 买入区间" in request["user_prompt"])


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_actual_synthesis_and_explanations_consume_same_research_without_new_gate(tmp_path, status):
    output = research(status=status)
    provider = CaptureProvider(tmp_path)
    result = execute(provider, [output])
    main = next(row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload)
    received = context(main)
    assert "NARRATIVE_SYNTHESIS_SCOPE_V1" in main["system_prompt"]
    assert "不逐人复制全部评委发言" in main["system_prompt"]
    assert received["qualitative_deep_dive"] == output["qualitative_deep_dive"]
    assert received["execution"]["status"] == status
    for row in provider.requests:
        identity = stage(row)
        if not isinstance(identity, tuple):
            assert context(row) == received
        elif set(identity) & set(output["qualitative_deep_dive"]):
            assert context(row)["qualitative_deep_dive"] == {
                key: value for key, value in output["qualitative_deep_dive"].items() if key in identity
            }
    # Post-attachment alone is insufficient: it already worked before the fix.
    assert result["results"][0]["agent_analysis"]["qualitative_deep_dive"] == output["qualitative_deep_dive"]


def test_research_is_not_promoted_to_frozen_leaf_or_numeric_authority(tmp_path):
    provider = CaptureProvider(tmp_path)
    execute(provider, [research()])
    for row in provider.requests:
        prompt = row["system_prompt"]
        for marker in ("FROZEN_STAGE_EVIDENCE=", "ALLOWED_LEAF_EVIDENCE_PATHS=", "ALLOWED_NUMERIC_BASIS_VALUES="):
            if marker in prompt:
                frozen = json.loads(prompt.split(marker, 1)[1].split("\n", 1)[0])
                assert "987654321" not in json.dumps(frozen)
                assert "qualitative_deep_dive" not in json.dumps(frozen)
        if MARKER in prompt:
            assert "不是新增原始事实" in prompt
            assert "不得作为买区数值依据" in prompt


def test_research_revision_invalidates_only_affected_stage_caches(tmp_path):
    first = CaptureProvider(tmp_path)
    execute(first, [research()])
    repeated = CaptureProvider(tmp_path)
    execute(repeated, [research()])
    assert repeated.calls == 0
    changed = CaptureProvider(tmp_path)
    execute(changed, [research(revision="revised")])
    identities = [stage(row) for row in changed.requests]
    assert "narrative_core" in identities, "Changed Task2.5 reused stale narrative cache"
    assert {item for item in identities if isinstance(item, str)} == {
        "narrative_core", "buy_zone_value", "buy_zone_growth", "buy_zone_technical", "buy_zone_youzi",
    }
    assert [item for item in identities if isinstance(item, tuple)] == [
        item for item in map(stage, first.requests) if isinstance(item, tuple) and "13_policy" in item
    ]


def test_partial_research_repair_does_not_reuse_old_in_memory_narrative(tmp_path):
    initial = research(status="incomplete")
    repaired = research()
    critical = {
        "severity": "critical", "category": "agent-os-boundary", "dim": "qualitative_deep_dive",
        "issue": "六维定性研究未满足 UZI 证据门", "evidence": "status=incomplete", "suggested_fix": "完成三组分析后重算",
    }
    provider = CaptureProvider(tmp_path)
    result = execute(provider, [initial, repaired], reviews=[
        {"issues": [critical], "critical_count": 1, "warning_count": 0, "info_count": 0}, PASS_REVIEW,
    ])
    main = [row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload]
    assert len(main) == 2, "Repaired Task2.5 attached to stale in-memory synthesis"
    assert context(main[0])["qualitative_deep_dive"] == initial["qualitative_deep_dive"]
    assert context(main[1])["qualitative_deep_dive"] == repaired["qualitative_deep_dive"]
    assert result["results"][0]["recovery_history"][1]["reused_agent_analysis"] is False
