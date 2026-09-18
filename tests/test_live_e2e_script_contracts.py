from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from research_system.api.routes.market_data_center import (
    CollectionFreezeConfirmRequest,
    CollectionJobRequest,
    CollectionRetryRequest,
)
from research_system.api.routes.report_signals import ReportJobRequest
from research_system.api.routes.strategies import (
    CreateAssignmentRequest,
    CreateStrategyRequest,
)
from research_system.api.routes.strategy_backtests import (
    StrategyBacktestPreflightRequest,
    StrategyBacktestRequest,
)
from scripts.run_uzi_report_e2e import (
    DATA,
    ApiFailure,
    LiveAcceptance,
    api_url,
    bar_date_evidence,
    classify_reports,
    execution_provenance,
    server_job_timing,
)


def test_live_api_routing_respects_finance_v2_and_artifact_v1():
    for base in (
        "http://fixture.invalid",
        "http://fixture.invalid/api/v1",
        "http://fixture.invalid/api/v2",
    ):
        assert (
            api_url(base, DATA + "/collection-jobs")
            == "http://fixture.invalid/api/v2" + DATA + "/collection-jobs"
        )
        assert (
            api_url(base, "/finance/report-jobs")
            == "http://fixture.invalid/api/v2/finance/report-jobs"
        )
        assert (
            api_url(base, "/runs/run/report")
            == "http://fixture.invalid/api/v1/runs/run/report"
        )
        for path in ("/runs", "/runs?limit=1", "/holding-report-batches"):
            assert api_url(base, path) == "http://fixture.invalid/api/v1" + path
        assert api_url(base, "/runs-extra") == "http://fixture.invalid/api/v2/runs-extra"
        assert (
            api_url(base, "/holding-report-batches/batch")
            == "http://fixture.invalid/api/v1/holding-report-batches/batch"
        )


def runner():
    run = object.__new__(LiveAcceptance)
    run.state = {
        "pipeline": "running",
        "native_report": "not_started",
        "stages": {"historical_collection": {"id": "parent"}},
    }
    run.save = lambda: None
    run.exports = {}
    run.export = lambda name, value: run.exports.update({name: value})
    return run


def test_collection_waits_for_candidate_finalization_without_recollecting(monkeypatch):
    run = runner()
    pending = {"status": "partial", "checkpoint": {"publication": {"error_code": "collection_retry_pending"}}}
    ready = {"status": "partial", "publication_candidate": {"proposal_hash": "fixed"},
             "checkpoint": {"publication": {"status": "pending_confirmation"}}}
    replies = iter([pending, ready])
    calls = []
    def api(method, path):
        calls.append((method, path))
        return next(replies)
    run.api = api
    monkeypatch.setattr("scripts.run_uzi_report_e2e.time.sleep", lambda _: None)
    assert run.wait("/job", stage="historical_retry", collection=True) == ready
    assert calls == [("GET", "/job"), ("GET", "/job")]


def test_probe_uses_snapshot_detail_bar_date_not_partition_day():
    run = runner()

    def collection(stage, payload):
        CollectionJobRequest.model_validate(payload)
        return {"status": "succeeded", "start_date": "2026-09-06"}

    run.collection = collection
    paths = []

    def api(method, path, payload=None):
        paths.append(path)
        if "/snapshots?" in path:
            return {"items": [{"snapshot_id": "kline", "dimension_id": "2_kline"}]}
        return {
            "snapshot_id": "kline",
            "dimension_id": "2_kline",
            "as_of_date": "2026-09-06",
            "payload": {"ohlcv": [{"date": "2026-09-04", "close": 10.0}]},
            "content_hash": "a" * 64,
        }

    run.api = api
    run.probe()
    assert paths[-1] == DATA + "/snapshots/kline"
    assert "as_of_date=2026-09-06" in paths[0]
    assert run.state["today_probe_bar_evidence"][0]["last_bar_date"] == "2026-09-04"
    assert (
        bar_date_evidence({"as_of_date": "2026-09-06", "payload": {}})["last_bar_date"]
        is None
    )


def test_collection_allows_only_one_targeted_retry_and_no_extra_agent_recovery():
    run = runner()
    result = {
        "status": "partial",
        "freeze_status": "ready",
        "portfolio_summary": {"retryable_slots": 2},
        "child_collection_job_ids": ["child"],
    }
    run.collection = lambda stage, payload: result
    run.wait = lambda *args, **kwargs: result
    calls = []

    def api(method, path, payload=None):
        calls.append(path)
        if path.endswith("retry-failed"):
            CollectionRetryRequest.model_validate(payload)
            raise ApiFailure(
                method,
                path,
                409,
                {"error": {"code": "collection_retry_unavailable"}},
            )
        assert not path.endswith("agent-recovery")
        return {"id": "child", "research_archive": {"status": "partial"}}

    run.api = api
    run.collect()
    assert (
        run.state["stages"]["historical_collection"]["targeted_retry_conflict"]["code"]
        == "collection_retry_unavailable"
    )
    assert (
        run.exports["historical_collection_children"][0]["research_archive"]["status"]
        == "partial"
    )
    run.collect()
    assert sum(path.endswith("retry-failed") for path in calls) == 1
    assert sum(path.endswith("agent-recovery") for path in calls) == 0
    run.state["stages"]["historical_collection"].pop("targeted_retry_requested")
    run.state["stages"]["historical_collection"]["research_evidence_requested"] = True
    run.collect()
    assert sum(path.endswith("retry-failed") for path in calls) == 1
    run.state["stages"]["historical_collection"].pop("research_evidence_requested")
    run.api = lambda method, path, payload=None: (_ for _ in ()).throw(
        ApiFailure(method, path, 500, {"error": {"code": "internal_error"}})
    )
    with pytest.raises(ApiFailure):
        run.collect()


def test_freeze_checks_confirmed_bundle_hashes_and_never_uses_latest():
    run = runner()
    bundles = [
        {"asset_role": "fund", "bundle_id": "fund-exact", "manifest_hash": "a" * 64}
    ]
    bundles += [
        {
            "asset_role": "holding",
            "bundle_id": f"holding-{i}",
            "manifest_hash": "b" * 64,
        }
        for i in range(10)
    ]
    response = {
        "freeze_status": "ready",
        "can_confirm_freeze": True,
        "publication_candidate": {"proposal_hash": "f" * 64},
    }
    paths = []

    def api(method, path, payload=None):
        paths.append(path)
        if method == "POST":
            CollectionFreezeConfirmRequest.model_validate(payload)
            return {
                "freeze_status": "frozen",
                "checkpoint": {"publication": {"bundles": bundles}},
            }
        if "/bundles/" in path:
            row = next(row for row in bundles if path.endswith(row["bundle_id"]))
            return {**row, "integrity_verified": True}
        return response

    run.api = api
    run.freeze()
    assert run.state["fund_bundle_id"] == "fund-exact"
    assert all("latest" not in path for path in paths)
    run.api = lambda method, path, payload=None: (
        {"freeze_status": "frozen", "checkpoint": {"publication": {"bundles": bundles}}}
        if "/collection-jobs/" in path
        else {"integrity_verified": True, "manifest_hash": "changed"}
    )
    with pytest.raises(RuntimeError, match="hash/integrity"):
        run.freeze()
    assert run.state["blocking_stage"] == "frozen_bundle_integrity"


def test_synthetic_failure_review_is_system_error_not_native_refusal():
    report = [
        {
            "nodes": [
                {
                    "node_type": "holding_uzi",
                    "status": "blocked",
                    "source": {
                        "report_allowed": False,
                        "critical_count": 1,
                        "results": [{"computation_error": "TimeoutError"}],
                    },
                }
            ]
        }
    ]
    assert classify_reports(report)[0] == "generation_failed"
    report[0]["nodes"][0]["source"] = {"report_allowed": None}
    report[0]["nodes"][0]["status"] = "completed"
    assert classify_reports(report)[0] == "generation_failed"


def test_wait_does_not_finish_during_archive_stage(monkeypatch):
    run = runner()
    items = iter(
        [
            {"status": "partial", "research_archive": {"status": "running"}},
            {"status": "partial", "research_archive": {"status": "partial"}},
        ]
    )
    run.api = lambda *args, **kwargs: next(items)
    monkeypatch.setattr("scripts.run_uzi_report_e2e.time.sleep", lambda seconds: None)
    assert (
        run.wait("/fixture", stage="wait", collection=True)["research_archive"][
            "status"
        ]
        == "partial"
    )


def test_html_render_only_requests_delivered_runs_and_records_timing(tmp_path):
    run = runner()
    run.output = tmp_path
    run.args = SimpleNamespace(base_url="http://fixture.invalid/api/v1")
    calls = []
    html = b"<!doctype html><html><body>Native fixture report</body></html>"

    def request(method, url, **kwargs):
        calls.append((method, url))
        return SimpleNamespace(
            ok=True,
            status_code=200,
            content=html,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Server-Timing": "uzi_html_render;dur=12.340",
            },
        )

    run.http = SimpleNamespace(request=request)
    run.render_reports(
        [
            {
                "nodes": [
                    {
                        "node_type": "holding_uzi",
                        "status": "blocked",
                        "run_id": "blocked",
                        "source": {"report_allowed": False},
                    },
                    {
                        "node_type": "holding_uzi",
                        "status": "completed",
                        "run_id": "delivered",
                        "source": {"report_allowed": True},
                    },
                    {
                        "node_type": "holding_uzi",
                        "status": "completed",
                        "run_id": "unproven",
                        "source": {},
                    },
                ]
            }
        ]
    )
    assert calls == [
        ("GET", "http://fixture.invalid/api/v1/runs/delivered/report?format=html")
    ]
    assert run.state["render"] == "completed"
    evidence = run.state["render_reports"][0]
    assert evidence["http_status"] == 200
    assert evidence["server_timing"] == "uzi_html_render;dur=12.340"
    assert evidence["content_hash"] == hashlib.sha256(html).hexdigest()
    assert (tmp_path / evidence["file"]).read_bytes() == html
    run.render_reports(
        [
            {
                "nodes": [
                    {
                        "node_type": "holding_uzi",
                        "status": "blocked",
                        "source": {"report_allowed": False},
                    }
                ]
            }
        ]
    )
    assert run.state["render"] == "not_run"
    assert len(calls) == 1


def test_failed_html_response_is_not_saved_as_a_valid_report(tmp_path):
    run = runner()
    run.output = tmp_path
    run.args = SimpleNamespace(base_url="http://fixture.invalid/api/v1")
    run.http = SimpleNamespace(
        request=lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            status_code=409,
            content=b'{"error":"unavailable"}',
            headers={"Content-Type": "application/json"},
        )
    )
    run.render_reports(
        [
            {
                "nodes": [
                    {
                        "node_type": "holding_uzi",
                        "status": "completed",
                        "run_id": "delivered",
                        "source": {"report_allowed": True},
                    }
                ]
            }
        ]
    )
    assert run.state["render"] == "failed"
    assert run.state["render_reports"][0]["http_status"] == 409
    assert not list(tmp_path.glob("*.html"))


def test_stage_resume_clears_current_error_but_retains_attempt_history():
    run = runner()
    initial = {"type": "ApiFailure", "message": "initial wrong API version"}
    run.state.update(pipeline="failed", last_error=initial, blocking_stage="obsolete")
    run.probe = lambda: None
    run.execute_stage("probe")
    assert run.state["pipeline"] == "running"
    assert "last_error" not in run.state and "blocking_stage" not in run.state
    assert run.state["error_history"] == [initial]
    run.probe = lambda: (_ for _ in ()).throw(RuntimeError("fixture interruption"))
    with pytest.raises(RuntimeError):
        run.execute_stage("probe")
    assert run.state["pipeline"] == "failed"
    assert len(run.state["error_history"]) == 2
    run.probe = lambda: None
    run.execute_stage("probe")
    assert "last_error" not in run.state
    assert len(run.state["error_history"]) == 2
    assert [row["status"] for row in run.state["stage_attempts"]["probe"]] == [
        "completed",
        "failed",
        "completed",
    ]
    assert all(
        row["elapsed_seconds"] >= 0 for row in run.state["stage_attempts"]["probe"]
    )


def test_wait_preserves_multiple_invocation_timing_and_server_elapsed():
    run = runner()
    run.api = lambda *args, **kwargs: {
        "status": "succeeded",
        "created_at": "2026-09-06T01:00:00Z",
        "started_at": "2026-09-06T01:00:02Z",
        "completed_at": "2026-09-06T01:01:00Z",
    }
    run.state["stages"]["wait"] = {"wait_seconds": 22.5}
    run.wait("/fixture", stage="wait", collection=True)
    run.wait("/fixture", stage="wait", collection=True)
    stage = run.state["stages"]["wait"]
    assert len(stage["wait_sessions"]) == 3
    assert stage["wait_seconds_total"] >= 22.5
    assert stage["server_timing"]["server_execution_seconds"] == 58
    assert stage["server_timing"]["server_elapsed_seconds"] == 60
    assert (
        server_job_timing({"created_at": "2026-09-06T01:00:00Z"}).get(
            "server_elapsed_seconds"
        )
        is None
    )


@pytest.mark.parametrize("image_metadata_missing", [False, True])
def test_execution_provenance_hashes_untracked_code_and_redacts_docker_inspection(
    tmp_path, image_metadata_missing,
):
    code = tmp_path / "new_adapter.py"
    code.write_text("fixture = 1\n")
    (tmp_path / ".env").write_text("SECRET=do-not-export\n")
    calls = []

    def command(args):
        calls.append(args)
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return b"fixture-commit\n"
        if args[:2] == ["git", "diff"]:
            return b"fixture tracked diff"
        if args[:2] == ["git", "ls-files"]:
            return b"new_adapter.py\0.env\0"
        if args[:2] == ["docker", "ps"]:
            return b"container-fixture\n" if args[-1].endswith("=api") or image_metadata_missing else b""
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(
                {
                    "container_id": "container-fixture",
                    "image_id": "sha256:actual-image",
                    "image_name": "cognitrade-api:local",
                    "started_at": "2026-09-06T01:00:00Z",
                }
            ).encode()
        if image_metadata_missing:
            raise subprocess.CalledProcessError(1, args, stderr=b"SECRET not for export")
        return json.dumps(
            {
                "image_id": "sha256:actual-image",
                "repo_digests": [],
                "created_at": "2026-09-06T00:00:00Z",
            }
        ).encode()

    original = execution_provenance(
        "http://127.0.0.1:8080", root=tmp_path, command=command
    )
    assert original["untracked_code"] == [
        {
            "path": "new_adapter.py",
            "sha256": hashlib.sha256(code.read_bytes()).hexdigest(),
        }
    ]
    assert original["deployment"]["containers"][0]["image_id"] == "sha256:actual-image"
    assert original["deployment"]["status"] == "captured"
    if image_metadata_missing:
        assert len(original["deployment"]["containers"]) == 2, "Continue inspecting other services"
        assert all(row["image_metadata_error"] == "CalledProcessError"
                   and "image" not in row for row in original["deployment"]["containers"])
    assert all(".Config.Env" not in " ".join(args) for args in calls)
    assert "SECRET" not in json.dumps(original)
    code.write_text("fixture = 2\n")
    modified = execution_provenance(
        "https://remote.fixture.invalid", root=tmp_path, command=command
    )
    assert modified["code_fingerprint"] != original["code_fingerprint"]
    assert modified["deployment"]["status"] == "not_inspected_remote_endpoint"


def test_market_audit_uses_task_snapshot_ids_and_blocked_replay_is_not_zero_trades(
    tmp_path,
):
    run = runner()
    run.output = tmp_path
    run.state.update(
        frozen_bundles=[{"asset_role": "holding", "symbol": str(i)} for i in range(10)],
        strategy_version_id="version",
        native_report="input_blocked",
    )
    run.collection = lambda *_: {
        "tasks": [
            {"dimension_id": "2_kline", "snapshot_id": "exact-task-snapshot"},
            {"dimension_id": "2_kline", "snapshot_id": "exact-task-snapshot"},
        ]
    }
    seen = []

    def api(method, path, payload=None):
        seen.append(path)
        if path.endswith("/snapshots/exact-task-snapshot"):
            return {"snapshot_id": "exact-task-snapshot", "content_hash": "fixed"}
        assert path == "/finance/strategy-backtests/preflight"
        return {"can_run": False, "executable_plan_count": 0}

    run.api = api
    run.market()
    assert len(seen) == 1
    assert (
        run.state["outcome_market_evidence"][0]["snapshot_id"] == "exact-task-snapshot"
    )
    run.backtest()
    assert run.state["pipeline"] == "blocked"
    assert run.state["blocking_stage"] == "report_input_preflight"
    assert run.state["trade_count"] is None
    assert run.state["trading_execution_demonstrated"] is False
    assert "/finance/strategy-backtests" not in seen


@pytest.mark.parametrize("delivered", [0, 4])
def test_cancelled_report_does_not_launch_or_misclassify_replay(delivered):
    run = runner()
    counts = {"delivered": delivered, "cancelled": 10 - delivered}
    run.state.update(
        native_report="partial_delivery" if delivered else "cancelled",
        native_report_counts=counts,
    )
    run.api = lambda *_: pytest.fail("Cancelled report must not initiate replay")
    run.backtest()
    assert run.state["pipeline"] == "cancelled"
    assert run.state["replay"] == "not_run_cancelled"
    assert run.state["blocking_stage"] == "report_cancelled"
    assert run.state["native_report_counts"] == counts
    assert run.state["trade_count"] is None
    assert run.state["replay_acceptance"]["backtest_engine_executed"] is False


@pytest.mark.parametrize("can_run", [True, False])
def test_cancelled_report_preserves_existing_replay_without_creating_another(can_run):
    run = runner()
    run.state.update(
        native_report="partial_delivery",
        native_report_counts={"delivered": 4, "cancelled": 6},
        strategy_version_id="version",
    )
    run.state["stages"]["backtest"] = {"id": "existing-replay"}

    def api(method, path, payload):
        assert path == "/finance/strategy-backtests/preflight"
        return {"can_run": can_run}

    run.api = api
    run.wait = lambda path, **_: {"status": "completed", "result": {"trades": []}}
    run.backtest()
    assert run.state["pipeline"] == "cancelled"
    assert run.state["stages"]["backtest"]["id"] == "existing-replay"
    assert run.state["replay_acceptance"]["backtest_run_created"] is True
    assert run.state["trade_count"] == (0 if can_run else None)


def test_report_and_backtest_payloads_follow_current_api_contracts(tmp_path):
    run = runner()
    run.output = tmp_path
    run.state["fund_bundle_id"] = "frozen-parent"
    paths = []

    def api(method, path, payload=None):
        paths.append(path)
        if path == "/finance/strategies":
            CreateStrategyRequest.model_validate(payload)
            assert payload["document"]["report_inputs"]["research_profile"] == "medium"
            return {"id": "strategy"}
        if path.endswith("/publish"):
            return {"id": "version"}
        if path == "/finance/strategy-assignments":
            CreateAssignmentRequest.model_validate(payload)
            assert (
                payload["enabled"] is False and payload["schedule"]["enabled"] is False
            )
            return {"id": "assignment"}
        if path == "/finance/report-jobs":
            ReportJobRequest.model_validate(payload)
            assert payload["fund_bundle_id"] == "frozen-parent"
            return {"id": "report-job"}
        if path == "/holding-report-batches/batch":
            return {"batch_id": "batch"}
        if path == "/finance/daily-report-sets/report-set":
            return {
                "id": "report-set",
                "nodes": [
                    {
                        "node_type": "holding_uzi",
                        "status": "completed",
                        "run_id": str(i),
                        "source": {"report_allowed": True},
                    }
                    for i in range(10)
                ],
            }
        if path == "/finance/strategy-backtests/preflight":
            StrategyBacktestPreflightRequest.model_validate(payload)
            return {"can_run": True, "signal_count": 10, "market_data_checked": True}
        if path == "/finance/strategy-backtests":
            StrategyBacktestRequest.model_validate(payload)
            assert payload["report_policy"] == "existing_only"
            return {"id": "backtest"}
        raise AssertionError(path)

    run.api = api
    run.wait = lambda path, **kwargs: {
        "status": "completed",
        "progress": {
            "report_set_ids": ["report-set"],
            "batches": {"2026-09-02": "batch"},
        },
        "result": {"trades": []},
    }
    run.render_reports = lambda reports: run.state.update(render="completed")
    run.report()
    run.backtest()
    assert "/holding-report-batches/batch" in paths
    assert run.state["native_report"] == "allowed"
    assert run.state["pipeline"] == "completed"
    assert run.state["replay"] == "completed_without_trades"
    assert run.state["trading_execution_demonstrated"] is False
