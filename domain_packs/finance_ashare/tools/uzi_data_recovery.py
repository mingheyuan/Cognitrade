from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from research_system.adapters.vendors.uzi_qualitative_fallback import UziQualitativeFallback
from research_system.adapters.vendors.uzi_browser_adapter import UziBrowserAdapter
from research_system.runtime.platform.settings import Settings


# UZI 3.9.2 data_integrity.py checks, projected onto Cognitrade's canonical
# feature names.  Criticality and dimension ownership intentionally match the
# upstream contract; source-specific field aliases stay in build_features().
INTEGRITY_CHECKS: tuple[tuple[str, str, str, str, bool], ...] = (
    # UZI dimension id, canonical feature, UZI source path, label, critical.
    ("0_basic", "name", "name", "公司名称", True),
    ("0_basic", "price", "price", "当前股价", True),
    ("0_basic", "industry", "industry", "所属行业", True),
    ("0_basic", "market_cap_yi", "market_cap", "总市值", True),
    ("0_basic", "pe_ttm", "pe_ttm", "PE-TTM", False),
    ("0_basic", "pb", "pb", "PB", False),
    ("1_financials", "roe_history", "roe_history", "ROE 历史", True),
    ("1_financials", "revenue_history", "revenue_history", "营收历史", False),
    ("1_financials", "net_profit_history", "net_profit_history", "净利历史", False),
    ("1_financials", "financial_health", "financial_health", "财务健康度", False),
    ("2_kline", "stage_num", "stage", "K 线阶段", True),
    ("2_kline", "ma_bull_aligned", "ma_align", "均线多空", False),
    ("2_kline", "macd", "macd", "MACD", False),
    ("10_valuation", "pe", "pe", "PE", False),
    ("10_valuation", "pe_quantile_5y", "pe_quantile", "PE 5 年分位", False),
    ("10_valuation", "pb_quantile_5y", "pb_quantile", "PB 5 年分位", False),
    ("7_industry", "industry_growth", "growth", "行业增速", False),
    ("14_moat", "moat_total", "scores", "护城河评分", False),
)

ENRICHMENT_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("macro", "3_macro", "宏观周期"),
    ("peers", "4_peers", "同业对标"),
    ("chain", "5_chain", "上下游"),
    ("research", "6_research", "券商研报"),
    ("industry", "7_industry", "行业景气"),
    ("materials", "8_materials", "原材料"),
    ("futures", "9_futures", "期货关联"),
    ("governance", "11_governance", "治理/减持"),
    ("capital_flow", "12_capital_flow", "北向/两融"),
    ("policy", "13_policy", "政策环境"),
    ("moat", "14_moat", "护城河"),
    ("events", "15_events", "事件驱动"),
    ("lhb", "16_lhb", "龙虎榜/游资"),
    ("sentiment", "17_sentiment", "大V舆情"),
    ("trap", "18_trap", "杀猪盘"),
    ("contests", "19_contests", "实盘比赛"),
)

_FIELD_RECOVERY_HINTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("0_basic", "name"): ("mx: '{code} 公司简称'", "browser: https://xueqiu.com/S/{code_raw}", "ws: '{code} 公司简介'"),
    ("0_basic", "price"): ("mx: '{code} 最新价'", "browser: https://xueqiu.com/S/{code_raw}", "ws: '{code} 股价 2026'"),
    ("0_basic", "industry"): ("mx: '{code} 所属申万行业'", "browser: https://xueqiu.com/S/{code_raw}/F10", "ws: '{code} 所属行业'"),
    ("0_basic", "market_cap"): ("mx: '{code} 总市值'", "browser: https://quote.eastmoney.com/{eastmoney_code}.html", "ws: '{code} 市值'"),
    ("0_basic", "pe_ttm"): ("mx: '{code} 市盈率TTM'", "browser: https://xueqiu.com/S/{code_raw}", "infer: 市值 / 归母净利润"),
    ("0_basic", "pb"): ("mx: '{code} 市净率'", "browser: https://xueqiu.com/S/{code_raw}"),
    ("1_financials", "roe_history"): ("mx: '{code} 最近5年ROE'", "browser: https://xueqiu.com/S/{code_raw}/F10/main"),
    ("1_financials", "revenue_history"): ("mx: '{code} 最近5年营业收入'", "browser: https://xueqiu.com/S/{code_raw}/F10/main"),
    ("1_financials", "net_profit_history"): ("mx: '{code} 最近5年净利润'", "browser: https://xueqiu.com/S/{code_raw}/F10/main"),
    ("1_financials", "financial_health"): ("infer: 从 ROE/debt_ratio/fcf 综合判断", "mx: '{code} 财务健康度'"),
    ("2_kline", "stage"): ("infer: 从 ma20/ma60 多空排列推断 Wyckoff stage", "browser: https://xueqiu.com/S/{code_raw}"),
    ("10_valuation", "pe"): ("mx: '{code} 市盈率'", "browser: https://xueqiu.com/S/{code_raw}"),
    ("10_valuation", "pe_quantile"): ("mx: '{code} PE 5年分位数'", "ws: '{code} PE历史分位'"),
    ("10_valuation", "pb_quantile"): ("mx: '{code} PB 5年分位数'", "ws: '{code} PB历史分位'"),
    ("7_industry", "growth"): ("mx: '{industry} 行业增速 2026'", "ws: '{industry} 行业规模 增速 2026'"),
    ("14_moat", "scores"): ("agent: Porter 5 Forces + web search 护城河评分",),
}

_ENRICHMENT_HINTS: dict[str, tuple[str, ...]] = {
    "3_macro": ("ws: '中国 {industry} 宏观环境 利率 2026'",),
    "4_peers": ("mx: '{industry} 同行业公司 市值排名'", "ws: '{name} 同行业竞争者 对比'"),
    "5_chain": ("browser: https://xueqiu.com/S/{code_raw}/F10", "ws: '{name} 上下游产业链'"),
    "6_research": ("mx: '{code} 券商研报 目标价'", "ws: '{name} 最新研报 2026'"),
    "7_industry": ("mx: '{industry} 行业规模 TAM'", "ws: '{industry} 行业景气 2026'"),
    "8_materials": ("ws: '{name} 原材料 成本构成'",),
    "9_futures": ("ws: '{industry} 期货 相关品种'",),
    "11_governance": ("mx: '{code} 股东结构 高管减持'", "browser: https://quote.eastmoney.com/{eastmoney_code}.html"),
    "12_capital_flow": ("mx: '{code} 北向持仓 融资融券'", "browser: https://data.eastmoney.com/zlsj/{code_raw}.html"),
    "13_policy": ("ws: '{industry} 最新政策 2026'",),
    "14_moat": ("ws: '{name} 核心竞争力 技术壁垒 市场份额'",),
    "15_events": ("mx: '{code} 最新公告'", "ws: '{name} {code} 最新公告 中标 研发 2026'"),
    "16_lhb": ("mx: '{code} 龙虎榜'", "browser: https://data.eastmoney.com/stock/lhb/{code_raw}.html"),
    "17_sentiment": ("browser: https://xueqiu.com/S/{code_raw}", "ws: 'site:xueqiu.com {code}'"),
    "18_trap": ("infer: 从龙虎榜+换手率+涨跌幅综合判断",),
    "19_contests": ("ws: '{code} 实盘比赛 持仓'",),
}

_QUALITATIVE_TARGETS: dict[str, dict[str, Any]] = {
    "macro": {
        "query": "{industry} 2026 宏观环境 利率周期 汇率 大宗商品 行业影响",
        "field": "rate_cycle",
    },
    "industry": {
        "query": "{industry} 2026 行业增速 TAM 市场规模 渗透率",
        "field": "growth",
    },
    "materials": {
        "query": "{name} {code} 主营业务 主要原材料 成本构成",
        "field": "core_material",
    },
    "futures": {
        "query": "{industry} 行业 上下游 期货品种 套保 大宗",
        "field": "contract_trend",
    },
    "policy": {
        "query": "{industry} 2026 国家政策 监管动态 补贴 税收 影响",
        "field": "snippets.policy_dir",
    },
    "events": {
        "query": "{name} {code} 最新公告 重大事件 业绩 合同",
        "field": "event_timeline",
    },
}

_PLAYWRIGHT_PROFILE_DIMS = {
    "lite": frozenset(),
    "medium": frozenset({"peers", "materials", "events", "sentiment", "industry", "moat"}),
    "deep": frozenset({
        "peers", "materials", "events", "sentiment", "industry", "moat",
        "macro", "policy", "trap", "contests",
    }),
}

_PLAYWRIGHT_STRATEGIES: dict[str, tuple[str, ...]] = {
    "peers": ("xueqiu_f10",),
    "materials": ("em_data",),
    "events": ("cninfo",),
    "sentiment": ("xueqiu_posts",),
    "macro": ("stats_gov",),
    "industry": ("baidu_search",),
    "moat": ("baidu_baike",),
    "policy": ("csrc",),
    "trap": ("xiaohongshu",),
    "contests": ("xueqiu_contests",),
}

_SNAPSHOT_TO_UZI_DIM = {
    "market": "0_basic",
    "fundamental": "1_financials",
    "kline": "2_kline",
    **{snapshot_type: dimension for snapshot_type, dimension, _label in ENRICHMENT_DIMENSIONS},
    "valuation": "10_valuation",
}


def assess_integrity(snapshots: list[dict[str, Any]], symbols: Iterable[str]) -> dict[str, Any]:
    from domain_packs.finance_ashare.subagents.uzi_methodology import build_features

    reports = []
    for symbol in dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()):
        features = build_features(snapshots, symbol)
        missing_critical: list[dict[str, str]] = []
        missing_optional: list[dict[str, str]] = []
        passed = 0
        for dimension, field, uzi_path, label, critical in INTEGRITY_CHECKS:
            value = _integrity_value(features, field)
            if _missing(value):
                row = {"dim": dimension, "path": uzi_path, "canonical_field": field, "label": label}
                (missing_critical if critical else missing_optional).append(row)
            else:
                passed += 1
        missing_enrichment = []
        for snapshot_type, dimension, label in ENRICHMENT_DIMENSIONS:
            snapshot = _find_snapshot(snapshots, snapshot_type, symbol)
            if not _snapshot_has_content(snapshot):
                missing_enrichment.append({"dim": dimension, "snapshot_type": snapshot_type, "label": label})
        coverage = round(passed / len(INTEGRITY_CHECKS) * 100, 0) if INTEGRITY_CHECKS else 0
        report = {
            "symbol": symbol,
            "ok": not missing_critical and len(missing_enrichment) < 7,
            "critical_missing": bool(missing_critical),
            "missing_critical": missing_critical,
            "missing_optional": missing_optional,
            "missing_enrichment": missing_enrichment,
            "fallback_dims": _fallback_dimensions(snapshots, symbol),
            "coverage_pct": coverage,
            "passed_checks": passed,
            "total_checks": len(INTEGRITY_CHECKS),
        }
        report["recovery_tasks"] = generate_recovery_tasks(report, features)
        reports.append(report)
    return {
        "schema_version": "uzi-3.9.2-agent-os-v1",
        "symbols": reports,
        "ok": bool(reports) and all(report["ok"] for report in reports),
        "critical_missing": any(report["critical_missing"] for report in reports),
        "coverage_pct": round(
            sum(float(report["coverage_pct"]) for report in reports) / len(reports),
            1,
        ) if reports else 0.0,
        "recovery_task_count": sum(len(report["recovery_tasks"]) for report in reports),
    }


def generate_recovery_tasks(report: dict[str, Any], features: dict[str, Any]) -> list[dict[str, Any]]:
    context = {
        "code": str(features.get("ticker") or features.get("symbol") or report.get("symbol") or ""),
        "name": str(features.get("name") or report.get("symbol") or ""),
        "industry": str(features.get("industry") or "综合"),
    }
    context["code_raw"] = context["code"].split(".")[0]
    market_suffix = context["code"].upper()
    context["eastmoney_code"] = (
        ("1." if market_suffix.endswith(".SH") or context["code_raw"].startswith(("5", "6", "9")) else "0.")
        + context["code_raw"]
        if context["code_raw"]
        else ""
    )
    tasks: list[dict[str, Any]] = []
    critical_rows = list(report.get("missing_critical") or [])
    for row in [*critical_rows, *list(report.get("missing_optional") or [])]:
        hints = _FIELD_RECOVERY_HINTS.get(
            (str(row.get("dim")), str(row.get("path"))),
            ("ws: '{name} " + str(row.get("label") or "") + "'",),
        )
        tasks.append({
            "dim": row.get("dim"),
            "field": row.get("path"),
            "canonical_field": row.get("canonical_field"),
            "label": row.get("label"),
            "severity": "critical" if row in critical_rows else "optional",
            "suggested_actions": [_render_hint(hint, context) for hint in hints],
            "status": "pending",
        })
    for row in list(report.get("missing_enrichment") or []):
        hints = _ENRICHMENT_HINTS.get(str(row.get("dim")), ("ws: '{name} " + str(row.get("label") or "") + "'",))
        tasks.append({
            "dim": row.get("dim"),
            "snapshot_type": row.get("snapshot_type"),
            "field": "_entire_dim",
            "label": row.get("label"),
            "severity": "enrichment",
            "suggested_actions": [_render_hint(hint, context) for hint in hints],
            "status": "pending",
        })
    return tasks


def recover_qualitative_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    query: str,
    symbols: Iterable[str],
    profile: str | None,
    settings: Settings,
) -> dict[str, Any]:
    """Apply UZI's six-dimension real fallback and preserve every attempt."""

    selected_profile = _require_profile(profile)
    if selected_profile == "lite":
        return {
            "schema_version": "uzi-3.9.2-agent-os-v1",
            "profile": selected_profile,
            "enabled": False,
            "order": ["mx_api", "ddgs"],
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "profile_ddgs_budget_zero",
        }
    fallback = UziQualitativeFallback(settings)
    attempts: list[dict[str, Any]] = []
    resolved_symbols = list(dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()))
    for symbol in resolved_symbols:
        identity = _identity(snapshots, symbol)
        context = {
            "code": symbol,
            "name": identity["name"],
            "industry": identity["industry"] or "综合",
        }
        for snapshot_type, target in _QUALITATIVE_TARGETS.items():
            snapshot = _find_snapshot(snapshots, snapshot_type, symbol)
            if snapshot is None or not _qualitative_field_missing(snapshot, str(target["field"])):
                continue
            rendered_query = str(target["query"]).format(**context)
            result = fallback.collect(
                query=rendered_query,
                dimension=snapshot_type,
                symbol=symbol,
            )
            _apply_recovery(snapshot, snapshot_type, result)
            attempts.append({
                "symbol": symbol,
                "snapshot_type": snapshot_type,
                "status": result["status"],
                "source_id": result.get("source_id"),
                "query": rendered_query,
                "source_trace": list(result.get("source_trace") or []),
            })
    return {
        "schema_version": "uzi-3.9.2-agent-os-v1",
        "profile": selected_profile,
        "enabled": True,
        "order": ["mx_api", "ddgs"],
        "attempted": len(attempts),
        "succeeded": sum(row["status"] == "available" for row in attempts),
        "failed": sum(row["status"] != "available" for row in attempts),
        "attempts": attempts,
    }


def recover_playwright_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    query: str,
    symbols: Iterable[str],
    profile: str | None,
    settings: Settings,
) -> dict[str, Any]:
    """Run UZI 3.9.2's profile-scoped final browser fallback stage."""

    selected_profile = _require_profile(profile)
    enabled_dimensions = _PLAYWRIGHT_PROFILE_DIMS.get(selected_profile, frozenset())
    attempts: list[dict[str, Any]] = []
    if not enabled_dimensions:
        return {
            "schema_version": "uzi-3.9.2-agent-os-v1",
            "profile": selected_profile,
            "enabled": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "profile_playwright_mode_off",
        }
    if not settings.browser_sources_enabled or not settings.sensitive_sources_enabled:
        return {
            "schema_version": "uzi-3.9.2-agent-os-v1",
            "profile": selected_profile,
            "enabled": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "sensitive_browser_policy_disabled",
        }
    browser = UziBrowserAdapter(settings)
    resolved_symbols = list(dict.fromkeys(str(item).strip() for item in symbols if str(item).strip()))
    for symbol in resolved_symbols:
        identity = _identity(snapshots, symbol)
        for snapshot_type in sorted(enabled_dimensions):
            snapshot = _find_snapshot(snapshots, snapshot_type, symbol)
            if snapshot is None:
                continue
            needs, reason = _snapshot_needs_browser(snapshot)
            if not needs:
                continue
            browser_query = _browser_query(snapshot_type, query, symbol, identity)
            result = browser.collect_many(
                source_ids=_PLAYWRIGHT_STRATEGIES[snapshot_type],
                symbol=symbol,
                query=browser_query,
            )
            evidence = dict(result.get("evidence") or {})
            projected = _project_browser_evidence(snapshot_type, evidence, identity)
            success = bool(projected)
            trace = list(result.get("source_trace") or [])
            if evidence and not projected:
                trace.append({
                    "source_id": "playwright",
                    "strategy_id": snapshot_type,
                    "status": "parse_failed",
                    "reason": "uzi_strategy_parser_returned_no_structured_fields",
                })
            _apply_browser_recovery(snapshot, evidence, projected, trace, reason)
            attempts.append({
                "symbol": symbol,
                "snapshot_type": snapshot_type,
                "status": "available" if success else "data_gap",
                "reason": reason,
                "source_ids": list(_PLAYWRIGHT_STRATEGIES[snapshot_type]),
                "source_trace": list(result.get("source_trace") or []),
            })
    return {
        "schema_version": "uzi-3.9.2-agent-os-v1",
        "profile": selected_profile,
        "enabled": True,
        "attempted": len(attempts),
        "succeeded": sum(row["status"] == "available" for row in attempts),
        "failed": sum(row["status"] != "available" for row in attempts),
        "attempts": attempts,
        "disabled_reason": None,
    }


def _require_profile(profile: str | None) -> str:
    selected = str(profile or "").strip().lower()
    if selected not in _PLAYWRIGHT_PROFILE_DIMS:
        raise ValueError("research_profile is required; choose lite, medium or deep")
    return selected


def _apply_recovery(snapshot: dict[str, Any], snapshot_type: str, result: dict[str, Any]) -> None:
    facts = snapshot.setdefault("facts", {})
    trace = list(snapshot.get("source_trace") or [])
    trace.extend(list(result.get("source_trace") or []))
    snapshot["source_trace"] = trace
    facts["_uzi_recovery"] = {
        "query": result.get("query"),
        "source": result.get("source_id"),
        "status": result.get("status"),
    }
    if result.get("status") != "available":
        gaps = list(facts.get("data_gap") or [])
        gap = f"{snapshot_type}_autofill_failed"
        facts["data_gap"] = list(dict.fromkeys([*gaps, gap]))
        has_primary_content = any(
            not _missing(value)
            for key, value in facts.items()
            if not str(key).startswith("_") and key not in {"availability", "data_gap", "message", "degrade_reason"}
        )
        facts["availability"] = "degraded" if has_primary_content else "data_gap"
        snapshot["degraded"] = True
        snapshot["quality_status"] = "degraded" if has_primary_content else "data_gap"
        snapshot["degrade_reason"] = gap
        return
    text = str(result.get("text") or "")
    facts.setdefault("_autofill", {})
    facts["_autofill"].update({
        "query": result.get("query"),
        "snippet": text,
        "source": result.get("source_id"),
    })
    if snapshot_type == "macro":
        facts["rate_cycle"] = text[:80] + ("…" if len(text) > 80 else "")
    elif snapshot_type == "industry":
        facts["growth"] = text[:80] + ("…" if len(text) > 80 else "")
    elif snapshot_type == "materials":
        facts["core_material"] = text[:60] + ("…" if len(text) > 60 else "")
    elif snapshot_type == "futures":
        facts["contract_trend"] = text[:60] + ("…" if len(text) > 60 else "")
    elif snapshot_type == "policy":
        facts.setdefault("snippets", {}).setdefault("policy_dir", []).append({
            "title": text[:120],
            "url": "",
            "source": result.get("source_id"),
        })
    elif snapshot_type == "events":
        facts["event_timeline"] = [text[:120]]
    if result.get("results"):
        facts["fallback_search_results"] = deepcopy(result["results"])
    if result.get("browser_evidence"):
        facts["browser_evidence"] = deepcopy(result["browser_evidence"])
    facts["availability"] = "degraded"
    facts["data_gap"] = [
        gap
        for gap in list(facts.get("data_gap") or [])
        if gap != f"{snapshot_type}_autofill_failed"
    ]
    snapshot["fallback_applied"] = True
    snapshot["degraded"] = True
    snapshot["quality_status"] = "degraded"
    snapshot["degrade_reason"] = f"{snapshot_type}_recovered_via_{result.get('source_id')}"


def _apply_browser_recovery(
    snapshot: dict[str, Any],
    evidence: dict[str, Any],
    projected: dict[str, Any],
    trace: list[dict[str, Any]],
    reason: str,
) -> None:
    facts = snapshot.setdefault("facts", {})
    snapshot["source_trace"] = [*list(snapshot.get("source_trace") or []), *trace]
    facts["_uzi_playwright_recovery"] = {
        "status": "available" if projected else "data_gap",
        "trigger_reason": reason,
        "source_ids": list(evidence),
        "projected_fields": list(projected),
    }
    if not projected:
        return
    facts["browser_evidence"] = {
        **dict(facts.get("browser_evidence") or {}),
        **deepcopy(evidence),
    }
    facts.update(deepcopy(projected))
    facts["availability"] = "degraded"
    snapshot["fallback_applied"] = True
    snapshot["degraded"] = True
    snapshot["quality_status"] = "degraded"
    snapshot["degrade_reason"] = "recovered_via_playwright"


def _project_browser_evidence(
    snapshot_type: str,
    evidence: dict[str, Any],
    identity: dict[str, str],
) -> dict[str, Any]:
    """Project browser DOM evidence into UZI 3.9.2 strategy result keys.

    Raw excerpts and links remain attached separately for audit.  A browser
    page only counts as a successful recovery when its dimension parser
    produces a real structured field, matching upstream Playwright behavior.
    """

    items = [item for item in evidence.values() if isinstance(item, dict)]
    text = " ".join(str(item.get("text_excerpt") or "") for item in items).strip()
    links = [
        link
        for item in items
        for link in list(item.get("links") or [])
        if isinstance(link, dict)
    ]
    titles = list(dict.fromkeys(
        str(link.get("text") or "").strip()
        for link in links
        if len(str(link.get("text") or "").strip()) >= 5
    ))

    if snapshot_type == "peers":
        peers = []
        seen = set()
        for link in links:
            match = re.search(r"/S/((?:SH|SZ|BJ)\d{6}|HK\d{5})", str(link.get("url") or ""), re.I)
            name = str(link.get("text") or "").strip()
            if not match or len(name) < 2:
                continue
            raw = match.group(1).upper()
            code = f"{raw[2:]}.{raw[:2]}"
            if code in seen:
                continue
            seen.add(code)
            peers.append({"name": name[:20], "code": code})
            if len(peers) >= 20:
                break
        return {"peer_table_playwright": peers} if peers else {}

    if snapshot_type == "materials":
        match = re.search(r"主营业务[：:]?\s*([^。；\n]{5,200})", text)
        return {"core_business_playwright": match.group(1).strip()[:200]} if match else {}

    if snapshot_type == "events":
        event_titles = [title for title in titles if any(word in title for word in ("公告", "报告", "业绩", "合同", "中标"))][:10]
        return {"event_titles_playwright": event_titles} if event_titles else {}

    if snapshot_type == "sentiment":
        posts = titles[:8]
        return {"xueqiu_posts_playwright": posts} if posts else {}

    if snapshot_type == "macro":
        headlines = [title for title in titles if len(title) >= 10][:10]
        return {"macro_headlines_playwright": headlines} if headlines else {}

    if snapshot_type == "industry":
        result: dict[str, Any] = {}
        if titles:
            result["baidu_search_titles_playwright"] = titles[:10]
        if text:
            result["baidu_search_descs_playwright"] = [text[:500]]
        return result

    if snapshot_type == "moat":
        if len(text) < 5:
            return {}
        return {"baike_intro_playwright": text[:500], "baike_basic_info_playwright": {}}

    if snapshot_type == "policy":
        policy_titles = [title for title in titles if len(title) >= 10][:15]
        return {"csrc_policy_titles_playwright": policy_titles} if policy_titles else {}

    if snapshot_type == "trap":
        name = identity.get("name") or ""
        trap_titles = [
            title for title in titles
            if (name and name in title) or "推荐" in title or "老师" in title
        ][:10]
        return {
            "xhs_trap_post_count_playwright": len(trap_titles),
            "xhs_trap_titles_playwright": trap_titles,
        } if trap_titles else {}

    if snapshot_type == "contests":
        rows = []
        for name, gain in re.findall(r"([^\s%]{3,40})\s+(-?\d+(?:\.\d+)?)%", text):
            rows.append({"name": name, "total_gain_pct": float(gain)})
            if len(rows) >= 10:
                break
        return {"xueqiu_contest_top10_playwright": rows} if rows else {}

    return {}


def _integrity_value(features: dict[str, Any], field: str) -> Any:
    if field == "roe_history":
        return features.get("roe_5y_min")
    if field == "revenue_history":
        return features.get("revenue_growth_3y_cagr")
    if field == "net_profit_history":
        return features.get("net_profit_growth_3y")
    if field == "financial_health":
        return next(
            (features.get(key) for key in ("debt_ratio", "ocf_to_net_income_ratio", "roe") if features.get(key) is not None),
            None,
        )
    return features.get(field)


def _find_snapshot(snapshots: list[dict[str, Any]], snapshot_type: str, symbol: str) -> dict[str, Any] | None:
    global_candidate = None
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or snapshot.get("snapshot_type") != snapshot_type:
            continue
        row_symbol = str(snapshot.get("symbol") or "").strip()
        if row_symbol == symbol:
            return snapshot
        if not row_symbol:
            global_candidate = snapshot
    return global_candidate


def _snapshot_has_content(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("degraded") and not snapshot.get("fallback_applied"):
        return False
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    status = str(facts.get("availability") or "").lower()
    if status in {"data_gap", "degraded", "no_data", "provider_unavailable", "rate_limited", "parse_failed"}:
        return False
    return any(
        not _missing(value)
        for key, value in facts.items()
        if key not in {"availability", "data_gap", "message", "degrade_reason"}
    )


def _fallback_dimensions(snapshots: list[dict[str, Any]], symbol: str) -> list[dict[str, str]]:
    result = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        row_symbol = str(snapshot.get("symbol") or "")
        if row_symbol not in {"", symbol}:
            continue
        if snapshot.get("fallback_applied"):
            facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
            result.append({
                "dim": _SNAPSHOT_TO_UZI_DIM.get(
                    str(snapshot.get("snapshot_type") or ""),
                    str(snapshot.get("snapshot_type") or ""),
                ),
                "snapshot_type": str(snapshot.get("snapshot_type") or ""),
                "reason": str((facts.get("_uzi_recovery") or {}).get("source") or "provider_fallback"),
            })
    return result


def _identity(snapshots: list[dict[str, Any]], symbol: str) -> dict[str, str]:
    name = symbol
    industry = ""
    for snapshot in snapshots:
        if str(snapshot.get("symbol") or "") != symbol:
            continue
        facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
        info = facts.get("info") if isinstance(facts.get("info"), dict) else {}
        name = str(info.get("股票简称") or info.get("名称") or info.get("name") or name)
        industry = str(
            facts.get("industry")
            or info.get("行业")
            or info.get("所属行业")
            or info.get("industry")
            or industry
        )
    return {"name": name, "industry": industry}


def _qualitative_field_missing(snapshot: dict[str, Any], field: str) -> bool:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    if facts.get("_uzi_recovery"):
        return False
    current: Any = facts
    for part in field.split("."):
        if not isinstance(current, dict):
            return True
        current = current.get(part)
    if field == "event_timeline":
        return _missing(current) and all(
            _missing(facts.get(key))
            for key in ("event_notices", "recent_news", "recent_notices", "news_multi_source")
        )
    return _missing(current) or (field == "contract_trend" and "无直接" in str(current))


def _snapshot_needs_browser(snapshot: dict[str, Any]) -> tuple[bool, str]:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    if snapshot.get("degraded"):
        return True, "snapshot_degraded"
    public = {
        key: value
        for key, value in facts.items()
        if not str(key).startswith("_")
        and key not in {"availability", "data_gap", "message", "degrade_reason"}
    }
    if not public:
        return True, "facts_empty"
    valid = sum(not _missing(value) for value in public.values())
    quality = valid / len(public)
    return (True, f"valid_field_ratio={quality:.2f}") if quality < 0.5 else (False, f"valid_field_ratio={quality:.2f}")


def _browser_query(snapshot_type: str, query: str, symbol: str, identity: dict[str, str]) -> str:
    name = identity.get("name") or symbol
    industry = identity.get("industry") or "综合"
    return {
        "industry": f"{industry} 行业景气度 增速 市场规模",
        "moat": name,
        "trap": f"{name} 老师 推荐",
        "policy": f"{industry} 监管 政策",
    }.get(snapshot_type, query or f"{name} {symbol}")


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in {"", "—", "-", "N/A", "None", "0", "0.0"}:
        return True
    return isinstance(value, (list, dict)) and not value


def _render_hint(template: str, context: dict[str, str]) -> str:
    try:
        return template.format(**context)
    except (KeyError, IndexError, ValueError):
        return template


__all__ = [
    "ENRICHMENT_DIMENSIONS",
    "INTEGRITY_CHECKS",
    "assess_integrity",
    "generate_recovery_tasks",
    "recover_qualitative_snapshots",
    "recover_playwright_snapshots",
]
