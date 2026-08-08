"""Strict ArtifactBoard boundary for UZI 3.9.2 analysis and 3.9.4 presentation."""

from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import hashlib
import re
from threading import BoundedSemaphore, RLock
from typing import Any, Iterable

from domain_packs.finance_ashare.reporting.uzi_392_exact_report import (
    UziNativeQualityGateError,
    render_exact_uzi_392_report,
)


_PDF_RENDER_SLOTS = BoundedSemaphore(2)
_PDF_CACHE_LOCK = RLock()
_PDF_CACHE: OrderedDict[str, bytes] = OrderedDict()
_PDF_CACHE_LIMIT = 16


class ReportArtifactMissingError(ValueError):
    """The run does not contain a native UZI report that passed its gate."""


class ReportPdfUnavailableError(RuntimeError):
    """The local Chromium PDF renderer is unavailable."""


@dataclass(frozen=True)
class RenderedUziReport:
    html: str
    filename_stem: str
    symbol: str
    source_artifact_ids: tuple[str, ...]


def _record(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _latest(rows: list[dict[str, Any]], artifact_type: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in reversed(rows)
            if row.get("artifact_type") == artifact_type
            and str(row.get("status") or "active") == "active"
        ),
        None,
    )


def _validated_segmental(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for artifact in reversed(rows):
        if artifact.get("artifact_type") != "uzi_native_command_result":
            continue
        command = _record(artifact.get("content"))
        if command.get("command_id") != "segmental-model":
            continue
        for row in list(command.get("results") or []):
            result = _record(_record(row).get("result"))
            if result.get("phase") == "validated":
                model = _record(result.get("filled_model"))
                if model:
                    return model, _record(result.get("validation"))
    return None, None


def render_uzi_native_html(
    artifacts: Iterable[dict[str, Any]],
    *,
    run_id: str,
) -> RenderedUziReport:
    """Render only the exact UZI template from a gate-passed native artifact."""

    rows = [dict(item) for item in artifacts if isinstance(item, dict)]
    native_artifact = _latest(rows, "uzi_native_analysis")
    if native_artifact is None:
        raise ReportArtifactMissingError("uzi_native_analysis artifact 暂不可用")
    segmental_model, segmental_validation = _validated_segmental(rows)
    try:
        html_document, symbol = render_exact_uzi_392_report(
            _record(native_artifact.get("content")),
            segmental_model=segmental_model,
            segmental_validation=segmental_validation,
        )
    except UziNativeQualityGateError as exc:
        raise ReportArtifactMissingError(str(exc)) from exc
    safe_symbol = re.sub(r"[^A-Za-z0-9._-]", "-", symbol).strip("-") or "unknown"
    return RenderedUziReport(
        html=html_document,
        filename_stem=f"uzi-report-{safe_symbol}-{run_id}",
        symbol=symbol,
        source_artifact_ids=tuple(
            str(artifact.get("artifact_id"))
            for artifact in rows
            if artifact.get("artifact_id")
        ),
    )


def render_uzi_native_pdf(html_document: str) -> bytes:
    """Render the same exact HTML to PDF with the bundled Chromium runtime."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ReportPdfUnavailableError("未安装 Playwright") from exc
    digest = hashlib.sha256(html_document.encode("utf-8")).hexdigest()
    with _PDF_CACHE_LOCK:
        cached = _PDF_CACHE.get(digest)
        if cached is not None:
            _PDF_CACHE.move_to_end(digest)
            return cached
    if not _PDF_RENDER_SLOTS.acquire(timeout=60):
        raise ReportPdfUnavailableError("Chromium PDF 渲染队列等待超时")
    try:
        # Check again after waiting in the bounded queue: another worker may
        # already have rendered the identical immutable HTML.
        with _PDF_CACHE_LOCK:
            cached = _PDF_CACHE.get(digest)
            if cached is not None:
                _PDF_CACHE.move_to_end(digest)
                return cached
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(
                        viewport={"width": 1440, "height": 900},
                        device_scale_factor=1,
                    )
                    page.set_content(html_document, wait_until="load", timeout=30_000)
                    page.emulate_media(
                        media="print",
                        color_scheme="light",
                        reduced_motion="reduce",
                    )
                    page.evaluate(
                        """() => {
                            document.documentElement.setAttribute('data-theme', 'light');
                            document.getElementById('boot-overlay')?.remove();
                            document.querySelectorAll('details').forEach(element => {
                                element.open = true;
                            });
                        }"""
                    )
                    page.evaluate("() => document.fonts.ready")
                    payload = page.pdf(
                        print_background=True,
                        prefer_css_page_size=True,
                        tagged=True,
                        outline=True,
                    )
                finally:
                    browser.close()
        except (PlaywrightError, OSError) as exc:
            raise ReportPdfUnavailableError("Chromium PDF 渲染器暂不可用") from exc
    finally:
        _PDF_RENDER_SLOTS.release()
    if not payload.startswith(b"%PDF"):
        raise ReportPdfUnavailableError("Chromium 返回了无效的 PDF 数据")
    with _PDF_CACHE_LOCK:
        _PDF_CACHE[digest] = payload
        _PDF_CACHE.move_to_end(digest)
        while len(_PDF_CACHE) > _PDF_CACHE_LIMIT:
            _PDF_CACHE.popitem(last=False)
    return payload


__all__ = [
    "ReportArtifactMissingError",
    "ReportPdfUnavailableError",
    "RenderedUziReport",
    "render_uzi_native_html",
    "render_uzi_native_pdf",
]
