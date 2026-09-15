"""Full SKILL duties are not the legacy quick/profile switches or CHECKS."""

import socket
from copy import deepcopy
from threading import Event
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    UziRequestCancelled,
    UziRequestDeadline,
)
from tests.test_uzi_392_analysis import _lite_snapshots
from tests.test_uzi_native_review_layers import _review
from tests.test_uzi_synthesis_qualitative_context import CaptureProvider, research

WARNING = {
    "severity": "warning", "category": "data", "dim": "12_capital_flow",
    "issue": "recorded scoped source missing", "evidence": "data={}; error=scoped recovery",
    "suggested_fix": "Collect a new source version; never rewrite the frozen input",
}
NATIVE = {"issues": [WARNING], "critical_count": 0, "warning_count": 1,
          "info_count": 0, "passed": True, "checks_run": ["recorded_fixture_check"]}
SOURCE_CRITICAL = {
    "severity": "critical", "category": "data", "dim": "19_contests",
    "issue": "No frozen historical contest archive", "evidence": "data={}",
    "suggested_fix": "Requires a new historical evidence version",
}
DEBATE = {"punchline": "冻结事实有分歧，本条仅用于测试，不是投资建议。",
          **{side: ["基于冻结事实的测试辩论，不编造价格。"] * 3
             for side in ("bull_say_rounds", "bear_say_rounds")}}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *_args: pytest.fail("No network in closure regression"))


class DebateProvider(CaptureProvider):
    def parse(self, **kwargs):
        result = super().parse(**kwargs)
        if kwargs["schema"] is host.AgentNarrativeCorePayload:
            result["great_divide_override"] = {
                "punchline": DEBATE["punchline"],
                **{side: [{"text": text, "evidence_paths": ["raw.dimensions.0_basic.data.price"]}
                          for text in DEBATE[side]] for side in ("bull_say_rounds", "bear_say_rounds")},
            }
        return result


@pytest.mark.parametrize("profile", ["lite", "medium", "deep"])
def test_full_skill_accepts_and_requires_agent_debate_in_every_profile(tmp_path, profile):
    host._initialize_native_imports()
    prepared = host._prepare_native_computation(host.build_uzi_raw(_lite_snapshots(), "600000"),
                                                profile=profile, school=None)
    provider = DebateProvider(tmp_path)
    result = host._generate_agent_analysis(
        prepared["raw_after_derivation"], prepared["dimensions"], prepared["panel"],
        profile=profile, school=None, llm_provider=provider, recovery_tasks=[], review_feedback=[],
        full_agent_workflow=True, qualitative_research=research(),
    )
    assert result["great_divide_override"] == DEBATE
    prompt = next(row for row in provider.requests if row["schema"] is host.AgentNarrativeCorePayload)
    assert "不要返回 great_divide_override" not in prompt["user_prompt"]
    assert "great_divide_override 必须包含多空各三轮" in prompt["system_prompt"]


def test_missing_agent_debate_is_not_native_python_failure():
    with patch.object(host, "run_native_python_review", return_value={**NATIVE, "issues": [], "warning_count": 0}):
        result = _review()
    assert result["native_python_review"]["critical_count"] == 0
    assert result["native_skill_completion"]["passed"] is False
    assert any(row["dim"] == "great_divide_override" for row in result["native_skill_completion"]["issues"])


def test_unhandled_warning_cannot_claim_complete_skill_but_native_severity_is_preserved():
    agent = {"agent_reviewed": True, "great_divide_override": DEBATE}
    with patch.object(host, "run_native_python_review", return_value=deepcopy(NATIVE)):
        result = _review(agent_analysis=agent)
    assert result["native_python_review"] == NATIVE
    assert result["native_skill_completion"]["passed"] is False
    assert "review_acknowledged" not in agent, "No fabricated acknowledgement on read"
    assert result["native_skill_completion"]["warning_handling"]["pending_count"] == 1


@pytest.mark.parametrize("ack", [{WARNING["dim"]: "Recorded input is empty; requires a new data version."},
                                 {"warning_12_capital_flow": "No recorded full-market scan; not a timeout."}])
def test_real_explicit_reason_satisfies_workflow_without_erasing_warning(ack):
    with patch.object(host, "run_native_python_review", return_value=deepcopy(NATIVE)):
        result = _review(agent_analysis={"agent_reviewed": True, "great_divide_override": DEBATE,
                                        "review_acknowledged": ack})
    assert result["native_python_review"] == NATIVE
    assert result["native_skill_completion"]["passed"] is True
    assert result["native_skill_completion"]["warning_handling"]["pending_count"] == 0


@pytest.mark.parametrize("ack", [{WARNING["dim"]: True}, {WARNING["dim"]: " "},
                                 {"all": "All handled"}, {"12": "Handled"}])
def test_generic_or_empty_ack_cannot_complete_full_workflow(ack):
    with patch.object(host, "run_native_python_review", return_value=deepcopy(NATIVE)):
        result = _review(agent_analysis={"agent_reviewed": True, "great_divide_override": DEBATE,
                                        "review_acknowledged": ack})
    assert result["native_skill_completion"]["passed"] is False


def test_profile_mode_warning_keeps_native_quick_semantics():
    with patch.object(host, "run_native_python_review", return_value=deepcopy(NATIVE)):
        result = _review(full_agent_workflow=False)
    assert result["report_allowed"] is True
    assert result["native_python_review"] == NATIVE


class WarningProvider(CaptureProvider):
    def __init__(self, workspace, replies=None):
        super().__init__(workspace)
        self.replies = replies

    def parse(self, **kwargs):
        self.requests.append(kwargs)
        assert kwargs["schema"] is host.NativeWarningAcknowledgements
        if self.replies is not None:
            return self.replies.pop(0)
        return {"acknowledgements": [{"issue_index": 0, "reason": "录制来源未返回数据；本报告未补采，需要新的数据版本。"}]}


def acknowledge(provider, agent=None, review=None):
    return host._acknowledge_native_warnings(
        agent or {"agent_reviewed": True, "great_divide_override": DEBATE}, review or NATIVE,
        llm_provider=provider, symbol="600000", profile="medium", input_hash="frozen-fixture-hash",
    )


def test_warning_step_is_actual_model_output_scoped_cached_and_immutable(tmp_path):
    agent = {"agent_reviewed": True, "great_divide_override": DEBATE, "narrative_override": {"core_conclusion": "Original"}}
    before = deepcopy(agent)
    provider = WarningProvider(tmp_path)
    result = acknowledge(provider, agent)
    assert agent == before and NATIVE["issues"] == [WARNING]
    assert {k: v for k, v in result.items() if k != "review_acknowledged"} == before
    assert result["review_acknowledged"][WARNING["issue"]] == "录制来源未返回数据；本报告未补采，需要新的数据版本。"
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert "for each warning: 要么修，要么在 agent_analysis.review_acknowledged 显式写原因" in request["system_prompt"]
    assert "不执行 suggested_fix" in request["user_prompt"]
    assert not request.get("tools")
    assert acknowledge(provider, agent) == result
    assert len(provider.requests) == 1, "Same input/review/Agent should reuse validated warning output"
    changed = deepcopy(NATIVE)
    changed["issues"][0]["evidence"] = "new warning evidence"
    acknowledge(provider, agent, changed)
    assert len(provider.requests) == 2, "Changed issue evidence cannot reuse stale acknowledgement"


def test_warning_step_no_request_when_already_handled_or_no_warnings(tmp_path):
    provider = WarningProvider(tmp_path)
    agent = {"review_acknowledged": {WARNING["dim"]: "Recorded reason"}}
    assert acknowledge(provider, agent) == agent
    blocked = {**NATIVE, "critical_count": 1, "warning_count": 0,
               "issues": [SOURCE_CRITICAL], "passed": False}
    acknowledge(provider, review=blocked)
    assert provider.requests == []


def test_warning_step_handles_warnings_without_acknowledging_source_critical(tmp_path):
    provider = WarningProvider(tmp_path)
    blocked = {**NATIVE, "critical_count": 1, "passed": False,
               "issues": [SOURCE_CRITICAL, WARNING]}
    original = deepcopy(blocked)
    result = acknowledge(provider, review=blocked)
    assert len(provider.requests) == 1
    assert set(result["review_acknowledged"]) == {WARNING["issue"]}
    assert blocked == original
    assert "No frozen historical contest archive" not in provider.requests[0]["system_prompt"]


def test_warning_step_repairs_only_ack_payload_and_stops_repeated_no_progress(tmp_path):
    provider = WarningProvider(tmp_path, [{"acknowledgements": []}] * 3)
    with pytest.raises(host.UziStageFailures) as exc:
        acknowledge(provider)
    assert {row["stage"] for row in exc.value.failures} == {"native_warning_review"}
    assert len(provider.requests) == 2
    assert not list(tmp_path.glob("cache/uzi_agent_stages/*.json"))


def test_warning_step_honors_existing_cancellation_before_request(tmp_path):
    cancelled = Event()
    cancelled.set()
    provider = WarningProvider(tmp_path)
    with pytest.raises(UziRequestCancelled):
        acknowledge(host.with_uzi_request_control(provider, cancellation=cancelled))
    assert provider.requests == []


@pytest.mark.parametrize("warning_fails", [False, True])
@pytest.mark.parametrize("source_gap", [False, True])
def test_execute_symbol_handles_warning_then_rechecks_without_research_rerun(tmp_path, warning_fails, source_gap):
    agent = {"agent_reviewed": True, "great_divide_override": DEBATE,
             "dim_commentary": {}, "narrative_override": {}, "data_gap_acknowledged": {}}
    snapshots = _lite_snapshots()
    original = deepcopy(snapshots)
    provider = WarningProvider(tmp_path, [{"acknowledgements": []}] * 3 if warning_fails else None)
    native = deepcopy(NATIVE)
    if source_gap:
        native.update(critical_count=1, passed=False)
        native["issues"].insert(0, deepcopy(SOURCE_CRITICAL))
    with (patch.object(host, "_generate_agent_analysis", return_value=agent) as analysis,
          patch.object(host, "_validate_agent_analysis", return_value=[]),
          patch.object(host, "apply_qualitative_deep_dive", return_value=research()) as qualitative,
          patch.object(host, "build_native_panel_overrides", return_value={
              "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
              "per_investor_override": {},
          }) as roleplay,
          patch.object(host, "run_native_python_review", return_value=native) as review):
        result = host.execute_native_analysis(snapshots, ["600000"], profile="medium",
                                              llm_provider=provider, full_agent_workflow=True)
    assert result["report_allowed"] is (not warning_fails and not source_gap)
    assert analysis.call_count == qualitative.call_count == roleplay.call_count == 1
    assert review.call_count == 2
    assert len(provider.requests) == (2 if warning_fails else 1)
    assert snapshots == original
    stock = result["results"][0]
    assert stock["self_review"]["native_python_review"] == native
    assert stock["self_review"]["native_skill_completion"]["passed"] is not warning_fails
    if source_gap and not warning_fails:
        assert stock["self_review"]["critical_count"] == 1
        assert stock["self_review"]["execution_status"]["status"] == "completed"
        assert stock["self_review"]["issues"][0] == SOURCE_CRITICAL
    assert ("review_acknowledged" in stock["agent_analysis"]) is not warning_fails
    if warning_fails:
        assert stock["recovery_history"][-1]["exhausted_reason"] == "native_warning_review_stage_exhausted"
    assert "review_acknowledged" not in agent


def test_expired_parent_deadline_cannot_start_warning_request(tmp_path):
    provider = WarningProvider(tmp_path)
    with pytest.raises(UziRequestDeadline):
        acknowledge(host.with_uzi_request_control(provider, deadline_at=0.01))
    assert provider.requests == []


def test_missing_debate_fails_only_narrative_and_does_not_cache_it(tmp_path):
    host._initialize_native_imports()
    prepared = host._prepare_native_computation(host.build_uzi_raw(_lite_snapshots(), "600000"),
                                                profile="medium", school=None)
    class MissingDebateProvider(CaptureProvider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            if kwargs["schema"] is host.AgentNarrativeCorePayload:
                result.pop("great_divide_override", None)
            return result

    provider = MissingDebateProvider(tmp_path)
    with pytest.raises(host.UziStageFailures) as exc:
        host._generate_agent_analysis(
            prepared["raw_after_derivation"], prepared["dimensions"], prepared["panel"],
            profile="medium", school=None, llm_provider=provider, recovery_tasks=[], review_feedback=[],
            full_agent_workflow=True, qualitative_research=research(),
        )
    assert [row["stage"] for row in exc.value.failures] == ["narrative_core"]
    assert len(provider.requests) == 5, "Missing debate must stop before the four price requests"
    # A repaired report can reuse all four validated dimension stages.
    repaired = DebateProvider(tmp_path)
    host._generate_agent_analysis(
        prepared["raw_after_derivation"], prepared["dimensions"], prepared["panel"],
        profile="medium", school=None, llm_provider=repaired, recovery_tasks=[], review_feedback=[],
        full_agent_workflow=True, qualitative_research=research(),
    )
    assert len(repaired.requests) == 5
    assert not any(row["schema"] is host.AgentDimensionPayload for row in repaired.requests)


def test_provider_schema_error_gets_only_local_repair_with_same_deadline(tmp_path):
    class ParsedProvider(WarningProvider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            if len(self.requests) == 1:
                result = {"acknowledgements": []}
            return kwargs["schema"].model_validate(result)
    provider = ParsedProvider(tmp_path)
    result = acknowledge(provider)
    assert WARNING["issue"] in result["review_acknowledged"]
    assert len(provider.requests) == 2
    assert provider.requests[1]["deadline_at"] <= provider.requests[0]["deadline_at"]
