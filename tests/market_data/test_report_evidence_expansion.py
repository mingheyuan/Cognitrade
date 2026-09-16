"""Report lead → explicit archive → new input; no report-side network."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q
from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
from research_system.market_data.collection import CollectionJobError, MarketDataCollectionCoordinator
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.models import MarketDataCollectionJob
from research_system.market_data.report_repairs import report_repair_plan
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ArchiveCandidate
from research_system.market_data.research_collection import persist_research_documents, recover_research_documents
from research_system.market_data.research_requests import ResearchDocumentRequest, pending_source_requests
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, _input, seed_strict_bundle
from tests.market_data.test_research_archive import CUTOFF, LATER, collector, page
from tests.market_data.test_scoped_research_recovery import _client, _service
from tests.test_uzi_qualitative_group_diagnostics import evidence_bundle, group_payload

URL = "https://www.pbc.gov.cn/release/new.html"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *args, **kwargs: pytest.fail("unexpected network"))


def test_unbound_lead_is_not_accepted_as_evidence_but_has_a_collectable_plan():
    payload = group_payload("A · Macro-Policy")
    payload["dimensions"][0]["evidence"][0]["url"] = URL
    group = q.QualitativeGroupResult.model_validate(payload)
    leads = pending_source_requests(group, evidence_bundle())
    assert len(leads) == 1 and leads[0]["url"] == URL
    assert "finding" not in leads[0] and len(leads[0]["finding_hash"]) == 64
    with pytest.raises(ValueError, match="qualitative_ungrounded_url"):
        q._validate_group(group, ("3_macro", "13_policy"), evidence_bundle())
    content = {"report_allowed": False, "results": [{"qualitative_execution": {"pending_evidence": leads}}]}
    assert native_recovery(content)["kind"] == "evidence_pending"
    plan = report_repair_plan(content, symbol="600519", as_of=AS_OF, today=date(2026, 9, 15))
    assert plan["collection_requires_confirmation"] is True
    assert plan["collection_request"]["research_document_requests"] == [{"dimension": "3_macro", "url": URL}]
    assert plan["collection_request"]["mode"] == "missing_or_stale"
    assert plan["report_retry"]["refresh_inputs"] is True


def test_actual_group_failure_preserves_pending_lead_in_execution_not_native_evidence(monkeypatch):
    bundle = evidence_bundle()
    monkeypatch.setattr(q, "_fixed_report_inputs", lambda *args, **kwargs: ([], {}, []))
    monkeypatch.setattr(q, "_dimension_context", lambda rows, dimension, **kwargs: bundle[dimension])
    monkeypatch.setattr(q, "_qualitative_stage_cache_path", lambda *args, **kwargs: None)
    def evaluate(provider, **kwargs):
        payload = group_payload(kwargs["group_label"])
        if kwargs["group_label"].startswith("A"):
            payload["dimensions"][0]["evidence"][0]["url"] = URL
        return q.QualitativeGroupResult.model_validate(payload)
    monkeypatch.setattr(q, "_evaluate_group", evaluate)
    result = q.apply_qualitative_deep_dive({}, snapshot_rows=[], llm_provider=SimpleNamespace(available=True))
    assert result["agent_reviewed"] is False
    assert "3_macro" not in result["qualitative_deep_dive"]
    assert result["qualitative_agent_execution"]["pending_evidence"][0]["url"] == URL


@pytest.mark.parametrize("url", ["http://127.0.0.1/a", "file:///tmp/a", "https://gov.cn.evil.invalid/a",
    "https://user:password@www.pbc.gov.cn/a", "https://www.pbc.gov.cn/a?access_token=secret",
    "https://www.pbc.gov.cn:444/a", "https://www.pbc.gov.cn/a\nheader"])
def test_unsafe_source_is_never_a_fetch_request(url):
    with pytest.raises(ValueError):
        ResearchDocumentRequest(dimension="3_macro", url=url)


@pytest.mark.parametrize("url", [
    "https://webapi.cninfo.com.cn/#/apiDoc",
    "http://webapi.cninfo.com.cn/#/thematicStatistics",
    "https://webapi.cninfo.com.cn/",
    "https://www.cninfo.com.cn/search/",
])
def test_navigation_is_not_an_original_document_retry(url):
    with pytest.raises(ValueError):
        ResearchDocumentRequest(dimension="7_industry", url=url)
    payload = group_payload("B · Industry-Events")
    payload["dimensions"][0]["evidence"][0]["url"] = url
    group = q.QualitativeGroupResult.model_validate(payload)
    assert pending_source_requests(group, evidence_bundle()) == []
    content = {"results": [{"qualitative_execution": {"pending_evidence": [
        {"dimension": "7_industry", "url": url},
    ]}}]}
    plan = report_repair_plan(content, symbol="688072", as_of=AS_OF, today=date(2026, 9, 16))
    assert plan["pending_evidence"] == []
    assert plan["collection_request"] is None


def test_explicit_archive_only_fetches_requested_documents_and_keeps_pit_checks(tmp_path):
    archive = collector(tmp_path, page(modified="2026-08-19", document_id="new"))
    archive._official_candidates = Mock(return_value=[ArchiveCandidate("https://www.pbc.gov.cn/unrelated.html")])
    original_get = archive.http.get
    archive.http.get = Mock(side_effect=original_get)
    result = archive.collect(dimension="3_macro", symbol="600519.SH", subject="经济", cutoff_at=CUTOFF,
                             requested_urls=[URL])
    assert result["status"] == "ready"
    assert [call.args[0] for call in archive.http.get.call_args_list] == [URL]
    assert result["evidence"][0].metadata["evidence_mode"] == "public_archive_reconstruction"
    invalid = collector(tmp_path / "invalid", page())
    invalid._official_candidates = lambda *args, **kwargs: []
    result = invalid.collect(dimension="3_macro", symbol="600519.SH", subject="经济", cutoff_at=CUTOFF,
                             requested_urls=[URL])
    assert result["evidence"] == []
    assert any(row.get("reason") == "historical_version_unproven" for row in result["attempts"])


def test_explicit_new_url_is_not_skipped_by_existing_archive_and_creates_new_snapshot(tmp_path):
    from research_system.market_data.ingestion import SnapshotIngestionService
    db = StrategyDatabase(f"sqlite:///{tmp_path / 'archive.sqlite'}")
    db.create_schema()
    make = lambda identifier: collector(tmp_path / identifier, page(modified="2026-08-19", document_id=identifier)).archive(
        ArchiveCandidate(f"https://www.pbc.gov.cn/release/{identifier}.html"), dimension="3_macro", cutoff_at=CUTOFF)
    previous, new = make("old"), make("new")
    archive = SimpleNamespace(collect=Mock(return_value={"evidence": [new], "attempts": [], "status": "ready"}))
    coordinator = MarketDataCollectionCoordinator(db, collector=Mock(side_effect=AssertionError("raw recollection")), archive_collector=archive)
    try:
        with db.session() as session:
            repo = MarketDataRepository(session)
            original = SnapshotIngestionService(repo).ingest(_input("3_macro"))
            old = persist_research_documents(repo, registry=DEFAULT_DIMENSION_REGISTRY, snapshot_id=original.id,
                documents=[previous], collection_task_id=None, job_id="initial", now=LATER)
            old_id, old_hash, old_payload = old.id, old.content_hash, deepcopy(old.payload_json)
        request = {"symbols": ["600519.SH"], "asset_family": "equity", "start_date": AS_OF.isoformat(),
            "end_date": AS_OF.isoformat(), "dimensions": ["3_macro"], "mode": "missing_or_stale",
            "include_research_archive": True, "research_document_requests": [{"dimension": "3_macro", "url": URL}]}
        coordinator._submit = Mock()
        with _client(_service(coordinator)) as client:
            response = client.post("/api/v2/finance/market-data/collection-jobs", json=request)
        assert response.status_code == 202, response.text
        job = response.json()
        with db.session() as session:
            assert session.get(MarketDataCollectionJob, job["id"]).request_json["research_document_requests"] == request["research_document_requests"]
        result = recover_research_documents(db, job_id=job["id"], settings=SimpleNamespace(workspace_root=tmp_path),
            registry=DEFAULT_DIMENSION_REGISTRY, archive_collector=archive)
        assert archive.collect.call_args.kwargs["requested_urls"] == [URL]
        assert result["persisted_snapshot_count"] == 1
        with db.session() as session:
            repo = MarketDataRepository(session)
            old = repo.require_snapshot(old_id)
            assert (old.content_hash, old.payload_json) == (old_hash, old_payload)
            revised = repo.require_snapshot(result["persisted_snapshots"][0]["snapshot_id"])
            assert revised.id != old_id and revised.payload_json == old_payload
            assert {row.source_url for row in repo.list_snapshot_evidence(revised.id)} >= {previous.source_url, URL}
        coordinator.collector.assert_not_called()
    finally:
        coordinator.close(); db.close()


@pytest.mark.parametrize("changes", [{"include_research_archive": False}, {"dimensions": ["7_industry"]},
    {"symbols": ["600519.SH", "688012.SH"]}, {"end_date": date(2026, 8, 21)}])
def test_collection_rejects_implicit_or_out_of_scope_leads(tmp_path, changes):
    db = StrategyDatabase("sqlite://")
    db.create_schema()
    coordinator = MarketDataCollectionCoordinator(db, collector=Mock(), archive_collector=object())
    try:
        with pytest.raises(CollectionJobError) as error:
            coordinator.create_job(**{ "tenant_id": "tenant", "user_id": "user", "actor_id": "user",
                "symbols": ["600519.SH"], "start_date": AS_OF, "end_date": AS_OF,
                "dimensions": ["3_macro"], "include_research_archive": True,
                "research_document_requests": [{"dimension": "3_macro", "url": URL}], "submit": False, **changes})
        assert error.value.code == "research_document_scope_invalid"
    finally:
        coordinator.close(); db.close()


def test_complete_worker_archive_publish_revision_keeps_original_and_other_stock(tmp_path):
    from research_system.market_data.report_inputs import DatabaseReportInputGateway

    db = StrategyDatabase(f"sqlite:///{tmp_path / 'worker.sqlite'}")
    db.create_schema()
    seed_strict_bundle(db, "600519.SH")
    seed_strict_bundle(db, "688012.SH")
    gateway = DatabaseReportInputGateway(db, clock=lambda: LATER)
    original = gateway.freeze_report_manifest(symbols=["600519.SH", "688012.SH"], as_of_date=AS_OF,
        tenant_id="tenant", user_id="user", workflow_pins={"research_profile": "lite"}, context={})
    before = deepcopy(original)
    archive = collector(tmp_path, page(modified="2026-08-19", document_id="new"))
    archive._official_candidates = lambda *args, **kwargs: []
    raw = Mock(side_effect=AssertionError("qualified structured data must not be recollected"))
    coordinator = MarketDataCollectionCoordinator(db, collector=raw, archive_collector=archive,
        settings=SimpleNamespace(workspace_root=tmp_path), research_provider=object(), quant_auxiliary_source=object(),
        postfetch_sources=object())  # Fail loudly if a targeted request starts these unrelated stages.
    try:
        request = dict(tenant_id="tenant", user_id="user", actor_id="user", symbols=["600519.SH"],
            start_date=AS_OF, end_date=AS_OF, dimensions=["3_macro"], mode="missing_or_stale",
            include_research_archive=True, publish_when_complete=False,
            research_document_requests=[{"dimension": "3_macro", "url": URL}], submit=False)
        job = coordinator.create_job(**request, idempotency_key="explicit-source")
        with pytest.raises(CollectionJobError) as error:
            coordinator.create_job(**{**request, "research_document_requests": [
                {"dimension": "3_macro", "url": "https://www.pbc.gov.cn/different.html"}]},
                idempotency_key="explicit-source")
        assert error.value.status_code == 409
        coordinator.run_job_now(job["id"])
        result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        assert result["research_archive"]["succeeded"] == 1, result
        assert not any(name in result["progress"] for name in
                       ("native_postfetch", "research_enrichment", "native_quant_auxiliary"))
        assert result["can_confirm_freeze"] is True, result
        coordinator.confirm_freeze(job["id"], tenant_id="tenant", user_id="user", actor_id="user",
            proposal_hash=result["publication_candidate"]["proposal_hash"], acknowledge_degraded=True)
        revised = gateway.revise_report_manifest(manifest_id=original["manifest_id"], manifest_hash=original["manifest_hash"],
            symbols=["600519.SH"], tenant_id="tenant", user_id="user")
        assert revised["manifest_hash"] != original["manifest_hash"]
        assert revised["items"][0]["bundle_id"] != original["items"][0]["bundle_id"]
        assert revised["items"][0]["context"]["evidence_bindings"] != original["items"][0]["context"]["evidence_bindings"]
        loaded = gateway.load_frozen_report_manifest(manifest_id=original["manifest_id"], manifest_hash=original["manifest_hash"],
            tenant_id="tenant", user_id="user")
        assert revised["items"][1] == loaded["items"][1]
        assert [row["snapshots"] for row in loaded["items"]] == [row["snapshots"] for row in before["items"]]
        assert original == before
        raw.assert_not_called()
    finally:
        coordinator.close(); db.close()
