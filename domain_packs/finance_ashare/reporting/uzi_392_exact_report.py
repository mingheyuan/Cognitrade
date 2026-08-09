"""ArtifactBoard adapter for UZI 3.9.4 analysis and exact 3.9.4 report presentation.

Visible DOM, CSS and JavaScript come from the pinned upstream template and
renderer helpers.  Cognitrade metadata is deliberately kept out of the visible
document and remains in the surrounding Artifact records/API headers.
"""

from __future__ import annotations

import base64
from datetime import datetime
from html import escape
import importlib
from pathlib import Path
import re
from threading import RLock
from typing import Any

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
    UZI_REPORT_VERSION,
    Uzi392FetcherRuntime,
)


_VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "uzi_3_9_2"
_TEMPLATE = _VENDOR_ROOT / "assets" / "report-template.html"
_AVATARS = _VENDOR_ROOT / "assets" / "avatars"
_SEGMENTAL_RENDER_LOCK = RLock()


class UziNativeQualityGateError(ValueError):
    """The pinned self-review still has critical issues."""


def render_exact_uzi_392_report(
    native_analysis: dict[str, Any],
    *,
    segmental_model: dict[str, Any] | None = None,
    segmental_validation: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Render one allowed UZI native-analysis result as standalone HTML."""

    results = [row for row in native_analysis.get("results") or [] if isinstance(row, dict)]
    if not results:
        raise UziNativeQualityGateError("UZI native 分析没有可用结果")
    if len(results) != 1:
        raise UziNativeQualityGateError(
            "UZI native HTML/PDF 导出要求且仅允许一个已分析 symbol"
        )
    if native_analysis.get("report_allowed") is not True:
        raise UziNativeQualityGateError("UZI 关键质量门禁尚未通过")
    result = results[0]
    review = result.get("self_review") if isinstance(result.get("self_review"), dict) else {}
    if int(review.get("critical_count") or 0) != 0 or result.get("report_allowed") is not True:
        raise UziNativeQualityGateError("UZI 关键质量门禁尚未通过")
    raw = _escape_payload(result.get("raw") if isinstance(result.get("raw"), dict) else {})
    dimensions = _escape_payload(result.get("dimensions") if isinstance(result.get("dimensions"), dict) else {})
    panel = _escape_payload(result.get("panel") if isinstance(result.get("panel"), dict) else {})
    synthesis = _escape_payload(result.get("synthesis") if isinstance(result.get("synthesis"), dict) else {})
    if not (raw and dimensions and panel and synthesis):
        raise UziNativeQualityGateError("UZI native 报告所需数据不完整")

    renderer = _renderer_module()
    basic = ((raw.get("dimensions") or {}).get("0_basic") or {}).get("data") or {}
    market = raw.get("market") or basic.get("market") or "A"
    currency_symbol = {"H": "HK$", "U": "$"}.get(market, "¥")
    debate = synthesis.get("debate") or {}
    divide = synthesis.get("great_divide") or {}
    dashboard = synthesis.get("dashboard") or {}
    perspective = dashboard.get("data_perspective") or {}
    intelligence = dashboard.get("intelligence") or {}
    battle_plan = dashboard.get("battle_plan") or {}
    zones = synthesis.get("buy_zones") or {}
    trap = ((raw.get("dimensions") or {}).get("18_trap") or {}).get("data") or {}
    trap_level = trap.get("trap_level") or "🟢 安全"
    trap_color, trap_emoji = renderer.trap_color_emoji(trap_level)
    bull = debate.get("bull") or {}
    bear = debate.get("bear") or {}
    rounds = debate.get("rounds") or []
    last_round = rounds[-1] if rounds else {}
    investors = panel.get("investors") or []

    def chat_key(row: dict[str, Any]) -> tuple[int, float]:
        rank = {"bullish": 0, "bearish": 1, "neutral": 2}.get(row.get("signal", "neutral"), 3)
        return rank, -float(row.get("confidence") or 0)

    chat_ordered = sorted(investors, key=chat_key)
    signal_distribution = panel.get("signal_distribution") or {}
    template = _TEMPLATE.read_text(encoding="utf-8")
    safe = renderer._safe
    market_status = renderer.market_status()
    replacements = {
        "{{NAME}}": safe(synthesis.get("name") or basic.get("name")),
        "{{TICKER}}": safe(synthesis.get("ticker") or basic.get("code") or raw.get("ticker")),
        "{{CURRENCY}}": currency_symbol,
        "{{ONE_LINER}}": safe(basic.get("one_liner") or basic.get("industry") or ""),
        "{{PRICE}}": str(safe(basic.get("price"))),
        "{{CHANGE_PCT}}": f"{basic.get('change_pct', 0):+.2f}%" if basic.get("change_pct") is not None else "—",
        "{{CHANGE_DIR}}": "up" if (basic.get("change_pct") or 0) >= 0 else "down",
        "{{MCAP}}": str(safe(basic.get("market_cap"))),
        "{{PE}}": str(safe(basic.get("pe_ttm"))),
        "{{PB}}": str(safe(basic.get("pb"))),
        "{{INDUSTRY}}": str(safe(basic.get("industry"))),
        "{{OVERALL_SCORE}}": str(synthesis.get("overall_score", 0)),
        "{{OVERALL_SCORE_INT}}": str(int(synthesis.get("overall_score", 0))),
        "{{VERDICT_LABEL}}": safe(synthesis.get("verdict_label")) + (
            f" · {synthesis['verdict_detail']}" if synthesis.get("verdict_detail") else ""
        ),
        "{{TRAP_LEVEL}}": trap_level,
        "{{TRAP_COLOR}}": trap_color,
        "{{TRAP_EMOJI}}": trap_emoji,
        "{{TRAP_RECOMMENDATION}}": safe(trap.get("recommendation"), "数据正常，未发现异常推广痕迹"),
        "{{CORE_CONCLUSION}}": safe(dashboard.get("core_conclusion")),
        "{{DP_TREND}}": safe(perspective.get("trend")),
        "{{DP_PRICE}}": safe(perspective.get("price")),
        "{{DP_VOLUME}}": safe(perspective.get("volume")),
        "{{DP_CHIPS}}": safe(perspective.get("chips")),
        "{{INTEL_NEWS}}": safe(intelligence.get("news")),
        "{{INTEL_RISKS}}": safe(", ".join(intelligence.get("risks") or [])),
        "{{INTEL_CATALYSTS}}": safe(", ".join(intelligence.get("catalysts") or [])),
        "{{BP_ENTRY}}": safe(battle_plan.get("entry")),
        "{{BP_POSITION}}": safe(battle_plan.get("position")),
        "{{BP_STOP}}": safe(battle_plan.get("stop")),
        "{{BP_TARGET}}": safe(battle_plan.get("target")),
        "{{BULL_ID}}": safe(bull.get("investor_id"), "_placeholder"),
        "{{BULL_NAME}}": safe(bull.get("name"), "（未选出）"),
        "{{BULL_SCORE}}": str(divide.get("bull_score", 0)),
        "{{BULL_LAST_SAY}}": safe(last_round.get("bull_say"), "—"),
        "{{BEAR_ID}}": safe(bear.get("investor_id"), "_placeholder"),
        "{{BEAR_NAME}}": safe(bear.get("name"), "（未选出）"),
        "{{BEAR_SCORE}}": str(divide.get("bear_score", 0)),
        "{{BEAR_LAST_SAY}}": safe(last_round.get("bear_say"), "—"),
        "{{PUNCHLINE}}": safe(divide.get("punchline") or debate.get("punchline")),
        "{{ZONE_VALUE_PRICE}}": str(safe((zones.get("value") or {}).get("price"))),
        "{{ZONE_VALUE_RATIONALE}}": safe((zones.get("value") or {}).get("rationale")),
        "{{ZONE_GROWTH_PRICE}}": str(safe((zones.get("growth") or {}).get("price"))),
        "{{ZONE_GROWTH_RATIONALE}}": safe((zones.get("growth") or {}).get("rationale")),
        "{{ZONE_TECH_PRICE}}": str(safe((zones.get("technical") or {}).get("price"))),
        "{{ZONE_TECH_RATIONALE}}": safe((zones.get("technical") or {}).get("rationale")),
        "{{ZONE_YOUZI_PRICE}}": str(safe((zones.get("youzi") or {}).get("price"))),
        "{{ZONE_YOUZI_RATIONALE}}": safe((zones.get("youzi") or {}).get("rationale")),
        "{{GENERATED_AT}}": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "{{BULL_COUNT}}": str(signal_distribution.get("bullish", 0)),
        "{{BEAR_COUNT}}": str(signal_distribution.get("bearish", 0)),
        "{{NEUT_COUNT}}": str(signal_distribution.get("neutral", 0)),
        "{{CONSENSUS_PCT}}": f"{panel.get('panel_consensus', 0):.0f}",
        "{{BULL_TAG}}": safe((bull.get("group") and renderer.GROUP_LABELS.get(bull.get("group"))) or bull.get("tagline"), ""),
        "{{BEAR_TAG}}": safe((bear.get("group") and renderer.GROUP_LABELS.get(bear.get("group"))) or bear.get("tagline"), ""),
        "{{BULL_SIGNAL_CN}}": {"bullish": "看多", "neutral": "中性", "bearish": "看空"}.get(divide.get("bull_signal", ""), "看多"),
        "{{BEAR_SIGNAL_CN}}": {"bullish": "看多", "neutral": "中性", "bearish": "看空"}.get(divide.get("bear_signal", ""), "看空"),
        "{{TOTAL_COUNT}}": str(len(investors)),
        "{{MARKET_STATUS}}": market_status.get("label", ""),
        "{{MARKET_STATUS_CLASS}}": "open" if market_status.get("is_open") else "closed",
        "{{DATA_FETCHED_AT}}": (raw.get("fetched_at") or "")[:19].replace("T", " "),
        "{{PLUGIN_VERSION}}": UZI_REPORT_VERSION,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, str(value))

    injections = {
        "<!-- INJECT_JURY_SEATS -->": "\n".join(renderer.render_jury_seat(row) for row in investors),
        "<!-- INJECT_CHAT_MESSAGES -->": "\n".join(renderer.render_chat_message(row) for row in chat_ordered),
        "<!-- INJECT_VOTE_BARS -->": renderer.render_vote_bars(panel.get("vote_distribution") or {}),
        "<!-- INJECT_TOP3_BULLS -->": renderer.render_top3_bulls(investors),
        "<!-- INJECT_TOP3_BEARS -->": renderer.render_top3_bears(investors),
        "<!-- INJECT_PANEL_INSIGHTS -->": renderer.render_panel_insights(synthesis, panel),
        "<!-- INJECT_SCHOOL_SCORES -->": renderer.render_school_scores(synthesis, panel),
        "<!-- INJECT_RISKS -->": renderer.render_risks(synthesis.get("risks") or []),
        "<!-- INJECT_DEBATE_ROUNDS -->": renderer.render_debate_rounds(debate),
        "<!-- INJECT_FRIENDLY_LAYER -->": renderer.render_friendly_layer(synthesis, raw),
        "<!-- INJECT_FUND_MANAGERS -->": renderer.render_fund_managers(synthesis.get("fund_managers") or raw.get("fund_managers") or []),
        "<!-- INJECT_DIM_FINANCIAL -->": renderer.render_dim_category("fin", dimensions, raw),
        "<!-- INJECT_DIM_MARKET -->": renderer.render_dim_category("mkt", dimensions, raw),
        "<!-- INJECT_DIM_INDUSTRY -->": renderer.render_dim_category("ind", dimensions, raw),
        "<!-- INJECT_DIM_COMPANY -->": renderer.render_dim_category("co", dimensions, raw),
        "<!-- INJECT_DIM_ENV -->": renderer.render_dim_category("env", dimensions, raw),
        "<!-- INJECT_DIM_SAFETY -->": renderer.render_dim_category("saf", dimensions, raw),
        "<!-- INJECT_INSTITUTIONAL_MODELING -->": renderer._render_institutional_section(raw),
        "<!-- INJECT_SEGMENTAL -->": _render_segmental_in_memory(
            ticker=str(raw.get("ticker") or result.get("symbol") or "unknown"),
            model=_escape_payload(segmental_model or {}),
            validation=_escape_payload(segmental_validation or {}),
            synthesis=synthesis,
        ),
        "<!-- INJECT_DATA_GAP_BANNER -->": (
            renderer._render_school_lock_banner(synthesis)
            + renderer._render_data_gap_banner(synthesis.get("data_gaps"), raw=raw, syn=synthesis)
        ),
        "<!-- INJECT_STYLE_CHIP -->": renderer._render_style_chip(synthesis),
    }
    for marker, value in injections.items():
        template = template.replace(marker, value)
    if currency_symbol != "¥":
        template = template.replace("¥", currency_symbol)
    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}|<!-- INJECT_[A-Z0-9_]+ -->", template)))
    if unresolved:
        raise RuntimeError(f"存在未解析的 UZI native 模板标记：{', '.join(unresolved)}")
    return _inline_avatars(template), str(raw.get("ticker") or result.get("symbol") or "unknown")


def _render_segmental_in_memory(
    *,
    ticker: str,
    model: dict[str, Any],
    validation: dict[str, Any],
    synthesis: dict[str, Any],
) -> str:
    """Call UZI's exact segmental renderer without its filesystem cache."""

    if not model.get("segments"):
        return ""
    _renderer_module()
    cache_module = importlib.import_module("lib.cache")
    segmental = importlib.import_module("lib.report.segmental")
    svg_primitives = importlib.import_module("lib.report.svg_primitives")
    original = cache_module.read_task_output
    original_sparkline = getattr(segmental, "svg_sparkline", None)

    def read_task_output(_ticker: str, task: str) -> dict[str, Any] | None:
        return {
            "segmental_model": model,
            "segmental_validation": validation,
            "synthesis": synthesis,
        }.get(task)

    # The vendored renderer imports read_task_output inside its function.
    # Serialize this tiny adapter window so concurrent PDF exports cannot see
    # another report's ArtifactBoard payload.
    with _SEGMENTAL_RENDER_LOCK:
        cache_module.read_task_output = read_task_output
        # UZI 3.9.4's segmental module calls the native primitive but omitted
        # its import.  Inject that exact upstream function at the adapter
        # boundary without modifying the pinned vendored source/hash.
        segmental.svg_sparkline = svg_primitives.svg_sparkline
        try:
            return str(segmental._render_segmental_block(ticker) or "")
        finally:
            cache_module.read_task_output = original
            if original_sparkline is None:
                delattr(segmental, "svg_sparkline")
            else:
                segmental.svg_sparkline = original_sparkline


def _renderer_module() -> Any:
    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[3] / "workspace")
    return importlib.import_module("assemble_report")


def _inline_avatars(html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1).split("/", 1)[1]
        path = _AVATARS / name
        if not path.is_file():
            return match.group(0)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'src="data:image/svg+xml;base64,{encoded}"'

    return re.sub(r'src="(avatars/[^"]+)"', replace, html)


def _escape_payload(value: Any) -> Any:
    if isinstance(value, str):
        return escape(value, quote=True)
    if isinstance(value, dict):
        return {key: _escape_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_escape_payload(item) for item in value]
    return value


__all__ = ["UziNativeQualityGateError", "render_exact_uzi_392_report"]
