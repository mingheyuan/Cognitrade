"""Offline failure diagnostics: actual collector path, no sources or models."""

from types import SimpleNamespace

import pytest

from research_system.market_data import research_archive as archive
from tests.market_data.test_research_archive import CUTOFF

SECRET = "sensitive-sentinel"
URL = f"https://pdf.dfcfw.com/pdf/H3_AP202608171828048524_1.pdf?token={SECRET}#fragment"


class Response:
    status_code = 200

    def __init__(self, body, content_type):
        self.body = body
        self.headers = {"Content-Type": content_type, "Authorization": SECRET, "Set-Cookie": SECRET}
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("this regression must not call any source")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def collect(tmp_path, monkeypatch, body, content_type="text/html", pages=None):
    response = Response(body, content_type)
    candidate = archive.ArchiveCandidate(
        URL, "海光信息研究报告", CUTOFF, "AP202608171828048524",
        "https://reportapi.eastmoney.com/report/list", "f" * 64, True, "688041",
    )
    collector = archive.ResearchArchiveCollector(
        store=archive.ResearchDocumentStore(tmp_path),
        http=SimpleNamespace(get=lambda *args, **kwargs: response),
        clock=lambda: CUTOFF,
    )
    monkeypatch.setattr(collector, "_research_candidates", lambda *args: [candidate])
    monkeypatch.setattr(collector, "_official_candidates", lambda *args, **kwargs: [])
    if pages is not None:
        monkeypatch.setattr(archive, "PdfReader", lambda *args, **kwargs: SimpleNamespace(
            pages=[SimpleNamespace(extract_text=lambda value=value: value) for value in pages],
        ))
    result = collector.collect(dimension="6_research", symbol="688041.SH", subject="海光信息", cutoff_at=CUTOFF)
    assert response.closed
    return result


def failure(result):
    row = next(row for row in result["attempts"] if row["stage"] == "document")
    assert row["reason"] == "archive_body_unusable"
    assert row["retryable"] is False
    assert row["message"] == "未取得可引用原文正文"
    assert result["evidence"] == [] and result["unrecoverable"] is False
    return row


def test_short_html_records_safe_statistics_without_headers_url_query_or_body(tmp_path, monkeypatch):
    body = f"<html><body>{SECRET}</body></html>".encode()
    result = collect(tmp_path, monkeypatch, body, f"text/html; private={SECRET}")
    row = failure(result)
    assert row["url"] == URL.split("?")[0]
    assert row["http_status"] == 200
    assert row["body_diagnostics"] == {
        "version": "archive-body-diagnostics-v1", "http_status": 200,
        "content_type_class": "html", "response_bytes": len(body),
        "text_characters": len(SECRET), "stripped_text_characters": len(SECRET),
        "is_pdf": False, "extraction_branch": "html", "page_count": None,
        "pages_processed": None, "pages_without_text_count": None,
        "rejection_reasons": ["text_too_short"],
    }
    assert SECRET not in str(result)
    assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("marker,reason", [
    ("ACCESS DENIED", "access_denied"), ("captcha", "captcha"), ("Just a moment", "challenge"),
])
def test_static_challenge_reasons_remain_separate_from_short_text(tmp_path, monkeypatch, marker, reason):
    text = marker + " 研究报告" * 30
    row = failure(collect(tmp_path, monkeypatch, f"<body>{text}</body>".encode()))
    assert row["body_diagnostics"]["rejection_reasons"] == [reason]
    assert row["body_diagnostics"]["text_characters"] >= 80


def test_multiple_causes_are_recorded_without_changing_old_error(tmp_path, monkeypatch):
    row = failure(collect(tmp_path, monkeypatch, b"<body>access denied captcha just a moment</body>"))
    assert row["body_diagnostics"]["rejection_reasons"] == ["text_too_short", "access_denied", "captcha", "challenge"]


def test_empty_pdf_uses_actual_extracted_page_statistics(tmp_path, monkeypatch):
    body = b"%PDF-offline"
    row = failure(collect(tmp_path, monkeypatch, body, "application/pdf", pages=["", " ", "short", "", ""]))
    assert row["body_diagnostics"] == {
        "version": "archive-body-diagnostics-v1", "http_status": 200,
        "content_type_class": "pdf", "response_bytes": len(body),
        "text_characters": 10, "stripped_text_characters": 5,
        "is_pdf": True, "extraction_branch": "pdf", "page_count": 5,
        "pages_processed": 5, "pages_without_text_count": 4,
        "rejection_reasons": ["text_too_short"],
    }
    assert not list(tmp_path.rglob("*"))


def test_declared_pdf_but_html_body_does_not_claim_pdf_extraction(tmp_path, monkeypatch):
    row = failure(collect(tmp_path, monkeypatch, b"<body>captcha</body>", "application/pdf"))
    assert row["body_diagnostics"]["content_type_class"] == "pdf"
    assert row["body_diagnostics"]["is_pdf"] is False
    assert row["body_diagnostics"]["page_count"] is None


@pytest.mark.parametrize("declared,expected", [
    (None, "missing"), ("", "missing"), ("text/plain", "plain_text"),
    ("APPLICATION/JSON;charset=utf-8", "json"), ("application/octet-stream", "binary"),
    ("application/xhtml+xml", "html"), (f"unknown/{SECRET}", "other"),
])
def test_content_type_is_classified_not_copied(tmp_path, monkeypatch, declared, expected):
    row = failure(collect(tmp_path, monkeypatch, b"<body>x</body>", declared))
    assert row["body_diagnostics"]["content_type_class"] == expected
    assert SECRET not in str(row)


def test_pdf_parser_exception_keeps_its_existing_code_not_body_failure(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError(SECRET)

    monkeypatch.setattr(archive, "PdfReader", broken)
    result = collect(tmp_path, monkeypatch, b"%PDF-broken", "application/pdf")
    row = next(row for row in result["attempts"] if row["stage"] == "document")
    assert row["reason"] == "archive_text_extraction_failed"
    assert row["retryable"] is False and "body_diagnostics" not in row
    assert SECRET not in row["message"]


@pytest.mark.parametrize("length,accepted", [(79, False), (80, True)])
def test_existing_character_boundary_is_unchanged(tmp_path, monkeypatch, length, accepted):
    text = "研究报告" + "字" * (length - 4)
    result = collect(tmp_path, monkeypatch, f"<body>{text}</body>".encode())
    assert bool(result["evidence"]) is accepted
    if not accepted:
        assert failure(result)["body_diagnostics"]["rejection_reasons"] == ["text_too_short"]


def test_marker_after_existing_500_character_window_does_not_gain_new_gate(tmp_path, monkeypatch):
    text = "研究报告" + "字" * 500 + "captcha"
    result = collect(tmp_path, monkeypatch, f"<body>{text}</body>".encode())
    assert len(result["evidence"]) == 1
    assert "body_diagnostics" not in result["evidence"][0].metadata
