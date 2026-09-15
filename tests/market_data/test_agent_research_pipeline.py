"""Explicit Agent recovery reuses the owning worker's research pipeline."""

from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest

from research_system.market_data import agent_recovery as recovery


@pytest.fixture(autouse=True)
def offline_recovery(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *args, **kwargs: pytest.fail("recovery wiring test attempted network"))
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: pytest.fail("recovery wiring test attempted network"))
    monkeypatch.setattr(recovery, "_persist_recovered_snapshots", lambda *args, **kwargs: [])
    monkeypatch.setattr(recovery, "_coverage_after_recovery", lambda *args, **kwargs: [])
    monkeypatch.setattr(recovery, "recover_uzi_draft_snapshots", lambda *args, **kwargs: {"attempted": 0, "succeeded": 0, "failed": 0})


def _prepared(monkeypatch, historical):
    monkeypatch.setattr(recovery, "_load_draft_inputs", lambda *args, **kwargs: {
        "scopes": [] if historical else [{"symbol": "600519.SH"}],
        "snapshots": [], "symbols": ["600519.SH"], "task_ids": {},
        "skipped_historical_scopes": [{"as_of_date": "2026-08-20"}] if historical else [],
    })


def _stages():
    return {
        "research_archive": {"status": "succeeded", "attempted": 1, "succeeded": 1, "failed": 0,
                             "persisted_snapshot_count": 1, "persisted_snapshots": [{"snapshot_id": "archived"}]},
        "research_enrichment": {"status": "completed", "revisions": [{"snapshot_id": "revised", "input_snapshot_id": "archived", "dimension": "7_industry"}], "rejected": []},
        "native_quant_auxiliary": {"status": "completed", "inputs_hash": "frozen-auxiliary-hash"},
    }


@pytest.mark.parametrize("historical", [False, True])
def test_worker_pipeline_runs_once_and_preserves_all_three_stages(monkeypatch, historical):
    _prepared(monkeypatch, historical)
    monkeypatch.setattr(recovery, "recover_research_documents", lambda *args, **kwargs: pytest.fail("archive must not run a second time"))
    calls = []
    stages = _stages()
    for name, offset in zip(stages, (0, 10, 20), strict=True):
        stages[name].update(
            pipeline_started_at="2026-09-08T09:00:00+00:00",
            started_at=f"2026-09-08T09:00:{offset:02d}+00:00",
            completed_at=f"2026-09-08T09:00:{offset + 10:02d}+00:00",
        )
    result = recovery.recover_collection_job_draft(
        SimpleNamespace(), job_id="explicit-job", settings=SimpleNamespace(),
        research_pipeline=lambda: calls.append("same-worker-budget-and-fence") or deepcopy(stages),
    )
    assert calls == ["same-worker-budget-and-fence"]
    assert all(result["stages"][key] == value for key, value in stages.items())
    assert result["persisted_snapshot_count"] == 2
    assert {row["snapshot_id"] for row in result["persisted_snapshots"]} == {"archived", "revised"}
    assert result["attempted"] == result["succeeded"] == 2
    assert result["failed"] == 0


def test_legacy_recovery_discloses_missing_enrichment_and_quant_pipeline(monkeypatch):
    _prepared(monkeypatch, True)
    calls = []
    monkeypatch.setattr(recovery, "recover_research_documents", lambda *args, **kwargs: calls.append(kwargs) or _stages()["research_archive"])
    result = recovery.recover_collection_job_draft(SimpleNamespace(), job_id="legacy-explicit", settings=SimpleNamespace())
    assert len(calls) == 1
    assert result["persisted_snapshot_count"] == 1
    for key in ("research_enrichment", "native_quant_auxiliary"):
        assert result["stages"][key]["status"] == "not_run"
        assert result["stages"][key]["reason_code"] == "research_pipeline_not_configured"


def test_cancelled_recovery_does_not_start_archive_enrichment_or_quant(monkeypatch):
    _prepared(monkeypatch, True)
    cancellation = Event()
    cancellation.set()
    result = recovery.recover_collection_job_draft(
        SimpleNamespace(), job_id="cancelled", settings=SimpleNamespace(), cancellation=cancellation,
        research_pipeline=lambda: pytest.fail("cancelled worker cannot start pipeline"),
    )
    for key in ("research_archive", "research_enrichment", "native_quant_auxiliary"):
        assert result["stages"][key]["status"] == "not_run"
        assert result["stages"][key]["reason_code"] == "collection_cancelled"


def test_missing_pipeline_stage_does_not_silently_run_a_second_pipeline(monkeypatch):
    _prepared(monkeypatch, True)
    monkeypatch.setattr(recovery, "recover_research_documents", lambda *args, **kwargs: pytest.fail("missing stage cannot expand authority"))
    result = recovery.recover_collection_job_draft(
        SimpleNamespace(), job_id="partial-pipeline", settings=SimpleNamespace(),
        research_pipeline=lambda: {"research_archive": _stages()["research_archive"]},
    )
    assert result["stages"]["research_enrichment"]["status"] == "not_run"
    assert result["stages"]["native_quant_auxiliary"]["reason_code"] == "research_pipeline_stage_missing"
