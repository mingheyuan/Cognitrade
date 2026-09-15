"""Offline archive/version boundary regressions; no source or model requests."""

from datetime import date
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_system.market_data.contracts import canonical_hash
from research_system.market_data.research_archive import ArchiveCandidate, ArchiveEvidenceError
from tests.market_data.test_research_archive import CUTOFF, LATER, collector


def _archive(tmp_path, monkeypatch, *, pages=166, body_chars=1000):
    bodies = [f"600519 年度报告 营业成本 原材料采购 第{i + 1}页 " + "正文" * body_chars for i in range(pages)]
    bodies[-1] += " FINAL_PAGE_HEDGING_NOTE"
    monkeypatch.setattr(
        "research_system.market_data.research_archive.PdfReader",
        lambda *a, **kw: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda b=b: b) for b in bodies]),
    )
    source = b"%PDF-offline-166-page-original"
    evidence = collector(tmp_path, source, observed=CUTOFF).archive(
        ArchiveCandidate("https://static.cninfo.com.cn/finalpage/2026-08-19/123.PDF", "600519年度报告"),
        dimension="8_materials", symbol="600519.SH", cutoff_at=CUTOFF,
    )
    return evidence, "\n".join(bodies), source


def _legacy_key(evidence):
    return canonical_hash({
        "provider": evidence.provider, "source_url": evidence.source_url,
        "content_hash": evidence.resolved_content_hash(), "published_at": evidence.published_at,
        "grade": evidence.grade, "immutable_proof": evidence.immutable_proof, "is_manual": evidence.is_manual,
    })


def test_archive_reads_all_pages_and_preserves_full_text_outside_inline_budget(tmp_path, monkeypatch):
    evidence, full, source = _archive(tmp_path, monkeypatch)
    binding = evidence.metadata["text_extraction"]
    assert binding["page_count"] == binding["pages_processed"] == 166
    assert binding["complete"] is True
    assert binding["full_document_reading_claimed"] is False
    assert binding["inline_truncated"] is True
    assert binding["source_content_hash"] == hashlib.sha256(source).hexdigest()
    assert binding["text_content_hash"] == hashlib.sha256(full.encode()).hexdigest()
    assert binding["text_characters"] == len(full)
    assert binding["text_utf8_bytes"] == len(full.encode())
    assert Path(binding["text_content_ref"]).read_text() == full
    assert "FINAL_PAGE_HEDGING_NOTE" in full and "FINAL_PAGE_HEDGING_NOTE" not in evidence.inline_content
    assert len(evidence.inline_content.encode()) <= 240_000
    assert Path(evidence.content_ref).read_bytes() == source


def test_new_text_identity_changes_evidence_key_but_old_key_and_path_portability_do_not(tmp_path, monkeypatch):
    new, _, _ = _archive(tmp_path, monkeypatch)
    old = new.model_copy(deep=True)
    old.metadata.pop("text_extraction", None)
    old.metadata["archive_version"] = "research-archive-v2"
    assert old.evidence_key() == _legacy_key(old)
    assert new.evidence_key() != old.evidence_key()
    moved = new.model_copy(deep=True)
    moved.metadata["text_extraction"]["text_content_ref"] = "/another-host/archive/text.txt"
    assert moved.evidence_key() == new.evidence_key()
    changed = new.model_copy(deep=True)
    changed.metadata["text_extraction"]["text_content_hash"] = "f" * 64
    assert changed.evidence_key() != new.evidence_key()


def _publish(tmp_path, monkeypatch, *, damage=None):
    from research_system.market_data.bundles import BundlePublisher
    from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.repository import MarketDataRepository
    from research_system.market_data.research_collection import persist_research_documents
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import _input

    new, full, _ = _archive(tmp_path, monkeypatch)
    old = new.model_copy(deep=True)
    old.metadata.pop("text_extraction", None)
    old.metadata["archive_version"] = "research-archive-v2"
    if damage:
        binding = new.metadata["text_extraction"]
        if damage == "hash":
            body = full.encode()
            Path(binding["text_content_ref"]).write_bytes(body[:-1] + b"X")
        elif damage == "missing":
            Path(binding["text_content_ref"]).unlink()
        elif damage == "version":
            binding["version"] = "research-text-v999"
        elif damage == "source":
            binding["source_content_hash"] = "f" * 64
        elif damage == "length":
            binding["text_utf8_bytes"] += 1
        else:
            binding["text_content_ref"] = str(tmp_path / "not-content-addressed.txt")
            Path(binding["text_content_ref"]).write_text(full)
    db = StrategyDatabase("sqlite:///:memory:")
    db.create_schema()
    session = db.session()
    # The context manager must remain alive while returned ORM objects are used.
    live = session.__enter__()
    repo = MarketDataRepository(live)
    ingestion = SnapshotIngestionService(repo)
    for contract in DEFAULT_DIMENSION_REGISTRY:
        options = {"evidence": [old], "observed_at": CUTOFF, "ingested_at": CUTOFF} if contract.dimension == "8_materials" else {}
        item = ingestion.ingest(_input(contract.dimension, **options))
        if contract.dimension == "8_materials":
            original = item
    before = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY", as_of_date=date(2026, 8, 20), now=LATER)
    revised = persist_research_documents(
        repo, registry=DEFAULT_DIMENSION_REGISTRY, snapshot_id=original.id, documents=[new],
        collection_task_id=None, job_id="explicit-new-text-version", now=CUTOFF,
    )
    after = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY", as_of_date=date(2026, 8, 20), now=LATER)
    return db, session, repo, old, new, full, original, revised, before, after


def test_new_revision_materializes_verified_full_text_old_bundle_stays_inline_without_network(tmp_path, monkeypatch):
    from research_system.market_data.bundles import BundleMaterializer
    import requests

    values = _publish(tmp_path, monkeypatch)
    db, session, repo, old, new, full, original, revised, before, after = values
    try:
        assert revised.id != original.id
        assert revised.payload_json == original.payload_json
        assert before.id != after.id
        monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("report must not use network"))
        old_result = BundleMaterializer(repo).materialize(before.id)
        new_result = BundleMaterializer(repo).materialize(after.id)
        old_ref = next(r for r in old_result.source_refs if r.get("source_kind") == "research_document")
        new_refs = [r for r in new_result.source_refs if r.get("source_kind") == "research_document"]
        assert old_ref["content"] == old.inline_content
        assert any(r["content"] == full for r in new_refs)
        assert "text_extraction" not in old_ref
        assert any(r.get("text_extraction", {}).get("text_content_hash") == new.metadata["text_extraction"]["text_content_hash"] for r in new_refs)
        assert "text_extractions" in revised.query_manifest_json["research_archive"]
        assert BundleMaterializer(repo).materialize(before.id).source_refs == old_result.source_refs
        from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import _fixed_report_inputs
        _, _, docs = _fixed_report_inputs(new_result.snapshots, {"symbol": "600519.SH"})
        assert any(row["finding"] == full for row in docs)
        from research_system.market_data.research_collection import persist_research_documents
        from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
        repeated = persist_research_documents(
            repo, registry=DEFAULT_DIMENSION_REGISTRY, snapshot_id=revised.id, documents=[new],
            collection_task_id=None, job_id="repeat-same-extraction", now=CUTOFF,
        )
        assert repeated.id == revised.id
    finally:
        session.__exit__(None, None, None)
        db.close()


@pytest.mark.parametrize("damage", ["hash", "missing", "version", "source", "length", "filename"])
def test_new_binding_fails_closed_on_corrupt_missing_or_unknown_version(tmp_path, monkeypatch, damage):
    from research_system.market_data.bundles import BundleIntegrityError, BundleMaterializer

    values = _publish(tmp_path, monkeypatch, damage=damage)
    db, session, repo, old, new, full, original, revised, before, after = values
    try:
        assert revised.id != original.id
        with pytest.raises(BundleIntegrityError):
            BundleMaterializer(repo).materialize(after.id)
        # New-version corruption cannot cause implicit upgrade or failure of the old input.
        assert any(r.get("content") == old.inline_content for r in BundleMaterializer(repo).materialize(before.id).source_refs)
    finally:
        session.__exit__(None, None, None)
        db.close()


def test_pdf_extraction_failure_after_page_100_never_returns_a_successful_partial_archive(tmp_path, monkeypatch):
    pages = [SimpleNamespace(extract_text=lambda: "600519 年度报告 原材料采购。" * 30) for _ in range(100)]
    def broken():
        raise RuntimeError("unsafe source text must not leak")
    pages.append(SimpleNamespace(extract_text=broken))
    monkeypatch.setattr("research_system.market_data.research_archive.PdfReader", lambda *a, **k: SimpleNamespace(pages=pages))
    with pytest.raises(ArchiveEvidenceError) as error:
        collector(tmp_path, b"%PDF-offline", observed=CUTOFF).archive(
            ArchiveCandidate("https://static.cninfo.com.cn/finalpage/2026-08-19/123.PDF"),
            dimension="8_materials", symbol="600519.SH", cutoff_at=CUTOFF,
        )
    assert error.value.code == "archive_text_extraction_failed"
    assert "unsafe source text" not in str(error.value)
    assert not list(tmp_path.rglob("*.txt"))


def test_blank_pdf_pages_are_disclosed_not_claimed_as_ocr_complete():
    from research_system.market_data.research_archive_text import extract_pdf_text

    reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "page one"), SimpleNamespace(extract_text=lambda: "")])
    text, details = extract_pdf_text(reader)
    assert text == "page one\n"
    assert details["page_count"] == details["pages_processed"] == 2
    assert details["pages_without_text"] == [2]


def test_extracted_text_budget_rejects_instead_of_silently_truncating(monkeypatch):
    from research_system.market_data import research_archive_text as text_module

    monkeypatch.setattr(text_module, "MAX_EXTRACTED_TEXT_BYTES", 100)
    reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "中" * 34)])
    with pytest.raises(text_module.ResearchTextError, match="archive_text_too_large"):
        text_module.extract_pdf_text(reader)


@pytest.mark.parametrize("boundary", ["cancelled", "deadline"])
def test_extraction_checks_cancellation_and_deadline_after_page(boundary):
    from threading import Event
    from research_system.market_data import research_archive_text as text_module

    stop = Event()
    def page_text():
        stop.set()
        return "not a complete extraction"
    reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=page_text)])
    with pytest.raises(text_module.ResearchTextError, match="archive_cancelled_or_deadline"):
        text_module.extract_pdf_text(reader, cancellation=stop, deadline=0 if boundary == "deadline" else float("inf"))


def test_full_text_storage_does_not_raise_existing_model_prompt_budget():
    from domain_packs.finance_ashare.subagents.uzi_input_views import DEFAULT_PROMPT_VIEW_MAX_CHARACTERS, UziInputBudgetExceeded
    from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import _check_qualitative_prompt_budget

    assert DEFAULT_PROMPT_VIEW_MAX_CHARACTERS == 500_000
    with pytest.raises(UziInputBudgetExceeded):
        _check_qualitative_prompt_budget("正文" * 250_000, "x")
