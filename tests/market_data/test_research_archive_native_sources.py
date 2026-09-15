"""Native-supported public originals must survive the archive boundary."""

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_system.market_data.research_archive import (
    ArchiveCandidate,
    ArchiveEvidenceError,
    ResearchArchiveCollector,
    ResearchDocumentStore,
    _check_url,
)
from tests.market_data.test_research_archive import Response

CUTOFF = datetime(2026, 9, 12, 15, 59, 59, tzinfo=timezone.utc)
URL = "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/20260912/123456.shtml"


def native_domains():
    path = Path(__file__).resolve().parents[2] / "domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts/lib/web_search.py"
    table = next(node for node in ast.parse(path.read_text()).body
                 if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                 and node.target.id == "TRUSTED_DOMAINS_BY_DIM")
    return ast.literal_eval(table.value)


@pytest.mark.parametrize("dimension", ["3_macro", "13_policy", "7_industry", "15_events", "8_materials", "9_futures"])
def test_archive_accepts_pinned_native_research_source_domains(dimension):
    for domain in native_domains()[dimension]:
        _check_url(f"https://{domain}/research/123456.html", dimension)


@pytest.mark.parametrize("unusable_cninfo", [0, 12])
def test_event_discovery_archives_exchange_original_when_cninfo_has_no_document(tmp_path, monkeypatch, unusable_cninfo):
    body = ('<html><head><meta name="pubdate" content="2026-09-12T08:00:00+08:00">'
            '<meta name="articleid" content="123456"></head><body><article>'
            + "688041 海光信息公告披露研发合同进展，原始公告全文。" * 20 + "</article></body></html>").encode()
    queries, downloads = [], []

    def search(**kwargs):
        queries.append(kwargs)
        return {"results": [{"url": URL, "title": "海光信息公告"}]}

    def get(url, **kwargs):
        downloads.append(url)
        assert kwargs["allow_redirects"] is False
        return Response(body if url == URL else b"No document")

    collector = ResearchArchiveCollector(store=ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=get), search=search, clock=lambda: CUTOFF)
    monkeypatch.setattr(collector, "_cninfo_candidates", lambda *_args, **_kwargs: [
        ArchiveCandidate(f"https://www.cninfo.com.cn/archive/{i}.html") for i in range(unusable_cninfo)])
    result = collector.collect(dimension="15_events", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert len(queries) == 1 and "site:sse.com.cn" in queries[0]["query"]
    assert "before:2026-09-13" in queries[0]["query"]
    assert URL in downloads and downloads.index(URL) <= 1
    assert len(result["evidence"]) == 1
    assert result["evidence"][0].source_url == URL
    assert Path(result["evidence"][0].content_ref).read_bytes() == body
    assert result["research_completion"] == "not_assessed"


@pytest.mark.parametrize("url", ["https://sse.com.cn.attacker.invalid/doc.html", "http://127.0.0.1/doc.html",
                                "https://user:password@sse.com.cn/doc.html", "https://sse.com.cn:8443/doc.html"])
def test_native_source_support_preserves_url_safety(url):
    with pytest.raises(ArchiveEvidenceError):
        _check_url(url, "15_events")


def test_exchange_date_url_does_not_prove_historical_visibility(tmp_path):
    body = ('<article>' + "688041 海光信息公告披露研发合同进展。" * 20 + '</article>').encode()
    collector = ResearchArchiveCollector(store=ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=lambda *_args, **_kwargs: Response(body)),
        clock=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc))
    with pytest.raises(ArchiveEvidenceError) as error:
        collector.archive(ArchiveCandidate(URL), dimension="15_events", symbol="688041.SH", cutoff_at=CUTOFF)
    assert error.value.code == "archive_publication_unknown"
