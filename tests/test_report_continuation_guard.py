"""Frozen-input admission must precede continuation cache/scheduler reuse."""
from copy import deepcopy
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from research_system.market_data.report_inputs import ReportInputError, ReportInputService
from research_system.runtime.agent_os import AgentOS
from research_system.schemas.protocol import RequestEnvelope
from research_system.strategies.runtime_pins import deployed_workflow_pins


def request_for(day="2026-09-02", *, pins=None):
    row = {
        "dimension_id": "9_futures",
        "facts": {"contracts": [{"latest_record": {"日期": day, "收盘价": 962}}]},
    }
    return RequestEnvelope(intent="stock_check", user_query="历史报告", slots={
        "report_data_manifest": {
            "as_of_date": "2026-09-02", "workflow_pins": pins or {},
            "items": [{"snapshots": [row]}],
        },
        "snapshots": [row],
    }, constraints={"data_source_mode": "bundle_only"})


def boundary():
    # No gateway mutation/materialization is available to continuation checks.
    return ReportInputService(SimpleNamespace(runtime_settings=None))


@pytest.mark.parametrize("checkpoint", ["analysis_completed_composer_pending", "analysis_cache_hit"])
def test_future_facts_rejected_before_resume_scheduler_or_cache(checkpoint):
    agent = object.__new__(AgentOS)
    agent._runner = Mock(side_effect=AssertionError(f"must not reach {checkpoint}"))
    agent.bind_continuation_preflight(boundary().validate_continuation_request)
    request = request_for("2026-09-03")
    original = request.model_dump(mode="json")
    with pytest.raises(ReportInputError) as error:
        agent._run_graph_runner(run_id="old-run", request=request, thread_id="thread",
                                resume=True, step=True)
    assert error.value.code == "bundle_snapshot_future_price_fact"
    agent._runner.assert_not_called()
    assert request.model_dump(mode="json") == original


@pytest.mark.parametrize("method", ["_recover_graph_task", "_recover_graph_worker_task", "_rerun_graph_task"])
def test_recovery_guard_precedes_checkpoint_mutation(method):
    agent = object.__new__(AgentOS)
    agent._load_graph_request = lambda run_id: (request_for("2026-09-03"), None)
    agent._runner = Mock(side_effect=AssertionError("checkpoint must not be rewritten"))
    agent.bind_continuation_preflight(boundary().validate_continuation_request)
    args = ("old-run", "composer") if method == "_rerun_graph_task" else ("old-run",)
    with pytest.raises(ReportInputError, match="截止日"):
        getattr(agent, method)(*args, step=True)
    agent._runner.assert_not_called()


def test_safe_continuation_keeps_cache_reuse_and_isolates_other_run(monkeypatch):
    import research_system.runtime.agent_os as module
    calls = []
    validator = boundary().validate_continuation_request
    agent = object.__new__(AgentOS)
    agent.bind_continuation_preflight(lambda request: (calls.append("guard"), validator(request)))
    agent._lock, agent._active = RLock(), {}
    agent._update_graph_agent_state = lambda result: None
    snapshot = SimpleNamespace(graph={}, run_id="safe", thread_id="thread", request_id="request",
                               user_id=None, tenant_id=None, status="running")
    result = SimpleNamespace(snapshot=snapshot)
    outcome = SimpleNamespace(result=result, model_copy=lambda **kwargs: result)
    runner = SimpleNamespace(snapshot_path="not-read", step=lambda **kwargs: (calls.append("cache_hit"), outcome)[1])
    agent._runner = lambda *args, **kwargs: runner
    monkeypatch.setattr(module, "read_json", lambda path: {})
    monkeypatch.setattr(module.ExecutionRunSnapshot, "model_validate", lambda payload: snapshot)
    with pytest.raises(ReportInputError):
        agent._run_graph_runner(run_id="bad", request=request_for("2026-09-03"),
                                thread_id="thread", resume=True, step=True)
    calls.clear()
    safe = request_for(pins=deployed_workflow_pins())
    original = safe.model_dump(mode="json")
    assert agent._run_graph_runner(run_id="safe", request=safe, thread_id="thread",
                                   resume=True, step=True) is result
    assert calls == ["guard", "cache_hit"]
    assert safe.model_dump(mode="json") == original


@pytest.mark.parametrize("changed", ["prompt_hash", "model_id", "workflow_commit", "model_parameters"])
def test_complete_pins_must_match_current_execution(changed):
    pins = deployed_workflow_pins()
    pins[changed] = {"reasoning_effort": "different"} if changed == "model_parameters" else "different"
    with pytest.raises(ReportInputError) as error:
        boundary().validate_continuation_request(request_for(pins=pins))
    assert error.value.code == "report_workflow_version_mismatch"


def test_legacy_pins_and_future_events_remain_read_only_compatible():
    request = request_for(pins={"prompt_hash": "legacy-partial"})
    request.slots["snapshots"][0]["facts"]["contracts"][0]["expiry_date"] = "2027-01-01"
    request.slots["snapshots"][0]["facts"]["future_events"] = [{"date": "2027-01-01"}]
    original = deepcopy(request.model_dump(mode="json"))
    boundary().validate_continuation_request(request)
    boundary().validate_continuation_request(RequestEnvelope(user_query="普通请求"))
    assert request.model_dump(mode="json") == original


def test_legacy_separate_snapshot_slots_cannot_hide_future_prices():
    request = request_for("2026-09-03")
    request.slots["report_data_manifest"]["items"][0]["snapshots"] = []
    with pytest.raises(ReportInputError):
        boundary().validate_continuation_request(request)


def test_false_historical_empty_is_rejected_before_cache_reuse():
    request = request_for()
    row = request.slots["snapshots"][0]
    row["facts"] = {"observation_status": "verified_empty"}
    row["query_manifest"] = {
        "provider": "uzi_futures_multi", "pit_mode": "historical_reconstruction",
        "collection_outcome": "verified_empty", "source_trace": [],
    }
    original = request.model_dump(mode="json")
    with pytest.raises(ReportInputError) as error:
        boundary().validate_continuation_request(request)
    assert error.value.code == "bundle_snapshot_unproven_empty_result"
    assert request.model_dump(mode="json") == original


def test_service_binds_guard_before_deferred_recovery():
    from research_system.api.agent_os_run_service import AgentOSRunService
    events = []
    service = object.__new__(AgentOSRunService)
    service.agent_os = SimpleNamespace(
        bind_resolved_request_preflight=lambda callback: events.append("initial"),
        bind_continuation_preflight=lambda callback: events.append("continuation"),
    )
    service._fund_holding_reports = SimpleNamespace(recover_pending_batches=lambda: events.append("fund"))
    service._deferred_report_recovery = True
    service._recover_pending_runs = lambda: events.append("deferred")
    service.bind_report_inputs(boundary())
    assert events == ["initial", "continuation", "deferred", "fund"]


def test_startup_defers_frozen_report_until_safety_boundary_is_bound(tmp_path, monkeypatch):
    import research_system.api.agent_os_run_service as module
    run_root = tmp_path / "old-report"
    run_root.mkdir()
    request = request_for("2026-09-03").model_dump(mode="json")
    monkeypatch.setattr(module, "read_json", lambda path: {
        "agent_os_pending.json": {}, "request.json": request,
    }.get(path.name))
    service = object.__new__(module.AgentOSRunService)
    service.agent_os = SimpleNamespace(settings=SimpleNamespace(runs_root=tmp_path), adopt_prepared_run=Mock())
    service._report_inputs = None
    service._deferred_report_recovery = False
    service._submit_background_worker = Mock()
    service._recover_pending_runs()
    assert service._deferred_report_recovery is True
    service.agent_os.adopt_prepared_run.assert_not_called()
    service._submit_background_worker.assert_not_called()


def test_existing_artifact_replay_does_not_apply_new_execution_guard():
    agent = object.__new__(AgentOS)
    guard = Mock(side_effect=AssertionError("reading old reports must remain available"))
    agent.bind_continuation_preflight(guard)
    agent._runner = Mock()
    agent._runner.return_value.replay.return_value = "immutable old artifacts"
    assert agent.replay("old-report") == "immutable old artifacts"
    guard.assert_not_called()
