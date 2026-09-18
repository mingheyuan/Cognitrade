from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel, ValidationError

from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
    _agent_failure_kind,
    _agent_repairable,
    _review_feedback_for_agent_stage,
)
from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    UziStageFailures,
    model_failure_record,
    parse_uzi_structured,
)
from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
from research_system.runtime.providers.codex_provider import CodexProvider
from research_system.runtime.providers.errors import (
    LLMCancelledError,
    LLMDeadlineError,
    LLMProviderError,
    StructuredOutputError,
)


class Payload(BaseModel):
    value: int


@pytest.fixture(autouse=True)
def fixed_native_signature_copy(monkeypatch):
    # Stage recovery tests require identical rule-skeleton input, including
    # native random signature comments newly exposed to the narrative.
    monkeypatch.setattr("random.choice", lambda choices: choices[0])


def invalid_output():
    try:
        Payload.model_validate({"value": "bad gateway: timeout token=secret"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected invalid output")


def test_sdk_validation_is_output_error_in_both_parse_paths():
    provider = CodexProvider(SimpleNamespace(llm_request_timeout_seconds=5, default_model="test",
                                            codex_reasoning_effort="low"))
    with patch.object(provider, "_execute", return_value='{"value":"invalid"}'):
        for method in (provider.parse, provider.parse_cancellable):
            with pytest.raises(StructuredOutputError) as caught:
                method(system_prompt="sys", user_prompt="user", schema=Payload)
            assert isinstance(caught.value.__cause__, ValidationError)


def test_wrapped_validation_is_not_provider_failure_or_transient_retry():
    cause = invalid_output()
    wrapper = LLMProviderError(f"llm_request_failed: ValidationError: {cause}")
    wrapper.__cause__ = cause
    assert _agent_failure_kind(wrapper) == "agent_content_invalid"
    record = model_failure_record(wrapper, stage="qualitative_B")
    assert record["validation_fields"] == ["value"]
    assert "secret" not in str(record)
    assert "bad gateway" not in str(record)

    class Provider:
        calls = 0
        settings = SimpleNamespace(uzi_llm_max_concurrency=3)

        def parse(self, **_kwargs):
            self.calls += 1
            raise wrapper

    provider = Provider()
    with patch("domain_packs.finance_ashare.subagents.uzi_llm_support.sleep") as sleep:
        with pytest.raises(LLMProviderError):
            parse_uzi_structured(provider, schema=Payload)
    assert provider.calls == 1
    sleep.assert_not_called()


def test_mixed_failures_preserve_actual_deadline_and_output_causes():
    failures = [model_failure_record(invalid_output(), stage="qualitative_A"),
                model_failure_record(LLMDeadlineError("llm_stage_deadline_exceeded"), stage="roleplay_B")]
    aggregate = UziStageFailures(failures)
    assert _agent_failure_kind(aggregate) == "llm_stage_deadline"
    assert {x["kind"] for x in aggregate.failures} == {"llm_stage_deadline", "agent_content_invalid"}
    assert _agent_failure_kind(LLMCancelledError("llm_request_cancelled")) == "llm_cancelled"


def test_mixed_group_failure_retains_manual_scope_but_stops_automatic_waves():
    issue = {"dim": "qualitative_deep_dive", "category": "agent-os-boundary", "severity": "critical",
             "evidence": "llm_stage_deadline and ValidationError", "failure_records": [
                 model_failure_record(invalid_output(), stage="qualitative_A"),
                 model_failure_record(TimeoutError("llm_request_timeout: 5s"), stage="qualitative_B"),
             ]}
    assert not _agent_repairable([issue], SimpleNamespace(available=True))
    recovery = native_recovery({"results": [{"self_review": {"issues": [issue]}}]})
    assert {x["kind"] for x in recovery["failure_records"]} == {"agent_content_invalid", "llm_transport_timeout"}
    assert recovery["repairable_stages"] == ["qualitative_A"]
    assert recovery["exhausted_stages"] == ["qualitative_B"]
    assert recovery["retryable"] is False  # Never auto-rerun the whole stock.


def test_parallel_failure_records_do_not_leak_between_stocks():
    def run(stage, exception):
        return UziStageFailures([model_failure_record(exception, stage=stage)]).failures
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, "stock_A", invalid_output())
        b = pool.submit(run, "stock_B", LLMDeadlineError("deadline"))
    assert {x["stage"] for x in a.result()} == {"stock_A"}
    assert {x["stage"] for x in b.result()} == {"stock_B"}


def test_actual_parallel_stock_groups_repair_output_without_repeating_timeout(tmp_path):
    from test_uzi_qualitative_deep_dive import _Provider, _rows
    from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import apply_qualitative_deep_dive

    class MixedProvider(_Provider):
        def __init__(self, name, fail):
            super().__init__()
            self.fail = fail
            self.calls = {}
            self.settings = SimpleNamespace(workspace_root=tmp_path / name,
                                            default_model="test", uzi_llm_max_concurrency=4)

        def parse(self, **kwargs):
            label = kwargs["user_prompt"].splitlines()[0].split("：", 1)[1]
            with self._lock:
                self.calls[label] = self.calls.get(label, 0) + 1
                count = self.calls[label]
            if self.fail and count == 1:
                if label.startswith("A"):
                    raise invalid_output()
                if label.startswith("B"):
                    raise TimeoutError("llm_request_timeout: 5s")
            return super().parse(**{k: v for k, v in kwargs.items()
                                    if k in {"schema", "system_prompt", "user_prompt", "temperature"}})

    providers = [MixedProvider("stock-A", True), MixedProvider("stock-B", False)]
    def run(provider):
        return apply_qualitative_deep_dive({}, snapshot_rows=_rows(), llm_provider=provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, other = list(pool.map(run, providers))
    assert other["qualitative_agent_execution"]["status"] == "completed"
    failures = first["qualitative_agent_execution"]["failure_records"]
    assert {row["kind"] for row in failures} == {"agent_content_invalid", "llm_transport_timeout"}
    second = apply_qualitative_deep_dive(
        {}, snapshot_rows=_rows(), llm_provider=providers[0], previous_failures=failures,
        repair_feedback=first["qualitative_agent_execution"]["errors"],
    )
    assert {k[0]: v for k, v in providers[0].calls.items()} == {"A": 2, "B": 1, "C": 1}
    assert set(providers[1].calls.values()) == {1}
    assert second["qualitative_agent_execution"]["completed_group_count"] == 2
    assert {row["kind"] for row in second["qualitative_agent_execution"]["failure_records"]} == {"llm_transport_timeout"}
    assert second["agent_reviewed"] is False


def test_roleplay_repairs_only_invalid_group_and_retains_exhausted_group(tmp_path):
    from test_uzi_deep_roleplay import _RoleplayProvider, _panel
    from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import apply_deep_agent_roleplay

    class MixedProvider(_RoleplayProvider):
        def __init__(self):
            super().__init__()
            self.calls = {}
            self.settings = SimpleNamespace(workspace_root=tmp_path, default_model="test", uzi_llm_max_concurrency=4)

        def parse(self, **kwargs):
            label = kwargs["user_prompt"].splitlines()[0].split("：", 1)[1]
            with self._lock:
                self.calls[label] = self.calls.get(label, 0) + 1
                count = self.calls[label]
            if label.startswith("A") and count == 1:
                raise invalid_output()
            if label.startswith("C") and count == 1:
                raise TimeoutError("llm_request_timeout: 5s")
            kwargs["user_prompt"] = kwargs["user_prompt"].split("\nOUTPUT_REPAIR=", 1)[0]
            return super().parse(**{k: v for k, v in kwargs.items()
                                    if k in {"schema", "system_prompt", "user_prompt", "temperature"}})

    provider = MixedProvider()
    first = apply_deep_agent_roleplay(_panel("medium"), features={}, llm_provider=provider, required=True)
    second = apply_deep_agent_roleplay(_panel("medium"), features={}, llm_provider=provider, required=True,
                                     previous_failures=first["roleplay"]["failure_records"])
    assert next(v for k, v in provider.calls.items() if k.startswith("A")) == 2
    assert all(v == 1 for k, v in provider.calls.items() if not k.startswith("A"))
    assert {row["kind"] for row in second["roleplay"]["failure_records"]} == {"llm_transport_timeout"}
    assert second["per_investor_override"] == {}  # Never weaken native all-or-nothing gate.


def test_specific_schema_failure_does_not_invalidate_other_stage_caches():
    record = model_failure_record(invalid_output(), stage="dimensions_6_10")
    issues = [{"dim": "agent_analysis", "evidence": "agent_reviewed!=true"},
              {"dim": "agent_analysis", "evidence": "agent_content_invalid", "failure_records": [record]}]
    assert _review_feedback_for_agent_stage(issues, stage="dimensions", stage_id="dimensions_1_5") == []
    selected = _review_feedback_for_agent_stage(issues, stage="dimensions", stage_id="dimensions_6_10")
    assert len(selected) == 1
    assert selected[0]["failure_records"] == [record]
    assert _review_feedback_for_agent_stage(issues, stage="narrative") == []
    assert _review_feedback_for_agent_stage(issues, stage="buy_zone", zone_name="value") == []


def _native_missing_aggregate_issue():
    import importlib
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import _uzi_native_environment

    with _uzi_native_environment(profile="medium", school=None):
        return importlib.import_module("lib.self_review").check_agent_analysis_exists({"ag": None})[0].to_dict()


def _scoped_failure_review(**kwargs):
    """Keep the real native missing-aggregate issue; isolate unrelated data checks."""
    issues = [_native_missing_aggregate_issue()] if kwargs["agent_analysis"] is None else []
    for issue in kwargs["agent_validation_issues"]:
        issues.append({
            "severity": "critical", "category": "agent-os-boundary", "dim": issue["field"],
            "issue": issue["message"], "evidence": issue["failure_kind"],
            "suggested_fix": issue["suggestion"], "failure_records": issue["failure_records"],
        })
    qualitative = kwargs["qualitative_execution"]
    if qualitative.get("failure_records"):
        issues.append({"severity": "critical", "category": "agent-os-boundary",
                       "dim": "qualitative_deep_dive", "issue": "定性组输出错误",
                       "failure_records": qualitative["failure_records"]})
    return {"issues": issues, "critical_count": len(issues), "warning_count": 0, "info_count": 0}


@pytest.mark.parametrize("stage_args", [
    *[{"stage": "dimensions", "stage_id": f"dimensions_{start}_{start + 4}"} for start in (1, 6, 11, 16)],
    {"stage": "narrative"}, {"stage": "buy_zone", "zone_name": "growth"},
])
def test_native_missing_aggregate_is_not_broadcast_over_scoped_failure(stage_args):
    from copy import deepcopy
    native = _native_missing_aggregate_issue()
    cause = {"dim": "agent_analysis", "failure_records": [
        model_failure_record(LLMDeadlineError("deadline"), stage="buy_zone_value"),
    ]}
    issues = [native, cause]
    original = deepcopy(issues)
    assert _review_feedback_for_agent_stage(issues, **stage_args) == []
    assert issues == original  # Native critical is not deleted or downgraded.
    assert _review_feedback_for_agent_stage([native], **stage_args) == [native]
    unscoped = {"dim": "agent_analysis", "failure_records": [
        model_failure_record(invalid_output(), stage="agent_analysis"),
    ]}
    assert _review_feedback_for_agent_stage([native, unscoped], **stage_args) == [native, unscoped]
    unrelated = {"dim": "agent_analysis", "issue": "真正的通用结构错误", "evidence": "invalid root type"}
    assert _review_feedback_for_agent_stage([*issues, unrelated], **stage_args) == [unrelated]


@pytest.mark.parametrize("profile,expected_kind", [("lite", "pipeline_failure"), ("medium", "source_gap")])
def test_qualitative_input_budget_failure_does_not_start_downstream_analysis(profile, expected_kind):
    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import execute_native_analysis
    from domain_packs.finance_ashare.subagents.uzi_input_views import UziInputBudgetExceeded

    provider = _LiteAgentProvider()
    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    with (
        patch(module + ".build_native_panel_overrides", return_value={
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".apply_qualitative_deep_dive",
              side_effect=UziInputBudgetExceeded("input_budget_exceeded: recorded request")),
    ):
        # This fixture covers lite inputs. Medium deliberately retains real
        # missing dimensions, independently of the deterministic prompt error.
        result = execute_native_analysis(_lite_snapshots(), ["600000"], profile=profile,
                                         llm_provider=provider, full_agent_workflow=True)
    assert provider.calls == 0
    assert result["report_allowed"] is False
    assert result["recovery"]["kind"] == expected_kind
    assert bool(result["recovery"]["dimensions"]) is (profile == "medium")
    assert result["recovery"]["retryable"] is False
    assert any(record["error_type"] == "UziInputBudgetExceeded"
               for record in result["recovery"]["failure_records"])


def test_value_deadline_recovery_reuses_successful_same_input_stage_caches(tmp_path, monkeypatch):
    # This is an Agent-mode recovery contract. The native CLI/CI mode explicitly
    # downgrades missing agent_analysis to warning and is a different contract.
    monkeypatch.setenv("CI", "false")
    monkeypatch.delenv("UZI_CLI_ONLY", raising=False)
    from collections import Counter
    from threading import Lock
    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
        AgentBuyZonePayload, AgentDimensionPayload, AgentNarrativeCorePayload, execute_native_analysis,
    )

    class Provider(_LiteAgentProvider):
        def __init__(self):
            super().__init__()
            self.settings.workspace_root = tmp_path
            self.settings.uzi_llm_max_concurrency = 4
            self.counts, self.lock, self.other_zones = Counter(), Lock(), Event()

        def parse(self, **kwargs):
            schema = kwargs["schema"]
            stage = schema.__name__
            if schema is AgentBuyZonePayload:
                stage = next(name for name in ("value", "growth", "technical", "youzi")
                             if f" {name} 买入区间" in kwargs["user_prompt"])
            with self.lock:
                self.counts[stage] += 1
                if all(self.counts[name] for name in ("growth", "technical", "youzi")):
                    self.other_zones.set()
            if stage == "value":
                assert self.other_zones.wait(2)
                raise LLMDeadlineError("llm_stage_deadline_exceeded")
            return super().parse(**kwargs)

    provider = Provider()
    complete = {"qualitative_agent_execution": {"required": True, "status": "completed", "agent_reviewed": True}}
    incomplete = {"qualitative_agent_execution": {"required": True, "status": "incomplete", "agent_reviewed": False,
                  "failure_records": [model_failure_record(invalid_output(), stage="qualitative_A")]}}
    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    with (
        patch(module + ".build_native_panel_overrides", return_value={
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".apply_qualitative_deep_dive", side_effect=[incomplete, complete]),
        patch(module + ".run_native_self_review", side_effect=_scoped_failure_review),
    ):
        result = execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                                         llm_provider=provider, full_agent_workflow=True)
    assert provider.counts[AgentDimensionPayload.__name__] == 4
    assert provider.counts[AgentNarrativeCorePayload.__name__] == 1
    assert {key: provider.counts[key] for key in ("value", "growth", "technical", "youzi")} == {
        "value": 1, "growth": 1, "technical": 1, "youzi": 1,
    }
    issues = result["results"][0]["self_review"]["issues"]
    assert any(row.get("issue") == "agent_analysis.json 不存在" and row["severity"] == "critical" for row in issues)
    assert result["report_allowed"] is False


def test_exhausted_value_stops_more_waves_and_stays_run_local():
    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import _raise_exhausted_stage, execute_native_analysis
    provider = _LiteAgentProvider()
    value_failure = model_failure_record(LLMDeadlineError("deadline"), stage="buy_zone_value")
    calls = []

    def stages(*args, **kwargs):
        calls.append(kwargs["review_feedback"])
        if len(calls) == 1:
            raise UziStageFailures([value_failure])
        _raise_exhausted_stage(kwargs["review_feedback"], "buy_zone_value")
        return {"agent_reviewed": True}  # A budget leak would incorrectly reach this.

    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    incomplete = {"qualitative_agent_execution": {"required": True, "status": "incomplete", "agent_reviewed": False,
                  "failure_records": [model_failure_record(invalid_output(), stage="qualitative_A")]}}
    complete = {"qualitative_agent_execution": {"required": True, "status": "completed", "agent_reviewed": True}}
    with (
        patch(module + ".build_native_panel_overrides", return_value={
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".apply_qualitative_deep_dive", side_effect=[incomplete, complete, complete]),
        patch(module + "._generate_agent_analysis", side_effect=stages),
        patch(module + ".run_native_self_review", side_effect=_scoped_failure_review),
        patch(module + "._validate_agent_analysis", return_value=[]),
    ):
        result = execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                                         llm_provider=provider, full_agent_workflow=True)
        assert len(calls) == 1  # Research repaired first; no post-deadline wave.
        assert value_failure in result["recovery"]["failure_records"]
        assert result["report_allowed"] is False
        # A new execution (even with the same provider) gets no other run's
        # exhausted-stage ledger and can use its own valid evidence normally.
        other = execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                                        llm_provider=provider, full_agent_workflow=True)
        assert calls[1] == []
        assert other["report_allowed"] is True


@pytest.mark.parametrize("wave", ["dimensions", "buy_zones"])
def test_parallel_wave_keeps_both_executed_deadlines_for_next_cycle(tmp_path, wave):
    import re
    from collections import Counter
    from threading import Barrier, Lock
    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
        AgentBuyZonePayload, AgentDimensionPayload, execute_native_analysis,
    )
    targets = {"0_basic", "5_chain"} if wave == "dimensions" else {"value", "growth"}

    class Provider(_LiteAgentProvider):
        def __init__(self):
            super().__init__()
            self.settings.workspace_root = tmp_path
            self.settings.uzi_llm_max_concurrency = 4
            self.counts, self.lock, self.barrier = Counter(), Lock(), Barrier(2)

        def parse(self, **kwargs):
            stage = None
            if wave == "dimensions" and kwargs["schema"] is AgentDimensionPayload:
                stage = re.search(r"必须且只能是：([^；]+)；", kwargs["user_prompt"])[1].split(",")[0]
            if wave == "buy_zones" and kwargs["schema"] is AgentBuyZonePayload:
                stage = next(name for name in ("value", "growth", "technical", "youzi")
                             if f" {name} 买入区间" in kwargs["user_prompt"])
            with self.lock:
                self.counts[stage] += 1
                first = self.counts[stage] == 1
            if stage in targets:
                if first:
                    self.barrier.wait(timeout=2)  # Both really entered the provider.
                raise LLMDeadlineError("llm_stage_deadline_exceeded")
            return super().parse(**kwargs)

    provider = Provider()
    reviews = []

    def review(**kwargs):
        result = _scoped_failure_review(**kwargs)
        reviews.append(result)
        return result

    incomplete = {"qualitative_agent_execution": {"required": True, "status": "incomplete", "agent_reviewed": False,
                  "failure_records": [model_failure_record(invalid_output(), stage="qualitative_A")]}}
    complete = {"qualitative_agent_execution": {"required": True, "status": "completed", "agent_reviewed": True}}
    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    with (
        patch(module + ".build_native_panel_overrides", return_value={
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".apply_qualitative_deep_dive", side_effect=[incomplete, complete]),
        patch(module + ".run_native_self_review", side_effect=review),
    ):
        result = execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                                         llm_provider=provider, full_agent_workflow=True)
    failures = [failure for row in reviews[-1]["issues"] for failure in row.get("failure_records", [])
                if failure["kind"] == "llm_stage_deadline"]
    expected = {"dimensions_1_5", "dimensions_6_10"} if wave == "dimensions" else {"buy_zone_value", "buy_zone_growth"}
    assert {failure["stage"] for failure in failures} == expected
    assert {stage: provider.counts[stage] for stage in targets} == {stage: 1 for stage in targets}
    assert result["report_allowed"] is False


@pytest.mark.parametrize("control", ["cancel", "deadline"])
def test_buy_zone_aggregation_does_not_delay_global_control(tmp_path, control):
    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import AgentBuyZonePayload, execute_native_analysis
    from domain_packs.finance_ashare.subagents.uzi_llm_support import UziRequestCancelled
    clock, cancellation = [0.0], Event()

    class Provider(_LiteAgentProvider):
        def parse(self, **kwargs):
            if kwargs["schema"] is AgentBuyZonePayload:
                if control == "cancel":
                    cancellation.set()
                else:
                    clock[0] = 20.0
                raise invalid_output()
            return super().parse(**kwargs)

    provider = Provider()
    provider.settings.workspace_root = tmp_path
    provider.settings.uzi_llm_max_concurrency = 4
    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    with (
        patch(module + ".monotonic", side_effect=lambda: clock[0]),
        patch("domain_packs.finance_ashare.subagents.uzi_llm_support.monotonic", side_effect=lambda: clock[0]),
        patch(module + ".build_native_panel_overrides", return_value={
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".apply_qualitative_deep_dive", return_value={
            "qualitative_agent_execution": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".run_native_self_review") as review,
    ):
        if control == "cancel":
            with pytest.raises(UziRequestCancelled):
                execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium", llm_provider=provider,
                                        full_agent_workflow=True, cancellation=cancellation)
        else:
            # The public multi-stock adapter records a per-symbol global
            # deadline result; no further self-review/repair cycle may run.
            result = execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium", llm_provider=provider,
                                             full_agent_workflow=True, cancellation=cancellation, deadline_at=10.0)
            assert result["results"][0]["computation_error"].startswith("UziRequestDeadline:")
            assert result["report_allowed"] is False
        review.assert_not_called()


def test_cancelled_unstarted_buy_zone_future_does_not_become_a_fake_failure(tmp_path):
    from concurrent.futures import Future, ThreadPoolExecutor
    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import execute_native_analysis
    future_states = []

    class ZoneExecutor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, fn, zone, *_args):
            future = Future()
            if zone == "value":
                future.set_running_or_notify_cancel()
                future.set_exception(UziStageFailures([
                    model_failure_record(LLMDeadlineError("deadline"), stage="buy_zone_value"),
                ]))
            else:
                future.cancel()
                future.set_running_or_notify_cancel()  # Executor acknowledges no work ran.
            future_states.append((zone, future))
            return future

    provider = _LiteAgentProvider()
    provider.settings.workspace_root = tmp_path
    provider.settings.uzi_llm_max_concurrency = 4
    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    with (
        patch(module + ".ThreadPoolExecutor", side_effect=lambda **kwargs:
              ZoneExecutor() if kwargs.get("thread_name_prefix") == "uzi-buy-zones" else ThreadPoolExecutor(**kwargs)),
        patch(module + ".build_native_panel_overrides", return_value={
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".apply_qualitative_deep_dive", return_value={
            "qualitative_agent_execution": {"required": True, "status": "completed", "agent_reviewed": True},
        }),
        patch(module + ".run_native_self_review", side_effect=_scoped_failure_review),
    ):
        result = execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                                         llm_provider=provider, full_agent_workflow=True)
    records = [failure for row in result["results"][0]["self_review"]["issues"]
               for failure in row.get("failure_records", [])]
    assert {row["stage"] for row in records} == {"buy_zone_value"}
    assert all(future.cancelled() for zone, future in future_states if zone != "value")


def test_changed_roleplay_refreshes_narrative_but_reuses_independent_dimensions(tmp_path):
    """Exercise real stage caches; fake outputs/review isolate dependency routing."""
    from collections import Counter
    from threading import Lock

    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
        AgentDimensionPayload,
        AgentNarrativeCorePayload,
        execute_native_analysis,
    )

    class Provider(_LiteAgentProvider):
        def __init__(self):
            super().__init__()
            self.settings.workspace_root = tmp_path
            self.counts = Counter()
            self.lock = Lock()
            self.narrative_prompts = []

        def parse(self, **kwargs):
            schema = kwargs["schema"]
            with self.lock:
                self.counts[schema] += 1
                revision = self.counts[schema]
                if schema is AgentNarrativeCorePayload:
                    self.narrative_prompts.append(kwargs["system_prompt"])
            payload = super().parse(**kwargs)
            if schema is AgentNarrativeCorePayload:
                payload["core_conclusion"] = f"仅依据本轮冻结数据与评委结果生成的谨慎结论，叙事版本 {revision}。"
            return payload

    provider = Provider()
    roleplay_attempts = []

    def roleplay(panel, **_kwargs):
        roleplay_attempts.append(True)
        if len(roleplay_attempts) == 1:
            return {
                "roleplay": {"required": True, "status": "incomplete", "agent_reviewed": False,
                             "failure_records": [model_failure_record(invalid_output(), stage="roleplay_A+B")]},
                "per_investor_override": {},
            }
        investor = panel["investors"][0]["investor_id"]
        return {
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
            "per_investor_override": {investor: {"signal": "bullish", "score": 99,
                                                   "headline": "测试角色评审第二轮的不同结论"}},
        }

    panel_issue = {"severity": "critical", "category": "agent-os-boundary", "dim": "panel",
                   "issue": "完整 Agent 评委角色扮演未完成", "evidence": "status=incomplete",
                   "suggested_fix": "修复失败评委组"}
    reviews = [
        {"issues": [panel_issue], "critical_count": 1, "warning_count": 0, "info_count": 0},
        {"issues": [], "critical_count": 0, "warning_count": 0, "info_count": 0},
    ]
    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    with (
        patch(module + ".build_native_panel_overrides", side_effect=roleplay),
        patch(module + ".apply_qualitative_deep_dive", return_value={
            "qualitative_agent_execution": {"required": True, "status": "completed", "agent_reviewed": True},
            "qualitative_deep_dive": {},
        }) as qualitative,
        patch(module + ".run_native_self_review", side_effect=reviews),
        patch(module + "._validate_agent_analysis", return_value=[]),
    ):
        result = execute_native_analysis(_lite_snapshots(), ["600000"], profile="medium",
                                         llm_provider=provider, full_agent_workflow=True)

    stock = result["results"][0]
    assert len(roleplay_attempts) == 2
    assert qualitative.call_count == 1
    assert provider.counts[AgentDimensionPayload] == 4  # Twenty dimensions, once only.
    assert provider.counts[AgentNarrativeCorePayload] == 1  # No provisional report before roleplay repair.
    assert [row["reused_agent_analysis"] for row in stock["recovery_history"]] == [False, False]
    assert "叙事版本 1" in stock["agent_analysis"]["narrative_override"]["core_conclusion"]


def _real_frozen_input_failures():
    """Minimal cause vector from real run 01M1X6PZZ1DDR8SFW29VJ1D2XF.

    Keep its original StructuredOutputError; source_gap is a separate recovery
    diagnosis after the first Agent attempt, never a replacement exception.
    """
    failures = []
    for label, fields in (
        ("A · Macro-Policy", ["dimensions.1.evidence.0"]),
        ("C · Cost-Transmission", ["dimensions.0.evidence.0", "dimensions.0.evidence.1",
                                    "dimensions.1.evidence.0", "dimensions.1.evidence.1"]),
    ):
        failures.extend([
            {"stage": f"qualitative_{label}", "kind": "agent_content_invalid",
             "error_type": "StructuredOutputError", "validation_fields": fields,
             "validation_codes": ["value_error"]},
            {"stage": f"qualitative_{label}", "kind": "source_gap",
             "error_type": "FrozenEvidenceUnavailable"},
        ])
    return failures


@pytest.mark.parametrize("stage_args", [
    {"stage": "dimensions", "stage_id": "dimensions_16_20", "dimension_keys": ("18_trap", "19_contests")},
    {"stage": "narrative"}, {"stage": "buy_zone", "zone_name": "value"},
])
def test_fixed_native_data_critical_never_invalidates_output_stage(stage_args):
    from copy import deepcopy
    issue = {"severity": "critical", "category": "data", "dim": "19_contests",
             "issue": "维度 19_contests data 为空", "evidence": "_timeout=False, error=",
             "suggested_fix": "补齐后写入 agent_analysis.dim_commentary"}
    original = deepcopy(issue)
    assert _review_feedback_for_agent_stage([issue], **stage_args) == []
    assert issue == original


@pytest.mark.parametrize("repair_other_group", [False, True])
def test_real_source_gap_vector_keeps_first_full_analysis_without_futile_recompute(tmp_path, repair_other_group):
    import importlib
    from collections import Counter
    from copy import deepcopy
    from tests.test_uzi_392_analysis import _LiteAgentProvider, _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
        AgentDimensionPayload, AgentNarrativeCorePayload, execute_native_analysis,
    )

    class Provider(_LiteAgentProvider):
        def __init__(self):
            super().__init__()
            self.settings.workspace_root = tmp_path
            self.counts = Counter()

        def parse(self, **kwargs):
            self.counts[kwargs["schema"]] += 1
            return super().parse(**kwargs)

    raw19 = {"data": {}, "source": "cognitrade-historical-disclosure-v1",
             "_degradation": {"safe_for_report": True, "excluded_from_scoring": True,
                              "reason_code": "historical_source_current_only"}}
    snapshots = _lite_snapshots() + [{"snapshot_type": "contests", "symbol": "600000",
                                    "facts": {"_uzi_raw_result": deepcopy(raw19)}}]
    before = deepcopy(snapshots)
    execution = {"required": True, "status": "incomplete", "agent_reviewed": False,
                 "completed_group_count": 1, "failure_records": _real_frozen_input_failures(),
                 "input_gaps": [f"{dim}:frozen_citations_unavailable"
                                for dim in ("3_macro", "13_policy", "8_materials", "9_futures")]}
    executions = [deepcopy(execution)]
    if repair_other_group:
        # A has complete inputs and only a real output error. C and raw19 are
        # still fixed input gaps: repairing A must not invalidate other work.
        execution["failure_records"] = [row for row in execution["failure_records"]
                                        if not (row["stage"].startswith("qualitative_A") and row["kind"] == "source_gap")]
        execution["input_gaps"] = [gap for gap in execution["input_gaps"]
                                   if gap.startswith(("8_materials:", "9_futures:"))]
        repaired = deepcopy(execution)
        repaired["failure_records"] = [row for row in repaired["failure_records"]
                                       if not row["stage"].startswith("qualitative_A")]
        executions = [execution, repaired]

    def review(**kwargs):
        current_execution = kwargs["qualitative_execution"]
        # Unmodified native check runs against the actual analysis raw input.
        native = [row.to_dict() for row in importlib.import_module("lib.self_review").check_empty_dims(
            {"dims": kwargs["raw"]["dimensions"]}) if row.dim == "19_contests"]
        assert native[0]["severity"] == "critical"
        return {"issues": [*native, {"severity": "critical", "category": "agent-os-boundary",
                "dim": "qualitative_deep_dive", "issue": "六维定性研究未满足 UZI 证据门",
                "evidence": str(current_execution), "failure_records": current_execution["failure_records"]}],
                "critical_count": 2, "warning_count": 0, "info_count": 0}

    provider = Provider()
    module = "domain_packs.finance_ashare.subagents.uzi_392_analysis"
    with (
        patch(module + ".build_native_panel_overrides", return_value={
            "roleplay": {"required": True, "status": "completed", "agent_reviewed": True},
        }) as roleplay,
        patch(module + ".apply_qualitative_deep_dive", side_effect=[{
            "qualitative_agent_execution": item, "qualitative_deep_dive": {},
        } for item in executions]) as qualitative,
        patch(module + ".run_native_self_review", side_effect=review),
    ):
        result = execute_native_analysis(snapshots, ["600000"], profile="medium",
                                         llm_provider=provider, full_agent_workflow=True)
    assert roleplay.call_count == 1
    assert qualitative.call_count == (2 if repair_other_group else 1)
    assert provider.counts[AgentDimensionPayload] == 4
    assert provider.counts[AgentNarrativeCorePayload] == 1
    assert result["results"][0]["raw"]["dimensions"]["19_contests"] == raw19
    assert snapshots == before
    assert result["critical_count"] == 2 and result["report_allowed"] is False
    assert result["recovery"]["action"] == "collect_data"
    assert "new_manifest_required" in result["recovery"]["reason_codes"]
    assert result["recovery"]["dimensions"] == (
        ["19_contests", "8_materials", "9_futures"] if repair_other_group
        else ["13_policy", "19_contests", "3_macro", "8_materials", "9_futures"]
    )
    assert result["recovery"]["repairable_stages"] == []
    assert result["recovery"]["failure_records"] == executions[-1]["failure_records"]


def test_input_blocked_qualitative_group_preserves_error_but_other_group_repairs(tmp_path):
    from copy import deepcopy
    from test_uzi_qualitative_deep_dive import _Provider, _rows
    from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import apply_qualitative_deep_dive

    class Provider(_Provider):
        def __init__(self):
            super().__init__()
            self.calls = {}
            self.settings = SimpleNamespace(workspace_root=tmp_path, default_model="test", uzi_llm_max_concurrency=3)

        def parse(self, **kwargs):
            label = kwargs["user_prompt"].splitlines()[0].split("：", 1)[1]
            with self._lock:
                self.calls[label] = self.calls.get(label, 0) + 1
                first = self.calls[label] == 1
            if first and label[0] in {"A", "C"}:
                raise invalid_output()
            return super().parse(**{k: v for k, v in kwargs.items()
                                    if k in {"schema", "system_prompt", "user_prompt", "temperature"}})

    rows = _rows()
    for row in rows:
        if row["snapshot_type"] in {"materials", "futures"}:
            row["facts"]["sources"] = []
    before = deepcopy(rows)
    provider = Provider()
    first = apply_qualitative_deep_dive({}, snapshot_rows=rows, llm_provider=provider)
    failures = first["qualitative_agent_execution"]["failure_records"]
    c_failures = [row for row in failures if row["stage"].startswith("qualitative_C")]
    assert {row["kind"] for row in c_failures} == {"source_gap", "agent_content_invalid"}
    issue = {"category": "agent-os-boundary", "dim": "qualitative_deep_dive", "failure_records": failures}
    assert _agent_repairable([issue], provider)  # A still has usable input.
    second = apply_qualitative_deep_dive({}, snapshot_rows=rows, llm_provider=provider,
                                       previous_failures=failures)
    assert {k[0]: v for k, v in provider.calls.items()} == {"A": 2, "B": 1, "C": 1}
    assert second["qualitative_agent_execution"]["failure_records"] == c_failures
    assert rows == before
