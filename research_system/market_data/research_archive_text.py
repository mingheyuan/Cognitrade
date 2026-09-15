"""Versioned, content-addressed research text; no transport or report extraction."""

from __future__ import annotations

import hashlib
import re
from importlib.metadata import version
from pathlib import Path
from time import monotonic

TEXT_EXTRACTION_VERSION = "research-text-v1"
MAX_EXTRACTED_TEXT_BYTES = 12 * 1024 * 1024
INLINE_TEXT_BYTES = 240_000


class ResearchTextError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def extract_pdf_text(reader, *, cancellation=None, deadline=float("inf")):
    """Extract every page or fail explicitly, never return a successful prefix.

    A page's library call is synchronous: cancellation/deadline is checked on
    both sides, not a hard interruption of an individual pypdf operation.
    """
    texts, empty_pages, size = [], [], 0
    try:
        total = len(reader.pages)
        for index, page in enumerate(reader.pages):
            if (cancellation is not None and cancellation.is_set()) or monotonic() >= deadline:
                raise ResearchTextError("archive_cancelled_or_deadline")
            text = page.extract_text() or ""
            if (cancellation is not None and cancellation.is_set()) or monotonic() >= deadline:
                raise ResearchTextError("archive_cancelled_or_deadline")
            size += len(text.encode("utf-8")) + (1 if index else 0)
            if size > MAX_EXTRACTED_TEXT_BYTES:
                raise ResearchTextError("archive_text_too_large")
            texts.append(text)
            if not text.strip():
                empty_pages.append(index + 1)
        if len(texts) != total:
            raise ResearchTextError("archive_text_extraction_failed")
    except ResearchTextError:
        raise
    except Exception:  # noqa: BLE001 - normalize extractor errors without retaining document content
        raise ResearchTextError("archive_text_extraction_failed") from None
    return "\n".join(texts), {
        "extractor": "pypdf", "extractor_version": version("pypdf"),
        "page_count": total, "pages_processed": len(texts), "pages_without_text": empty_pages,
    }


def archive_text(store, text: str, *, source_hash: str, extraction: dict):
    body = text.encode("utf-8")
    if len(body) > MAX_EXTRACTED_TEXT_BYTES:
        raise ResearchTextError("archive_text_too_large")
    text_hash, text_ref = store.put(body, suffix="txt")
    inline = body[:INLINE_TEXT_BYTES].decode("utf-8", errors="ignore")
    binding = {
        "version": TEXT_EXTRACTION_VERSION,
        "source_content_hash": source_hash,
        "text_content_hash": text_hash, "text_content_ref": text_ref,
        "text_utf8_bytes": len(body), "text_characters": len(text),
        "complete": True, "full_document_reading_claimed": False,
        "inline_utf8_bytes": len(inline.encode("utf-8")),
        "inline_truncated": inline != text,
        **extraction,
    }
    return inline, binding


def text_binding_identity(metadata: dict):
    """Legacy identity stays unchanged; storage location is not content identity."""
    binding = metadata.get("text_extraction")
    if binding is None:
        return None
    if not isinstance(binding, dict):
        raise ResearchTextError("archive_text_binding_invalid")
    return {key: value for key, value in binding.items() if key != "text_content_ref"}


def snapshot_text_bindings(snapshot, evidence) -> dict:
    """Resolve only explicit immutable associations, not proof-side metadata."""
    archive = (snapshot.query_manifest_json or {}).get("research_archive", {})
    bindings = archive.get("text_extractions", []) if isinstance(archive, dict) else None
    if not isinstance(bindings, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("evidence_key"), str)
        or not isinstance(row.get("binding"), dict) for row in bindings
    ):
        raise ResearchTextError("archive_text_binding_set_invalid")
    expected = {row["evidence_key"]: row["binding"] for row in bindings}
    if len(expected) != len(bindings) or not set(expected).issubset({row.evidence_key for row in evidence}):
        raise ResearchTextError("archive_text_binding_evidence_missing_or_duplicated")
    return expected


def verified_text_projection(proof, expected: dict) -> dict:
    """Only read a text file already bound by a NEW immutable snapshot.

    Never discover adjacent files, parse PDFs, fetch URLs, or upgrade legacy
    excerpts. Full-page processing does not imply OCR/visual completeness.
    """
    binding = (proof.metadata_json or {}).get("text_extraction")
    if not isinstance(binding, dict) or binding != expected:
        raise ResearchTextError("archive_text_binding_mismatch")
    if binding.get("version") != TEXT_EXTRACTION_VERSION:
        raise ResearchTextError("archive_text_version_unsupported")
    digest = binding.get("text_content_hash")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or binding.get("source_content_hash") != proof.content_hash
            or binding.get("complete") is not True
            or binding.get("full_document_reading_claimed") is not False):
        raise ResearchTextError("archive_text_binding_invalid")
    size, characters = binding.get("text_utf8_bytes"), binding.get("text_characters")
    if (type(size) is not int or not 0 < size <= MAX_EXTRACTED_TEXT_BYTES
            or type(characters) is not int or characters <= 0):
        raise ResearchTextError("archive_text_binding_invalid")
    text_ref = binding.get("text_content_ref")
    if not isinstance(text_ref, str) or "\x00" in text_ref:
        raise ResearchTextError("archive_text_path_invalid")
    path = Path(text_ref)
    if not path.is_absolute() or path.name != f"{digest}.txt" or path.parent.name != digest[:2]:
        raise ResearchTextError("archive_text_path_invalid")
    try:
        if path.stat().st_size != size:
            raise ResearchTextError("archive_text_length_mismatch")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            raise ResearchTextError("archive_text_hash_mismatch")
        text = body.decode("utf-8")
    except (OSError, UnicodeError):
        raise ResearchTextError("archive_text_unavailable") from None
    if len(text) != characters or len(body) != size:
        raise ResearchTextError("archive_text_length_mismatch")
    return {"content": text, "text_extraction": dict(binding)}
