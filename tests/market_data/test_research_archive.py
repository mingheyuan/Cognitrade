from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from research_system.market_data.research_archive import (
    ArchiveCandidate,
    ArchiveEvidenceError,
    ResearchArchiveCollector,
    ResearchDocumentStore,
)
from research_system.market_data.research_readiness import research_cited_urls

CUTOFF = datetime(2026, 8, 20, 15, 59, 59, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 6, tzinfo=timezone.utc)


class Response:
    status_code = 200

    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        pass


def collector(tmp_path, content, *, observed=LATER):
    return ResearchArchiveCollector(
        store=ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=lambda *args, **kwargs: Response(content)),
        clock=lambda: observed,
    )


def page(*, modified="", document_id=""):
    return (
        f'<html><head><meta name="pubdate" content="2026-08-19">'
        f'<meta name="article:modified_time" content="{modified}">'
        f'<meta name="articleid" content="{document_id}"></head><body><article>'
        + "原始政策正文，公布经济统计与货币政策措施。" * 20
        + "</article></body></html>"
    ).encode()


def test_old_url_and_publication_date_do_not_prove_historical_body(tmp_path):
    with pytest.raises(ArchiveEvidenceError, match="无法证明") as captured:
        collector(tmp_path, page()).archive(
            ArchiveCandidate("https://www.pbc.gov.cn/release/1.html"),
            dimension="3_macro",
            cutoff_at=CUTOFF,
        )
    assert captured.value.code == "historical_version_unproven"
    assert not list(tmp_path.rglob("*.html"))


def test_publisher_versioned_body_is_persisted_and_late_revision_rejected(tmp_path):
    content = page(modified="2026-08-19T10:00:00+08:00", document_id="release-123")
    result = collector(tmp_path, content).archive(
        ArchiveCandidate("https://www.pbc.gov.cn/release/1.html"),
        dimension="3_macro",
        cutoff_at=CUTOFF,
    )
    assert result.grade.value == "B"
    assert result.metadata["evidence_mode"] == "public_archive_reconstruction"
    assert Path(result.content_ref).read_bytes() == content
    assert result.content_hash == hashlib.sha256(content).hexdigest()
    assert "货币政策" in result.inline_content
    with pytest.raises(ArchiveEvidenceError) as captured:
        collector(
            tmp_path, page(modified="2026-09-01", document_id="release-123")
        ).archive(
            ArchiveCandidate("https://www.pbc.gov.cn/release/1.html"),
            dimension="3_macro",
            cutoff_at=CUTOFF,
        )
    assert captured.value.code == "archive_after_cutoff"


def test_contemporaneous_capture_is_not_labelled_archive_reconstruction(tmp_path):
    result = collector(tmp_path, page(), observed=CUTOFF).archive(
        ArchiveCandidate("https://www.gov.cn/zhengce/test.htm"),
        dimension="13_policy",
        cutoff_at=CUTOFF,
    )
    assert result.grade.value == "A"
    assert result.metadata["evidence_mode"] == "contemporaneous_archive"


def _undated_company_pdf(monkeypatch, text=None):
    body = text if text is not None else "688041海光信息年度报告披露营业成本与晶圆采购、封装测试成本。" * 20
    monkeypatch.setattr(
        "research_system.market_data.research_archive.PdfReader",
        lambda *args, **kwargs: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: body)]),
    )
    return b"%PDF-offline-company-original", ArchiveCandidate(
        "https://static.cninfo.com.cn/finalpage/2026-04-01/1225068005.PDF",
        "海光信息年度报告",
    )


def test_current_pdf_without_first_publication_uses_observed_visibility_not_fake_release(tmp_path, monkeypatch):
    content, candidate = _undated_company_pdf(monkeypatch)
    evidence = collector(tmp_path, content, observed=CUTOFF).archive(
        candidate, dimension="8_materials", symbol="688041.SH", cutoff_at=CUTOFF,
    )
    assert evidence.published_at == evidence.observed_at == CUTOFF
    assert evidence.metadata["publication_time_basis"] == "observed_upper_bound"
    assert evidence.metadata["publisher_published_at"] is None
    assert evidence.metadata["visible_by"] == CUTOFF.isoformat()
    assert evidence.metadata["proof_basis"] == "captured_before_cutoff"
    assert evidence.grade.value == "A"
    assert Path(evidence.content_ref).read_bytes() == content


def test_publication_projection_separates_release_date_and_visible_bound_without_changing_old_refs(tmp_path, monkeypatch):
    from research_system.market_data import research_archive
    content, candidate = _undated_company_pdf(monkeypatch)
    evidence = collector(tmp_path, content, observed=CUTOFF).archive(
        candidate, dimension="8_materials", symbol="688041.SH", cutoff_at=CUTOFF,
    )
    fields = research_archive.research_publication_projection(evidence)
    assert fields["published_at"] is fields["publisher_published_at"] is None
    assert fields["visible_by"] == CUTOFF.isoformat()
    assert fields["publication_time_basis"] == "observed_upper_bound"
    assert "首次发布日期未知" in fields["publication_time_note"]
    old = evidence.model_copy(update={"metadata": {"evidence_mode": "contemporaneous_archive"}})
    assert research_archive.research_publication_projection(old) == {}
    # SQLite ORM timestamps are naive UTC; don't relabel them as local time.
    stored = SimpleNamespace(source_kind="research_document", metadata_json=evidence.metadata,
                             published_at=CUTOFF.replace(tzinfo=None), observed_at=CUTOFF.replace(tzinfo=None))
    assert research_archive.research_publication_projection(stored) == fields


def test_observed_only_source_reaches_bundle_and_native_research_context(tmp_path, monkeypatch):
    from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import _dimension_context
    from research_system.market_data.bundles import BundleMaterializer, BundlePublisher
    from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.repository import MarketDataRepository
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import _input

    content, candidate = _undated_company_pdf(monkeypatch, "600519年度报告披露营业成本与原材料采购的具体经营事实。" * 20)
    evidence = collector(tmp_path, content, observed=CUTOFF).archive(
        candidate, dimension="8_materials", symbol="600519.SH", cutoff_at=CUTOFF,
    )
    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    try:
        with database.session() as session:
            repo = MarketDataRepository(session)
            ingestion = SnapshotIngestionService(repo)
            for contract in DEFAULT_DIMENSION_REGISTRY:
                options = {"evidence": [evidence], "observed_at": CUTOFF, "ingested_at": CUTOFF} if contract.dimension == "8_materials" else {}
                ingestion.ingest(_input(contract.dimension, **options))
            bundle = BundlePublisher(repo).publish(
                symbol="600519.SH", market="CN_EQUITY", as_of_date=date(2026, 8, 20), now=LATER,
            )
            materialized = BundleMaterializer(repo).materialize(bundle.id)
            material = next(row for row in materialized.snapshots if row["dimension_id"] == "8_materials")
            ref = next(row for row in material["source_refs"] if row.get("source_kind") == "research_document")
            assert ref["published_at"] is ref["publisher_published_at"] is None
            assert ref["visible_by"] == CUTOFF.isoformat()
            assert ref["url"] in research_cited_urls(material)
            context = _dimension_context([material], "8_materials")
            assert context["allowed_urls"] == [ref["url"]]
            prompt_ref = context["snapshots"][0]["source_refs"][0]
            assert prompt_ref["published_at"] is None
            assert prompt_ref["publication_time_basis"] == "observed_upper_bound"
    finally:
        database.close()


def test_same_undated_pdf_cannot_reconstruct_historical_visibility(tmp_path, monkeypatch):
    content, candidate = _undated_company_pdf(monkeypatch)
    with pytest.raises(ArchiveEvidenceError) as captured:
        collector(tmp_path, content, observed=LATER).archive(
            candidate, dimension="8_materials", symbol="688041.SH", cutoff_at=CUTOFF,
        )
    assert captured.value.code == "archive_publication_unknown"
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.parametrize("text,reason", [
    ("", "archive_body_unusable"),
    ("600519另一公司年度报告披露营业成本和晶圆采购。" * 20, "archive_subject_unrelated"),
    ("688041海光信息本次董事会授权变更联系方式与通讯地址。" * 20, "archive_dimension_unrelated"),
], ids=["no-body", "wrong-company", "wrong-topic"])
def test_current_unknown_publication_preserves_body_identity_and_topic_guards(tmp_path, monkeypatch, text, reason):
    content, candidate = _undated_company_pdf(monkeypatch, text)
    with pytest.raises(ArchiveEvidenceError) as captured:
        collector(tmp_path, content, observed=CUTOFF).archive(
            candidate, dimension="8_materials", symbol="688041.SH", cutoff_at=CUTOFF,
        )
    assert captured.value.code == reason
    assert not list(tmp_path.rglob("*.pdf"))


def test_undated_pdf_download_finishing_after_cutoff_cannot_use_start_time(tmp_path, monkeypatch):
    content, candidate = _undated_company_pdf(monkeypatch)
    c = collector(tmp_path, content, observed=CUTOFF)
    clock = iter([CUTOFF - timedelta(seconds=1), CUTOFF + timedelta(seconds=1)])
    c.clock = lambda: next(clock)
    with pytest.raises(ArchiveEvidenceError) as captured:
        c.archive(candidate, dimension="8_materials", symbol="688041.SH", cutoff_at=CUTOFF)
    assert captured.value.code == "archive_publication_unknown"
    assert not list(tmp_path.rglob("*.pdf"))


def test_cninfo_pdf_requires_identity_and_preserves_original_bytes(
    tmp_path, monkeypatch
):
    content = b"%PDF-test-original"
    monkeypatch.setattr(
        "research_system.market_data.research_archive.PdfReader",
        lambda *args, **kwargs: SimpleNamespace(
            pages=[
                SimpleNamespace(extract_text=lambda: "已披露的公司年度报告正文。" * 20)
            ]
        ),
    )
    candidate = ArchiveCandidate(
        "http://static.cninfo.com.cn/finalpage/2026-08-19/123.PDF",
        "公司年度报告",
        datetime(2026, 8, 19, tzinfo=timezone.utc),
        "123",
        "http://www.cninfo.com.cn/new/hisAnnouncement/query",
        "f" * 64,
        True,
    )
    result = collector(tmp_path, content).archive(
        candidate, dimension="15_events", cutoff_at=CUTOFF
    )
    assert result.metadata["publisher_document_id"] == "123"
    assert Path(result.content_ref).read_bytes() == content
    assert result.grade.value == "B"


def test_search_is_discovery_only_and_one_rejected_document_does_not_stop_other_sources(
    tmp_path,
):
    c = collector(tmp_path, page())
    c.search = lambda **kwargs: {
        "results": [
            {"url": "http://127.0.0.1/secrets", "body": "never accepted"},
            {
                "url": "https://www.pbc.gov.cn/unversioned.html",
                "body": "old search snippet",
            },
        ]
    }
    result = c.collect(
        dimension="3_macro", symbol="600519.SH", subject="经济", cutoff_at=CUTOFF
    )
    assert result["evidence"] == []
    assert result["unrecoverable"] is False
    assert any(
        row.get("reason") == "historical_version_unproven" for row in result["attempts"]
    )


def test_historical_research_preflight_counts_archived_content_not_dated_links():
    snapshot = {
        "query_manifest": {"pit_mode": "historical_reconstruction"},
        "facts": {"rows": [{"url": "https://www.pbc.gov.cn/old", "title": "旧网址"}]},
    }
    assert research_cited_urls(snapshot) == set()
    snapshot["source_refs"] = [
        {
            "url": "https://www.pbc.gov.cn/verified",
            "source_kind": "research_document",
            "content": "已验证正文",
            "content_hash": "f" * 64,
            "content_ref": "/archive/content.html",
            "evidence_mode": "public_archive_reconstruction",
        }
    ]
    assert research_cited_urls(snapshot) == {"https://www.pbc.gov.cn/verified"}


def test_research_archive_creates_new_snapshot_and_keeps_original_payload(tmp_path):
    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.repository import MarketDataRepository
    from research_system.market_data.research_collection import (
        persist_research_documents,
    )
    from research_system.market_data.resolver import SnapshotResolver
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import _input

    evidence = collector(
        tmp_path, page(modified="2026-08-19", document_id="123")
    ).archive(
        ArchiveCandidate("https://www.pbc.gov.cn/release/1.html"),
        dimension="3_macro",
        cutoff_at=CUTOFF,
    )
    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    try:
        with database.session() as session:
            repository = MarketDataRepository(session)
            original = SnapshotIngestionService(repository).ingest(_input("3_macro"))
            original_hash, original_payload = (
                original.content_hash,
                dict(original.payload_json),
            )
            revision = persist_research_documents(
                repository,
                registry=DEFAULT_DIMENSION_REGISTRY,
                snapshot_id=original.id,
                documents=[evidence],
                collection_task_id=None,
                job_id="test-research-job",
                now=LATER,
            )
            assert revision.id != original.id
            assert original.content_hash == original_hash
            assert original.payload_json == revision.payload_json == original_payload
            assert (
                revision.query_manifest_json["pit_mode"] == "historical_reconstruction"
            )
            assert (
                len(repository.list_snapshot_evidence(revision.id))
                == len(repository.list_snapshot_evidence(original.id)) + 1
            )
            coverage = SnapshotResolver(repository).coverage(
                "600519.SH", "CN_EQUITY", date(2026, 8, 20)
            )
            selected = next(
                row for row in coverage.dimensions if row.dimension == "3_macro"
            )
            assert selected.snapshot_id == revision.id
            duplicate = persist_research_documents(
                repository,
                registry=DEFAULT_DIMENSION_REGISTRY,
                snapshot_id=revision.id,
                documents=[evidence],
                collection_task_id=None,
                job_id="repeat",
                now=LATER,
            )
            assert duplicate.id == revision.id
    finally:
        database.close()


def test_policy_historical_cutoff_reaches_cninfo_and_keeps_dated_regulatory_rows(
    monkeypatch,
):
    from research_system.adapters.vendors.akshare_catalyst_adapter import (
        AkShareCatalystAdapter,
    )
    from research_system.adapters.vendors.akshare_policy_adapter import (
        AkSharePolicyAdapter,
    )

    calls = []
    catalyst = AkShareCatalystAdapter()
    monkeypatch.setattr(catalyst, "_disclosure_keywords", lambda theme: ["半导体"])
    monkeypatch.setattr(
        "research_system.adapters.vendors.akshare_catalyst_adapter.ak.stock_zh_a_disclosure_report_cninfo",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(empty=True),
    )
    catalyst.get_regulatory_signals(
        theme="半导体", constituents=[], as_of_date=date(2026, 8, 20)
    )
    assert calls[0]["end_date"] == "20260820"
    adapter = AkSharePolicyAdapter()
    received = []
    adapter.catalyst = SimpleNamespace(
        get_policy_signals=lambda **kwargs: [],
        get_regulatory_signals=lambda **kwargs: (
            received.append(kwargs)
            or [
                {"公告时间": "2026-08-19", "公告标题": "经营事项"},
                {"公告时间": "2026-09-01", "公告标题": "未来事项"},
            ]
        ),
        build_catalyst_digest=lambda **kwargs: {},
    )
    # A current-feed source cannot honor as_of_date and must not be queried.
    adapter.catalyst.get_policy_signals = lambda **kwargs: pytest.fail("historical policy queried today's feed")
    output = adapter.get_policy_snapshot(theme="半导体", as_of_date=date(2026, 8, 20))
    assert received[0]["as_of_date"] == date(2026, 8, 20)
    assert [row["公告标题"] for row in output["regulatory_signals"]] == ["经营事项"]


def test_explicit_complete_collection_runs_scoped_archive_and_records_gaps(tmp_path):
    from research_system.market_data.collection import MarketDataCollectionCoordinator
    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.repository import MarketDataRepository
    from research_system.market_data.research_archive import RESEARCH_SOURCES
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import _input

    database = StrategyDatabase(f"sqlite:///{tmp_path / 'collection.sqlite'}")
    database.create_schema()
    calls = []
    active = 0
    peak = 0
    lock = Lock()

    def collect_archive(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        Event().wait(0.03)
        calls.append(kwargs)
        with lock:
            active -= 1
        return {
            "status": "evidence_gap",
            "evidence": [],
            "attempts": [
                {"reason": "historical_version_unproven", "source": "official_archive"}
            ],
        }

    coordinator = MarketDataCollectionCoordinator(
        database,
        collector=lambda **kwargs: pytest.fail(
            "valid existing snapshots must be reused"
        ),
        archive_collector=SimpleNamespace(collect=collect_archive),
        settings=SimpleNamespace(workspace_root=tmp_path),
    )
    try:
        with database.session() as session:
            ingestion = SnapshotIngestionService(MarketDataRepository(session))
            for contract in DEFAULT_DIMENSION_REGISTRY:
                ingestion.ingest(_input(contract.dimension))
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["600519.SH"],
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            max_concurrency=2,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        assert {row["dimension"] for row in calls} == set(RESEARCH_SOURCES)
        assert peak == 2 and active == 0
        assert result["research_archive"]["status"] == "partial"
        assert result["progress"]["research_archive"]["failed"] == len(RESEARCH_SOURCES)
        assert (
            result["research_archive"]["attempts"][0]["sources"][0]["reason"]
            == "historical_version_unproven"
        )
        calls.clear()
        probe = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["600519.SH"],
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            dimensions=["2_kline", "15_events"],
            submit=False,
        )
        coordinator.run_job_now(probe["id"])
        assert calls == []
        from research_system.market_data.research_collection import (
            recover_research_documents,
        )

        stopped = recover_research_documents(
            database,
            job_id=job["id"],
            settings=SimpleNamespace(workspace_root=tmp_path),
            registry=DEFAULT_DIMENSION_REGISTRY,
            archive_collector=SimpleNamespace(collect=collect_archive),
            max_duration_seconds=0,
        )
        assert stopped["deadline_exceeded"] and calls == []
        assert all(
            row["reason"] == "research_archive_deadline" for row in stopped["attempts"]
        )
    finally:
        coordinator.close()
        database.close()


def test_source_discovery_preserves_publisher_ids_and_queries_target_period(tmp_path):
    calls = []

    def reply(payload):
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: payload, close=lambda: None
        )

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("szse_stock.json"):
            return reply({"stockList": [{"code": "600519", "orgId": "gssh0600519"}]})
        return reply(
            {
                "data": [
                    {
                        "stockCode": "600519",
                        "infoCode": "AP202608190001",
                        "publishDate": "2026-08-19",
                        "title": "行业研究",
                    },
                    {
                        "stockCode": "600519",
                        "infoCode": "AP202609010002",
                        "publishDate": "2026-09-01",
                        "title": "未来研报",
                    },
                    {
                        "stockCode": "000001",
                        "infoCode": "AP202608190003",
                        "publishDate": "2026-08-19",
                        "title": "其他股票",
                    },
                ]
            }
        )

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return reply(
            {
                "announcements": [
                    {
                        "secCode": "600519",
                        "orgId": "gssh0600519",
                        "announcementTime": int(
                            datetime(2026, 8, 19, tzinfo=timezone.utc).timestamp()
                            * 1000
                        ),
                        "announcementId": "123",
                        "adjunctUrl": "finalpage/2026-08-19/123.PDF",
                        "announcementTitle": "半年度报告",
                    }
                ]
            }
        )

    c = ResearchArchiveCollector(
        store=ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=get, post=post),
        clock=lambda: LATER,
    )
    events = c._cninfo_candidates("600519.SH", CUTOFF, dimension="7_industry")
    research = c._research_candidates("600519.SH", CUTOFF)
    assert events[0].publisher_document_id == "123"
    assert calls[1][1]["data"]["stock"] == "600519,gssh0600519"
    assert calls[1][1]["data"]["column"] == "szse"
    assert calls[-1][1]["params"]["endTime"] == "2026-08-20"
    assert calls[-1][1]["params"]["pageSize"] == "50"
    assert [row.publisher_document_id for row in research] == ["AP202608190001"]
    assert research[0].immutable_attachment and research[0].listing_hash


def test_homepages_api_docs_and_unrelated_bodies_are_not_research_evidence(tmp_path):
    c = collector(tmp_path, page(modified="2026-08-19", document_id="release-1"))
    for url in (
        "https://www.pbc.gov.cn/",
        "https://www.pbc.gov.cn/release/index.html",
        "https://www.pbc.gov.cn/#/apiDoc",
    ):
        with pytest.raises(ArchiveEvidenceError) as error:
            c.archive(ArchiveCandidate(url), dimension="3_macro", cutoff_at=CUTOFF)
        assert error.value.code == "archive_listing_not_document"
    with pytest.raises(ArchiveEvidenceError) as error:
        c.archive(
            ArchiveCandidate("https://www.pbc.gov.cn/notice/123.html"),
            dimension="13_policy",
            cutoff_at=CUTOFF,
            subject="半导体",
        )
    assert error.value.code == "archive_subject_unrelated"
    with pytest.raises(ArchiveEvidenceError) as error:
        collector(
            tmp_path,
            page(modified="2026-08-19", document_id="123").replace(
                "原始政策正文，公布经济统计与货币政策措施。".encode(),
                "体育比赛新闻，球队公布球员最新比赛成绩。".encode(),
            ),
        ).archive(
            ArchiveCandidate("https://www.pbc.gov.cn/notice/123.html"),
            dimension="3_macro",
            cutoff_at=CUTOFF,
        )
    assert error.value.code == "archive_dimension_unrelated"


def test_controlled_browser_only_discovers_links_and_failure_detail_is_safe(tmp_path):
    import requests

    def fail(*args, **kwargs):
        raise requests.HTTPError(
            "403 forbidden token=secret", response=SimpleNamespace(status_code=403)
        )

    c = ResearchArchiveCollector(
        store=ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=fail),
        search=lambda **kwargs: {"results": []},
        browser_adapter=SimpleNamespace(
            collect_many=lambda **kwargs: {
                "evidence": {
                    "pbc": {
                        "text_excerpt": "not an archived body",
                        "links": [
                            {"url": "https://www.pbc.gov.cn/", "text": "首页"},
                            {
                                "url": "https://www.pbc.gov.cn/release/123.html",
                                "text": "经济数据",
                            },
                        ],
                    }
                },
                "source_trace": [{"source_id": "pbc", "status": "available"}],
            }
        ),
    )
    result = c.collect(
        dimension="3_macro", symbol="600519.SH", subject="白酒", cutoff_at=CUTOFF
    )
    assert result["evidence"] == []
    failed = next(row for row in result["attempts"] if row.get("http_status") == 403)
    assert "secret" not in failed["message"] and "[redacted]" in failed["message"]
    assert any(row["stage"] == "browser_discovery" for row in result["attempts"])


def test_http_documents_are_verified_before_optional_browser_fallback(tmp_path):
    order = []
    content = page(modified="2026-08-19", document_id="123")

    def get(url, **kwargs):
        order.append(url)
        return Response(content)

    def browser(**kwargs):
        order.append("browser")
        return {
            "evidence": {
                "pbc": {
                    "links": [{"url": "https://www.pbc.gov.cn/release/second.html"}]
                }
            },
            "source_trace": [],
        }

    c = ResearchArchiveCollector(
        store=ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=get),
        search=lambda **kwargs: {
            "results": [
                {"url": "https://www.pbc.gov.cn/release/first.html"},
                {"url": "https://www.pbc.gov.cn/release/second.html"},
            ]
        },
        browser_adapter=SimpleNamespace(collect_many=browser),
        clock=lambda: LATER,
    )
    result = c.collect(
        dimension="3_macro", symbol="600519.SH", subject="白酒", cutoff_at=CUTOFF
    )
    assert result["status"] == "ready" and "browser" not in order
    order.clear()
    c.search = lambda **kwargs: {
        "results": [{"url": "https://www.pbc.gov.cn/release/first.html"}]
    }
    result = c.collect(
        dimension="3_macro", symbol="600519.SH", subject="白酒", cutoff_at=CUTOFF
    )
    assert result["status"] == "ready"
    assert order == ["https://www.pbc.gov.cn/release/first.html"]
    assert result["research_completion"] == "not_assessed"
    # The fallback remains necessary when the HTTP original is unusable, not
    # merely because there is only one different URL in the inventory.
    order.clear()
    c.http = SimpleNamespace(get=lambda url, **kwargs: order.append(url) or Response(page() if url.endswith("first.html") else content))
    result = c.collect(
        dimension="3_macro", symbol="600519.SH", subject="白酒", cutoff_at=CUTOFF
    )
    assert result["status"] == "ready"
    assert order == [
        "https://www.pbc.gov.cn/release/first.html",
        "browser",
        "https://www.pbc.gov.cn/release/second.html",
    ]
