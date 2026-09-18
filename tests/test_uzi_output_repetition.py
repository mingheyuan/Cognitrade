"""Availability recovery must not alter native report acceptance or evidence."""
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as analysis
from domain_packs.finance_ashare.subagents import uzi_llm_support as support
from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery, repairable_output_failures
from research_system.runtime.providers.codex_output_trace import OutputTrace
from research_system.runtime.providers.errors import CodexTurnError, LLMOutputRepetitionError
from research_system.runtime.providers.structured_output import strict_schema


def trace_for(text, *, phase="final_answer", chunk=37):
    trace = OutputTrace()
    trace.record("item/started", {"item": {"id": "m", "type": "agentMessage", "phase": phase}})
    for start in range(0, len(text), chunk):
        trace.record("item/agentMessage/delta", {"itemId": "m", "delta": text[start:start + chunk]})
    return trace


@pytest.mark.parametrize("period", ["x", "secret!", "abc012" * 17])
@pytest.mark.parametrize("chunk", [1, 511, 1030, 9000])
def test_exact_repetition_detection_handles_offsets_periods_and_chunks(period, chunk):
    trace = trace_for('{"field":"different prefix ' + period * 5000, chunk=chunk)
    result = trace.repetition()
    assert result and result["window_characters"] == 4096
    assert result["period_characters"] <= len(period)
    assert "secret" not in json.dumps(trace.snapshot())
    assert all(len(m.repetition_tail) <= 4096 and len(m.pending) < 512 for m in trace.messages.values())


def test_guard_does_not_impose_output_length_or_cross_message_repetition():
    # Much longer than both real failed outputs, but nonperiodic: not rejected.
    trace = trace_for("".join(f"不同事实证据{i:06d}。" for i in range(8000)), chunk=503)
    assert trace.repetition() is None
    assert trace_for("same" * 2000, phase="commentary").repetition() is None
    split = OutputTrace()
    for index in range(10):
        split.record("item/started", {"item": {"id": str(index), "type": "agentMessage", "phase": "final_answer"}})
        split.record("item/agentMessage/delta", {"itemId": str(index), "delta": "x" * 2000})
    assert split.repetition() is None


class RepeatingProvider:
    def __init__(self, failures=1, cancellation=None, clock=None):
        self.settings = SimpleNamespace(uzi_llm_transient_retries=0)
        self.failures = failures
        self.calls = []
        self.cancellation = cancellation
        self.clock = clock

    def parse_cancellable(self, **kwargs):
        self.calls.append(dict(kwargs))
        if len(self.calls) <= self.failures:
            if self.cancellation:
                self.cancellation.set()
            if self.clock is not None:
                self.clock[0] += 7
            raise LLMOutputRepetitionError()
        return {"valid": True}


@pytest.mark.parametrize("stage", ["dimensions_11_15", "narrative_core"])
def test_only_affected_stage_retries_once_with_same_deadline_and_input(stage):
    provider = RepeatingProvider()
    events = []
    now = [100.0]
    provider.clock = now
    with patch.object(support, "monotonic", side_effect=lambda: now[0]):
        result = support.parse_uzi_structured(provider, uzi_stage=stage, schema=dict,
            system_prompt="immutable evidence", user_prompt="write this stage", deadline_at=120,
            progress_callback=events.append)
    assert result == {"valid": True} and len(provider.calls) == 2
    assert [c["deadline_at"] for c in provider.calls] == [120, 120]
    assert [c["timeout_seconds"] for c in provider.calls] == [20, 13]
    assert all(c["system_prompt"] == "immutable evidence" for c in provider.calls)
    assert all(c["output_repetition_guard"] for c in provider.calls)
    assert "不要续写" in provider.calls[1]["user_prompt"]
    assert [e["request_phase"] for e in events].count("repairing_output") == 1


def test_second_repetition_is_exhausted_not_outer_content_recovery():
    provider = RepeatingProvider(failures=10)
    with pytest.raises(LLMOutputRepetitionError) as error:
        support.parse_uzi_structured(provider, uzi_stage="narrative_core", schema=dict)
    assert len(provider.calls) == 2
    record = support.model_failure_record(error.value, stage="narrative_core")
    assert record["kind"] == "llm_output_repetition"
    assert not repairable_output_failures([record])
    with pytest.raises(support.UziStageFailures):
        analysis._raise_exhausted_stage([{"failure_records": [record]}], "narrative_core")
    recovery = native_recovery({"results": [{"self_review": {"execution_status": {"failure_records": [record]}}}]})
    assert recovery["kind"] == "output_invalid" and not recovery["retryable"]
    assert recovery["manual_retryable"] and recovery["exhausted_stages"] == ["narrative_core"]

    # A newly classified failure must still serialize as a native artifact.
    import jsonschema
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json").read_text())
    jsonschema.validate(recovery, schema["properties"]["recovery"])


def test_repetition_failure_has_specific_user_message_not_collect_data():
    from research_system.api.fund_holding_report_service import FundHoldingReportService
    from domain_packs.finance_ashare.subagents.uzi_stage_progress import failed_stage_next_action

    issues = [
        {"severity": "critical", "dim": "agent_analysis", "evidence": "file not found", "issue": "agent_analysis.json 不存在"},
        {"severity": "critical", "dim": "agent_analysis", "evidence": "llm_output_repetition", "issue": "正文未完成"},
        {"severity": "critical", "dim": "review_acknowledged", "issue": "仍需处理原生 warning"},
    ]
    detail = {"artifacts": [{"artifact_type": "uzi_native_analysis", "content": {"results": [{"self_review": {"issues": issues}}]}}]}
    message = FundHoldingReportService._terminal_report_error(detail, "gated")
    assert "模型连续重复输出" in message and "仍需处理原生 warning" in message
    assert "先在数据中心补齐" not in message and "agent_analysis.json 不存在" not in message
    assert "不重新采集" in failed_stage_next_action({"kind": "llm_output_repetition"})


def test_unconfirmed_runtime_exit_never_retries_original_repetition():
    error = CodexTurnError("codex_runtime_exit_unconfirmed")
    error.__context__ = LLMOutputRepetitionError()
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        raise error

    with pytest.raises(CodexTurnError):
        support.parse_uzi_structured(SimpleNamespace(parse=parse), uzi_stage="narrative_core", schema=dict)
    assert len(calls) == 1
    assert support.model_failure_kind(error) == "llm_provider_unavailable"


@pytest.mark.parametrize("repeat_first", [True, False])
def test_repetition_repair_does_not_expand_transient_retry_allowance(repeat_first):
    overload = lambda: CodexTurnError("server_overloaded", status_code=503)
    errors = [LLMOutputRepetitionError(), overload(), overload(), overload()]
    if not repeat_first:
        errors = [overload(), overload(), LLMOutputRepetitionError(), overload()]
    calls, sleeps = [], []

    def parse(**kwargs):
        calls.append(kwargs)
        raise errors[len(calls) - 1]

    provider = SimpleNamespace(parse=parse, settings=SimpleNamespace(uzi_llm_transient_retries=2))
    with patch.object(support, "sleep", side_effect=sleeps.append), patch.object(support, "random", return_value=0):
        with pytest.raises(CodexTurnError):
            support.parse_uzi_structured(provider, uzi_stage="narrative_core", schema=dict)
    assert len(calls) == 4  # Initial + two transient retries + one output repair.
    assert sleeps == [2, 4]
    assert len({call["deadline_at"] for call in calls}) == 1


@pytest.mark.parametrize("stage", [None, "qualitative_A", "roleplay_A", "buy_zone_value"])
def test_other_workloads_do_not_enable_or_retry_the_heuristic(stage):
    provider = RepeatingProvider()
    with pytest.raises(LLMOutputRepetitionError):
        support.parse_uzi_structured(provider, uzi_stage=stage, schema=dict)
    assert len(provider.calls) == 1 and "output_repetition_guard" not in provider.calls[0]


def test_cancellation_after_first_failure_prevents_repair_request():
    cancellation = Event()
    provider = RepeatingProvider(cancellation=cancellation)
    with pytest.raises(support.UziRequestCancelled):
        support.parse_uzi_structured(provider, uzi_stage="narrative_core", schema=dict, cancellation=cancellation)
    assert len(provider.calls) == 1


def test_expired_shared_budget_does_not_restart_stage():
    now = [100.0]
    provider = RepeatingProvider(clock=now)
    with patch.object(support, "monotonic", side_effect=lambda: now[0]), pytest.raises(support.UziRequestDeadline):
        support.parse_uzi_structured(provider, uzi_stage="dimensions_11_15", schema=dict, deadline_at=105)
    assert len(provider.calls) == 1


def test_exact_dimension_transport_membership_does_not_add_prose_quotas():
    original = analysis.AgentDimensionPayload.model_json_schema()
    ids = ["10_valuation", "11_governance", "12_capital_flow", "13_policy", "14_moat"]
    schema = strict_schema(analysis._dimension_batch_json_schema(ids))
    assert schema["properties"]["dimensions"]["minItems"] == 5
    assert schema["properties"]["dimensions"]["maxItems"] == 5
    assert schema["properties"]["data_gaps"]["maxItems"] == 5
    assert schema["$defs"]["AgentDimensionCommentary"]["properties"]["dimension_id"]["enum"] == ids
    assert "maxLength" not in schema["$defs"]["GroundedText"]["properties"]["text"]
    assert analysis.AgentDimensionPayload.model_json_schema() == original
