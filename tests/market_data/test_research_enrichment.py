"""No-network contracts for evidence-bound raw fact revisions."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from contextlib import contextmanager
from threading import Barrier, Event, Lock, get_ident
from time import monotonic
from types import SimpleNamespace

import pytest

from research_system.market_data.contracts import canonical_hash, cutoff_at_for_date
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataCollectionJob, MarketDataCollectionTask
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ArchiveCandidate
from research_system.market_data import research_enrichment as enrichment
from research_system.strategies.database import StrategyDatabase
from tests.market_data.research_suggestion_fixtures import fact_suggestion
from tests.market_data.test_pit_core import _input, _payload
from tests.market_data.test_research_archive import CUTOFF, LATER, collector, page


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("enrichment test attempted business network")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def source(tmp_path):
    quote = "2026年上半年，600519所在行业收入增长25%，行业集中度40%，成本占比40%。"
    body = page(modified="2026-08-19", document_id="source-123").replace(
        "原始政策正文，公布经济统计与货币政策措施。".encode(), quote.encode(), 1
    )
    evidence = collector(tmp_path / "docs", body).archive(
        ArchiveCandidate("https://www.pbc.gov.cn/release/123.html"),
        dimension="7_industry", symbol="600519.SH", cutoff_at=CUTOFF,
    )
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'enrichment.sqlite'}")
    database.create_schema()
    with database.session() as session:
        repo = MarketDataRepository(session)
        payload = _payload("7_industry")
        payload.update(raw={"growth": "10%"}, _uzi_raw_result={
            "data": {"growth": "10%"}, "error": "original source failed",
            "_timeout": True, "source": "original-provider",
        })
        snapshot = SnapshotIngestionService(repo).ingest(_input(
            "7_industry", payload=payload, evidence=[evidence], observed_at=LATER,
            ingested_at=LATER,
        ))
        row = repo.list_snapshot_evidence(snapshot.id)[0]
        assert row.inline_content.count(quote) == 1, "wire quote must locate a unique real fixture span"
        start = row.inline_content.index(quote)
        proposal = {
            "snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
            "dimension": "7_industry", "field": "growth",
            "old_exists": True, "old_value": "10%", "new_value": "25%",
            "unit": "%", "period_start": "2026-01-01", "period_end": "2026-06-30",
            "period_text": "2026年上半年", "finding": "原文披露行业收入增长25%",
            "citations": [{"evidence_id": row.id, "document_hash": row.content_hash,
                           "start": start, "end": start + len(quote), "quote": quote}],
        }
        snapshot_id = snapshot.id
    try:
        yield database, snapshot_id, proposal
    finally:
        database.close()


def test_revision_keeps_collector_envelope_and_old_snapshot_immutable(source):
    database, snapshot_id, proposal = source
    with database.session() as session:
        repo = MarketDataRepository(session)
        original = repo.require_snapshot(snapshot_id)
        old_payload, old_hash = deepcopy(original.payload_json), original.content_hash
        result = enrichment.persist_fact_revisions(
            repo, proposals=[proposal], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-test", now=LATER,
        )
        assert result["rejected"] == []
        revision = repo.require_snapshot(result["revisions"][0]["snapshot_id"])
        assert original.content_hash == old_hash and original.payload_json == old_payload
        assert revision.id != snapshot_id and revision.payload_json["raw"]["growth"] == "25%"
        assert revision.payload_json["_uzi_raw_result"] == old_payload["_uzi_raw_result"]
        assert revision.query_manifest_json["research_enrichment"]["input_snapshot_hash"] == old_hash
        assert revision.query_manifest_json.get("collection_outcome") == original.query_manifest_json.get("collection_outcome")
        assert result["revisions"][0]["native_projection"]["error"] == "original source failed"
        assert result["revisions"][0]["native_projection"]["data"]["growth"] == "25%"


@pytest.mark.parametrize("change,reason", [
    ({"snapshot_hash": "0" * 64}, "input_hash_mismatch"),
    ({"old_value": "wrong"}, "old_value_mismatch"),
    ({"field": "overall_score"}, "field_not_supported"),
    ({"field": "growth.value"}, "field_not_supported"),
    ({"new_value": "95%"}, "value_not_grounded"),
    ({"period_end": "2026-09-01"}, "period_after_cutoff"),
    ({"period_text": "2027年度"}, "period_not_grounded"),
    ({"period_start": "2026-02-01"}, "period_not_supported_or_mismatch"),
])
def test_rejected_proposals_do_not_write_new_snapshot(source, change, reason):
    database, snapshot_id, proposal = source
    with database.session() as session:
        repo = MarketDataRepository(session)
        result = enrichment.persist_fact_revisions(
            repo, proposals=[{**proposal, **change}], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-test", now=LATER,
        )
        assert result["revisions"] == []
        assert result["rejected"][0]["reason"] == reason
        assert repo.require_snapshot(snapshot_id).payload_json["raw"]["growth"] == "10%"


@pytest.mark.parametrize("key,value,reason", [
    ("document_hash", "0" * 64, "document_hash_mismatch"),
    ("start", 1, "excerpt_mismatch"),
    ("evidence_id", "not-associated", "evidence_not_bound"),
])
def test_exact_archived_evidence_binding_is_required(source, key, value, reason):
    database, _, proposal = source
    proposal = deepcopy(proposal)
    proposal["citations"][0][key] = value
    with database.session() as session:
        result = enrichment.persist_fact_revisions(
            MarketDataRepository(session), proposals=[proposal], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-test", now=LATER,
        )
        assert not result["revisions"]
        assert result["rejected"][0]["reason"] == reason


def test_search_snippet_cannot_supply_raw_facts(source, monkeypatch):
    database, snapshot_id, proposal = source
    with database.session() as session:
        repo = MarketDataRepository(session)
        evidence = deepcopy(repo.list_snapshot_evidence(snapshot_id)[0])
        evidence.source_kind = "search_snippet"
        monkeypatch.setattr(repo, "list_snapshot_evidence", lambda _: [evidence])
        result = enrichment.persist_fact_revisions(
            repo, proposals=[proposal], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-test", now=LATER,
        )
        assert result["rejected"][0]["reason"] == "original_document_required"


def test_same_document_can_ground_distinct_facts(source):
    database, _, proposal = source
    second = {**deepcopy(proposal), "field": "concentration", "old_exists": False,
              "old_value": None, "new_value": "40%", "finding": "原文披露行业集中度40%"}
    with database.session() as session:
        result = enrichment.persist_fact_revisions(
            MarketDataRepository(session), proposals=[proposal, second], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-test", now=LATER,
        )
        assert len(result["revisions"]) == 1
        assert result["revisions"][0]["facts_count"] == 2
        assert result["rejected"] == []


@pytest.mark.parametrize("owned", [False, True])
def test_fact_revision_fences_same_transaction_before_loading_input(source, monkeypatch, owned):
    database, _, proposal = source
    cancellation = Event()
    with database.session() as session:
        repository = MarketDataRepository(session)
        loaded = []
        fenced = []
        original_load = repository.require_snapshot

        def fence(candidate_session):
            assert candidate_session is session and not loaded
            fenced.append(candidate_session)
            return owned

        def load(snapshot_id):
            assert fenced, "must fence before loading identity-map objects"
            loaded.append(snapshot_id)
            return original_load(snapshot_id)

        monkeypatch.setattr(repository, "require_snapshot", load)
        result = enrichment.persist_fact_revisions(
            repository, proposals=[proposal], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-test", now=LATER, cancellation=cancellation, write_fence=fence,
        )
        assert len(fenced) == 1
        if owned:
            assert len(result["revisions"]) == 1 and not cancellation.is_set()
        else:
            assert not loaded and result["revisions"] == []
            assert result["rejected"] == [{"reason": "worker_lease_lost"}]
            assert cancellation.is_set()


def test_lost_fence_locally_cancels_read_only_worker_and_later_scope(source, monkeypatch):
    database, _, proposal = source
    job_id = _job(database)
    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        job.end_date = date(2026, 8, 21)
        task = session.query(MarketDataCollectionTask).filter_by(job_id=job_id).one()
        task.end_date = job.end_date
    requests = []

    def request(provider, **kwargs):
        requests.append(kwargs)
        return {"status": "completed", "proposals": [proposal]}

    monkeypatch.setattr(enrichment, "request_fact_proposals", request)
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=SimpleNamespace(available=True), deadline_at=monotonic() + 30,
        cancellation=SimpleNamespace(is_set=lambda: False), write_fence=lambda session: False,
        native_recompute=lambda *a, **kw: pytest.fail("lost lease cannot recompute"),
    )
    assert len(requests) == 1, "second day must not dispatch after loss of write ownership"
    assert requests[0]["cancellation"].is_set()
    assert requests[0]["cancellation"].wait(0)
    assert result["status"] == "cancelled" and result["revisions"] == []
    assert result["rejected"] == [{"reason": "worker_lease_lost"}]


def test_native_recompute_is_actual_prepare_entry_and_preserves_input(monkeypatch):
    from domain_packs.finance_ashare.subagents import uzi_392_analysis as native
    calls = []
    monkeypatch.setattr(native, "_prepare_native_computation", lambda raw, **kwargs: calls.append((deepcopy(raw), kwargs)) or {"features": {"industry_growth": 25}, "raw_after_derivation": raw})
    raw = {"ticker": "600519.SH", "dimensions": {"7_industry": {"data": {"growth": "25%"}}}}
    original = deepcopy(raw)
    result = enrichment.recompute_native_facts(raw, profile="medium", deadline_at=monotonic() + 10)
    assert calls == [(original, {"profile": "medium", "school": None})]
    assert raw == original
    assert result["input_hash"] == canonical_hash(original)
    assert result["output_hash"] == canonical_hash(result["result"])


def test_expired_or_cancelled_work_never_calls_model_or_recompute():
    cancel = Event()
    cancel.set()
    provider = SimpleNamespace(parse=lambda **kwargs: pytest.fail("must not call model"))
    for deadline, cancellation in [(monotonic() - 1, None), (monotonic() + 100, cancel)]:
        result = enrichment.request_fact_proposals(
            provider, group_label="A · Macro-Policy", dimensions=("3_macro", "13_policy"),
            inputs={}, deadline_at=deadline, cancellation=cancellation,
        )
        assert result["status"] in {"cancelled", "deadline_exceeded"}


def _job(database, *, cancelled=False, dimensions=("7_industry",)):
    with database.session() as session:
        job = MarketDataCollectionJob(
            tenant_id="offline", user_id="offline", idempotency_key="enrichment-only",
            requested_by="offline", start_date=date(2026, 8, 20), end_date=date(2026, 8, 20),
            symbols_json=["600519.SH"], dimensions_json=list(dimensions),
            progress_json={"other_worker_progress": 1}, cancel_requested=cancelled,
        )
        session.add(job)
        session.flush()
        for dimension in dimensions:
            session.add(MarketDataCollectionTask(
                job_id=job.id, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai",
                dimension=dimension, start_date=job.start_date, end_date=job.end_date,
            ))
        return job.id


def _three_group_job(source):
    database, snapshot_id, _ = source
    dimensions = tuple(key for _, keys in enrichment._GROUPS for key in keys)
    with database.session() as session:
        repo = MarketDataRepository(session)
        evidence = enrichment._evidence_inputs(repo, snapshot_id)
        for dimension in dimensions:
            if dimension != "7_industry":
                SnapshotIngestionService(repo).ingest(_input(
                    dimension, evidence=evidence, observed_at=LATER, ingested_at=LATER,
                ))
    return _job(database, dimensions=dimensions)


def test_three_groups_start_before_first_returns_and_persist_on_owner_thread(source, monkeypatch):
    database, _, proposal = source
    job_id = _three_group_job(source)
    owner = get_ident()
    rendezvous = Barrier(3, timeout=2)
    deadline = monotonic() + 30
    cancellation = Event()
    requests, writes, progress = [], [], []
    original_session = database.session
    original_persist = enrichment.persist_fact_revisions

    @contextmanager
    def owner_session():
        assert get_ident() == owner, "model worker must not open a DB session"
        with original_session() as session:
            yield session

    def request(provider, **kwargs):
        assert get_ident() != owner
        requests.append(kwargs)
        rendezvous.wait()
        return {"status": "completed", "proposals": [proposal] if "7_industry" in kwargs["dimensions"] else []}

    def persist(repo, **kwargs):
        assert get_ident() == owner
        writes.append(kwargs)
        return original_persist(repo, **kwargs)

    def fenced_progress(value):
        assert get_ident() == owner
        progress.append(value)

    monkeypatch.setattr(database, "session", owner_session)
    monkeypatch.setattr(enrichment, "request_fact_proposals", request)
    monkeypatch.setattr(enrichment, "persist_fact_revisions", persist)
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=SimpleNamespace(available=True), deadline_at=deadline,
        cancellation=cancellation, progress_callback=fenced_progress,
        native_recompute=lambda raw, **kwargs: {"raw": raw},
    )
    assert result["status"] == "completed", result
    assert len(requests) == len(writes) == len(result["groups"]) == 3
    assert all(row["deadline_at"] == deadline and row["cancellation"] is cancellation for row in requests)
    assert len({canonical_hash(row["inputs"]) for row in requests}) == 1
    assert len(result["revisions"]) == 1
    assert progress[-1] == result and len(progress) == 4
    with database.session() as session:
        revision = MarketDataRepository(session).require_snapshot(result["revisions"][0]["snapshot_id"])
        assert revision.payload_json["raw"]["growth"] == "25%"


@pytest.mark.parametrize("reason", ["cancelled", "deadline_exceeded"])
def test_three_group_runner_does_not_dispatch_after_stop(source, monkeypatch, reason):
    database, _, _ = source
    job_id = _three_group_job(source)
    cancellation = Event()
    deadline = monotonic() + 30
    if reason == "cancelled":
        cancellation.set()
    else:
        deadline = monotonic() - 1
    monkeypatch.setattr(enrichment, "request_fact_proposals", lambda *a, **kw: pytest.fail("stopped runner dispatched a group"))
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=SimpleNamespace(available=True), deadline_at=deadline,
        cancellation=cancellation,
    )
    assert result["status"] == reason
    assert result["groups"] == result["revisions"] == []


def test_parallel_groups_without_original_documents_never_request_model(source, monkeypatch):
    database, _, _ = source
    job_id = _three_group_job(source)
    monkeypatch.setattr(enrichment, "_document_error", lambda *a: "original_document_required")
    monkeypatch.setattr(enrichment, "request_fact_proposals", lambda *a, **kw: pytest.fail("missing originals dispatched model"))
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=SimpleNamespace(available=True), deadline_at=monotonic() + 30,
    )
    assert result["status"] == "partial" and result["revisions"] == []
    assert len(result["groups"]) == 3
    assert {row["status"] for row in result["groups"]} == {"original_documents_missing"}


def test_cancellation_after_parallel_requests_prevents_revision_write(source, monkeypatch):
    database, _, proposal = source
    job_id = _three_group_job(source)
    cancellation = Event()
    rendezvous = Barrier(3, timeout=2)
    calls = []

    def request(provider, **kwargs):
        calls.append(kwargs["group_label"])
        rendezvous.wait()
        cancellation.set()
        return {"status": "completed", "proposals": [proposal]}

    monkeypatch.setattr(enrichment, "request_fact_proposals", request)
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=SimpleNamespace(available=True), deadline_at=monotonic() + 30,
        cancellation=cancellation,
        native_recompute=lambda *a, **kw: pytest.fail("cancelled work recomputed"),
    )
    assert len(calls) == 3
    assert result["status"] == "cancelled" and result["revisions"] == []
    assert {row["reason"] for row in result["rejected"]} == {"cancelled"}


def test_parallel_scopes_from_separate_jobs_share_existing_physical_limit(monkeypatch):
    # Two independent jobs may have six orchestration workers, but must still
    # share the real parse_uzi_structured limiter (the deployed value is four).
    lock, first_four_entered = Lock(), Event()
    active = peak = count = 0
    deadline = monotonic() + 10
    url = "https://www.pbc.gov.cn/release/123.html"

    class Provider:
        settings = SimpleNamespace(uzi_llm_max_concurrency=4, uzi_llm_transient_retries=0)

        def parse(self, **kwargs):
            nonlocal active, peak, count
            with lock:
                active += 1
                peak = max(peak, active)
                count += 1
                if count == 4:
                    first_four_entered.set()
            try:
                assert kwargs["deadline_at"] <= deadline
                assert first_four_entered.wait(2), "shared limiter did not admit four requests"
                response = _response(None, url)
                response["proposals"] = []
                return response
            finally:
                with lock:
                    active -= 1

    monkeypatch.setattr(enrichment, "_validate_group", lambda *a: None)
    requested = set(enrichment.FACT_FIELDS)
    inputs = {key: {"documents": [{"inline_content": "已归档原文", "url": url}], "allowed_urls": [url]} for key in requested}

    def run_one_job():
        return list(enrichment._scope_group_results(
            Provider(), requested=requested, inputs=inputs, deadline_at=deadline,
            cancellation=None, profile="medium",
        ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(run_one_job), pool.submit(run_one_job)
        outputs = first.result() + second.result()
    assert count == 6 and peak == 4 and active == 0
    assert len(outputs) == 6 and all(output["status"] == "completed" for _, output in outputs)


def _response(proposal, url):
    return {
        "research": {"agent_reviewed": True, "group_label": "B · Industry-Events", "dimensions": [{
            "dimension_id": dimension,
            "evidence": [
                {"source": "official" if dimension == "7_industry" else "unknown",
                 "url": url if dimension == "7_industry" else None,
                 "retrieved_at": "2026-09-06" if dimension == "7_industry" else None,
                 "finding": finding}
                for finding in ("原文披露行业收入增长25%，有明确统计期间。", "原文披露行业集中度40%，与收入增长是不同事实。")
            ] if dimension == "7_industry" else [
                {"source": "unknown", "finding": "未归档截止日公司最新事件。"},
                {"source": "unknown", "finding": "缺少截止日公告相关原文，不能推断事件影响。"},
            ],
            "associations": [], "conclusion": "仅采用已归档原文中的可核实事实，缺失事实和传导结果保持明确披露。",
        } for dimension in ("7_industry", "15_events")]},
        "proposals": [fact_suggestion(proposal)] if proposal is not None else [],
        "unresolved": ["15_events 尚无已归档原文"],
    }


def test_model_fixture_projects_wire_without_mutating_complete_persistence_proposal(source):
    proposal = source[2]
    before = deepcopy(proposal)
    wire = _response(proposal, "https://offline.example/document")["proposals"][0]
    assert set(wire) == {"snapshot_id", "field", "new_value", "unit", "period_text", "finding", "citations"}
    assert all(set(row) == {"evidence_id", "quote"} for row in wire["citations"])
    assert proposal == before
    assert enrichment.FactProposal.model_validate(proposal).snapshot_hash == before["snapshot_hash"]
    assert proposal["citations"][0]["start"] == before["citations"][0]["start"]
    with source[0].session() as session:
        document = MarketDataRepository(session).list_snapshot_evidence(source[1])[0]
        assert document.inline_content.count(wire["citations"][0]["quote"]) == 1


def test_runner_revises_requested_scope_and_recomputes_full_input_with_common_deadline(source, monkeypatch):
    database, snapshot_id, proposal = source
    job_id = _job(database)
    with database.session() as session:
        repo = MarketDataRepository(session)
        basic = SnapshotIngestionService(repo).ingest(_input("0_basic"))
        basic_id = basic.id
        url = repo.list_snapshot_evidence(snapshot_id)[0].source_url
    deadline = monotonic() + 30
    requests, native_inputs, transport_inputs = [], [], []
    transport = enrichment.native_transport_facts

    def checked_transport(snapshot, **kwargs):
        transport_inputs.append((snapshot.id, kwargs))
        return transport(snapshot, **kwargs)

    def parse(provider, **kwargs):
        requests.append(kwargs)
        return _response(proposal, url)

    monkeypatch.setattr(enrichment, "parse_uzi_structured", parse)
    monkeypatch.setattr(enrichment, "native_transport_facts", checked_transport)
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=SimpleNamespace(available=True), deadline_at=deadline,
        native_recompute=lambda raw, **kwargs: native_inputs.append(deepcopy(raw)) or {"features": {}, "raw_after_derivation": raw},
    )
    assert result["status"] == "completed", result
    assert len(requests) == 1 and requests[0]["deadline_at"] == deadline
    assert result["revisions"][0]["input_snapshot_id"] == snapshot_id
    assert len(native_inputs) == 1
    assert native_inputs[0]["dimensions"]["7_industry"]["data"]["growth"] == "25%"
    assert "0_basic" in native_inputs[0]["dimensions"]
    assert basic_id in {row["snapshot_id"] for row in result["recomputations"][0]["snapshot_inputs"]}
    assert {snapshot_id, result["revisions"][0]["snapshot_id"]}.issubset({row[0] for row in transport_inputs})
    assert all(row[1]["cutoff_at"] == cutoff_at_for_date(date(2026, 8, 20), "Asia/Shanghai") for row in transport_inputs)
    assert proposal["citations"][0]["evidence_id"] in {row.id for _, context in transport_inputs for row in context["evidence"]}
    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        assert job.progress_json["other_worker_progress"] == 1
        assert job.checkpoint_json["research_enrichment"] == result


def test_worker_callback_owns_fenced_progress_writes(source):
    database, _, _ = source
    job_id = _job(database)
    progress = []
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY, provider=None,
        deadline_at=monotonic() + 30, progress_callback=progress.append,
    )
    assert result["groups"][0]["status"] == "provider_unavailable"
    assert progress[-1] == result
    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        assert job.progress_json == {"other_worker_progress": 1}
        assert job.checkpoint_json == {}


def test_enrichment_prompt_keeps_observed_bound_separate_from_unknown_publication(monkeypatch, tmp_path):
    body = ("<html><body><article>" + "600519行业收入增长25%，行业集中度40%。" * 20 + "</article></body></html>").encode()
    current = collector(tmp_path / "current-doc", body, observed=CUTOFF).archive(
        ArchiveCandidate("https://www.pbc.gov.cn/release/123.html"),
        dimension="7_industry", symbol="600519.SH", cutoff_at=CUTOFF,
    )
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'current-research.sqlite'}")
    database.create_schema()
    try:
        with database.session() as session:
            SnapshotIngestionService(MarketDataRepository(session)).ingest(_input(
                "7_industry", evidence=[current], observed_at=CUTOFF, ingested_at=CUTOFF,
            ))
        job_id = _job(database)
        inputs = []
        monkeypatch.setattr(enrichment, "request_fact_proposals", lambda *args, **kwargs: inputs.append(kwargs["inputs"]) or {"status": "completed", "proposals": []})
        result = enrichment.run_research_enrichment(
            database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
            provider=SimpleNamespace(available=True), deadline_at=monotonic() + 30,
        )
        assert result["status"] == "completed"
        document = next(row for row in inputs[0]["7_industry"]["documents"] if row["document_hash"] == current.content_hash)
        assert document["published_at"] is document["publisher_published_at"] is None
        assert document["visible_by"] == CUTOFF.isoformat()
        assert document["publication_time_basis"] == "observed_upper_bound"
        assert "不得当作发布日" in document["publication_time_note"]
        assert "market_publication_date" not in document
    finally:
        database.close()


def test_enrichment_and_report_share_market_publication_view(source):
    from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import market_publication_time_view

    database, snapshot_id, _ = source
    with database.session() as session:
        repo = MarketDataRepository(session)
        snapshot = repo.require_snapshot(snapshot_id)
        proof = deepcopy(repo.list_snapshot_evidence(snapshot_id)[0])
        proof.published_at = datetime(2026, 8, 19, 16, tzinfo=timezone.utc)
        before = deepcopy(snapshot.payload_json)
        documents = enrichment._research_documents(snapshot, [proof], CUTOFF)
        assert len(documents) == 1
        view = market_publication_time_view(documents[0]["published_at"], snapshot.exchange_timezone)
        assert view["market_publication_date"] == "2026-08-20"
        assert all(documents[0][key] == value for key, value in view.items())
        context = {"7_industry": {"documents": documents}}
        citation_view, _ = enrichment._research_citation_view(context, None)
        assert citation_view["7_industry"]["document_publications"][proof.source_url] == [view["published_at_market"]]
        assert snapshot.payload_json == before


def test_profile_publication_view_is_local_without_mutating_validation_scope():
    scope = {"target": {"snapshot_id": "fixed", "exchange_timezone": "Asia/Shanghai", "raw": {}},
             "allowed_fields": [], "documents": [{"evidence_id": "proof", "document_hash": "hash",
                 "inline_content": "归档原文", "url": "https://example.org/original",
                 "published_at": "2026-08-31T16:00:00Z", "observed_at": "2026-09-02T00:00:00Z"}]}
    before = deepcopy(scope)
    prompt = enrichment._company_profile_prompt_scope(scope, {})
    assert prompt["documents"][0]["market_publication_date"] == "2026-09-01"
    citation_view, _ = enrichment._research_citation_view({"7_industry": {"documents": []}}, scope)
    assert citation_view["7_industry"]["document_publications"]["https://example.org/original"] == ["2026-09-01T00:00:00+08:00"]
    assert scope == before


def test_job_cancellation_blocks_all_revision_and_model_calls(source, monkeypatch):
    database, _, _ = source
    job_id = _job(database, cancelled=True)
    monkeypatch.setattr(enrichment, "request_fact_proposals", lambda *args, **kwargs: pytest.fail("cancelled job started model"))
    result = enrichment.run_research_enrichment(
        database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=SimpleNamespace(available=True), deadline_at=monotonic() + 30,
    )
    assert result["status"] == "cancelled" and result["revisions"] == []


def test_raw_root_and_bonus_presence_transports_are_preserved():
    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter
    from tests.market_data.test_native_symbol_transport import _source
    _, source = _source({"fund_managers": None, "similar_stocks": []})
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=date(2026, 8, 20))
    snapshot = SimpleNamespace(id="native", symbol="600519.SH", dimension="0_basic", content_hash="a" * 64,
                               payload_json=value.payload, status="available", query_manifest_json=value.query_manifest, validation_json={})
    cutoff = cutoff_at_for_date(date(2026, 8, 20), "Asia/Shanghai")
    raw = build_uzi_raw([enrichment._native_input_row(snapshot, evidence=value.evidence, cutoff_at=cutoff)], "600519.SH")
    assert raw["fund_managers"] is None and raw["similar_stocks"] == []
    assert "full" not in raw

    # A historical reconstruction may retain the current root spill for
    # provenance, but absence of an actual bound observation is not null/[]
    # evidence. The recomputation must pass through the same PIT boundary.
    unproven = enrichment._native_input_row(snapshot, evidence=[], cutoff_at=cutoff)
    assert "_uzi_top_level_fields" not in unproven["facts"]
    assert unproven["facts"]["_uzi_transport_limitations"]["bonus_status"] == "not_fixed_historical"
    reconstructed_raw = build_uzi_raw([unproven], "600519.SH")
    assert "fund_managers" not in reconstructed_raw and "similar_stocks" not in reconstructed_raw


def test_sdk_strict_schema_has_typed_required_scalars(source):
    from openai.lib._pydantic import to_strict_json_schema
    schema = to_strict_json_schema(enrichment.ResearchProposalGroup)
    proposal = schema["$defs"]["FactProposal"]
    for name in ("old_value", "new_value"):
        assert name in proposal["required"]
        assert all(branch.get("type") for branch in proposal["properties"][name]["anyOf"])
    valid = source[2]
    assert enrichment.FactProposal.model_validate({**valid, "new_value": True}).new_value is True
    with pytest.raises(Exception):
        enrichment.FactProposal.model_validate({**valid, "new_value": {"guess": 25}})


def test_actual_native_prepare_recomputes_offline_and_leaves_input_immutable():
    from tests.test_uzi_392_analysis import _lite_snapshots
    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
    raw = build_uzi_raw(_lite_snapshots(), "600000")
    before = deepcopy(raw)
    result = enrichment.recompute_native_facts(raw, profile="medium", deadline_at=monotonic() + 30)
    assert result["status"] == "completed"
    assert "20_valuation_models" in result["result"]["raw_after_derivation"]["dimensions"]
    assert result["result"]["panel"] and result["result"]["features"]
    assert result["output_hash"] == canonical_hash(result["result"])
    assert raw == before
