"""Offline append-only profile dependency rebuild and collection stage wiring."""
import importlib
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from research_system.market_data.contracts import canonical_hash, cutoff_at_for_date
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
    MarketDataSnapshotEvidence,
)
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_company_profile_revision import (
    persist,
    proposal_for,
    scope_for,
    seed,
)
from tests.market_data.test_pit_core import AS_OF, NOW, _input
from tests.market_data.test_research_stage_wiring import stage as stage  # noqa: PLC0414


def module():
    return importlib.import_module("research_system.market_data.company_profile_rebuild")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("live source forbidden"))


@pytest.fixture
def state(tmp_path, monkeypatch):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'rebuild.sqlite'}")
    database.create_schema()
    with database.session() as session:
        seeded = seed(session, tmp_path, full=True)
        old_peer = SnapshotIngestionService(seeded.repo).ingest(_input("4_peers"))
        scope = scope_for(seeded)
        revision = persist(seeded, scope, [proposal_for(seeded, scope)])
        basic_id = revision["revisions"][0]["snapshot_id"]
        job = MarketDataCollectionJob(tenant_id="offline", user_id="offline", idempotency_key="rebuild",
            requested_by="offline", start_date=AS_OF, end_date=AS_OF, origin="manual",
            symbols_json=["600519.SH"], dimensions_json=["7_industry", "4_peers"],
            request_json={"include_research_archive": True})
        session.add(job)
        session.flush()
        for dimension, original in [("7_industry", seeded.source), ("4_peers", old_peer)]:
            task = MarketDataCollectionTask(job_id=job.id, symbol="600519.SH", market="CN_EQUITY",
                exchange_timezone="Asia/Shanghai", dimension=dimension, start_date=AS_OF, end_date=AS_OF,
                status="succeeded", attempt_count=1)
            session.add(task)
            session.flush()
            session.add(MarketDataCollectionTaskResult(task_id=task.id, as_of_date=AS_OF, attempt=1,
                status="succeeded", snapshot_id=original.id))
        original_ids = [seeded.basic.id, seeded.source.id, old_peer.id, basic_id]
        originals = {key: session.get(MarketDataSnapshot, key).content_hash for key in original_ids}
        result = {"database": database, "job_id": job.id, "originals": originals,
                  "basic_id": basic_id, "source_id": seeded.source.id, "proof_id": seeded.proof.id,
                  "binding": deepcopy(seeded.binding)}
    monkeypatch.setattr(module(), "_today", lambda zone: AS_OF)
    yield result
    database.engine.dispose()


def collected(kwargs):
    raw = kwargs["fixed_basic_by_symbol"]["600519.SH"]["data"]
    return [{"symbol": "600519.SH", "snapshot_type": kind, "quality_status": "available",
        "retrieved_at": (NOW + timedelta(hours=1)).isoformat(),
        "source": {"provider_id": "offline:native"}, "facts": {"_uzi_raw_result": {
            "ticker": "600519.SH", "data": {"industry": raw["industry"], "self": deepcopy(raw),
                "peers": [{"code": "peer"}], "peer_count": 1}, "source": "offline:native", "fallback": False}}}
        for kind in kwargs["snapshot_types"]]


def run(state, collector=collected, **kwargs):
    if "task_ids" not in kwargs:
        with state["database"].session() as session:
            kwargs["task_ids"] = tuple(session.scalars(select(MarketDataCollectionTask.id).where(
                MarketDataCollectionTask.job_id == state["job_id"])))
    return module().run_company_profile_dependency_rebuild(state["database"], job_id=state["job_id"],
        registry=DEFAULT_DIMENSION_REGISTRY, collector=lambda **options: collector(options),
        deadline=kwargs.pop("deadline", monotonic() + 60), cancellation=kwargs.pop("cancellation", Event()),
        write_fence=kwargs.pop("write_fence", lambda session: True), **kwargs)


def test_fixed_basic_rebuilds_only_authorized_7_and_4_append_only_with_bound_archive(state):
    seen = []
    result = run(state, lambda options: seen.append({key: deepcopy(value) for key, value in options.items()
                                                   if key != "cancellation"}) or collected(options))
    assert len(seen) == 1
    options = seen[0]
    assert set(options["snapshot_types"]) == {"industry", "peers"}
    assert options["fixed_basic_by_symbol"]["600519.SH"]["data"]["industry"] == "集成电路设计"
    assert options["uzi_root_route"] is False and options["max_workers"] <= 5
    assert options.get("as_of_date") is None and "market" not in options["snapshot_types"]
    assert result["status"] == "partial" and len(result["revisions"]) == 2
    assert "8_materials" in result["scopes"][0]["pending_dimensions"]
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        assert {key: repo.require_snapshot(key).content_hash for key in state["originals"]} == state["originals"]
        basic = repo.require_snapshot(state["basic_id"])
        for row in result["revisions"]:
            snapshot = repo.require_snapshot(row["snapshot_id"])
            receipt = snapshot.query_manifest_json["company_profile_dependency_receipt"]
            assert receipt == {"version": "company-profile-dependency-v1", "input_basic_snapshot_id": basic.id,
                "input_basic_snapshot_hash": basic.content_hash,
                "dependency_fingerprint": basic.query_manifest_json["company_profile_revision"]["dependency_fingerprint"],
                "raw_result_hash": canonical_hash(project_native_result(snapshot, version="uzi-dim-result-v3"))}
            results = list(session.scalars(select(MarketDataCollectionTaskResult).where(MarketDataCollectionTaskResult.snapshot_id == snapshot.id)))
            assert len(results) == 1 and results[0].attempt == 2
            if row["dimension"] == "7_industry":
                assert state["proof_id"] in {proof.id for proof in repo.list_snapshot_evidence(snapshot.id)}
                assert snapshot.query_manifest_json["research_archive"]["text_extractions"][0]["binding"] == state["binding"]
                assert snapshot.query_manifest_json["research_archive"]["research_completion"] == "not_assessed"
                assert snapshot.query_manifest_json["research_archive"]["raw_enrichment"] == "not_run"
                assert snapshot.field_sources_json == {"$": "offline:native"}
                links = list(session.scalars(select(MarketDataSnapshotEvidence).where(
                    MarketDataSnapshotEvidence.snapshot_id == snapshot.id, MarketDataSnapshotEvidence.evidence_id == state["proof_id"])))
                assert links and all(link.purpose == "company_profile_inherited_archive_context" for link in links)
            else:
                assert row["document_status"] == "missing_original_documents"
        assert session.scalar(select(func.count()).select_from(MarketDataCollectionTaskResult)) == 4


def test_later_explicit_task_resumes_marker_without_new_proposal_and_reuses_completed(state):
    first = run(state)
    second = run(state, lambda options: pytest.fail("already rebuilt dimensions recollected"))
    assert first["scopes"][0]["pending_dimensions"] == second["scopes"][0]["pending_dimensions"]
    assert second["revisions"] == []
    assert second["scopes"][0]["reused_snapshot_ids"]


def test_task_scope_is_sole_write_authority(state):
    with state["database"].session() as session:
        task = session.scalar(select(MarketDataCollectionTask).where(MarketDataCollectionTask.dimension == "4_peers"))
        task.dimension = "5_chain"
    seen = []
    result = run(state, lambda options: seen.extend(options["snapshot_types"]) or collected(options))
    assert seen == ["industry"] and [row["dimension"] for row in result["revisions"]] == ["7_industry"]
    assert "4_peers" in result["scopes"][0]["pending_dimensions"]


def test_historical_rebuild_rejects_unbound_current_response(state, monkeypatch):
    monkeypatch.setattr(module(), "_today", lambda zone: AS_OF + timedelta(days=1))
    calls = []
    def transport(options):
        calls.append(options)
        assert options["as_of_date"] == AS_OF
        assert options["cutoff_at"] == cutoff_at_for_date(AS_OF, "Asia/Shanghai")
        # A bounded request does not prove its returned data is historical.
        # The old current-day fixture deliberately has no PIT response binding.
        return collected(options)
    result = run(state, transport)
    assert len(calls) == 1
    assert not result["revisions"]
    assert result["scopes"][0]["reason_code"] == "historical_rebuild_response_unbound"
    with state["database"].session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataCollectionTaskResult)) == 2
        assert {key: session.get(MarketDataSnapshot, key).content_hash
                for key in state["originals"]} == state["originals"]


@pytest.mark.parametrize("stop", ["cancelled", "deadline_exceeded", "worker_lease_lost"])
def test_stop_before_call_and_after_return_cannot_write(state, stop):
    cancellation = Event()
    fence = [True]
    def transport(options):
        response = collected(options)
        if stop == "cancelled": cancellation.set()
        elif stop == "worker_lease_lost": fence[0] = False
        else: options["cancellation"].deadline = 0
        return response
    result = run(state, transport, cancellation=cancellation, write_fence=lambda session: fence[0])
    assert not result["revisions"] and result["status"] == stop
    with state["database"].session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataCollectionTaskResult)) == 2


def test_network_call_has_no_open_database_session(state, monkeypatch):
    original = state["database"].session
    active = [0]
    @contextmanager
    def counted():
        with original() as session:
            active[0] += 1
            try: yield session
            finally: active[0] -= 1
    monkeypatch.setattr(state["database"], "session", counted)
    def transport(options):
        assert active[0] == 0
        return collected(options)
    assert len(run(state, transport)["revisions"]) == 2


def test_changed_input_hash_after_fetch_refuses_append(state):
    def transport(options):
        response = collected(options)
        with state["database"].session() as session:
            snapshot = session.get(MarketDataSnapshot, state["basic_id"])
            session.execute(update(MarketDataSnapshot).where(MarketDataSnapshot.id == snapshot.id).values(
                payload_json={**snapshot.payload_json, "injected": True}))
        return response
    result = run(state, transport)
    assert not result["revisions"]
    assert result["scopes"][0]["reason_code"] == "input_content_hash_mismatch"


def test_native_cached_nested_identity_is_disclosed_not_rewritten(state):
    def transport(options):
        rows = collected(options)
        for row in rows:
            if row["snapshot_type"] == "peers":
                row["facts"]["_uzi_raw_result"]["data"]["global_peer_comparison"] = {"target": {"name": "old", "local_industry": "old"}}
        return rows
    result = run(state, transport)
    peer = next(row for row in result["revisions"] if row["dimension"] == "4_peers")
    assert peer["known_upstream_issues"]
    with state["database"].session() as session:
        native = project_native_result(MarketDataRepository(session).require_snapshot(peer["snapshot_id"]))
        assert native["data"]["global_peer_comparison"]["target"]["local_industry"] == "old"


def test_no_marker_has_no_calls_and_no_added_stage_progress(state):
    with state["database"].session() as session:
        basic = session.get(MarketDataSnapshot, state["basic_id"])
        basic.revoked_at = NOW
    result = run(state, lambda options: pytest.fail("no marker must do nothing"))
    assert result["status"] == "not_required" and result["scopes"] == []
    with state["database"].session() as session:
        assert "company_profile_dependency_rebuild" not in session.get(MarketDataCollectionJob, state["job_id"]).progress_json


@pytest.mark.parametrize("stop", ["cancelled", "deadline_exceeded", "worker_lease_lost"])
def test_preexisting_stop_is_zero_collector_calls(state, stop):
    event = Event()
    if stop == "cancelled": event.set()
    result = run(state, lambda options: pytest.fail("stopped job called collector"), cancellation=event,
        deadline=0 if stop == "deadline_exceeded" else monotonic() + 60,
        write_fence=lambda session: stop != "worker_lease_lost")
    assert result["status"] == stop and not result["revisions"]


def test_subscription_job_is_not_explicit_authority(state):
    with state["database"].session() as session:
        session.get(MarketDataCollectionJob, state["job_id"]).origin = "subscription"
    assert run(state, lambda options: pytest.fail("implicit subscription collector"))["status"] == "explicit_collection_required"


@pytest.mark.parametrize("bad", ["missing", "corrupt"])
def test_inherited_bound_body_failure_is_not_dropped_to_structured_success(state, bad):
    from pathlib import Path
    path = Path(state["binding"]["text_content_ref"])
    if bad == "missing": path.unlink()
    else: path.write_bytes(b"modified offline fixture")
    result = run(state, lambda options: pytest.fail("invalid fixed document must stop before sources"))
    assert not result["revisions"] and result["status"] == "partial"
    assert result["scopes"][0]["reason_code"].startswith("archive_text_")


@pytest.mark.parametrize("fault", ["extra", "duplicate", "other_symbol"])
def test_returned_scope_cannot_expand_authority(state, fault):
    def transport(options):
        rows = collected(options)
        if fault == "extra": rows.append({**rows[0], "snapshot_type": "market"})
        elif fault == "duplicate": rows.append(deepcopy(rows[0]))
        else: rows[0]["symbol"] = "000001.SZ"
        return rows
    result = run(state, transport)
    assert not result["revisions"]
    with state["database"].session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataCollectionTaskResult)) == 2


def test_executed_native_failure_still_binds_dependency_without_improving_quality(state):
    def transport(options):
        return [{**row, "quality_status": "data_gap", "facts": {"_uzi_raw_result": {
            "data": {}, "error": "fixture provider failed", "source": "offline:native", "fallback": True}}} for row in collected(options)]
    result = run(state, transport)
    assert result["status"] == "partial"
    assert not {"7_industry", "4_peers"}.intersection(result["scopes"][0]["pending_dimensions"])
    with state["database"].session() as session:
        for row in result["revisions"]:
            snapshot = MarketDataRepository(session).require_snapshot(row["snapshot_id"])
            receipt = snapshot.query_manifest_json["company_profile_dependency_receipt"]
            native = project_native_result(snapshot, version="uzi-dim-result-v3")
            assert receipt["raw_result_hash"] == canonical_hash(native)
            assert native["data"] == {} and native["error"] == "fixture provider failed"
            assert native["fallback"] is True and row["status"] == "degraded"


def test_collector_exception_without_native_response_has_no_receipt_or_arbitrary_error_text(state):
    def transport(options):
        raise ValueError("secret fixture request context")
    result = run(state, transport)
    assert not result["revisions"] and "secret fixture" not in str(result)
    assert result["scopes"][0]["reason_code"] == "dependency_rebuild_failed"


@pytest.mark.parametrize("raw", [{}, {"data": []}, None])
def test_invalid_native_dimresult_cannot_get_a_receipt(state, raw):
    def transport(options):
        return [{**row, "facts": {"_uzi_raw_result": raw}} for row in collected(options)]
    result = run(state, transport)
    assert not result["revisions"]
    assert result["scopes"][0]["reason_code"] == "collector_dimresult_invalid"


def test_fence_lost_after_ingestion_rolls_back_snapshots_and_attempts_atomically(state, monkeypatch):
    fence = [True]
    original = SnapshotIngestionService.ingest
    def ingest(self, value):
        snapshot = original(self, value)
        fence[0] = False
        return snapshot
    monkeypatch.setattr(SnapshotIngestionService, "ingest", ingest)
    with state["database"].session() as session:
        before = session.scalar(select(func.count()).select_from(MarketDataSnapshot))
    result = run(state, write_fence=lambda session: fence[0])
    assert result["status"] == "worker_lease_lost" and not result["revisions"]
    with state["database"].session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataSnapshot)) == before
        assert session.scalar(select(func.count()).select_from(MarketDataCollectionTaskResult)) == 2


def test_stage_is_after_enrichment_before_aux_and_shares_deadline(stage, monkeypatch):
    coordinator, job, _, _, clock, calls = stage
    monkeypatch.setattr(module(), "has_company_profile_rebuild", lambda *args, **kwargs: True)
    def rebuilt(*args, **kwargs):
        calls.append(("profile_rebuild", kwargs))
        clock[0] += 20
        return {"status": "partial", "scopes": [{"pending_dimensions": ["8_materials"]}], "revisions": []}
    monkeypatch.setattr(module(), "run_company_profile_dependency_rebuild", rebuilt)
    task_ids = tuple(row["id"] for row in job["tasks"])
    coordinator._run_research_archive_stage(job["id"], worker_id="worker", cancellation=Event(), task_ids=task_ids)
    assert [kind for kind, _ in calls] == ["archive", "enrichment", "profile_rebuild", "auxiliary"]
    assert calls[1][1]["deadline_at"] == calls[2][1]["deadline"] == calls[3][1]["deadline"] == 610
    assert calls[2][1]["collector"] is coordinator.collector
    assert calls[2][1]["task_ids"] == task_ids
    with coordinator.database.session() as session:
        assert calls[2][1]["write_fence"](session) is True
    current = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
    assert current["progress"]["company_profile_dependency_rebuild"]["status"] == "partial"


def _add_tasks(state, dimensions):
    with state["database"].session() as session:
        existing = {row.dimension for row in session.scalars(select(MarketDataCollectionTask))}
        for dimension in dimensions:
            if dimension in existing: continue
            session.add(MarketDataCollectionTask(job_id=state["job_id"], symbol="600519.SH", market="CN_EQUITY",
                exchange_timezone="Asia/Shanghai", dimension=dimension, start_date=AS_OF, end_date=AS_OF))


def test_all_industry_consumers_are_task_scoped_and_9_receives_same_wave_8(state, monkeypatch):
    required = {"3_macro", "4_peers", "7_industry", "8_materials", "9_futures", "10_valuation", "13_policy", "similar_stocks", "16_lhb"}
    _add_tasks(state, required)
    seen = []
    def transport(options):
        seen.append(options)
        rows = collected(options)
        for row in rows:
            if row["snapshot_type"] == "materials":
                row["facts"]["_uzi_raw_result"]["data"]["materials_detail"] = [{"code": "CU0", "name": "铜"}]
        return rows
    monkeypatch.setattr(module(), "_recompute_fixed", lambda *args, **kwargs: {"status": "completed", "input_hash": "a" * 64, "output_hash": "b" * 64})
    result = run(state, transport)
    assert {row["dimension"] for row in result["revisions"]} == required
    assert result["scopes"][0]["pending_dimensions"] == []
    assert result["recomputations"][0]["status"] == "completed"
    future = next(row for row in result["revisions"] if row["dimension"] == "9_futures")
    material = next(row for row in result["revisions"] if row["dimension"] == "8_materials")
    with state["database"].session() as session:
        snapshot = MarketDataRepository(session).require_snapshot(future["snapshot_id"])
        assert snapshot.query_manifest_json["company_profile_materials_input"]["snapshot_id"] == material["snapshot_id"]


def test_single_9_unbound_8_stays_pending_without_recollecting_8(state):
    _add_tasks(state, ["9_futures"])
    seen = []
    result = run(state, lambda options: seen.extend(options["snapshot_types"]) or collected(options))
    assert "materials" not in seen and "futures" not in seen
    assert "9_futures" in result["scopes"][0]["pending_dimensions"]
    assert result["scopes"][0]["blocked_dimensions"]["9_futures"] == "materials_dependency_unbound"


def test_new_success_clears_only_old_task_error_metadata(state):
    with state["database"].session() as session:
        for task in session.scalars(select(MarketDataCollectionTask)):
            task.last_error_code, task.last_error_message = "old_failure", "old source failed"
    result = run(state)
    with state["database"].session() as session:
        for revision in result["revisions"]:
            task = session.get(MarketDataCollectionTask, revision["task_id"])
            assert task.last_error_code is None and task.last_error_message is None


def test_explicit_retry_of_bound_native_failure_executes_again_without_reusing_old_failure(state):
    def failed(options):
        return [{**row, "quality_status": "data_gap", "facts": {"_uzi_raw_result": {
            "data": {}, "source": "offline:native", "error": "fixture failure", "fallback": True}}}
            for row in collected(options)]
    first = run(state, failed)
    assert len(first["revisions"]) == 2
    second = run(state)
    assert len(second["revisions"]) == 2 and all(row["attempt"] == 3 for row in second["revisions"])


def test_single_9_uses_existing_bound_failed_8_as_native_none_not_hidden_refetch(state):
    _add_tasks(state, ["8_materials"])
    def first_transport(options):
        rows = collected(options)
        for row in rows:
            if row["snapshot_type"] == "materials":
                row["quality_status"] = "data_gap"
                row["facts"]["_uzi_raw_result"] = {"data": {}, "source": "offline:8", "error": "actual failure", "fallback": True}
        return rows
    first = run(state, first_transport)
    material = next(row for row in first["revisions"] if row["dimension"] == "8_materials")
    with state["database"].session() as session:
        for task in session.scalars(select(MarketDataCollectionTask)):
            task.dimension = {"7_industry": "9_futures", "4_peers": "5_chain", "8_materials": "6_research"}[task.dimension]
    calls = []
    second = run(state, lambda options: calls.append(options) or collected(options))
    assert len(calls) == 1 and calls[0]["snapshot_types"] == ["futures"]
    assert calls[0]["fixed_materials_by_symbol"]["600519.SH"]["error"] == "actual failure"
    assert [row["dimension"] for row in second["revisions"]] == ["9_futures"]
    with state["database"].session() as session:
        snapshot = MarketDataRepository(session).require_snapshot(second["revisions"][0]["snapshot_id"])
        assert snapshot.query_manifest_json["company_profile_materials_input"]["snapshot_id"] == material["snapshot_id"]


def test_actual_native_recompute_is_offline_and_only_records_hashes_after_all_bindings(state, monkeypatch):
    _add_tasks(state, ["3_macro", "8_materials", "9_futures", "10_valuation", "13_policy", "similar_stocks", "16_lhb"])
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("pure native computation fetched source"))
    result = run(state)
    assert result["scopes"][0]["pending_dimensions"] == []
    computed = result["recomputations"][0]
    assert computed["status"] == "completed", result
    assert len(computed["input_hash"]) == len(computed["output_hash"]) == 64
    assert "result" not in computed and computed["model_executions"] == 0 and computed["published"] is False


@pytest.mark.parametrize("request_9", [True, False])
def test_materials_retry_only_rebuilds_9_when_explicitly_requested(state, request_9):
    _add_tasks(state, ["8_materials", "9_futures"])
    def initial(options):
        rows = collected(options)
        for row in rows:
            if row["snapshot_type"] == "materials":
                row["quality_status"] = "data_gap"
                row["facts"]["_uzi_raw_result"] = {"data": {}, "error": "failed 8", "source": "offline:8", "fallback": True}
        return rows
    first = run(state, initial)
    assert {"8_materials", "9_futures"} <= {row["dimension"] for row in first["revisions"]}
    if not request_9:
        with state["database"].session() as session:
            task = session.scalar(select(MarketDataCollectionTask).where(MarketDataCollectionTask.dimension == "9_futures"))
            task.dimension = "5_chain"
    calls = []
    second = run(state, lambda options: calls.extend(options["snapshot_types"]) or collected(options))
    assert set(calls) == ({"materials", "futures"} if request_9 else {"materials"})
    assert ("9_futures" in second["scopes"][0]["pending_dimensions"]) is not request_9
    if request_9:
        old_future = next(row["snapshot_id"] for row in first["revisions"] if row["dimension"] == "9_futures")
        assert old_future not in second["scopes"][0]["reused_snapshot_ids"]


def _child_revision(state, field):
    from research_system.market_data.company_profile_revision import (
        persist_company_profile_revisions,
    )
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        parent = repo.require_snapshot(state["basic_id"])
        parent_identity = parent.id, parent.content_hash
        seeded = SimpleNamespace(repo=repo, basic=parent,
            source=repo.require_snapshot(state["source_id"]),
            proof=repo.list_snapshot_evidence(state["source_id"])[0])
        scope = scope_for(seeded)
        revision = persist_company_profile_revisions(repo,
            proposals=[proposal_for(seeded, scope, field)], scope=scope,
            authorized_basic_snapshot_ids={parent.id}, registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-second-profile-revision", now=NOW + timedelta(seconds=2))
        assert revision["rejected"] == []
        child_id = revision["revisions"][0]["snapshot_id"]
    return child_id, parent_identity


@pytest.mark.parametrize("parent_rebuilt", [True, False])
def test_business_only_child_revision_reuses_completed_or_rebuilds_pending_parent_consumers(state, parent_rebuilt):
    first = run(state) if parent_rebuilt else None
    child_id, parent_identity = _child_revision(state, "main_business")
    calls = []
    result = run(state, lambda options: calls.extend(options["snapshot_types"]) or collected(options))
    assert result["scopes"][0]["input_basic_snapshot_id"] == child_id
    if parent_rebuilt:
        assert calls == [] and result["revisions"] == []
        assert set(result["scopes"][0]["reused_snapshot_ids"]) == {row["snapshot_id"] for row in first["revisions"]}
        assert result["scopes"][0]["pending_dimensions"] == first["scopes"][0]["pending_dimensions"]
    else:
        assert set(calls) == {"industry", "peers"}
        assert {row["dimension"] for row in result["revisions"]} == {"7_industry", "4_peers"}
        assert "8_materials" in result["scopes"][0]["pending_dimensions"]
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        assert repo.require_snapshot(parent_identity[0]).content_hash == parent_identity[1]
        for row in result["revisions"]:
            receipt = repo.require_snapshot(row["snapshot_id"]).query_manifest_json["company_profile_dependency_receipt"]
            assert receipt["input_basic_snapshot_id"] == child_id


def test_industry_then_name_revision_routes_all_actual_consumers_to_current_basic(state, monkeypatch):
    child_id, _ = _child_revision(state, "name")
    _add_tasks(state, list(module().SUPPORTED))
    monkeypatch.setattr(module(), "_recompute_fixed", lambda *args, **kwargs: {"status": "completed"})
    calls = []
    result = run(state, lambda options: calls.extend(options["snapshot_types"]) or collected(options))
    assert set(calls) == set(module().SUPPORTED.values())
    assert {row["dimension"] for row in result["revisions"]} == set(module().SUPPORTED)
    assert result["scopes"][0]["pending_dimensions"] == []
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        for row in result["revisions"]:
            receipt = repo.require_snapshot(row["snapshot_id"]).query_manifest_json["company_profile_dependency_receipt"]
            assert receipt["input_basic_snapshot_id"] == child_id


def test_reused_bound_inputs_keep_document_gaps_and_upstream_identity_disclosures(state, monkeypatch):
    _add_tasks(state, ["3_macro", "8_materials", "9_futures", "10_valuation", "13_policy", "similar_stocks", "16_lhb"])
    monkeypatch.setattr(module(), "_recompute_fixed", lambda *args, **kwargs: {"status": "completed"})
    def transport(options):
        rows = collected(options)
        for row in rows:
            if row["snapshot_type"] == "peers":
                row["facts"]["_uzi_raw_result"]["data"]["global_peer_comparison"] = {"target": {"local_industry": "旧行业"}}
        return rows
    first = run(state, transport)
    assert first["status"] == "partial" and first["scopes"][0]["pending_dimensions"] == []
    second = run(state, lambda options: pytest.fail("bound available inputs must not be recollected for disclosure"))
    assert second["status"] == "partial" and second["revisions"] == []
    reused = {row["dimension"]: row for row in second["scopes"][0]["reused_inputs"]}
    assert reused["7_industry"]["document_status"] == "inherited_original_documents"
    assert reused["4_peers"]["document_status"] == "missing_original_documents"
    assert reused["4_peers"]["known_upstream_issues"] == [{
        "reason": "native_global_peer_cache_profile_mismatch", "field": "global_peer_comparison.target.local_industry"}]
    assert second["report_delivery_assessed"] is False
