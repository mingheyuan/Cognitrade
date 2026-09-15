"""Actual coordinator -> native sources -> immutable rows -> progress, offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime
from research_system.market_data import collection
from research_system.market_data.agent_recovery import DraftAgentRecoveryError
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
)
from research_system.market_data.native_postfetch import run_collection_postfetch
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase


class Sources:
    mx_available = False
    browser_enabled = True

    def __init__(self, sequence):
        self.sequence = sequence
        self.on_fetch = lambda: None

    def search(self, *args, **kwargs):
        pytest.fail("a peer/sentiment retry cannot search unrequested qualitative dimensions")

    def browser_html(self, source_id, url, **kwargs):
        self.sequence.append(("browser", source_id))
        self.on_fetch()
        return '<a href="/S/SZ000858">五粮液</a><script>{"title":"实际页面嵌入的舆情讨论标题"}</script>'


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a, **kw: pytest.fail("live network in lifecycle regression"))
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: pytest.fail("live network in lifecycle regression"))
    Uzi392FetcherRuntime(workspace_root=tmp_path)._initialize_import_boundary()
    monkeypatch.setattr(import_module("lib.playwright_fallback"), "_filter_dims_by_network", lambda dims: (dims, []))
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'postfetch.sqlite'}")
    database.create_schema()
    sequence = []
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()

    def collector(**kwargs):
        sequence.append(("structured", list(kwargs["snapshot_types"])))
        return [{"snapshot_type": kind, "symbol": "688041.SH", "snapshot_date": today.isoformat(),
                 "retrieved_at": datetime.now(timezone.utc).isoformat(), "quality_status": "data_gap",
                 "facts": {"_uzi_raw_result": {"ticker": "688041.SH", "data": {}, "source": "offline-recording",
                                              "error": "original structured source failed", "_timeout": True}}}
                for kind in kwargs["snapshot_types"]]

    def archive(*args, **kwargs):
        sequence.append(("archive", kwargs["max_duration_seconds"]))
        return {"attempted": 0, "succeeded": 0, "failed": 0}

    monkeypatch.setattr(collection, "recover_research_documents", archive)
    sources = Sources(sequence)
    coordinator = MarketDataCollectionCoordinator(database, collector=collector, archive_collector=object(),
        settings=SimpleNamespace(workspace_root=tmp_path, mx_api_key=None), postfetch_sources=sources)
    job = coordinator.create_job(tenant_id="offline", user_id="offline", actor_id="offline",
        symbols=["688041.SH"], start_date=today, end_date=today, dimensions=["4_peers", "17_sentiment"],
        include_research_archive=True, submit=False)
    yield SimpleNamespace(database=database, coordinator=coordinator, job=job, sequence=sequence, sources=sources, today=today)
    coordinator.close()
    database.close()


def run_stage(state, *, task_ids=None, cancellation=None, write_fence=None):
    return run_collection_postfetch(state.database, job_id=state.job["id"],
        registry=state.coordinator.registry, settings=state.coordinator.settings,
        task_ids=[task["id"] for task in state.job["tasks"]] if task_ids is None else task_ids,
        deadline_at=monotonic() + 60, cancellation=cancellation or Event(),
        write_fence=write_fence or (lambda session: True), sources=state.sources)


def test_first_explicit_collection_persists_native_browser_evidence_before_archive(state):
    state.coordinator.run_job_now(state.job["id"])
    result = state.coordinator.get_job(state.job["id"], tenant_id="offline", user_id="offline")
    stage = result["progress"]["native_postfetch"]
    assert stage["status"] == "completed", stage
    assert stage["attempted"] == stage["succeeded"] == 4
    assert stage["persisted_snapshot_count"] == 2
    assert [name for name, _ in state.sequence] == ["structured", "browser", "browser", "browser", "browser", "archive"]
    assert [row["force"] for row in stage["browser_passes"]] == [False, True]
    assert "response" not in repr(stage)
    assert 0 < state.sequence[-1][1] < 600
    with state.database.session() as session:
        repo = MarketDataRepository(session)
        for item in stage["persisted_snapshots"]:
            latest = repo.require_snapshot(item["snapshot_id"])
            original_id = latest.query_manifest_json["agent_recovery"]["input_snapshot_id"]
            original = repo.require_snapshot(original_id)
            assert original.payload_json["raw"] == {}
            assert latest.payload_json["_uzi_collector_original_result"] == original.payload_json["_uzi_raw_result"]
            native = project_native_result(latest)
            assert native["error"] == "original structured source failed" and native["_timeout"] is True
            assert native["source"] == "offline-recording + playwright_fallback + playwright_fallback"
            assert native["data"]
            evidence = [row for row in repo.list_snapshot_evidence(latest.id) if row.source_kind == "native_postfetch_observation"]
            assert len(evidence) == 2
            proof = evidence[0]
            assert sha256(Path(proof.content_ref).read_bytes()).hexdigest() == proof.content_hash
            assert proof.metadata_json["research_document"] is False
            assert proof.metadata_json["historical_reconstruction"] is False
            assert proof.metadata_json["source_published_at"] is None
    expected_ids = {row["snapshot_id"] for row in stage["persisted_snapshots"]}
    assert {task["snapshot_id"] for task in result["tasks"]} == expected_ids
    assert all(task["attempt_count"] == 1 for task in result["tasks"])


def test_collection_budget_survives_ingestion_and_is_carried_to_postfetch(state, monkeypatch):
    from domain_packs.finance_ashare.tools import uzi_native_postfetch as postfetch

    seed = {"scope_id": "actual-structured-wave", "scope": "stock_collection", "cap": "2", "used": 1, "skipped": 0}
    original_collector = state.coordinator.collector

    def collector(**kwargs):
        rows = original_collector(**kwargs)
        for row in rows:
            row["facts"]["_uzi_execution"] = {"lane": "skill_main_process",
                "input_contract": "native-skill-main-envelope-v5", "search_budget": seed.copy()}
        return rows

    calls = []
    monkeypatch.setattr(postfetch.UziWebSearchAdapter, "search", lambda self, **kw: (
        calls.append(kw["query"]) or {"results": [{"title": "实际业务公告", "body": "有效来源业务说明", "url": "https://example.test/1"}]}))
    monkeypatch.setattr(import_module("lib.web_search"), "_BUDGET_STATE", {"used": 100, "skipped": 0})
    state.coordinator.collector = collector
    state.coordinator.postfetch_sources = postfetch.PostfetchSources(state.coordinator.settings)
    job = state.coordinator.create_job(tenant_id="offline", user_id="offline", actor_id="offline",
        symbols=["688041.SH"], start_date=state.today, end_date=state.today, dimensions=["8_materials", "9_futures"],
        include_research_archive=True, submit=False)
    state.coordinator.run_job_now(job["id"])
    result = state.coordinator.get_job(job["id"], tenant_id="offline", user_id="offline")
    stage = result["progress"]["native_postfetch"]
    assert len(calls) == 1
    budget = stage["search_budgets"]["688041.SH"]
    assert budget == {**seed, "cap": 2, "used": 2, "skipped": 1}
    with state.database.session() as session:
        for item in stage["persisted_snapshots"]:
            latest = session.get(MarketDataSnapshot, item["snapshot_id"])
            original = session.get(MarketDataSnapshot, latest.query_manifest_json["agent_recovery"]["input_snapshot_id"])
            assert original.payload_json["_uzi_execution"]["search_budget"] == seed
            assert latest.query_manifest_json["native_postfetch"]["search_budget"] == budget
            assert latest.payload_json["_uzi_execution"]["search_budget"] == seed
            assert original.payload_json["_uzi_raw_result"]["data"] == {}
        assert {row.snapshot_id for row in session.scalars(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id.in_([row["id"] for row in job["tasks"]])))} == {
                row["snapshot_id"] for row in stage["persisted_snapshots"]}
    # Re-reading the same current attempt must use post-fetch consumption, not
    # the older structured receipt or a resolver's preferred valid snapshot.
    from research_system.market_data.native_postfetch import _collection_search_budgets
    loaded = _collection_search_budgets(state.database, job_id=job["id"],
        task_ids=[row["id"] for row in job["tasks"]],
        scopes=[{"symbol": "688041.SH", "as_of_date": state.today}])
    assert loaded["688041.SH"] == budget
    # A newly claimed retry has no current source result yet: it must not
    # inherit prior-attempt consumption just because the old result exists.
    with state.database.session() as session:
        for task in session.scalars(select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job["id"])):
            task.attempt_count += 1
    assert _collection_search_budgets(state.database, job_id=job["id"],
        task_ids=[row["id"] for row in job["tasks"]],
        scopes=[{"symbol": "688041.SH", "as_of_date": state.today}]) == {}


def test_scoped_stage_only_requests_authorized_dimension(state):
    task = next(row for row in state.job["tasks"] if row["dimension_id"] == "17_sentiment")
    result = run_stage(state, task_ids=[task["id"]])
    assert state.sequence == [("browser", "xueqiu_posts"), ("browser", "xueqiu_posts")]
    assert {item["dimension_id"] for item in result["persisted_snapshots"]} == {"17_sentiment"}


@pytest.mark.parametrize("case,expected", [
    ("missing", "collection_search_budget_unrecorded"),
    ("foreign_snapshot", "collection_search_budget_unrecorded"),
    ("conflicting_scope", "collection_search_budget_ambiguous"),
    ("invalid_secondary", "collection_search_budget_invalid"),
    ("skipped", None),
])
def test_budget_loader_does_not_guess_current_consumption(state, case, expected):
    from research_system.market_data.native_postfetch import _collection_search_budgets

    state.coordinator.run_job_now(state.job["id"])
    with state.database.session() as session:
        results = list(session.scalars(select(MarketDataCollectionTaskResult)))
        for index, result in enumerate(results):
            if case == "skipped":
                result.status = "skipped"
                continue
            snapshot = session.get(MarketDataSnapshot, result.snapshot_id)
            metadata = {"scope_id": "one-current-wave", "scope": "stock_collection", "cap": "60", "used": 5, "skipped": 0}
            if case == "conflicting_scope":
                metadata["scope_id"] = f"wave-{index}"
            if case == "invalid_secondary" and index:
                metadata["cap"] = 1.5
            payload = {**snapshot.payload_json, "_uzi_execution": {"search_budget": metadata}}
            if case == "missing":
                payload = {"raw": {}}
            # Seed a new fixture revision, retaining the real immutable-row
            # guards and all original snapshots even for malformed metadata.
            values = {column.key: deepcopy(getattr(snapshot, column.key))
                      for column in MarketDataSnapshot.__table__.columns if column.key not in {"id", "content_hash"}}
            values.update(payload_json=payload,
                collection_task_id=None if case == "foreign_snapshot" else snapshot.collection_task_id,
                content_hash=canonical_hash({"fixture_input": snapshot.content_hash, "payload": payload, "case": case}))
            revised = MarketDataSnapshot(**values)
            session.add(revised)
            session.flush()
            result.snapshot_id = revised.id
    values = _collection_search_budgets(state.database, job_id=state.job["id"],
        task_ids=[task["id"] for task in state.job["tasks"]],
        scopes=[{"symbol": "688041.SH", "as_of_date": state.today}])
    assert values == ({"688041.SH": {"reason_code": expected}} if expected else {})


def test_full_workflow_explicit_postfetch_runs_native_force_pass_on_nonempty_data(state):
    initial = state.coordinator.collector

    def nonempty_collector(**kwargs):
        rows = initial(**kwargs)
        for row in rows:
            row["quality_status"] = "available"
            row["facts"]["_uzi_raw_result"] = {
                "ticker": "688041.SH", "source": "existing-structured",
                "data": {"existing": "已有结构化内容，但不代替原生主动补搜"},
            }
        return rows

    state.coordinator.collector = nonempty_collector
    state.coordinator.run_job_now(state.job["id"])
    result = state.coordinator.get_job(state.job["id"], tenant_id="offline", user_id="offline")
    stage = result["progress"]["native_postfetch"]
    assert stage["attempted"] == 2
    assert [name for name, _ in state.sequence] == ["structured", "browser", "browser", "archive"]
    assert {row["dimension_id"] for row in stage["persisted_snapshots"]} == {"4_peers", "17_sentiment"}


@pytest.mark.parametrize("ids", [[], ["foreign-task"]])
def test_empty_or_foreign_scope_never_requests_anything(state, ids):
    if ids:
        with pytest.raises(DraftAgentRecoveryError, match="授权"):
            run_stage(state, task_ids=ids)
    else:
        assert run_stage(state, task_ids=ids)["status"] == "not_run"
    assert state.sequence == []


def test_historical_date_never_uses_current_search_or_browser(state):
    with state.database.session() as session:
        for task in session.scalars(select(MarketDataCollectionTask)):
            task.start_date = task.end_date = state.today - timedelta(days=1)
    result = run_stage(state)
    assert result["status"] == "not_run"
    assert result["reason_code"] == "no_current_day_postfetch_scope"
    assert state.sequence == []


@pytest.mark.parametrize("reason", ["cancel", "lease", "freeze", "disabled"])
def test_stopped_or_unrequested_stage_cannot_fetch(state, reason):
    cancellation = Event()
    if reason == "cancel":
        cancellation.set()
    if reason in {"freeze", "disabled"}:
        with state.database.session() as session:
            job = session.get(MarketDataCollectionJob, state.job["id"])
            if reason == "freeze":
                job.checkpoint_json = {"publication": {"status": "published"}}
            else:
                job.request_json = {**job.request_json, "include_research_archive": False}
    assert run_stage(state, cancellation=cancellation, write_fence=lambda s: reason != "lease")["status"] == "not_run"
    assert state.sequence == []


@pytest.mark.parametrize("reason", ["cancel", "lease"])
def test_source_return_after_stop_cannot_be_persisted(state, reason):
    cancellation = Event()
    owned = [True]
    def stop():
        cancellation.set() if reason == "cancel" else owned.__setitem__(0, False)
    state.sources.on_fetch = stop
    with pytest.raises(DraftAgentRecoveryError, match="取消、超时或租约"):
        run_stage(state, cancellation=cancellation, write_fence=lambda s: owned[0])
    with state.database.session() as session:
        assert list(session.scalars(select(MarketDataSnapshot))) == []
    assert len(state.sequence) == 1
