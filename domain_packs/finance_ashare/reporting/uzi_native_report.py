"""Render UZI 3.9.2-style reports from canonical ArtifactBoard records.

The renderer is deliberately Pack-owned and accepts only serialized artifacts.
It does not import, execute, clone, or shell out to the upstream UZI runtime.
HTML and PDF therefore share one immutable input contract and one template.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "uzi_native_report.html"
_UZI_SOURCE_COMMIT = "fce996c33e70eddce8e375f53cd252b549eb3d7c"
_CORE_ARTIFACT_TYPES = {
    "research_report",
    "report_presentation",
    "main_agent_synthesis",
    "valuation_model",
    "investor_panel_review",
    "risk_scan",
    "institutional_analysis",
    "research_workflow",
    "deep_analysis",
    "uzi_dimension_coverage",
}
_SPECIAL_METHOD_TYPES = {
    "ai_readiness",
    "earnings_preview",
    "model_update",
    "three_statement_model",
    "quick_lbo",
    "accretion_dilution",
    "segmental_model",
    "initiation_report",
    "earnings_review",
    "catalyst_calendar",
    "thesis_tracker",
    "morning_note",
    "ic_memo",
    "porter_bcg",
    "dd_checklist",
    "unit_economics",
    "value_creation_plan",
}


class ReportArtifactMissingError(ValueError):
    """The run does not contain an exportable UZI report."""


class ReportPdfUnavailableError(RuntimeError):
    """The local Chromium PDF renderer is unavailable."""


@dataclass(frozen=True)
class RenderedUziReport:
    html: str
    filename_stem: str
    symbol: str
    source_artifact_ids: tuple[str, ...]


_FIELD_LABELS = {
    "artifact_id": "Artifact ID",
    "artifact_type": "产物类型",
    "artifact_version": "产物版本",
    "source_refs": "数据来源",
    "risk_flags": "风险标记",
    "confidence": "置信度",
    "methodology_version": "方法论版本",
    "source_commit": "来源提交",
    "data_gap": "数据缺口",
    "quality_status": "质量状态",
    "status": "状态",
    "summary": "摘要",
    "conclusion": "结论",
    "recommendation": "建议",
    "reasoning": "判断依据",
    "checks": "检查项",
    "assumptions": "模型假设",
    "sensitivity": "敏感性分析",
    "methodology_log": "方法记录",
    "key_outputs": "关键输出",
    "fair_value": "代理价值",
    "margin_of_safety": "安全边际",
    "market_price": "市场价格",
    "relative_view": "相对估值判断",
    "peer_count": "同行样本数",
    "score": "评分",
    "weight": "权重",
    "signal": "信号",
    "headline": "核心观点",
    "perspective_label": "评审视角",
    "school_label": "流派",
    "required_dimensions": "所需维度",
    "generated_at": "生成时间",
    "published_at": "发布时间",
    "source_name": "来源",
    "source_type": "来源类型",
    "missing_fields": "缺失字段",
    "degrade_reason": "降级原因",
    "provider": "提供方",
    "uri": "来源地址",
    "retrieved_at": "采集时间",
    "content_hash": "内容摘要",
}

_STATUS_LABELS = {
    "available": "可用",
    "active": "有效",
    "completed": "已完成",
    "degraded": "部分降级",
    "data_gap": "数据缺口",
    "assumption_only": "仅模型假设",
    "provider_unavailable": "数据源不可用",
    "insufficient_evidence": "证据不足",
    "not_requested": "本次未请求",
    "needs_review": "需要复核",
    "attention": "需要关注",
    "high": "高风险",
    "low_observed": "未观察到明显风险",
    "below_proxy_value": "低于代理价值",
    "above_proxy_value": "高于代理价值",
    "near_proxy_value": "接近代理价值",
    "bullish": "看多",
    "bearish": "看空",
    "neutral": "中性",
    "buy": "买入",
    "sell": "卖出",
    "strong_buy": "强烈看多",
    "strong_sell": "强烈看空",
}


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, default: Any = "—") -> str:
    if value is None or value == "":
        return str(default)
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _e(value: Any, default: Any = "—") -> str:
    return escape(_text(value, default), quote=True)


def _label(key: Any) -> str:
    value = str(key or "")
    return _FIELD_LABELS.get(value, value.replace("_", " ").strip().title() or "字段")


def _localized(value: Any) -> str:
    raw = _text(value)
    return _STATUS_LABELS.get(raw, raw)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric(value: Any, unit: Any = "") -> str:
    number = _number(value)
    if number is None:
        return "—"
    if abs(number) >= 10000:
        rendered = f"{number:,.0f}"
    elif abs(number) >= 100:
        rendered = f"{number:,.2f}"
    else:
        rendered = f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{rendered}{_text(unit, '')}"


def _latest(artifacts: list[dict[str, Any]], artifact_type: str) -> dict[str, Any] | None:
    matches = [
        artifact
        for artifact in artifacts
        if str(artifact.get("artifact_type") or "") == artifact_type
        and str(artifact.get("status") or "active") in {"active", "needs_review"}
        and isinstance(artifact.get("content"), dict)
    ]
    return matches[-1] if matches else None


def _section(index: str, title: str, subtitle: str) -> str:
    return (
        f'<header class="section-head"><span>{_e(index)}</span><div>'
        f'<p>{_e(subtitle)}</p><h2>{_e(title)}</h2></div></header>'
    )


def _badge(value: Any) -> str:
    raw = str(value or "data_gap")
    status_class = re.sub(r"[^a-z0-9_-]", "-", raw.lower())
    return f'<span class="badge badge--{status_class}">{_e(_localized(raw))}</span>'


def _render_generic(value: Any, *, key: str = "", depth: int = 0) -> str:
    """Render complete JSON-compatible content without exposing raw JSON."""
    if depth > 8:
        return f'<span class="value">{_e(value)}</span>'
    if isinstance(value, dict):
        if not value:
            return '<span class="muted">暂无数据</span>'
        rows = []
        for child_key, child_value in value.items():
            rows.append(
                '<div class="kv-row">'
                f'<dt>{_e(_label(child_key))}</dt>'
                f'<dd>{_render_generic(child_value, key=str(child_key), depth=depth + 1)}</dd>'
                '</div>'
            )
        return f'<dl class="kv-tree">{"".join(rows)}</dl>'
    if isinstance(value, list):
        if not value:
            return '<span class="muted">暂无数据</span>'
        if all(not isinstance(item, (dict, list)) for item in value):
            return '<ul class="plain-list">' + "".join(f'<li>{_e(_localized(item))}</li>' for item in value) + '</ul>'
        return '<div class="nested-list">' + "".join(
            f'<article><span class="nested-index">{index + 1:02d}</span>{_render_generic(item, depth=depth + 1)}</article>'
            for index, item in enumerate(value)
        ) + '</div>'
    if key in {"status", "signal", "quality_status"}:
        return _badge(value)
    if key == "confidence":
        number = _number(value)
        return _e(f"{number * 100:.1f}%" if number is not None else value)
    if key in {"uri", "url", "source_url"} and str(value or "").startswith(("http://", "https://")):
        safe = _e(value)
        return f'<a href="{safe}" rel="noreferrer" target="_blank">{safe}</a>'
    return f'<span class="value">{_e(_localized(value))}</span>'


def _render_record_table(rows: list[Any]) -> str:
    records = [_record(row) for row in rows if isinstance(row, dict)]
    if not records:
        return '<span class="muted">暂无数据</span>'
    keys = list(dict.fromkeys(str(key) for row in records for key in row))
    header = "".join(f"<th>{_e(_label(key))}</th>" for key in keys)
    body = "".join(
        "<tr>" + "".join(f"<td>{_render_generic(row.get(key), key=key)}</td>" for key in keys) + "</tr>"
        for row in records
    )
    return f'<div class="table-wrap compact-table"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _render_hero(presentation: dict[str, Any], report: dict[str, Any], run_id: str) -> str:
    subject = _record(presentation.get("subject"))
    hero = _record(presentation.get("hero"))
    name = hero.get("name") or subject.get("name") or hero.get("symbol") or "UZI Research"
    symbol = hero.get("symbol") or subject.get("symbol") or "—"
    change = _number(hero.get("change_pct"))
    change_class = "up" if (change or 0) >= 0 else "down"
    score = _metric(hero.get("score"))
    data_gap = _list(presentation.get("data_gap"))
    holding = _record(report.get("holding_context"))
    holding_context = ""
    if holding:
        source_label = (
            "目标 ETF 穿透持仓"
            if holding.get("holding_source") == "target_etf_lookthrough"
            else "基金直接披露持仓"
        )
        holding_context = (
            '<aside class="holding-context">'
            '<div><span>基金持仓来源</span>'
            f'<strong>{_e(holding.get("fund_name"))}（{_e(holding.get("fund_code"))}）</strong></div>'
            f'<div><span>披露排名</span><strong>#{_e(holding.get("holding_rank"))}</strong></div>'
            f'<div><span>基金有效权重</span><strong>{_metric(holding.get("fund_weight_pct"), "%")}</strong></div>'
            f'<div><span>披露日期</span><strong>{_e(holding.get("disclosure_date"), "未取得")}</strong></div>'
            f'<div><span>持仓口径</span><strong>{_e(source_label)}</strong></div>'
            f'<p>{_e(holding.get("disclaimer"), "基金定期披露持仓不代表当前仍持有该证券。")}</p>'
            '</aside>'
        )
    return f"""
    <section class="hero" id="section-hero">
      <div class="hero-watermark">O.o</div>
      <div class="hero-copy">
        <p class="eyebrow">UZI 3.9.2 · AGENT OS NATIVE DELIVERY</p>
        <div class="hero-title"><h1>{_e(name)}</h1><span>{_e(symbol)}</span></div>
        <p class="hero-sector">{_e(subject.get('sector'), '行业信息未取得')} · 截至 {_e(subject.get('as_of'), '本次运行')}</p>
        <p class="hero-conclusion">{_e(presentation.get('core_conclusion') or report.get('conclusion'), '当前证据不足，无法形成结论。')}</p>
        {holding_context}
        <div class="hero-meta"><span>RUN {_e(run_id)}</span><span>PROFILE {_e(presentation.get('profile'), 'unknown')}</span><span>SOURCE {_e(_UZI_SOURCE_COMMIT[:8])}</span></div>
      </div>
      <div class="hero-terminal">
        <div><span>现价</span><strong>{_metric(hero.get('price'))}</strong></div>
        <div><span>涨跌</span><strong class="{change_class}">{_metric(change, '%')}</strong></div>
        <div><span>Panel 均分</span><strong>{score}</strong></div>
        <div><span>估值状态</span><strong>{_e(_localized(hero.get('verdict')))}</strong></div>
        <div><span>安全扫描</span><strong>{_e(_localized(hero.get('safety')))}</strong></div>
        <div><span>数据缺口</span><strong>{len(data_gap)}</strong></div>
      </div>
    </section>
    """


def _render_gap_banner(presentation: dict[str, Any]) -> str:
    gaps = [str(item) for item in _list(presentation.get("data_gap")) if str(item).strip()]
    if not gaps:
        return '<section class="quality-banner quality-banner--ok"><strong>数据质量门已通过</strong><span>本报告未记录结构化数据缺口；仍需复核来源时效。</span></section>'
    preview = "".join(f"<li>{_e(item)}</li>" for item in gaps)
    return (
        '<section class="quality-banner"><div><strong>数据质量提示</strong>'
        f'<span>{len(gaps)} 项缺口已显式保留，未进行模拟或中性补值。</span></div>'
        f'<details><summary>查看全部缺口</summary><ul>{preview}</ul></details></section>'
    )


def _render_dashboard(presentation: dict[str, Any]) -> str:
    dashboard = _record(presentation.get("dashboard"))
    metrics = _list(dashboard.get("metrics"))
    fundamentals = _record(dashboard.get("fundamentals"))
    valuation = _record(dashboard.get("valuation"))
    price = _record(dashboard.get("price"))
    cards = []
    for metric in metrics:
        row = _record(metric)
        cards.append(
            '<article class="metric-card">'
            f'<span>{_e(row.get("label") or _label(row.get("key")))}</span>'
            f'<strong>{_metric(row.get("value"), row.get("unit"))}</strong>'
            f'{_badge(row.get("status"))}</article>'
        )
    fundamental_specs = (
        ("revenue_yi", "营业收入", "亿元"),
        ("net_profit_yi", "净利润", "亿元"),
        ("fcf_yi", "自由现金流", "亿元"),
        ("revenue_growth", "营收增速", "%"),
        ("profit_growth", "利润增速", "%"),
        ("roe", "ROE", "%"),
    )
    fundamentals_html = "".join(
        f'<div><span>{label}</span><strong>{_metric(fundamentals.get(key), unit)}</strong></div>'
        for key, label, unit in fundamental_specs
    )
    risks = _list(dashboard.get("risks"))
    catalysts = _list(dashboard.get("catalysts"))
    cards_html = "".join(cards) or '<p class="muted">暂无核心指标</p>'
    return (
        _section("01", "核心仪表盘", "CORE DASHBOARD")
        + '<section class="dashboard-bento" id="section-core">'
        + f'<article class="bento bento--price"><p>MARKET</p><h3>{_metric(price.get("current"))}</h3><span>{_metric(price.get("change_pct"), "%")} · {_e(price.get("as_of"))}</span></article>'
        + f'<article class="bento bento--valuation"><p>VALUATION</p><h3>{_metric(valuation.get("fair_value"))}</h3><span>安全边际 {_metric(valuation.get("margin_of_safety"), "%")}</span></article>'
        + f'<article class="bento bento--fundamentals"><p>FUNDAMENTALS</p><div class="mini-grid">{fundamentals_html}</div></article>'
        + f'<article class="bento bento--intelligence"><p>INTELLIGENCE</p><div><strong>风险 {len(risks)}</strong><strong>催化 {len(catalysts)}</strong></div><span>仅展示真实返回记录，不补造事件。</span></article>'
        + f'<div class="metric-grid">{cards_html}</div>'
        + '</section>'
    )


def _vote_summary(vote: Any, fallback: str) -> tuple[str, str, Any, Any]:
    row = _record(vote)
    name = _text(row.get("perspective_label") or row.get("investor_name"), fallback)
    headline = _text(row.get("headline") or row.get("reasoning"), "证据不足，未形成明确观点")
    return name, headline, row.get("score"), row.get("confidence")


def _render_divide(presentation: dict[str, Any]) -> str:
    divide = _record(presentation.get("great_divide"))
    bull = _record(divide.get("bull"))
    bear = _record(divide.get("bear"))
    bull_name, bull_text, bull_score, bull_confidence = _vote_summary(bull.get("top_vote"), "未选出看多代表")
    bear_name, bear_text, bear_score, bear_confidence = _vote_summary(bear.get("top_vote"), "未选出看空代表")
    rounds = _list(divide.get("rounds"))
    round_html = "".join(
        '<article class="debate-round">'
        f'<header><strong>{_e(_record(row).get("label"), "方法论流派")}</strong><span>均分 {_metric(_record(row).get("average_score"))}</span></header>'
        f'<div><span>看多 {_e(_record(row).get("bullish_count"), "0")}</span><span>看空 {_e(_record(row).get("bearish_count"), "0")}</span><span>中性 {_e(_record(row).get("neutral_count"), "0")}</span><span>证据不足 {_e(_record(row).get("insufficient_evidence_count"), "0")}</span></div>'
        '</article>'
        for row in rounds
    )
    round_html = round_html or '<p class="muted">本次未生成流派辩论轮次。</p>'
    return (
        _section("02", "世纪分歧", "THE GREAT DIVIDE")
        + '<section class="divide" id="section-clash">'
        + '<article class="divide-side divide-side--bull"><span>多方代表</span>'
        + f'<div class="avatar">BULL</div><h3>{_e(bull_name)}</h3><strong>{_metric(bull_score)}</strong><p>{_e(bull_text)}</p><small>置信度 {_metric((_number(bull_confidence) or 0) * 100, "%")}</small></article>'
        + '<div class="versus">VS</div>'
        + '<article class="divide-side divide-side--bear"><span>空方代表</span>'
        + f'<div class="avatar">BEAR</div><h3>{_e(bear_name)}</h3><strong>{_metric(bear_score)}</strong><p>{_e(bear_text)}</p><small>置信度 {_metric((_number(bear_confidence) or 0) * 100, "%")}</small></article>'
        + f'<div class="punchline"><span>分歧结论</span><strong>{_e(divide.get("punchline"), "当前 Panel 未形成可审计的分歧结论。")}</strong></div>'
        + f'<div class="debate-rounds">{round_html}</div>'
        + '</section>'
    )


def _render_panel(presentation: dict[str, Any]) -> str:
    panel = _record(presentation.get("panel"))
    votes = [_record(vote) for vote in _list(panel.get("votes"))]
    distribution = _record(panel.get("distribution"))
    cards = []
    for index, vote in enumerate(votes):
        signal = str(vote.get("signal") or "insufficient_evidence")
        checks = _list(vote.get("checks"))
        cards.append(
            f'<article class="jury-card jury-card--{_e(signal)}">'
            f'<header><span>{index + 1:02d}</span><div><h3>{_e(vote.get("perspective_label"), "方法论席位")}</h3><p>{_e(vote.get("school_label") or vote.get("school"), "未分组")}</p></div>{_badge(signal)}</header>'
            f'<div class="jury-score"><strong>{_metric(vote.get("score"))}</strong><span>置信度 {_metric((_number(vote.get("confidence")) or 0) * 100, "%")}</span></div>'
            f'<h4>{_e(vote.get("headline"), "未形成标题")}</h4><p>{_e(vote.get("reasoning"), "当前证据不足")}</p>'
            f'<details><summary>检查项与所需维度</summary>{_render_generic({"checks": checks, "required_dimensions": _list(vote.get("required_dimensions"))})}</details>'
            '</article>'
        )
    cards_html = "".join(cards) or '<p class="muted">本次未请求 Panel，或没有生成评审席位。</p>'
    return (
        _section("03", "投资方法论评审团", "UZI INVESTOR PANEL")
        + '<section class="panel-section" id="section-jury">'
        + '<div class="panel-stats">'
        + f'<div><span>注册席位</span><strong>{_e(panel.get("registered_seat_count"), len(votes))}</strong></div>'
        + f'<div><span>实际返回</span><strong>{len(votes)}</strong></div>'
        + f'<div><span>平均分</span><strong>{_metric(panel.get("average_score"))}</strong></div>'
        + f'<div><span>看多</span><strong>{_e(distribution.get("bullish"), "0")}</strong></div>'
        + f'<div><span>看空</span><strong>{_e(distribution.get("bearish"), "0")}</strong></div>'
        + f'<div><span>证据不足</span><strong>{_e(distribution.get("insufficient_evidence"), "0")}</strong></div>'
        + '</div>'
        + f'<div class="jury-grid">{cards_html}</div>'
        + '</section>'
    )


def _score_ratio(value: Any) -> float:
    number = _number(value)
    if number is None:
        return 0.0
    if number <= 1:
        return max(0.0, number)
    if number <= 10:
        return max(0.0, min(1.0, number / 10.0))
    return max(0.0, min(1.0, number / 100.0))


def _radar_svg(items: list[dict[str, Any]]) -> str:
    scored = [item for item in items if _number(item.get("score")) is not None]
    if len(scored) < 3:
        return '<div class="radar-empty">有效评分维度不足，未绘制雷达图</div>'
    scored = scored[:22]
    center = 150.0
    radius = 112.0

    def point(index: int, scale: float) -> tuple[float, float]:
        angle = -math.pi / 2 + 2 * math.pi * index / len(scored)
        return center + math.cos(angle) * radius * scale, center + math.sin(angle) * radius * scale

    grids = []
    for level in (0.25, 0.5, 0.75, 1.0):
        grids.append(" ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, level) for i in range(len(scored)))))
    axes = "".join(
        f'<line x1="150" y1="150" x2="{x:.1f}" y2="{y:.1f}" />'
        for x, y in (point(i, 1.0) for i in range(len(scored)))
    )
    values = " ".join(
        f"{x:.1f},{y:.1f}"
        for index, item in enumerate(scored)
        for x, y in [point(index, _score_ratio(item.get("score")))]
    )
    return (
        '<svg class="radar" viewBox="0 0 300 300" role="img" aria-label="UZI 维度评分雷达图">'
        + '<g class="radar-grid">'
        + "".join(f'<polygon points="{points}" />' for points in grids)
        + axes
        + '</g>'
        + f'<polygon class="radar-value" points="{values}" />'
        + '</svg>'
    )


def _render_dimensions(presentation: dict[str, Any]) -> str:
    rows = [_record(item) for item in _list(presentation.get("deep_scan"))]
    cards = []
    for row in rows:
        gaps = _list(row.get("data_gap"))
        sources = _list(row.get("source_refs"))
        cards.append(
            '<article class="dimension-card">'
            f'<header><span>{_e(row.get("dimension_id"), "DIM")}</span>{_badge(row.get("status"))}</header>'
            f'<h3>{_e(row.get("label"), _label(row.get("dimension_id")))}</h3>'
            f'<div class="dimension-score"><strong>{_metric(row.get("score"))}</strong><span>权重 {_metric(row.get("weight"))}</span></div>'
            f'<p>{_e(row.get("layer"), "source")} · {_e(row.get("source_type"), "未标记来源类型")} · {len(sources)} 个来源引用</p>'
            f'<details><summary>证据与缺口</summary>{_render_generic({"evidence": _list(row.get("evidence")), "data_gap": gaps, "source_refs": sources})}</details>'
            '</article>'
        )
    cards_html = "".join(cards) or '<p class="muted">本次没有生成维度覆盖 Artifact。</p>'
    return (
        _section("04", "全维深度扫描", "22-DIMENSION DEEP SCAN")
        + '<section class="scan-section" id="section-scan">'
        + f'<aside class="radar-panel">{_radar_svg(rows)}<strong>{len(rows)} 个维度进入报告</strong><span>缺失维度保持 data_gap，不按中性分处理。</span></aside>'
        + f'<div class="dimension-grid">{cards_html}</div>'
        + '</section>'
    )


def _render_modeling(presentation: dict[str, Any]) -> str:
    groups = [_record(item) for item in _list(presentation.get("institutional_modeling"))]
    blocks = []
    for group in groups:
        methods = []
        for raw_method in _list(group.get("methods")):
            method = _record(raw_method)
            methods.append(
                '<article class="model-card">'
                f'<header><div><span>{_e(method.get("method_id"), "METHOD")}</span><h3>{_e(method.get("label"), "机构模型")}</h3></div>{_badge(method.get("status"))}</header>'
                f'<p class="model-summary">{_e(method.get("summary"), "本次未形成模型摘要")}</p>'
                f'<details open><summary>关键输出与假设</summary>{_render_generic({"key_outputs": _record(method.get("key_outputs")), "assumptions": _record(method.get("assumptions")), "risk_flags": _list(method.get("risk_flags")), "data_gap": _list(method.get("data_gap"))})}</details>'
                f'<details><summary>敏感性与方法记录</summary><h4>敏感性分析</h4>{_render_record_table(_list(method.get("sensitivity")))}<h4>方法记录</h4>{_render_generic(_list(method.get("methodology_log")))}</details>'
                '</article>'
            )
        methods_html = "".join(methods) or '<p class="muted">本次未请求机构模型。</p>'
        blocks.append(
            '<section class="model-group">'
            f'<header><h3>{_e(group.get("symbol"), "目标标的")}</h3>{_badge(group.get("status"))}</header>'
            f'<div class="model-grid">{methods_html}</div>'
            f'{_render_generic({"data_gap": _list(group.get("data_gap"))})}</section>'
        )
    blocks_html = "".join(blocks) or '<p class="muted">本次没有机构建模产物。</p>'
    return _section("05", "机构级建模", "INSTITUTIONAL MODELING") + f'<section id="section-modeling">{blocks_html}</section>'


def _render_risks_and_zones(presentation: dict[str, Any]) -> str:
    risks = [_record(item) if isinstance(item, dict) else {"summary": item} for item in _list(presentation.get("risk_list"))]
    zones = [_record(item) for item in _list(presentation.get("buy_zones"))]
    risk_html = "".join(
        '<article class="risk-card">'
        f'<header>{_badge(row.get("severity") or row.get("status") or "attention")}<span>{_e(row.get("signal_id") or row.get("type"), "RISK")}</span></header>'
        f'<h3>{_e(row.get("title") or row.get("summary") or row.get("signal"), "需要复核的风险")}</h3>'
        f'<p>{_e(row.get("evidence") or row.get("reasoning") or row.get("recommendation"), "当前没有更多风险说明")}</p>'
        '</article>'
        for row in risks
    )
    zone_html = "".join(
        '<article class="zone-card">'
        f'<header><span>{_e(row.get("label"), "价格观察")}</span>{_badge(row.get("status"))}</header>'
        f'<strong>{_metric(row.get("price"))}</strong><p>{_e(row.get("rationale"), "仅作观察，不构成买入建议。")}</p>'
        '</article>'
        for row in zones
    )
    risk_html = risk_html or '<p class="muted">风险扫描未返回可展示信号。</p>'
    zone_html = zone_html or '<p class="muted">没有经验证的价格区间。</p>'
    return (
        _section("06", "风险与价格观察", "RISK CONTROL · OBSERVATION ZONES")
        + '<section class="risk-zone-layout" id="section-risks">'
        + f'<div><h3>风险清单</h3><div class="risk-grid">{risk_html}</div></div>'
        + f'<div id="section-zones"><h3>价格观察</h3><div class="zone-grid">{zone_html}</div></div>'
        + '</section>'
    )


def _render_evidence(report: dict[str, Any]) -> str:
    rows = [_record(item) for item in _list(report.get("evidence"))]
    cards = []
    for row in rows:
        metadata = _record(row.get("metadata"))
        cards.append(
            '<article class="evidence-card">'
            f'<header><span>{_e(row.get("evidence_id"), "EVIDENCE")}</span><strong>{_e(row.get("source_name"), "未知来源")}</strong></header>'
            f'<h3>{_e(row.get("title"), "证据记录")}</h3><p>{_e(row.get("summary"), "没有摘要")}</p>'
            f'<footer><span>{_e(row.get("published_at"), "时间未取得")}</span><span>置信度 {_metric((_number(row.get("confidence")) or 0) * 100, "%")}</span></footer>'
            f'<details><summary>来源追踪</summary>{_render_generic(metadata)}</details>'
            '</article>'
        )
    cards_html = "".join(cards) or '<p class="muted">报告未返回结构化 evidence。</p>'
    return _section("07", "证据档案", "EVIDENCE ARCHIVE") + f'<section class="evidence-grid" id="section-evidence">{cards_html}</section>'


def _render_artifact_appendix(artifacts: list[dict[str, Any]]) -> str:
    relevant = [
        artifact
        for artifact in artifacts
        if str(artifact.get("artifact_type") or "") in (_CORE_ARTIFACT_TYPES | _SPECIAL_METHOD_TYPES)
    ]
    inventory = []
    details = []
    detail_types = _SPECIAL_METHOD_TYPES | {"institutional_analysis", "research_workflow", "deep_analysis", "main_agent_synthesis"}
    for artifact in relevant:
        artifact_type = str(artifact.get("artifact_type") or "unknown")
        inventory.append(
            '<article class="artifact-inventory-card">'
            f'<header><strong>{_e(artifact_type)}</strong>{_badge(artifact.get("status"))}</header>'
            f'<code>{_e(artifact.get("artifact_id"))}</code>'
            '<div class="artifact-inventory-stats">'
            f'<span>版本 <strong>{_e(artifact.get("artifact_version"), "1")}</strong></span>'
            f'<span>置信度 <strong>{_metric((_number(artifact.get("confidence")) or 0) * 100, "%")}</strong></span>'
            f'<span>来源 <strong>{len(_list(artifact.get("source_refs")))}</strong></span>'
            '</div>'
            f'<div class="artifact-risk-flags"><span>风险标记</span>{_render_generic(_list(artifact.get("risk_flags")))}</div>'
            '</article>'
        )
        if artifact_type in detail_types:
            details.append(
                '<details class="artifact-detail">'
                f'<summary>{_e(artifact_type)} · {_e(artifact.get("artifact_id"))}</summary>'
                f'{_render_generic(_record(artifact.get("content")))}'
                f'<h4>来源与风险</h4>{_render_generic({"source_refs": _list(artifact.get("source_refs")), "risk_flags": _list(artifact.get("risk_flags")), "metadata": _record(artifact.get("metadata"))})}'
                '</details>'
            )
    inventory_html = (
        f'<div class="artifact-inventory">{"".join(inventory)}</div>'
        if inventory
        else '<p class="muted">没有识别到 UZI Artifact。</p>'
    )
    return _section("08", "ArtifactBoard 审计附录", "AUDITABLE DELIVERY") + f'<section class="appendix" id="section-audit">{inventory_html}{"".join(details)}</section>'


def _fallback_presentation(report: dict[str, Any]) -> dict[str, Any]:
    valuation = _record(report.get("valuation_model"))
    model = _record((_list(valuation.get("models")) or [{}])[0])
    panel = _record(report.get("investor_panel_review"))
    risk = _record(report.get("risk_scan"))
    symbol = _text(model.get("symbol"), "")
    return {
        "version": "artifact-fallback",
        "profile": _record(report.get("uzi_profile")).get("selected"),
        "subject": {"symbol": symbol, "name": symbol},
        "hero": {
            "symbol": symbol,
            "name": symbol,
            "price": model.get("market_price"),
            "score": panel.get("average_score"),
            "verdict": model.get("verdict"),
            "safety": risk.get("risk_level"),
        },
        "core_conclusion": report.get("conclusion"),
        "dashboard": {"price": {"current": model.get("market_price")}, "valuation": {"fair_value": model.get("fair_value"), "margin_of_safety": model.get("margin_of_safety")}},
        "great_divide": {"bull": {}, "bear": {}, "rounds": [], "punchline": _record(panel.get("disagreement")).get("summary")},
        "panel": panel,
        "deep_scan": [],
        "risk_list": _list(risk.get("signals")),
        "buy_zones": [],
        "institutional_modeling": [],
        "data_gap": _list(_record(report.get("uzi_dimension_coverage")).get("data_gap")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def render_uzi_native_html(
    artifacts: Iterable[dict[str, Any]],
    *,
    run_id: str,
) -> RenderedUziReport:
    rows = [dict(item) for item in artifacts if isinstance(item, dict)]
    report_artifact = _latest(rows, "research_report")
    presentation_artifact = _latest(rows, "report_presentation")
    if report_artifact is None:
        raise ReportArtifactMissingError("research_report artifact is unavailable")
    report = _record(report_artifact.get("content"))
    presentation_content = _record(presentation_artifact.get("content")) if presentation_artifact is not None else {}
    is_uzi_presentation = str(presentation_content.get("theme") or "").startswith("uzi_")
    if not _record(report.get("uzi_profile")) and not is_uzi_presentation:
        raise ReportArtifactMissingError("the run does not contain a UZI report")
    presentation = (
        presentation_content
        if presentation_artifact is not None
        else _fallback_presentation(report)
    )
    subject = _record(presentation.get("subject"))
    hero = _record(presentation.get("hero"))
    symbol = _text(subject.get("symbol") or hero.get("symbol"), "unknown")
    safe_symbol = re.sub(r"[^A-Za-z0-9._-]", "-", symbol).strip("-") or "unknown"
    body = "".join(
        (
            _render_hero(presentation, report, run_id),
            _render_gap_banner(presentation),
            _render_dashboard(presentation),
            _render_divide(presentation),
            _render_panel(presentation),
            _render_dimensions(presentation),
            _render_modeling(presentation),
            _render_risks_and_zones(presentation),
            _render_evidence(report),
            _render_artifact_appendix(rows),
        )
    )
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    generated_at = _text(presentation.get("generated_at"), datetime.now(timezone.utc).isoformat())
    replacements = {
        "{{REPORT_TITLE}}": _e(f"{_text(hero.get('name') or subject.get('name'), symbol)} UZI 研究报告"),
        "{{RUN_ID}}": _e(run_id),
        "{{SYMBOL}}": _e(symbol),
        "{{PROFILE}}": _e(presentation.get("profile"), "unknown"),
        "{{GENERATED_AT}}": _e(generated_at),
        "{{SOURCE_COMMIT}}": _e(_UZI_SOURCE_COMMIT),
        "{{REPORT_BODY}}": body,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    unresolved = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", template)))
    if unresolved:
        raise RuntimeError(f"unresolved UZI report template markers: {', '.join(unresolved)}")
    source_ids = tuple(
        str(artifact.get("artifact_id"))
        for artifact in rows
        if artifact.get("artifact_id") and str(artifact.get("artifact_type") or "") in (_CORE_ARTIFACT_TYPES | _SPECIAL_METHOD_TYPES)
    )
    return RenderedUziReport(
        html=template,
        filename_stem=f"uzi-report-{safe_symbol}-{run_id}",
        symbol=symbol,
        source_artifact_ids=source_ids,
    )


def render_uzi_native_pdf(html_document: str) -> bytes:
    """Render the canonical HTML to PDF using the bundled Chromium runtime."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - guarded by deployment requirements
        raise ReportPdfUnavailableError("Playwright is not installed") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                page.set_content(html_document, wait_until="load", timeout=30_000)
                page.evaluate("() => document.fonts.ready")
                page.locator("details").evaluate_all(
                    "elements => elements.forEach(element => { element.open = true })"
                )
                page.emulate_media(media="print")
                payload = page.pdf(
                    print_background=True,
                    prefer_css_page_size=True,
                    tagged=True,
                    outline=True,
                )
            finally:
                browser.close()
    except (PlaywrightError, OSError) as exc:
        raise ReportPdfUnavailableError("Chromium PDF renderer is unavailable") from exc
    if not payload.startswith(b"%PDF"):
        raise ReportPdfUnavailableError("Chromium returned an invalid PDF payload")
    return payload


__all__ = [
    "ReportArtifactMissingError",
    "ReportPdfUnavailableError",
    "RenderedUziReport",
    "render_uzi_native_html",
    "render_uzi_native_pdf",
]
