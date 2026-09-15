"""Offline contracts for original documents, not Agent finding completion."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from research_system.market_data.research_archive import (
    ArchiveCandidate,
    ArchiveEvidenceError,
)
from research_system.market_data.research_collection import research_archive_gap_dimensions
from tests.market_data.test_research_archive import CUTOFF, LATER, Response, collector, page


@pytest.fixture(autouse=True)
def no_business_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("archive alignment tests must not access the network")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def test_one_document_inventory_is_ready_without_claiming_findings_complete(tmp_path):
    calls = []
    c = collector(tmp_path, page(modified="2026-08-19", document_id="123"))
    c.search = lambda **kwargs: {"results": [{"url": "https://www.pbc.gov.cn/release/1.html"}]}
    c.browser_adapter = SimpleNamespace(collect_many=lambda **kwargs: pytest.fail("one validated original does not require a second URL"))
    c.http = SimpleNamespace(get=lambda url, **kwargs: calls.append(url) or Response(page(modified="2026-08-19", document_id="123")))
    result = c.collect(dimension="3_macro", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert result["status"] == "ready"
    assert result["document_inventory"]["document_count"] == 1
    assert result["research_completion"] == "not_assessed"
    assert result["raw_enrichment"] == "not_run"
    assert len(calls) == 1


def test_official_government_macro_original_is_not_limited_to_two_hosts(tmp_path):
    result = collector(tmp_path, page(modified="2026-08-19", document_id="123")).archive(
        ArchiveCandidate("https://jrj.sh.gov.cn/20260819/release.html"),
        dimension="3_macro", cutoff_at=CUTOFF,
    )
    assert result.metadata["relevance"]["subject_basis"] == "market_wide_macro"
    assert result.metadata["evidence_mode"] == "public_archive_reconstruction"


def test_duplicate_bytes_at_two_urls_are_one_document_not_two_findings(tmp_path):
    c = collector(tmp_path, page(modified="2026-08-19", document_id="123"))
    c.search = lambda **kwargs: {"results": [{"url": "https://www.pbc.gov.cn/release/1.html"}, {"url": "https://www.pbc.gov.cn/release/2.html"}]}
    result = c.collect(dimension="3_macro", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert len(result["evidence"]) == 1
    assert result["document_inventory"]["document_count"] == 1
    assert result["research_completion"] == "not_assessed"


def test_one_archived_original_does_not_schedule_another_url(monkeypatch):
    evidence = SimpleNamespace(source_kind="research_document", source_url="https://www.pbc.gov.cn/release/1.html", inline_content="两条事实可以来自同一篇正文", content_ref="/archive/sha.html", content_hash="a" * 64)
    repository = SimpleNamespace(list_snapshot_evidence=lambda snapshot_id: [evidence])
    monkeypatch.setattr("research_system.market_data.research_collection.SnapshotResolver", lambda *args, **kwargs: SimpleNamespace(coverage=lambda *args, **kwargs: SimpleNamespace(dimensions=[SimpleNamespace(dimension="3_macro", snapshot_id="original")])) )
    assert research_archive_gap_dimensions(repository, registry=None, symbol="688041.SH", market="CN_EQUITY", as_of_date=date(2026, 8, 20), dimensions=["3_macro"]) == []


@pytest.mark.parametrize("dimension,body", [("8_materials", "海光信息688041的晶圆采购与封装测试构成营业成本。"), ("9_futures", "海光信息688041本期衍生品投资和套期保值不适用。")])
def test_cninfo_company_disclosures_can_support_cost_and_hedging(tmp_path, monkeypatch, dimension, body):
    c = collector(tmp_path, b"%PDF-company-original")
    monkeypatch.setattr("research_system.market_data.research_archive.PdfReader", lambda *args, **kwargs: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: body * 12)]))
    discovered = []
    candidate = ArchiveCandidate("https://static.cninfo.com.cn/finalpage/2026-08-19/1225000001.PDF", "海光信息半年度报告", datetime(2026, 8, 19, tzinfo=timezone.utc), "1225000001", "https://www.cninfo.com.cn/new/hisAnnouncement/query", "f" * 64, True, "688041")
    c._cninfo_candidates = lambda symbol, cutoff, **kwargs: discovered.append(kwargs["dimension"]) or [candidate]
    c.search = lambda **kwargs: {"results": []}
    result = c.collect(dimension=dimension, symbol="688041.SH", subject="数字芯片设计", cutoff_at=CUTOFF)
    assert discovered == [dimension]
    assert result["status"] == "ready"
    assert result["evidence"][0].metadata["relevance"]["subject_basis"] == "publisher_listing_security_match"
    assert result["evidence"][0].metadata["evidence_mode"] == "public_archive_reconstruction"


def test_unrelated_company_pdf_cannot_pass_using_industry_keywords(tmp_path, monkeypatch):
    monkeypatch.setattr("research_system.market_data.research_archive.PdfReader", lambda *args, **kwargs: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "北方华创002371半导体原材料采购成本。" * 12)]))
    c = collector(tmp_path, b"%PDF-wrong-company")
    candidate = ArchiveCandidate("https://static.cninfo.com.cn/finalpage/2026-08-19/1225000002.PDF", "北方华创年度报告", datetime(2026, 8, 19, tzinfo=timezone.utc), "1225000002", "https://www.cninfo.com.cn/new/hisAnnouncement/query", "f" * 64, True, "002371")
    with pytest.raises(ArchiveEvidenceError) as error:
        c.archive(candidate, dimension="8_materials", symbol="688041.SH", subject="半导体", cutoff_at=CUTOFF)
    assert error.value.code == "archive_subject_unrelated"


def test_policy_accepts_explicit_frozen_industry_context_not_company_name_only(tmp_path):
    content = page(modified="2026-08-19", document_id="policy-1").replace("原始政策正文，公布经济统计与货币政策措施。".encode(), "半导体行业发展政策通知，明确研发支持措施。".encode())
    result = collector(tmp_path, content).archive(ArchiveCandidate("https://www.gov.cn/zhengce/123.htm"), dimension="13_policy", symbol="688041.SH", subject="海光信息", subject_terms=("半导体",), cutoff_at=CUTOFF)
    assert result.metadata["relevance"]["matched_subject_terms"] == ["半导体"]


def test_versioned_official_index_article_is_not_a_listing(tmp_path):
    result = collector(tmp_path, page(modified="2026-08-19", document_id="123")).archive(ArchiveCandidate("https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/2026081912000000001/index.html"), dimension="3_macro", cutoff_at=CUTOFF)
    assert result.metadata["publisher_document_id"] == "123"
    assert result.metadata["evidence_mode"] == "public_archive_reconstruction"


def test_official_versioned_article_links_pdf_with_auditable_parent_proof(tmp_path, monkeypatch):
    page_url = "https://www.pbc.gov.cn/release/2026081912000000001/index.html"
    pdf_url = "https://www.pbc.gov.cn/release/2026081912000000001/2026081911590000001.pdf"
    content = page(modified="2026-08-19", document_id="123").replace(b"</article>", b'<a href="2026081911590000001.pdf">Full report</a></article>')
    calls = []
    c = collector(tmp_path, content)
    c.http = SimpleNamespace(get=lambda url, **kwargs: calls.append(url) or Response(b"%PDF-pbc-report" if url == pdf_url else content))
    c.search = lambda **kwargs: {"results": [{"url": page_url}]}
    monkeypatch.setattr("research_system.market_data.research_archive.PdfReader", lambda *args, **kwargs: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "经济统计与货币政策执行报告正文。" * 12)]))
    result = c.collect(dimension="3_macro", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert calls == [page_url, pdf_url]
    assert len(result["evidence"]) == 2
    attachment = next(row for row in result["evidence"] if row.mime_type == "application/pdf")
    assert attachment.metadata["listing_url"] == page_url
    assert attachment.metadata["listing_hash"] == result["evidence"][0].content_hash
    assert attachment.metadata["proof_basis"] == "publisher_versioned_article_and_identified_attachment"


def test_unversioned_page_or_mutable_pdf_link_does_not_prove_history(tmp_path):
    content = page().replace(b"</article>", b'<a href="latest.pdf">Full report</a></article>')
    c = collector(tmp_path, content)
    c.search = lambda **kwargs: {"results": [{"url": "https://www.pbc.gov.cn/release/article.html"}]}
    result = c.collect(dimension="3_macro", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert result["evidence"] == []
    assert any(row.get("reason") == "historical_version_unproven" for row in result["attempts"])


@pytest.mark.parametrize("link", ["latest.pdf", "https://untrusted.example/2026081911590000001.pdf"])
def test_versioned_article_does_not_authorize_mutable_or_external_attachment(tmp_path, link):
    calls = []
    url = "https://www.pbc.gov.cn/release/article.html"
    content = page(modified="2026-08-19", document_id="123").replace(b"</article>", f'<a href="{link}">Full report</a></article>'.encode())
    c = collector(tmp_path, content)
    c.http = SimpleNamespace(get=lambda target, **kwargs: calls.append(target) or Response(content))
    c.search = lambda **kwargs: {"results": [{"url": url}]}
    result = c.collect(dimension="3_macro", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert calls == [url]
    assert len(result["evidence"]) == 1


def test_late_revised_publisher_page_cannot_supply_a_historical_attachment(tmp_path):
    calls = []
    url = "https://www.pbc.gov.cn/release/article.html"
    content = page(modified="2026-09-01", document_id="123").replace(b"</article>", b'<a href="2026081911590000001.pdf">Full report</a></article>')
    c = collector(tmp_path, content)
    c.http = SimpleNamespace(get=lambda target, **kwargs: calls.append(target) or Response(content))
    c.search = lambda **kwargs: {"results": [{"url": url}]}
    result = c.collect(dimension="3_macro", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert calls == [url]
    assert result["evidence"] == []
    assert result["attempts"][-1]["reason"] == "archive_after_cutoff"


def test_company_discovery_queries_report_and_esg_with_exact_stock_and_cutoff(tmp_path):
    calls = []
    c = collector(tmp_path, b"unused")
    c._cninfo_org_ids = {"688041": "gssh0688041"}

    def post(url, **kwargs):
        calls.append(kwargs["data"])
        payload = {"announcements": [{
            "secCode": "688041", "orgId": "gssh0688041",
            "announcementTime": int(datetime(2026, 8, 19, tzinfo=timezone.utc).timestamp() * 1000),
            "announcementId": "1225000001", "adjunctUrl": "finalpage/2026-08-19/1225000001.PDF",
            "announcementTitle": "海光信息2025年度环境、社会和公司治理（ESG）报告",
        }]}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload, close=lambda: None)

    c.http = SimpleNamespace(post=post)
    result = c._cninfo_candidates("688041.SH", CUTOFF, dimension="8_materials")
    assert {row["searchkey"] for row in calls} == {"年度报告", "ESG", "社会责任"}
    assert all(row["stock"] == "688041,gssh0688041" for row in calls)
    assert all(row["seDate"] == "2025-08-20~2026-08-20" for row in calls)
    assert len(result) == 1 and result[0].security_code == "688041"


def test_explicit_recovery_reuses_single_original_without_changing_raw(tmp_path):
    from research_system.market_data.collection import MarketDataCollectionCoordinator
    from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.repository import MarketDataRepository
    from research_system.market_data.research_collection import persist_research_documents
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import _input

    evidence = collector(tmp_path / "docs", page(modified="2026-08-19", document_id="123")).archive(
        ArchiveCandidate("https://www.pbc.gov.cn/release/1.html"), dimension="3_macro", cutoff_at=CUTOFF
    )
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'reuse.sqlite'}")
    database.create_schema()
    coordinator = MarketDataCollectionCoordinator(
        database, collector=lambda **kwargs: pytest.fail("qualified raw must not be recollected"),
        archive_collector=SimpleNamespace(collect=lambda **kwargs: pytest.fail("single archived original must not be downloaded again")),
        settings=SimpleNamespace(workspace_root=tmp_path),
    )
    try:
        with database.session() as session:
            repository = MarketDataRepository(session)
            original = SnapshotIngestionService(repository).ingest(_input("3_macro"))
            revision = persist_research_documents(
                repository, registry=DEFAULT_DIMENSION_REGISTRY, snapshot_id=original.id,
                documents=[evidence, evidence], collection_task_id=None, job_id="first", now=LATER,
            )
            assert revision.payload_json == original.payload_json
            assert len([row for row in repository.list_snapshot_evidence(revision.id) if row.source_kind == "research_document"]) == 1
        job = coordinator.create_job(
            tenant_id="tenant", user_id="user", actor_id="user", symbols=["600519.SH"],
            start_date=date(2026, 8, 20), end_date=date(2026, 8, 20), dimensions=["3_macro"],
            include_research_archive=True, submit=False,
        )
        coordinator.run_job_now(job["id"])
        output = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        stage = output["progress"]["research_archive"]
        assert stage["document_inventory_ready"] is True
        assert stage["evidence_check_passed"] is None
        assert stage["research_completion"] == "not_assessed"
        assert stage["raw_enrichment"] == "not_run"
        assert stage["persisted_snapshot_count"] == 0
        assert stage["attempts"][0]["reused"] is True
    finally:
        coordinator.close()
        database.close()
