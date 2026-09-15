"""Explicit data-centre research shares one deadline and never escapes injection."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from research_system.market_data import collection, quant_auxiliary, research_enrichment
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.strategies.database import StrategyDatabase


@pytest.fixture
def stage(tmp_path, monkeypatch):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'stage.sqlite'}")
    database.create_schema()
    provider, source = object(), object()
    coordinator = MarketDataCollectionCoordinator(
        database, collector=lambda **kw: [], archive_collector=object(),
        settings=SimpleNamespace(workspace_root=tmp_path),
        research_provider=provider, quant_auxiliary_source=source,
    )
    today = datetime.now(timezone.utc).astimezone().date()
    job = coordinator.create_job(
        tenant_id="tenant", user_id="user", actor_id="user", symbols=["688041.SH"],
        start_date=today, end_date=today, dimensions=["3_macro"],
        include_research_archive=True, submit=False,
    )
    monkeypatch.setattr(coordinator, "_fence_collection_worker", lambda *args, **kw: True)
    clock = [10.0]
    monkeypatch.setattr(collection, "monotonic", lambda: clock[0])
    calls = []

    def archive(*args, **kw):
        calls.append(("archive", kw))
        clock[0] += 20
        return {"attempted": 1, "succeeded": 1, "failed": 0}

    def enrich(*args, **kw):
        calls.append(("enrichment", kw))
        clock[0] += 20
        return {"status": "completed", "revisions": [], "rejected": []}

    def auxiliary(*args, **kw):
        calls.append(("auxiliary", kw))
        return {"status": "completed", "bindings": []}

    monkeypatch.setattr(collection, "recover_research_documents", archive)
    monkeypatch.setattr(research_enrichment, "run_research_enrichment", enrich)
    monkeypatch.setattr(quant_auxiliary, "collect_job_quant_auxiliary_inputs", auxiliary, raising=False)
    yield coordinator, job, provider, source, clock, calls
    coordinator.close()
    database.close()


def test_explicit_archive_enrichment_and_auxiliary_share_deadline(stage):
    coordinator, job, provider, source, _clock, calls = stage
    cancellation = SimpleNamespace(is_set=lambda: False)
    coordinator._run_research_archive_stage(job["id"], worker_id="worker", cancellation=cancellation)
    assert [kind for kind, _ in calls] == ["archive", "enrichment", "auxiliary"]
    assert calls[0][1]["max_duration_seconds"] == 600
    assert calls[1][1]["deadline_at"] == calls[2][1]["deadline"] == 610
    assert calls[1][1]["provider"] is provider
    with coordinator.database.session() as session:
        assert calls[1][1]["write_fence"](session) is True
    assert calls[2][1]["source"] is source
    assert all(kwargs["cancellation"] is cancellation for _, kwargs in calls)
    result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
    assert result["progress"]["research_archive"]["status"] == "succeeded"
    assert result["progress"]["research_enrichment"]["status"] == "completed"
    assert result["progress"]["native_quant_auxiliary"]["status"] == "completed"


@pytest.mark.parametrize("recovery", [False, True])
def test_research_stage_times_are_independent_but_budget_is_shared(stage, monkeypatch, recovery):
    coordinator, job, _provider, _source, clock, calls = stage
    origin = datetime(2026, 9, 8, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return (origin + timedelta(seconds=clock[0])).astimezone(tz)

    monkeypatch.setattr(collection, "datetime", FakeDateTime)
    if recovery:
        monkeypatch.setattr(coordinator, "_fence_agent_worker", lambda *a, **kw: True)
        monkeypatch.setattr(coordinator, "_fence_collection_worker", Mock(side_effect=AssertionError("wrong lease")))

    def auxiliary(*args, **kwargs):
        calls.append(("auxiliary", kwargs))
        clock[0] += 7
        kwargs["progress_callback"]({"completed": 1})
        clock[0] += 3
        return {"status": "completed", "bindings": []}

    monkeypatch.setattr(quant_auxiliary, "collect_job_quant_auxiliary_inputs", auxiliary)
    result = coordinator._run_research_archive_stage(
        job["id"], worker_id="worker", cancellation=SimpleNamespace(is_set=lambda: False), recovery=recovery,
    )
    stages = [result[name] for name in ("research_archive", "research_enrichment", "native_quant_auxiliary")]
    seconds = lambda value: (datetime.fromisoformat(value) - origin).total_seconds()
    assert [seconds(row["started_at"]) for row in stages] == [10, 30, 50]
    assert [seconds(row["completed_at"]) for row in stages] == [30, 50, 60]
    assert [seconds(row["completed_at"]) - seconds(row["started_at"]) for row in stages] == [20, 20, 10]
    assert {seconds(row["pipeline_started_at"]) for row in stages} == {10}
    assert calls[0][1]["max_duration_seconds"] == 600
    assert calls[1][1]["deadline_at"] == calls[2][1]["deadline"] == 610


def test_injected_archive_transport_does_not_create_live_model_or_quant_source(stage, monkeypatch):
    coordinator, job, *_rest, calls = stage
    coordinator.research_provider = None
    coordinator.quant_auxiliary_source = None
    registry = Mock(side_effect=AssertionError("injected transport cannot use live provider"))
    monkeypatch.setattr("research_system.runtime.providers.provider_registry.ProviderRegistry", registry)
    coordinator._run_research_archive_stage(job["id"], worker_id="worker", cancellation=SimpleNamespace(is_set=lambda: False))
    assert [kind for kind, _ in calls] == ["archive"]
    registry.assert_not_called()


def test_expired_archive_does_not_start_another_model_budget(stage, monkeypatch):
    coordinator, job, _provider, _source, clock, calls = stage

    def expire(*args, **kwargs):
        calls.append(("archive", kwargs))
        clock[0] = 611
        return {"attempted": 1, "succeeded": 0, "failed": 1}

    monkeypatch.setattr(collection, "recover_research_documents", expire)
    coordinator._run_research_archive_stage(job["id"], worker_id="worker", cancellation=SimpleNamespace(is_set=lambda: False))
    assert [kind for kind, _ in calls] == ["archive"]
    result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
    assert result["progress"]["research_enrichment"]["reason_code"] == "research_deadline_exceeded"
    for name in ("research_enrichment", "native_quant_auxiliary"):
        assert result["progress"][name]["started_at"] is None
        assert result["progress"][name]["pipeline_started_at"] == result["progress"]["research_archive"]["pipeline_started_at"]


def test_settlement_keeps_independent_research_stage_results():
    job = SimpleNamespace(
        progress_json={"research_archive": {"status": "succeeded"},
                       "research_enrichment": {"status": "partial"},
                       "native_quant_auxiliary": {"status": "unassessed"}},
        checkpoint_json={},
    )
    MarketDataCollectionCoordinator._set_job_progress(job, [])
    assert job.progress_json["research_enrichment"]["status"] == "partial"
    assert job.progress_json["native_quant_auxiliary"]["status"] == "unassessed"


def test_explicit_agent_recovery_uses_its_own_fence_and_same_three_stages(stage, monkeypatch):
    coordinator, job, _provider, _source, _clock, calls = stage
    collection_fence = Mock(side_effect=AssertionError("recovery does not own collection lease"))
    monkeypatch.setattr(coordinator, "_fence_collection_worker", collection_fence)
    monkeypatch.setattr(coordinator, "_fence_agent_worker", lambda *args, **kwargs: True)
    result = coordinator._run_research_archive_stage(
        job["id"], worker_id="agent", cancellation=SimpleNamespace(is_set=lambda: False),
        recovery=True,
    )
    assert set(result) == {"research_archive", "research_enrichment", "native_quant_auxiliary"}
    assert [kind for kind, _ in calls] == ["archive", "enrichment", "auxiliary"]
    assert calls[1][1]["deadline_at"] == calls[2][1]["deadline"] == 610
    with coordinator.database.session() as session:
        assert calls[1][1]["write_fence"](session) is True
    collection_fence.assert_not_called()


@pytest.mark.parametrize("cancelled,lost", [(True, False), (False, True)])
def test_recovery_cancel_or_lost_lease_cannot_start_research(stage, monkeypatch, cancelled, lost):
    coordinator, job, *_rest, calls = stage
    monkeypatch.setattr(coordinator, "_fence_agent_worker", lambda *args, **kwargs: not lost)
    result = coordinator._run_research_archive_stage(
        job["id"], worker_id="agent", cancellation=SimpleNamespace(is_set=lambda: cancelled),
        recovery=True,
    )
    assert result == {}
    assert calls == []
