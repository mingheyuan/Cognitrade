"""Offline acceptance attribution; no business APIs or model requests."""

from copy import deepcopy

import pytest

from scripts import run_uzi_report_e2e as e2e


def reports(review=None, *, status="failed", source=None):
    payload = {"report_allowed": False, "critical_count": 1, **(source or {})}
    if review is not None:
        payload["results"] = [{"self_review": deepcopy(review)}]
    return [{"id": "report-set", "nodes": [{
        "id": "node", "node_type": "holding_uzi", "subject_code": "688041",
        "run_id": "run", "status": status, "source": payload,
    }]}]


def layered(**updates):
    return {
        "result_scope": "combined_delivery_gate",
        "native_python_review": {"passed": True, "critical_count": 0},
        "native_skill_completion": {"status": "completed", "critical_count": 0},
        "execution_status": {"status": "completed"},
        **updates,
    }


def test_unassessed_dependency_is_not_native_refusal():
    data = reports(layered(
        input_dependencies={"status": "unassessed", "reason_codes": ["native_quant_inputs_missing"]},
        native_python_review={"status": "not_run"},
        native_skill_completion={"status": "not_started"},
        execution_status={"status": "not_started"},
    ))
    status, counts = e2e.classify_reports(data)
    assert status == "input_blocked"
    assert counts["input_blocked"] == 1
    assert counts["native_blocked"] == 0
    summary = e2e.summarize_report_acceptance(data)
    assert summary["layer_counts"]["input_unassessed"] == 1
    assert summary["layer_counts"]["native_python_critical"] == 0
    assert summary["holdings"][0]["layers"]["native_python_review"]["statuses"] == ["not_run"]


def test_execution_failure_does_not_erase_native_or_skill_blocker():
    data = reports(layered(
        native_python_review={"passed": False, "critical_count": 1},
        native_skill_completion={"status": "incomplete", "critical_count": 1},
        execution_status={"status": "failed", "failure_records": [{"kind": "pipeline_failure"}]},
    ))
    assert e2e.classify_reports(data)[0] == "generation_failed"
    summary = e2e.summarize_report_acceptance(data)
    assert summary["counts"]["system_failed"] == 1
    assert summary["layer_counts"]["execution_failed"] == 1
    assert summary["layer_counts"]["native_python_critical"] == 1
    assert summary["layer_counts"]["skill_incomplete"] == 1


def test_skill_incomplete_is_not_a_native_python_critical():
    data = reports(layered(native_skill_completion={"status": "incomplete", "critical_count": 1}))
    status, counts = e2e.classify_reports(data)
    assert status == "skill_incomplete"
    assert counts["skill_incomplete"] == 1
    assert counts["native_blocked"] == 0


def test_real_native_python_critical_is_native_blocked():
    data = reports(layered(native_python_review={"passed": False, "critical_count": 1}))
    assert e2e.classify_reports(data)[1]["native_blocked"] == 1


def test_layer_failure_cannot_be_overridden_by_flat_delivery_flag():
    data = reports(layered(execution_status={"status": "failed"}),
                   status="completed", source={"report_allowed": True})
    assert e2e.classify_reports(data)[1]["delivered"] == 0


def test_legacy_aggregate_is_compatible_but_not_pure_native_evidence():
    data = reports()
    assert e2e.classify_reports(data)[1]["native_blocked"] == 1
    summary = e2e.summarize_report_acceptance(data)
    assert summary["layer_counts"]["legacy_unattributed"] == 1
    assert summary["layer_counts"]["native_python_critical"] == 0
    assert summary["holdings"][0]["review_scope"] == "legacy_unattributed"


def runner():
    run = object.__new__(e2e.LiveAcceptance)
    run.state = {"strategy_version_id": "version", "native_report": "allowed", "stages": {}}
    run.save = lambda: None
    run.export = lambda *_: None
    return run


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_uncompleted_backtest_is_not_zero_trade_acceptance(status):
    run = runner()
    run.api = lambda method, path, payload=None: (
        {"can_run": True} if path.endswith("/preflight") else {"id": "backtest"}
    )
    run.wait = lambda *_args, **_kwargs: {"status": status}
    run.backtest()
    assert run.state["trade_count"] is None
    assert run.state["trading_execution_demonstrated"] is False
    assert run.state["replay"] == status


def test_completed_empty_ledger_is_the_only_zero_trade_success():
    run = runner()
    run.api = lambda method, path, payload=None: (
        {"can_run": True} if path.endswith("/preflight") else {"id": "backtest"}
    )
    run.wait = lambda *_args, **_kwargs: {"status": "completed", "result": {"trades": []}}
    run.backtest()
    assert run.state["trade_count"] == 0
    assert run.state["replay"] == "completed_without_trades"


def test_completed_without_ledger_is_not_invented_zero_trades():
    run = runner()
    run.api = lambda method, path, payload=None: (
        {"can_run": True} if path.endswith("/preflight") else {"id": "backtest"}
    )
    run.wait = lambda *_args, **_kwargs: {"status": "completed"}
    run.backtest()
    assert run.state["trade_count"] is None
    assert run.state["replay"] == "failed"


def test_blocked_preflight_does_not_create_run_or_claim_zero_trades():
    run = runner()
    calls = []

    def api(method, path, payload=None):
        calls.append((method, path))
        return {"can_run": False}

    run.api = api
    run.backtest()
    assert calls == [("POST", "/finance/strategy-backtests/preflight")]
    assert run.state["trade_count"] is None
    assert run.state["replay_acceptance"]["backtest_run_created"] is False
    assert run.state["replay_acceptance"]["backtest_engine_executed"] is False


def test_wait_failure_preserves_created_run_but_not_a_zero_trade_claim():
    run = runner()
    run.state["trade_count"] = 0
    run.api = lambda method, path, payload=None: (
        {"can_run": True} if path.endswith("/preflight") else {"id": "backtest"}
    )

    def wait(*_args, **_kwargs):
        raise TimeoutError("offline poll failure")

    run.wait = wait
    with pytest.raises(TimeoutError):
        run.backtest()
    assert run.state["replay_acceptance"]["backtest_run_created"] is True
    assert run.state["replay_acceptance"]["backtest_engine_executed"] is None
    assert run.state["trade_count"] is None


def test_failed_partial_ledger_is_diagnostic_not_completed_trade_acceptance():
    run = runner()
    run.api = lambda method, path, payload=None: (
        {"can_run": True} if path.endswith("/preflight") else {"id": "backtest"}
    )
    run.wait = lambda *_args, **_kwargs: {"status": "failed", "result": {"trades": [{"id": "partial"}]}}
    run.backtest()
    assert run.state["replay_acceptance"]["observed_partial_trade_count"] == 1
    assert run.state["replay_acceptance"]["backtest_engine_executed"] is True
    assert run.state["trade_count"] is None
    assert run.state["trading_execution_demonstrated"] is False


def test_report_stage_persists_separate_layer_diagnostics():
    run = runner()
    run.state.update(strategy_id="strategy", assignment_id="assignment", stages={"report": {"id": "job"}})
    data = reports(layered(native_skill_completion={"status": "incomplete"}))
    run.api = lambda *_args, **_kwargs: data[0]
    run.wait = lambda *_args, **_kwargs: {"progress": {"report_set_ids": ["report-set"]}}
    run.render_reports = lambda *_: None
    run.report()
    assert run.state["report_acceptance"]["layer_counts"]["skill_incomplete"] == 1
    assert run.state["native_report"] == "skill_incomplete"
