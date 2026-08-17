"""Strict ArtifactBoard boundary for UZI 3.9.4 analysis and 3.9.4 presentation."""

from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import hashlib
import importlib
from pathlib import Path
import re
from threading import BoundedSemaphore, RLock
from typing import Any, Iterable

from domain_packs.finance_ashare.reporting.uzi_392_exact_report import (
    UziNativeQualityGateError,
    render_exact_uzi_392_report,
)
from domain_packs.finance_ashare.subagents.uzi_portfolio_contract import (
    normalize_portfolio_holdings,
    ticker_identity,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime


_PDF_RENDER_SLOTS = BoundedSemaphore(2)
_PDF_CACHE_LOCK = RLock()
_PDF_CACHE: OrderedDict[str, bytes] = OrderedDict()
_PDF_CACHE_LIMIT = 16


class ReportArtifactMissingError(ValueError):
    """The run does not contain a native UZI report that passed its gate."""


class ReportPdfUnavailableError(RuntimeError):
    """The local Chromium PDF renderer is unavailable."""


class ReportImageUnavailableError(RuntimeError):
    """The local Chromium screenshot renderer is unavailable."""


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
    """Render the exact UZI single, versus or portfolio presentation."""

    rows = [dict(item) for item in artifacts if isinstance(item, dict)]
    native_artifact = _latest(rows, "uzi_native_analysis")
    if native_artifact is None:
        raise ReportArtifactMissingError("uzi_native_analysis artifact 暂不可用")
    report_artifact = _latest(rows, "research_report")
    report = _record((report_artifact or {}).get("content"))
    if report.get("report_allowed") is not True:
        raise ReportArtifactMissingError("UZI critical 质量门未通过")
    delivery_type = str(report.get("delivery_type") or "")
    if delivery_type == "uzi_versus_3_9_4":
        return _render_uzi_versus(
            rows,
            native_artifact=native_artifact,
            report=report,
            run_id=run_id,
        )
    if delivery_type == "uzi_portfolio_3_9_4":
        return _render_uzi_portfolio(
            rows,
            native_artifact=native_artifact,
            report=report,
            run_id=run_id,
        )
    if delivery_type != "uzi_native_3_9_4":
        raise ReportArtifactMissingError("该运行不是 UZI 3.9.4 原生报告")
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


def _initialize_native_report_imports() -> None:
    Uzi392FetcherRuntime(
        workspace_root=Path(__file__).resolve().parents[3] / "workspace",
    )


def _native_metrics(native_content: dict[str, Any]) -> list[dict[str, Any]]:
    _initialize_native_report_imports()
    extract_metrics = importlib.import_module("lib.versus_runner")._extract_metrics
    metrics: list[dict[str, Any]] = []
    for result in list(native_content.get("results") or []):
        if not isinstance(result, dict) or result.get("report_allowed") is not True:
            continue
        raw = _record(result.get("raw"))
        ticker = str(raw.get("ticker") or result.get("symbol") or "").strip()
        if not ticker:
            continue
        metrics.append(extract_metrics({
            "syn": _record(result.get("synthesis")),
            "raw": raw,
            "panel": _record(result.get("panel")),
            "ticker": ticker,
        }))
    return metrics


def _source_artifact_ids(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        str(artifact.get("artifact_id"))
        for artifact in rows
        if artifact.get("artifact_id")
    )


def _render_uzi_versus(
    rows: list[dict[str, Any]],
    *,
    native_artifact: dict[str, Any],
    report: dict[str, Any],
    run_id: str,
) -> RenderedUziReport:
    native_content = _record(native_artifact.get("content"))
    metrics = _native_metrics(native_content)
    if not 2 <= len(metrics) <= 4:
        raise ReportArtifactMissingError("UZI --versus 需要 2 至 4 个通过质量门的分析结果")
    render_html = importlib.import_module("lib.versus_runner")._render_html
    html_document = render_html(metrics, str(native_content.get("profile") or "lite"))
    symbols = "_vs_".join(str(row.get("ticker") or "") for row in metrics)
    safe_symbols = re.sub(r"[^A-Za-z0-9._-]", "-", symbols).strip("-") or "comparison"
    return RenderedUziReport(
        html=html_document,
        filename_stem=f"uzi-versus-{safe_symbols}-{run_id}",
        symbol=symbols,
        source_artifact_ids=_source_artifact_ids(rows),
    )


def _render_uzi_portfolio(
    rows: list[dict[str, Any]],
    *,
    native_artifact: dict[str, Any],
    report: dict[str, Any],
    run_id: str,
) -> RenderedUziReport:
    native_content = _record(native_artifact.get("content"))
    metrics = _native_metrics(native_content)
    holdings = [dict(row) for row in list(report.get("portfolio_holdings") or []) if isinstance(row, dict)]
    if not metrics:
        raise ReportArtifactMissingError("UZI --portfolio 至少需要一个可用成分")
    portfolio_runner = importlib.import_module("lib.portfolio_runner")
    normalized_holdings = normalize_portfolio_holdings(holdings)
    matched_metrics: list[dict[str, Any]] = []
    for holding in normalized_holdings:
        holding_keys = {
            key
            for key in (
                ticker_identity(holding.get("resolved_ticker")),
                ticker_identity(holding.get("ticker") or holding.get("symbol")),
            )
            if key
        }
        metric = next(
            (
                row
                for row in metrics
                if ticker_identity(row.get("ticker")) in holding_keys
            ),
            None,
        )
        if metric is None:
            continue
        metric = dict(metric)
        metric["_weight"] = float(holding.get("weight") or 0.0)
        metric["_note"] = str(holding.get("note") or "")
        matched_metrics.append(metric)
    metrics = matched_metrics
    if not metrics:
        raise ReportArtifactMissingError("UZI --portfolio 没有与持仓匹配的可用成分")
    health = portfolio_runner._portfolio_health(metrics)
    portfolio_name = str(report.get("portfolio_name") or "UZI Portfolio")
    html_document = portfolio_runner._render_html(
        portfolio_name,
        metrics,
        health,
        str(native_content.get("profile") or "lite"),
    )
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", portfolio_name).strip("-") or "portfolio"
    return RenderedUziReport(
        html=html_document,
        filename_stem=f"uzi-portfolio-{safe_name}-{run_id}",
        symbol=portfolio_name,
        source_artifact_ids=_source_artifact_ids(rows),
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


def render_uzi_native_image(html_document: str, *, selector: str) -> bytes:
    """Apply UZI's native Playwright share-card/war-report screenshot flow."""

    if selector not in {"#share-card", "#war-report"}:
        raise ValueError("unsupported UZI report image selector")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ReportImageUnavailableError("未安装 Playwright") from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context(
                    device_scale_factor=2,
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                page.set_content(html_document, wait_until="load", timeout=30_000)
                page.evaluate("document.fonts && document.fonts.ready")
                page.evaluate(
                    """selector => {
                        const element = document.querySelector(selector);
                        if (!element) return false;
                        element.style.position = 'static';
                        element.style.left = '0';
                        element.style.top = '0';
                        return true;
                    }""",
                    selector,
                )
                locator = page.locator(selector)
                if locator.count() != 1:
                    raise ReportImageUnavailableError(f"UZI 报告缺少 {selector} 元素")
                payload = locator.screenshot(omit_background=False)
            finally:
                browser.close()
    except (PlaywrightError, OSError) as exc:
        raise ReportImageUnavailableError("Chromium 图片渲染器暂不可用") from exc
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ReportImageUnavailableError("Chromium 返回了无效的 PNG 数据")
    return payload


def render_uzi_native_one_liner(
    artifacts: Iterable[dict[str, Any]],
    *,
    report_locator: str,
) -> tuple[str, str]:
    """Render UZI's exact single-stock one-liner from ArtifactBoard data."""

    rows = [dict(item) for item in artifacts if isinstance(item, dict)]
    report = _record((_latest(rows, "research_report") or {}).get("content"))
    if report.get("delivery_type") != "uzi_native_3_9_4" or report.get("report_allowed") is not True:
        raise ReportArtifactMissingError("one-liner 仅适用于通过质量门的 UZI 单股报告")
    native = _record((_latest(rows, "uzi_native_analysis") or {}).get("content"))
    results = [row for row in list(native.get("results") or []) if isinstance(row, dict)]
    if len(results) != 1 or results[0].get("report_allowed") is not True:
        raise ReportArtifactMissingError("one-liner 需要一个通过质量门的单股结果")
    result = results[0]
    synthesis = _record(result.get("synthesis"))
    panel = _record(result.get("panel"))
    raw = _record(result.get("raw"))
    divide = _record(synthesis.get("great_divide"))
    trap = _record(_record(_record(raw.get("dimensions")).get("18_trap")).get("data"))
    trap_level = str(trap.get("trap_level") or "🟢 安全")
    _initialize_native_report_imports()
    _trap_color, trap_emoji = importlib.import_module("lib.report.institutional").trap_color_emoji(trap_level)
    score = int(float(synthesis.get("overall_score") or 0))
    signal_distribution = _record(panel.get("signal_distribution"))
    bullish = int(signal_distribution.get("bullish") or 0)
    long_active = int(panel.get("long_active") or sum(
        int(signal_distribution.get(key) or 0)
        for key in ("bullish", "neutral", "bearish")
    ))
    symbol = str(result.get("symbol") or raw.get("ticker") or "unknown")
    one_liner = (
        f"{synthesis.get('name')} 体检结果：{score} 分，{synthesis.get('verdict_label')}。\n"
        f"{long_active} 位多头评委里 {bullish} 人喊买。\n"
        f"💬 {divide.get('punchline') or '—'}\n"
        f"{trap_emoji} {trap_level}\n"
        f"全文 → {report_locator}"
    )
    safe_symbol = re.sub(r"[^A-Za-z0-9._-]", "-", symbol).strip("-") or "unknown"
    return one_liner, f"uzi-one-liner-{safe_symbol}.txt"


__all__ = [
    "ReportArtifactMissingError",
    "ReportPdfUnavailableError",
    "ReportImageUnavailableError",
    "RenderedUziReport",
    "render_uzi_native_html",
    "render_uzi_native_pdf",
    "render_uzi_native_image",
    "render_uzi_native_one_liner",
]
