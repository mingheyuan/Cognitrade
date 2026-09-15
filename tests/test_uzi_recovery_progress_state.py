"""Recovery progress is changed content, not a new ID or a repeated title."""
import socket
from copy import deepcopy

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from research_system.api.fund_holding_report_service import FundHoldingReportService
from tests.test_uzi_392_analysis import _lite_snapshots, _LiteAgentProvider
from tests.test_uzi_synthesis_qualitative_context import PASS_REVIEW


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Recovery regressions cannot fetch data or models")
    monkeypatch.setattr(socket.socket, "connect", deny)


def execute(monkeypatch, revisions, *, pass_on=None, metadata_only=False):
    original = host._generate_agent_analysis
    generated, reviewed = [], []
    issue = {"severity": "critical", "category": "agent-os-boundary", "dim": "agent_analysis",
             "issue": "同一输出问题尚未完全修复", "evidence": "fixture", "suggested_fix": "继续局部修复输出"}

    def generate(*args, **kwargs):
        result = original(*args, **kwargs)
        revision = revisions[len(generated)]
        if not metadata_only:
            result["narrative_override"]["core_conclusion"] += f" 局部修复内容版本 {revision}。"
        result["generated_at"] = f"test-time-{revision}"
        result["artifact_id"] = f"test-artifact-{revision}"
        generated.append(deepcopy(result))
        return result

    def review(**kwargs):
        reviewed.append(True)
        if pass_on and len(reviewed) >= pass_on:
            return PASS_REVIEW
        return {"issues": [issue], "critical_count": 1, "warning_count": 0, "info_count": 0}

    monkeypatch.setattr(host, "_generate_agent_analysis", generate)
    monkeypatch.setattr(host, "run_native_self_review", review)
    rows = _lite_snapshots()
    before = deepcopy(rows)
    result = host.execute_native_analysis(rows, ["600000"], profile="lite", llm_provider=_LiteAgentProvider())
    assert rows == before
    return result["results"][0], generated


def test_changed_content_with_same_issue_can_finish_next_local_repair(monkeypatch):
    result, generated = execute(monkeypatch, [1, 2, 3], pass_on=3)
    assert len(generated) == 3
    assert result["report_allowed"] is True
    history = result["recovery_history"]
    assert history[0]["recovery_progress"]["output_hash"] != history[1]["recovery_progress"]["output_hash"]
    assert history[0]["recovery_progress"]["input_hash"] == history[1]["recovery_progress"]["input_hash"]


def test_only_random_metadata_is_not_progress(monkeypatch):
    result, generated = execute(monkeypatch, [1, 2, 3], metadata_only=True)
    assert len(generated) == 2
    assert result["report_allowed"] is False
    assert result["recovery_history"][-1]["exhausted_reason"] == "unchanged_recovery_state_after_recompute"
    from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
    recovery = native_recovery({"results": [result], "recovery_history": result["recovery_history"]})
    assert recovery["kind"] == "no_progress"
    assert recovery["retryable"] is False


def test_repeated_content_cycle_is_detected_not_only_consecutive_repeat(monkeypatch):
    result, generated = execute(monkeypatch, [1, 2, 1, 3])
    assert len(generated) == 3
    assert result["recovery_history"][-1]["exhausted_reason"] == "unchanged_recovery_state_after_recompute"


def detail(*, raw=None, history=None, cycle=1):
    return {"snapshot": {"status": "running"}, "artifacts": [{"artifact_type": "uzi_native_analysis",
        "artifact_id": f"generated-{cycle}", "content": {
            "generated_at": f"time-{cycle}", "recovery_history": history or [],
            "results": [{"raw": raw or {"price": 10}, "agent_analysis": {"core_conclusion": "unchanged"}}],
        }}]}


def test_outer_duplicate_recovery_history_is_not_new_content():
    step = {"stage": "agent_recompute_self_review", "status": "quality_gate_blocked", "cycle": 1}
    first = detail(history=[step])
    repeated = detail(history=[step, {**step, "cycle": 2}], cycle=2)
    assert FundHoldingReportService._recovery_fingerprint(first) == FundHoldingReportService._recovery_fingerprint(repeated)


@pytest.mark.parametrize("key", ["updated_at", "generated_at", "artifact_id", "cycle"])
def test_business_facts_are_not_stripped_like_runtime_metadata(key):
    before = detail(raw={"dimensions": {"data": {key: "source-fact-A"}}})
    after = detail(raw={"dimensions": {"data": {key: "source-fact-B"}}})
    original = deepcopy(before)
    assert FundHoldingReportService._recovery_fingerprint(before) != FundHoldingReportService._recovery_fingerprint(after)
    assert before == original


def test_recovery_hash_keeps_fact_types_order_and_original_hash_algorithm():
    from domain_packs.finance_ashare.subagents.uzi_recovery import (
        content_hash,
        recovery_content_hash,
    )
    payload = {"raw": {"facts": [0, None, False, "中文", {"updated_at": "2026-09-02"}]}}
    assert recovery_content_hash(payload) == content_hash(payload)
    assert len({recovery_content_hash({"raw": value}) for value in ({}, {"x": None}, {"x": 0}, {"x": False})}) == 4
    assert recovery_content_hash({"raw": [1, 2]}) != recovery_content_hash({"raw": [2, 1]})


def test_problem_type_changes_but_request_identity_does_not():
    from domain_packs.finance_ashare.subagents.uzi_recovery import (
        recovery_progress_state,
    )
    issue = {"dim": "agent_analysis", "issue": "failure", "failure_records": [{
        "stage": "narrative_core", "kind": "agent_content_invalid", "request_id": "one"}]}
    kwargs = {"input_hash": "same-input", "outputs": {"agent_analysis": None}}
    before = recovery_progress_state(**kwargs, issues=[issue])
    issue["failure_records"][0]["request_id"] = "two"
    assert recovery_progress_state(**kwargs, issues=[issue]) == before
    issue["failure_records"][0]["kind"] = "source_gap"
    assert recovery_progress_state(**kwargs, issues=[issue])["state_hash"] != before["state_hash"]


@pytest.mark.parametrize("status,category,kind", [("cancelled", "data", "cancelled"), ("gated", "data", "source_gap")])
def test_no_progress_cannot_override_cancellation_or_data_gap(status, category, kind):
    from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
    content = {"recovery_history": [{"exhausted_reason": "unchanged_recovery_state_after_recompute"}],
               "results": [{"self_review": {"issues": [{"severity": "critical", "category": category, "dim": "7_industry"}]}}]}
    assert native_recovery(content, run_status=status)["kind"] == kind


def test_old_no_progress_does_not_override_a_newer_successful_review():
    from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
    history = [{"exhausted_reason": "unchanged_recovery_state_after_recompute"}, {"status": "passed"}]
    content = {"report_allowed": True, "recovery_history": history, "results": [{"self_review": {"issues": []}}]}
    assert native_recovery(content, run_status="completed")["kind"] == "complete"
    content["report_allowed"] = False
    content["results"][0]["self_review"]["issues"] = [{"severity": "critical", "dim": "agent_analysis", "issue": "new issue"}]
    assert native_recovery(content, run_status="gated")["kind"] == "output_invalid"
