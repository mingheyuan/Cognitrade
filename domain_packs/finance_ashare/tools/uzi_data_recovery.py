from __future__ import annotations

import importlib
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any

from research_system.adapters.vendors.mx_api_adapter import MxApiAdapter
from research_system.adapters.vendors.uzi_browser_adapter import UziBrowserAdapter
from research_system.adapters.vendors.uzi_qualitative_fallback import (
    is_junk_fallback_text,
)
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter
from research_system.runtime.platform.settings import Settings

# UZI 3.9.4 data_integrity.py checks, projected onto Cognitrade's canonical
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
        # UZI 3.9.4 deliberately removed the industry name here.  Including
        # it caused industry outlook snippets to be mistaken for macro data.
        "query": "2026 中国 利率 货币政策 降息 汇率 大宗商品 宏观环境",
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

# HARD-GATE-QUALITATIVE is an Agent-stage contract, separate from Stage 1's
# empty-field autofill.  The three groups and browser pages below are the
# DomainPack execution boundary for the exact UZI 3.9.4 task2.5 prompts.
_QUALITATIVE_AGENT_GROUPS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("A · Macro-Policy", ("macro", "policy")),
    ("B · Industry-Events", ("industry", "events")),
    ("C · Cost-Transmission", ("materials", "futures")),
)
_QUALITATIVE_AGENT_BROWSER_STRATEGIES: dict[str, tuple[str, ...]] = {
    "macro": ("stats_gov", "em_pmi"),
    "policy": ("gov_policy", "csrc", "miit_policy"),
    "industry": ("xueqiu_industry", "em_board", "ths_f10"),
    "events": ("cninfo", "xueqiu_announcements"),
    "materials": ("em_data", "ppi100", "em_futures"),
    "futures": ("em_futures", "shfe"),
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
_UZI_DIM_TO_SNAPSHOT = {dimension: snapshot for snapshot, dimension in _SNAPSHOT_TO_UZI_DIM.items()}
_BROWSER_SOURCES_BY_DIM: dict[str, tuple[str, ...]] = {
    "0_basic": ("xueqiu_f10", "stockstar"),
    "1_financials": ("xueqiu_f10", "ths_f10"),
    "2_kline": ("xueqiu_f10",),
    "4_peers": ("xueqiu_f10",),
    "5_chain": ("xueqiu_f10", "em_data"),
    "11_governance": ("em_data", "ths_f10"),
    "12_capital_flow": ("em_data",),
    "16_lhb": ("em_data",),
    "17_sentiment": ("xueqiu_posts",),
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
        "schema_version": "uzi-3.9.4-agent-os-v1",
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


def recover_suggested_fixes_until_exhausted(
    snapshots: list[dict[str, Any]],
    *,
    query: str,
    symbols: Iterable[str],
    profile: str | None,
    settings: Settings,
    cancellation: Any | None = None,
) -> dict[str, Any]:
    """Execute every UZI recovery hint until it resolves or is exhausted.

    The loop has no round counter.  Its only terminal conditions are success,
    cancellation, or exhaustion of the finite ``(symbol, dim, field, action)``
    worklist generated by the pinned UZI 3.9.4 integrity module.  Numeric and
    factual values are never parsed from unstructured text; qualitative search
    evidence is stored with an explicit inference disclosure.
    """

    selected_profile = _require_profile(profile)
    resolved_symbols = list(dict.fromkeys(
        str(item).strip() for item in symbols if str(item).strip()
    ))
    before = _exact_integrity(snapshots, resolved_symbols)
    attempts: list[dict[str, Any]] = []
    seen_actions: set[tuple[str, str, str, str]] = set()
    cycles: list[dict[str, Any]] = []
    while True:
        if cancellation is not None and callable(getattr(cancellation, "is_set", None)) and cancellation.is_set():
            exhausted_reason = "cancelled"
            break
        current = _exact_integrity(snapshots, resolved_symbols)
        # Upstream emits one finite worklist ordered as critical -> optional ->
        # enrichment.  The previous host loop stopped as soon as the critical
        # coverage gate reached 60%, which meant an entirely empty enrichment
        # dimension (notably 12_capital_flow) was never attempted at all.
        # Exhaust every real action before returning; the caller can still
        # distinguish a cleared critical gate from unresolved warnings.
        work = []
        for target_severity in ("critical", "optional", "enrichment"):
            for report in current["symbols"]:
                symbol = str(report.get("symbol") or "")
                for task in list(report.get("recovery_tasks") or []):
                    if not isinstance(task, dict):
                        continue
                    if str(task.get("severity") or "") != target_severity:
                        continue
                    ordered_actions = sorted(
                        (
                            str(action)
                            for action in list(task.get("suggested_actions") or [])
                            if str(action).strip()
                        ),
                        key=_recovery_action_priority,
                    )
                    for action in ordered_actions:
                        identity = (
                            symbol,
                            str(task.get("dim") or ""),
                            str(task.get("field") or ""),
                            action,
                        )
                        if identity not in seen_actions:
                            work.append((identity, task, action))
            if work:
                break
        if not work:
            remaining = _task_identities(current)
            exhausted_reason = (
                "all_recovery_tasks_cleared"
                if not remaining
                else "all_real_actions_exhausted"
            )
            break
        identity, task, action = work[0]
        seen_actions.add(identity)
        prior_tasks = _task_identities(current)
        outcome = _execute_recovery_action(
            snapshots,
            symbol=identity[0],
            task=task,
            action=action,
            query=query,
            settings=settings,
        )
        recomputed = _exact_integrity(snapshots, resolved_symbols)
        remaining_tasks = _task_identities(recomputed)
        resolved = (identity[0], identity[1], identity[2]) in prior_tasks and (
            identity[0], identity[1], identity[2]
        ) not in remaining_tasks
        attempt = {
            "stage": str(outcome.get("stage") or "suggested_fix"),
            "symbol": identity[0],
            "dim": identity[1],
            "field": identity[2],
            "suggested_fix": action,
            "status": "resolved" if resolved else str(outcome.get("status") or "exhausted"),
            "source_id": outcome.get("source_id"),
            "source_ids": outcome.get("source_ids"),
            "source_trace": list(outcome.get("source_trace") or []),
            "exhausted_reason": None if resolved else outcome.get("exhausted_reason"),
        }
        attempts.append(attempt)
        cycles.append({
            "cycle": len(cycles) + 1,
            "action": identity,
            "critical_before": bool(current.get("critical_missing")),
            "critical_after": bool(recomputed.get("critical_missing")),
            "coverage_before": current.get("coverage_pct"),
            "coverage_after": recomputed.get("coverage_pct"),
        })

    after = _exact_integrity(snapshots, resolved_symbols)
    return {
        "schema_version": "uzi-3.9.4-suggested-fix-loop-v1",
        "profile": selected_profile,
        "stage_order": [
            "integrity_check",
            "playwright",
            "mx_api",
            "official_web_ddgs",
            "qualitative_agent_deferred",
            "unsafe_inference_rejected",
            "recompute",
        ],
        "before_recovery": before,
        "after_recovery": after,
        "attempted": len(attempts),
        "succeeded": sum(row.get("status") == "resolved" for row in attempts),
        "failed": sum(row.get("status") != "resolved" for row in attempts),
        "attempts": attempts,
        "cycles": cycles,
        "critical_cleared": not bool(after.get("critical_missing")),
        "exhausted": exhausted_reason not in {
            "critical_cleared",
            "all_recovery_tasks_cleared",
        },
        "exhausted_reason": exhausted_reason,
        "round_limit": None,
    }


def recover_uzi_draft_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    query: str,
    symbols: Iterable[str],
    profile: str | None,
    settings: Settings,
    cancellation: Any | None = None,
) -> dict[str, Any]:
    """Run UZI's complete Agent recovery chain against mutable draft data.

    This function deliberately mutates only the caller-owned in-memory
    snapshots.  A data-center caller must append recovered snapshots as new
    database rows and let a user explicitly publish the resulting immutable
    Bundle; it must never mutate an existing Snapshot or Bundle.
    """

    selected_profile = _require_profile(profile)
    resolved_symbols = list(
        dict.fromkeys(str(item).strip() for item in symbols if str(item).strip())
    )
    integrity_before = assess_integrity(snapshots, resolved_symbols)
    cancelled_stage = {
        "schema_version": "uzi-3.9.4-agent-os-v1",
        "profile": selected_profile,
        "enabled": False,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "attempts": [],
        "disabled_reason": "cancelled",
    }
    qualitative_recovery = (
        dict(cancelled_stage)
        if _cancelled(cancellation)
        else recover_qualitative_snapshots(
            snapshots,
            query=query,
            symbols=resolved_symbols,
            profile=selected_profile,
            settings=settings,
        )
    )
    playwright_recovery = (
        dict(cancelled_stage)
        if _cancelled(cancellation)
        else recover_playwright_snapshots(
            snapshots,
            query=query,
            symbols=resolved_symbols,
            profile=selected_profile,
            settings=settings,
        )
    )
    strict_recovery = (
        dict(cancelled_stage)
        if _cancelled(cancellation)
        else recover_suggested_fixes_until_exhausted(
            snapshots,
            query=query,
            symbols=resolved_symbols,
            profile=selected_profile,
            settings=settings,
            cancellation=cancellation,
        )
    )
    qualitative_agent_evidence = (
        dict(cancelled_stage)
        if _cancelled(cancellation)
        else collect_qualitative_agent_evidence(
            snapshots,
            query=query,
            symbols=resolved_symbols,
            profile=selected_profile,
            settings=settings,
            cancellation=cancellation,
        )
    )
    integrity_after = assess_integrity(snapshots, resolved_symbols)
    attempted = sum(
        int(stage.get("attempted") or 0)
        for stage in (
            strict_recovery,
            qualitative_recovery,
            playwright_recovery,
            qualitative_agent_evidence,
        )
    )
    succeeded = sum(
        int(stage.get("succeeded") or 0)
        for stage in (
            strict_recovery,
            qualitative_recovery,
            playwright_recovery,
            qualitative_agent_evidence,
        )
    )
    return {
        "schema_version": "uzi-3.9.4-draft-agent-recovery-v1",
        "profile": selected_profile,
        "stage_order": [
            "qualitative_mx_ddgs",
            "playwright_fallback",
            "suggested_fix_loop",
            "qualitative_agent_evidence",
            "integrity_recompute",
        ],
        "integrity_before": integrity_before,
        "integrity_after": integrity_after,
        "strict_recovery": strict_recovery,
        "qualitative_recovery": qualitative_recovery,
        "playwright_recovery": playwright_recovery,
        "qualitative_agent_evidence": qualitative_agent_evidence,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": max(attempted - succeeded, 0),
    }


def _exact_integrity(
    snapshots: list[dict[str, Any]],
    symbols: list[str],
) -> dict[str, Any]:
    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )
    from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
        Uzi392FetcherRuntime,
    )

    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[3] / "workspace")
    module = importlib.import_module("lib.data_integrity")
    reports = []
    for symbol in symbols:
        raw = build_uzi_raw(snapshots, symbol)
        report = dict(module.validate(raw))
        report["symbol"] = symbol
        report["recovery_tasks"] = list(module.generate_recovery_tasks(raw, report))
        reports.append(report)
    return {
        "symbols": reports,
        "ok": bool(reports) and all(bool(row.get("ok")) for row in reports),
        "critical_missing": any(bool(row.get("critical_missing")) for row in reports),
        "coverage_pct": round(
            sum(float(row.get("coverage_pct") or 0) for row in reports) / len(reports), 1
        ) if reports else 0.0,
        "recovery_task_count": sum(len(row.get("recovery_tasks") or []) for row in reports),
    }


def _task_identities(report: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (
            str(row.get("symbol") or ""),
            str(task.get("dim") or ""),
            str(task.get("field") or ""),
        )
        for row in list(report.get("symbols") or [])
        if isinstance(row, dict)
        for task in list(row.get("recovery_tasks") or [])
        if isinstance(task, dict)
    }


def _recovery_action_priority(action: str) -> tuple[int, str]:
    prefix = action.split(":", 1)[0].strip().lower()
    # Exact UZI data_integrity recovery priority: browser > MX > WebSearch >
    # inference.  Agent work is deferred to the governed analysis stage.
    return ({"browser": 0, "mx": 1, "ws": 2, "agent": 3, "infer": 4}.get(prefix, 5), action)


def _execute_recovery_action(
    snapshots: list[dict[str, Any]],
    *,
    symbol: str,
    task: dict[str, Any],
    action: str,
    query: str,
    settings: Settings,
) -> dict[str, Any]:
    prefix, _, instruction = action.partition(":")
    prefix = prefix.strip().lower()
    instruction = instruction.strip().strip("'")
    dimension = str(task.get("dim") or "")
    snapshot_type = _UZI_DIM_TO_SNAPSHOT.get(dimension)
    snapshot = _find_snapshot(snapshots, str(snapshot_type or ""), symbol)
    if snapshot is None:
        return {
            "stage": prefix or "suggested_fix",
            "status": "exhausted",
            "exhausted_reason": "target_snapshot_missing",
        }
    if prefix == "browser":
        sources = _BROWSER_SOURCES_BY_DIM.get(dimension) or _PLAYWRIGHT_STRATEGIES.get(str(snapshot_type), ())
        if not sources:
            return {"stage": "playwright", "status": "exhausted", "exhausted_reason": "no_uzi_browser_strategy"}
        result = UziBrowserAdapter(settings).collect_many(
            source_ids=tuple(sources), symbol=symbol, query=query or instruction,
        )
        evidence = dict(result.get("evidence") or {})
        projected = _project_browser_evidence(str(snapshot_type), evidence, _identity(snapshots, symbol))
        _apply_browser_recovery(
            snapshot,
            evidence,
            projected,
            list(result.get("source_trace") or []),
            f"suggested_fix:{action}",
        )
        return {
            "stage": "playwright",
            "status": "available" if projected else "exhausted",
            "source_ids": list(sources),
            "source_trace": list(result.get("source_trace") or []),
            "exhausted_reason": None if projected else "no_structured_real_field_returned",
        }
    if prefix == "mx":
        if not settings.mx_api_key:
            return {"stage": "mx_api", "status": "disabled", "source_id": "mx_api", "exhausted_reason": "credentials_missing"}
        try:
            payload = MxApiAdapter(settings.mx_api_key).query_text(query=instruction)
            text = str(payload.get("text") or "").strip()
            if is_junk_fallback_text(text):
                return {"stage": "mx_api", "status": "parse_failed", "source_id": "mx_api", "exhausted_reason": "junk_filtered"}
            applied = _apply_qualitative_action(snapshot, str(snapshot_type), text, "mx_api", instruction)
            return {
                "stage": "mx_api", "status": "available" if applied else "exhausted", "source_id": "mx_api",
                "exhausted_reason": None if applied else "numeric_or_factual_text_not_projected",
            }
        except Exception as exc:  # noqa: BLE001 - provider boundary is isolated
            return {
                "stage": "mx_api", "status": "provider_unavailable", "source_id": "mx_api",
                "exhausted_reason": f"{exc.__class__.__name__}:{str(exc)[:120]}",
            }
    if prefix == "ws":
        try:
            payload = UziWebSearchAdapter().search(query=instruction, dimension=str(snapshot_type), max_results=3)
            rows = [row for row in list(payload.get("results") or []) if isinstance(row, dict)]
            text = "；".join(
                f"{str(row.get('title') or '').strip()} — {str(row.get('body') or '').strip()[:120]}".strip(" —")
                for row in rows
            )[:600]
            applied = bool(rows) and not is_junk_fallback_text(text) and _apply_qualitative_action(
                snapshot, str(snapshot_type), text, "ddgs", instruction, results=rows,
            )
            return {
                "stage": "official_web_ddgs", "status": "available" if applied else "exhausted", "source_id": "ddgs",
                "source_trace": list(payload.get("source_trace") or []),
                "exhausted_reason": None if applied else "no_structured_real_field_returned",
            }
        except Exception as exc:  # noqa: BLE001 - provider boundary is isolated
            return {
                "stage": "official_web_ddgs", "status": "provider_unavailable", "source_id": "ddgs",
                "exhausted_reason": f"{exc.__class__.__name__}:{str(exc)[:120]}",
            }
    if prefix == "agent":
        return {"stage": "qualitative_agent", "status": "deferred", "exhausted_reason": "executed_in_agent_analysis_stage"}
    if prefix == "infer":
        return {
            "stage": "qualitative_inference",
            "status": "forbidden",
            "exhausted_reason": "unstructured_inference_cannot_create_numeric_or_factual_observation",
        }
    return {"stage": "suggested_fix", "status": "exhausted", "exhausted_reason": "unsupported_suggested_fix"}


def _apply_qualitative_action(
    snapshot: dict[str, Any],
    snapshot_type: str,
    text: str,
    source_id: str,
    query: str,
    *,
    results: list[dict[str, Any]] | None = None,
) -> bool:
    if snapshot_type not in {
        "macro", "chain", "research", "industry", "materials", "futures",
        "policy", "moat", "events", "sentiment", "trap", "contests",
    }:
        return False
    result = {
        "status": "available",
        "source_id": source_id,
        "text": text,
        "query": query,
        "results": list(results or []),
        "source_trace": [{"source_id": source_id, "status": "available", "transport": "search"}],
    }
    _apply_recovery(snapshot, snapshot_type, result)
    facts = snapshot.setdefault("facts", {})
    evidence = {
        "text": text,
        "query": query,
        "source_id": source_id,
        "disclosure": "基于公开信息推断，非一手数据",
        "results": list(results or []),
    }
    rows = list(facts.get("recovery_evidence") or [])
    rows.append(evidence)
    facts["recovery_evidence"] = rows
    return True


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
    from domain_packs.finance_ashare.tools.uzi_native_postfetch import (
        recover_snapshot_postfetch,
    )

    return recover_snapshot_postfetch(snapshots, symbols=symbols, profile=selected_profile,
                                      settings=settings, qualitative=True, browser=False)


def collect_qualitative_agent_evidence(
    snapshots: list[dict[str, Any]],
    *,
    query: str,
    symbols: Iterable[str],
    profile: str | None,
    settings: Settings,
    cancellation: Any | None = None,
) -> dict[str, Any]:
    """Execute UZI 3.9.4 task2.5's three real-source evidence groups.

    UZI's Stage 1 MX/DDGS helper only fills empty qualitative fields.  Its
    separate HARD-GATE-QUALITATIVE then requires three parallel Agents to use
    WebSearch, Chrome/Playwright and MX (when configured) before analysis.  In
    Agent OS, external tools run at the governed DomainPack boundary, so this
    function performs those exact actions and freezes their unmodified output
    into the six dimension snapshots.  The later LLM stage can cite only URLs
    present here; failed sources remain explicit and never become evidence.
    """

    selected_profile = _require_profile(profile)
    # This is the interactive/full Agent hard gate, not Stage 1's optional
    # DDGS autofill budget.  Formal Lite reports therefore collect the same
    # six-dimension cited evidence as Medium/Deep; only the standalone
    # ``quick_scan`` command bypasses this function at the caller boundary.

    resolved_symbols = list(dict.fromkeys(
        str(item).strip() for item in symbols if str(item).strip()
    ))
    attempts: list[dict[str, Any]] = []
    completed_groups = 0

    for symbol in resolved_symbols:
        identity = _identity(snapshots, symbol)
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="uzi-qualitative-evidence") as pool:
            futures = {
                pool.submit(
                    _collect_qualitative_agent_group,
                    snapshots,
                    symbol=symbol,
                    identity=identity,
                    group_label=group_label,
                    dimensions=dimensions,
                    query=query,
                    settings=settings,
                ): group_label
                for group_label, dimensions in _QUALITATIVE_AGENT_GROUPS
            }
            for future in as_completed(futures):
                if _cancelled(cancellation):
                    for pending in futures:
                        pending.cancel()
                    raise RuntimeError("qualitative_agent_evidence_cancelled")
                group_label = futures[future]
                try:
                    group_attempts = future.result()
                except Exception as exc:  # noqa: BLE001 - one Agent group is isolated
                    group_attempts = [{
                        "group_label": group_label,
                        "status": "provider_unavailable",
                        "reason": f"{exc.__class__.__name__}:{str(exc)[:200]}",
                    }]
                else:
                    completed_groups += 1
                attempts.extend(group_attempts)

    return {
        "schema_version": "uzi-3.9.4-qualitative-agent-evidence-v1",
        "profile": selected_profile,
        "enabled": True,
        "stage_order": ["mx_api_if_configured", "web_search", "playwright"],
        "group_count": len(resolved_symbols) * len(_QUALITATIVE_AGENT_GROUPS),
        "completed_group_count": completed_groups,
        "attempted": len(attempts),
        "succeeded": sum(row.get("status") == "available" for row in attempts),
        "failed": sum(row.get("status") != "available" for row in attempts),
        "attempts": attempts,
        "disabled_reason": None,
    }


def _collect_qualitative_agent_group(
    snapshots: list[dict[str, Any]],
    *,
    symbol: str,
    identity: dict[str, str],
    group_label: str,
    dimensions: tuple[str, str],
    query: str,
    settings: Settings,
) -> list[dict[str, Any]]:
    attempts = []
    for snapshot_type in dimensions:
        snapshot = _find_snapshot(snapshots, snapshot_type, symbol)
        if snapshot is None:
            attempts.append({
                "group_label": group_label,
                "symbol": symbol,
                "snapshot_type": snapshot_type,
                "status": "data_gap",
                "reason": "target_snapshot_missing",
            })
            continue
        rendered_query = _qualitative_agent_query(snapshot_type, symbol, identity, query)
        urls_before = _http_urls(snapshot)
        traces: list[dict[str, Any]] = []
        evidence: dict[str, Any] = {
            "group_label": group_label,
            "query": rendered_query,
            "mx_api": None,
            "web_search_results": [],
            "browser_evidence": {},
        }

        # UZI task2.5: MX is mandatory when configured, but MX text has no
        # source URL and therefore cannot by itself satisfy the citation gate.
        if settings.mx_api_key:
            try:
                mx_payload = MxApiAdapter(settings.mx_api_key).query_text(query=rendered_query)
                mx_text = str(mx_payload.get("text") or "").strip()
                if is_junk_fallback_text(mx_text):
                    traces.append({"source_id": "mx_api", "status": "parse_failed", "reason": "junk_filtered"})
                else:
                    evidence["mx_api"] = {
                        "text": mx_text,
                        "query": rendered_query,
                        "source_id": "mx_api",
                    }
                    traces.append({"source_id": "mx_api", "status": "available", "transport": "api"})
            except Exception as exc:  # noqa: BLE001 - optional MX source is isolated
                traces.append({
                    "source_id": "mx_api",
                    "status": "provider_unavailable",
                    "error_type": exc.__class__.__name__,
                })
        else:
            traces.append({"source_id": "mx_api", "status": "disabled", "reason": "credentials_missing"})

        # WebSearch is always attempted even when MX returned text.  This is
        # the distinction between Stage 1 autofill and the task2.5 Agent gate.
        try:
            web_payload = UziWebSearchAdapter().search(
                query=rendered_query,
                dimension=snapshot_type,
                max_results=8,
            )
            web_rows = [
                deepcopy(row)
                for row in list(web_payload.get("results") or [])
                if isinstance(row, dict) and str(row.get("url") or "").startswith(("http://", "https://"))
            ]
            evidence["web_search_results"] = web_rows
            traces.extend(list(web_payload.get("source_trace") or []))
        except Exception as exc:  # noqa: BLE001 - optional web source is isolated
            traces.append({
                "source_id": "ddgs",
                "status": "provider_unavailable",
                "error_type": exc.__class__.__name__,
            })

        browser_sources = _QUALITATIVE_AGENT_BROWSER_STRATEGIES[snapshot_type]
        browser_payload = UziBrowserAdapter(settings).collect_many(
            source_ids=browser_sources,
            symbol=symbol,
            query=rendered_query,
        )
        evidence["browser_evidence"] = deepcopy(browser_payload.get("evidence") or {})
        traces.extend(list(browser_payload.get("source_trace") or []))

        facts = snapshot.setdefault("facts", {})
        existing = list(facts.get("agent_qualitative_evidence") or [])
        existing.append(evidence)
        facts["agent_qualitative_evidence"] = existing
        snapshot["source_trace"] = [*list(snapshot.get("source_trace") or []), *traces]
        urls_after = _http_urls(snapshot)
        new_urls = sorted(set(urls_after).difference(urls_before))
        status = "available" if len(urls_after) >= 2 and new_urls else "data_gap"
        attempts.append({
            "group_label": group_label,
            "symbol": symbol,
            "snapshot_type": snapshot_type,
            "status": status,
            "query": rendered_query,
            "url_count_before": len(urls_before),
            "url_count_after": len(urls_after),
            "new_url_count": len(new_urls),
            "browser_source_ids": list(browser_sources),
            "source_trace": traces,
            "reason": None if status == "available" else "requires_two_cited_sources",
        })
    return attempts


def recover_playwright_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    query: str,
    symbols: Iterable[str],
    profile: str | None,
    settings: Settings,
) -> dict[str, Any]:
    """Run fixed SKILL's explicitly requested, profile-scoped active pass."""

    selected_profile = _require_profile(profile)
    if selected_profile == "lite":
        return {
            "schema_version": "uzi-3.9.4-agent-os-v1",
            "profile": selected_profile,
            "enabled": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "profile_playwright_mode_off",
        }
    if not settings.browser_sources_enabled:
        return {
            "schema_version": "uzi-3.9.4-agent-os-v1",
            "profile": selected_profile,
            "enabled": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "browser_sources_disabled",
        }
    from domain_packs.finance_ashare.tools.uzi_native_postfetch import (
        recover_snapshot_postfetch,
    )

    return recover_snapshot_postfetch(snapshots, symbols=symbols, profile=selected_profile,
                                      settings=settings, qualitative=False, browser=True, force_browser=True)


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
    """Project browser DOM evidence into UZI 3.9.4 strategy result keys.

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
            match = re.search(
                r"/S/((?:SH|SZ|BJ)\d{6}|HK\d{5})",
                str(link.get("url") or ""),
                re.IGNORECASE,
            )
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


def _qualitative_agent_query(
    snapshot_type: str,
    symbol: str,
    identity: dict[str, str],
    fallback_query: str,
) -> str:
    """Render the exact six UZI 3.9.4 qualitative search intents."""

    name = identity.get("name") or symbol
    industry = identity.get("industry") or "综合"
    code_raw = symbol.split(".", 1)[0]
    return {
        "macro": "2026 中国 利率 货币政策 降息 汇率 大宗商品 宏观环境",
        "policy": f"{industry} 2026 国家政策 监管动态 补贴 税收 影响",
        "industry": f"{industry} 2026 行业增速 TAM 市场规模 渗透率",
        "events": f"{name} {code_raw} 最新公告 重大事件 业绩 合同",
        "materials": f"{name} {code_raw} 主营业务 主要原材料 成本构成",
        "futures": f"{industry} 行业 上下游 期货品种 套保 大宗",
    }.get(snapshot_type, fallback_query or f"{name} {symbol}")


def _http_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            urls.extend(_http_urls(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            urls.extend(_http_urls(item))
    elif isinstance(value, str):
        urls.extend(
            match.rstrip(".,;，。；)")
            for match in re.findall(r"https?://[^\s\"'<>]+", value)
        )
    return list(dict.fromkeys(urls))


def _cancelled(cancellation: Any | None) -> bool:
    check = getattr(cancellation, "is_set", None)
    return bool(callable(check) and check())


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
    "collect_qualitative_agent_evidence",
    "generate_recovery_tasks",
    "recover_playwright_snapshots",
    "recover_qualitative_snapshots",
    "recover_suggested_fixes_until_exhausted",
    "recover_uzi_draft_snapshots",
]
