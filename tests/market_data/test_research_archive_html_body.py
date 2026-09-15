"""Publisher chrome cannot supply the document's subject, facts or attachments."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_system.market_data.research_archive import (
    ArchiveCandidate,
    ArchiveEvidenceError,
    ResearchArchiveCollector,
    ResearchDocumentStore,
)
from tests.market_data.test_research_archive import CUTOFF, LATER, Response

URL = "https://paper.cnstock.com/html/2026-08/19/content_123456.htm"


def page(text, navigation="", *, versioned=False, main_class="article-content"):
    metadata = ('<meta name="pubdate" content="2026-08-19T08:00:00+08:00">'
                '<meta name="articleid" content="123456">'
                '<meta name="article:modified_time" content="2026-08-19T08:00:00+08:00">') if versioned else ""
    return (f'<html><head><title>原文测试</title>{metadata}</head><body><div class="newnav2023">'
            + navigation + f'</div><div class="{main_class}"><h1>公告正文</h1><div class="content_article">'
            + text + '</div></div><div class="footer">页脚免责声明，不是正文事实</div></body></html>').encode()


def collector(tmp_path, content, observed=CUTOFF):
    return ResearchArchiveCollector(store=ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=lambda *_args, **_kwargs: Response(content)), clock=lambda: observed)


def test_cnstock_article_preserves_full_body_but_not_other_company_navigation(tmp_path):
    text = "688041 海光信息公告披露交易进展，原始事实应完整保留。" * 20
    content = page(text, "导航公司600000披露业绩，版面导航并非本文。")
    evidence = collector(tmp_path, content).archive(ArchiveCandidate(URL),
        dimension="15_events", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert text in evidence.inline_content
    assert "600000" not in evidence.inline_content and "版面导航" not in evidence.inline_content
    assert "页脚免责声明" not in evidence.inline_content
    assert Path(evidence.content_ref).read_bytes() == content
    assert evidence.content_hash == hashlib.sha256(content).hexdigest()
    assert evidence.metadata["text_extraction"]["html_body_selector"] == "paper.cnstock.com:.article-content"


def test_target_mentioned_only_in_navigation_does_not_pass_relevance(tmp_path):
    content = page("600000 其他公司公告正文记录其独立经营情况。" * 20, "688041 海光信息公告")
    with pytest.raises(ArchiveEvidenceError) as error:
        collector(tmp_path, content).archive(ArchiveCandidate(URL), dimension="15_events",
            symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert error.value.code == "archive_subject_unrelated"
    assert not list(tmp_path.rglob("*.html"))


def test_long_page_navigation_does_not_reject_real_publisher_document(tmp_path):
    links = "".join(f'<a href="node_{i}.htm">第{i}版导航及其他公司的披露信息</a>' for i in range(200))
    content = page("688041 海光信息公告原文。" * 20, links)
    evidence = collector(tmp_path, content).archive(ArchiveCandidate(URL), dimension="15_events",
        symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert "第199版" not in evidence.inline_content


def test_versioned_page_does_not_promote_sidebar_pdf_to_article_attachment(tmp_path):
    content = page("688041 海光信息公告正文。" * 20 + '<a href="20260819123456.pdf">本公告附件</a>',
                   '<a href="20260819000000.pdf">其他文章附件</a>', versioned=True)
    acquire = collector(tmp_path, content, LATER)
    evidence = acquire.archive(ArchiveCandidate(URL), dimension="15_events",
        symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    attachments = acquire._article_attachments(evidence, "15_events")
    assert len(attachments) == 1 and attachments[0].url.endswith("20260819123456.pdf")
    assert attachments[0].listing_hash == evidence.content_hash


def test_body_selection_does_not_invent_historical_publication_or_version(tmp_path):
    content = page("688041 海光信息公告正文，2026-08-19。" * 20)
    with pytest.raises(ArchiveEvidenceError) as error:
        collector(tmp_path, content, LATER).archive(ArchiveCandidate(URL), dimension="15_events",
            symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert error.value.code == "archive_publication_unknown"


def test_schema_articlebody_and_main_are_document_scoped(tmp_path):
    for wrapper in ('<div itemprop="articleBody">', '<main>'):
        content = ('<body><div>其他公司导航 600000</div>' + wrapper
                   + '688041 海光信息公告原文。' * 20 + ('</main>' if wrapper == '<main>' else '</div>') + '</body>').encode()
        evidence = collector(tmp_path, content).archive(ArchiveCandidate("https://www.stcn.com/article/detail/123456.html"),
            dimension="15_events", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
        assert "600000" not in evidence.inline_content


def test_actual_listing_without_document_container_is_still_rejected(tmp_path):
    content = ('<body>' + ''.join(f'<a href="content_{i}.htm">688041 海光信息公告标题</a>' for i in range(20)) + '</body>').encode()
    with pytest.raises(ArchiveEvidenceError) as error:
        collector(tmp_path, content).archive(ArchiveCandidate(URL), dimension="15_events",
            symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert error.value.code == "archive_listing_not_document"
