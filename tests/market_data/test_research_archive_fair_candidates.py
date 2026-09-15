"""Offline contract for bounded cross-source fallback; all responses are fake."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from research_system.market_data.research_archive import (
    ArchiveCandidate,
    ResearchArchiveCollector,
    ResearchDocumentStore,
    _fair_research_candidates,
)
from tests.market_data.test_research_archive import CUTOFF, Response


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("fair-candidate tests must remain offline")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def setup_collector(tmp_path, monkeypatch, *, primary_count=25, official_count=8,
                    official_body=None, published=CUTOFF, observed=CUTOFF,
                    official_url=None):
    primary = [ArchiveCandidate(f"https://pdf.dfcfw.com/pdf/primary-{i}.pdf", published_at=CUTOFF)
               for i in range(primary_count)]
    secondary = [ArchiveCandidate(official_url or f"https://data.eastmoney.com/report/original-{i}.html",
                                 published_at=published)
                 for i in range(official_count)]
    called = []

    def get(url, **kwargs):
        called.append(url)
        body = official_body if "data.eastmoney.com" in url else None
        return Response(body or b"<body>short</body>")

    collector = ResearchArchiveCollector(store=ResearchDocumentStore(tmp_path),
                                         http=SimpleNamespace(get=get), clock=lambda: observed)
    monkeypatch.setattr(collector, "_research_candidates", lambda *args: primary)
    monkeypatch.setattr(collector, "_official_candidates", lambda *args, **kwargs: secondary)
    return collector, called


def run(collector, *, limit=2):
    return collector.collect(dimension="6_research", symbol="688041.SH", subject="海光信息",
                             cutoff_at=CUTOFF, limit=limit)


def research_body():
    return ("<html><body>海光信息688041研究报告与盈利预测。" * 20 + "</body></html>").encode()


def test_failed_large_primary_pool_does_not_starve_valid_secondary(tmp_path, monkeypatch):
    collector, calls = setup_collector(tmp_path, monkeypatch, official_body=research_body())
    result = run(collector, limit=1)
    assert result["document_inventory"]["document_count"] == 1
    assert len(calls) <= 12
    assert calls[:2] == ["https://pdf.dfcfw.com/pdf/primary-0.pdf",
                         "https://data.eastmoney.com/report/original-0.html"]


def test_all_failed_sources_share_the_same_twelve_candidate_budget(tmp_path, monkeypatch):
    collector, calls = setup_collector(tmp_path, monkeypatch)
    result = run(collector)
    assert result["evidence"] == [] and len(calls) == 12
    assert sum("pdf.dfcfw.com" in url for url in calls) == 6
    assert sum("data.eastmoney.com" in url for url in calls) == 6


def test_secondary_listing_still_reaches_existing_refusal_without_http(tmp_path, monkeypatch):
    collector, calls = setup_collector(tmp_path, monkeypatch, official_count=1,
                                       official_url="https://data.eastmoney.com/report/index.html")
    result = run(collector)
    assert result["evidence"] == []
    assert any(row.get("reason") == "archive_listing_not_document" for row in result["attempts"])
    assert all("index.html" not in url for url in calls) and len(calls) <= 12


def test_single_source_preserves_order_and_existing_budget(tmp_path, monkeypatch):
    collector, calls = setup_collector(tmp_path, monkeypatch, official_count=0)
    assert run(collector)["evidence"] == []
    assert calls == [f"https://pdf.dfcfw.com/pdf/primary-{i}.pdf" for i in range(12)]


def test_fair_candidate_selection_deduplicates_without_increasing_budget():
    primary = [ArchiveCandidate(f"https://pdf.dfcfw.com/{i}.pdf") for i in range(25)]
    fallback = [primary[0], ArchiveCandidate("https://data.eastmoney.com/research.html")]
    selected = _fair_research_candidates([primary, fallback])
    assert len(selected) == len({row.url for row in selected}) == 12
    assert fallback[1] in selected
    assert _fair_research_candidates([]) == []


def test_attachment_candidates_share_the_twelve_attempt_cap(tmp_path, monkeypatch):
    collector, _ = setup_collector(tmp_path, monkeypatch, primary_count=25, official_count=0)
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Response(research_body() + url.encode())

    collector.http = SimpleNamespace(get=get)
    # Simulate an existing validated publisher-to-attachment discovery hook;
    # every emitted candidate still passes the actual archive body/PIT checks.
    monkeypatch.setattr(collector, "_article_attachments", lambda item, dimension: [
        ArchiveCandidate(item.source_url + f"-attachment-{i}.pdf", published_at=CUTOFF)
        for i in range(2)
    ])
    result = run(collector, limit=100)
    documents = [row for row in result["attempts"] if row["stage"] == "document"]
    assert len(calls) == len(documents) == 12
    assert len(result["evidence"]) == 12


def test_other_dimensions_keep_first_source_priority_without_starving_fallback(tmp_path, monkeypatch):
    collector, calls = setup_collector(tmp_path, monkeypatch)
    cninfo = [ArchiveCandidate(f"https://static.cninfo.com.cn/report-{i}.pdf", published_at=CUTOFF)
              for i in range(25)]
    monkeypatch.setattr(collector, "_cninfo_candidates", lambda *args, **kwargs: cninfo)
    result = collector.collect(dimension="7_industry", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    attempts = [row for row in result["attempts"] if row["stage"] == "document"]
    assert attempts[0]["url"] == cninfo[0].url
    assert attempts[1]["url"] not in {row.url for row in cninfo}
    assert len(attempts) <= 12
    assert len(calls) < 12  # Irrelevant fallback documents still fail existing URL/topic gates.


@pytest.mark.parametrize("case,reason", [
    ("future", "archive_after_cutoff"),
    ("unproven_history", "historical_version_unproven"),
    ("unrelated", "archive_dimension_unrelated"),
])
def test_fallback_does_not_relax_actual_body_relevance_or_pit(tmp_path, monkeypatch, case, reason):
    body = research_body() if case != "unrelated" else ("<body>无关的其他内容。" * 20 + "</body>").encode()
    collector, calls = setup_collector(
        tmp_path, monkeypatch, primary_count=0, official_count=1, official_body=body,
        published=CUTOFF + timedelta(days=1) if case == "future" else CUTOFF,
        observed=CUTOFF + timedelta(days=2) if case == "unproven_history" else CUTOFF,
    )
    result = run(collector)
    assert result["evidence"] == [] and len(calls) == 1
    assert any(row.get("reason") == reason for row in result["attempts"])
    assert not list(tmp_path.rglob("*.html"))
