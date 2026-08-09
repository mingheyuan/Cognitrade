from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


UZI_TIER1_METHOD_VERSION = "3.9.2-adapted-tier1-v1"

_QUALITY_STATUSES = {"available", "degraded", "data_gap", "assumption_only"}
_AI_LEVERAGE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("算力 / AI 芯片", ("算力", "ai芯片", "ai 芯片", "asic", "gpu", "risc-v", "ai服务器", "ai 服务器", "数据中心")),
    ("光互连 / 光模块", ("光模块", "光芯片", "cpo", "硅光", "光通信", "eml", "vcsel", "磷化铟", "inp")),
    ("存储 / HBM", ("hbm", "存储", "ddr", "abf", "cowos", "先进封装")),
    ("供电 / 散热", ("液冷", "散热", "服务器电源", "pdu", "bbu")),
    ("互连 / PCB", ("pcb", "高速铜", "铜缆", "连接器")),
    ("AI 终端", ("ar眼镜", "ar 眼镜", "光波导", "micro-led", "近眼显示", "机器视觉")),
    ("AI 应用 / 软件", ("大模型", "aigc", "生成式", "智能体", "agent", "ai应用", "ai 应用", "推理", "训练")),
)
_AI_DEMAND_KEYWORDS = (
    "订单", "中标", "签约", "长协", "在手", "backlog", "供货", "导入", "量产",
    "扩产", "产能", "放量", "提价", "缺货", "满产", "认证", "送样", "收入",
)
_SECTOR_WATCH_METRICS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("软件", "saas", "云", "互联网"), ("经常性收入", "净留存率", "在手合同", "付费客户数")),
    (("银行", "保险", "证券", "金融"), ("净息差", "不良率与拨备", "信贷增速", "管理资产规模")),
    (("医药", "生物", "医疗", "制药"), ("核心品种放量", "处方量", "集采影响", "管线进度")),
    (("白酒", "食品饮料", "消费"), ("终端动销", "渠道库存", "吨价", "合同负债")),
    (("光模块", "光通信", "cpo", "硅光"), ("高速产品出货量", "高端产品占比", "毛利率", "订单能见度")),
    (("新能源", "光伏", "储能", "锂电"), ("出货量", "单位盈利", "产能利用率", "原材料价格传导")),
    (("半导体", "芯片", "晶圆", "封装"), ("产能利用率", "平均售价", "库存周期", "先进制程占比")),
    (("汽车", "整车", "零部件"), ("销量与交付量", "单车售价", "毛利率", "新车型周期")),
)
_DEFAULT_WATCH_METRICS = ("营收与一致预期", "毛利率趋势", "经营现金流", "管理层指引")
_MODEL_UPDATE_SPECS: dict[str, dict[str, Any]] = {
    "rev_growth": {"label": "营收增速", "unit": "pct", "aliases": ("revenue_growth", "revenue_yoy", "revenue_growth_latest"), "positive": True},
    "gross_margin": {"label": "毛利率", "unit": "pct", "aliases": ("gross_margin",), "positive": True},
    "net_margin": {"label": "净利率", "unit": "pct", "aliases": ("net_margin",), "positive": True},
    "capex_pct": {"label": "资本开支/营收", "unit": "pct", "aliases": ("capex_pct",), "positive": False},
    "stage1_growth": {"label": "DCF 第一阶段增速", "unit": "pct", "aliases": ("stage_one_growth",), "positive": True},
    "terminal_g": {"label": "DCF 永续增长率", "unit": "pct", "aliases": ("terminal_growth",), "positive": True},
    "beta": {"label": "Beta", "unit": "x", "aliases": ("beta",), "positive": False},
    "target_pe": {"label": "目标市盈率", "unit": "x", "aliases": ("pe_ttm", "pe"), "positive": True},
    "target_price": {"label": "目标价", "unit": "price", "aliases": ("target_price", "target_price_avg"), "positive": True},
}


def build_ai_readiness(
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
) -> dict[str, Any]:
    companies: list[dict[str, Any]] = []
    for symbol in symbols:
        rows = _rows_for_symbol(snapshot_rows, symbol)
        market = _snapshot(rows, "market")
        relevant = [
            row for row in rows
            if str(row.get("snapshot_type") or "") in {"chain", "industry", "events", "research", "moat"}
            and _row_has_observation(row)
        ]
        text = " ".join(_text(row.get("facts")) for row in relevant).casefold()
        leverage_points = _leverage_points(text)
        ai_hits = _unique(
            keyword
            for _, keywords in _AI_LEVERAGE_PATTERNS
            for keyword in keywords
            if keyword.casefold() in text
        )
        chain_sources = [row for row in relevant if row.get("snapshot_type") in {"chain", "industry", "research"}]
        if not chain_sources:
            chain_gate = _gate("ai_chain", "是否有可验证的 AI 产业链关系", "data_gap", [], "chain_or_industry_evidence_missing")
        elif ai_hits:
            chain_gate = _gate("ai_chain", "是否有可验证的 AI 产业链关系", "pass", ai_hits[:8])
        else:
            chain_gate = _gate("ai_chain", "是否有可验证的 AI 产业链关系", "fail", [], "observed_sources_do_not_show_ai_chain_exposure")

        demand_hits = [keyword for keyword in _AI_DEMAND_KEYWORDS if keyword.casefold() in text]
        demand_sources = [row for row in relevant if row.get("snapshot_type") in {"chain", "events", "research"}]
        if not demand_sources:
            demand_gate = _gate("real_demand", "是否有收入、订单或产能证据", "data_gap", [], "demand_evidence_missing")
        elif chain_gate["status"] == "pass" and demand_hits:
            demand_gate = _gate("real_demand", "是否有收入、订单或产能证据", "pass", demand_hits[:8])
        elif chain_gate["status"] == "fail":
            demand_gate = _gate("real_demand", "是否有收入、订单或产能证据", "fail", demand_hits[:8], "ai_chain_relationship_not_observed")
        else:
            demand_gate = _gate("real_demand", "是否有收入、订单或产能证据", "data_gap", demand_hits[:8], "ai_specific_demand_not_verifiable")

        moat = _snapshot(rows, "moat")
        irreplaceable = _find_bool((moat or {}).get("facts"), ("ai_irreplaceable", "irreplaceable"))
        moat_score = _find_number((moat or {}).get("facts"), ("moat_total", "moat_score", "score"))
        if not _row_has_observation(moat):
            moat_gate = _gate("durable_position", "卡位是否不可替代且可持续", "data_gap", [], "moat_evidence_missing")
        elif irreplaceable is not None:
            moat_gate = _gate(
                "durable_position",
                "卡位是否不可替代且可持续",
                "pass" if irreplaceable else "fail",
                [f"explicit_irreplaceable={str(irreplaceable).lower()}"],
            )
        elif moat_score is not None:
            moat_gate = _gate(
                "durable_position",
                "卡位是否不可替代且可持续",
                "pass" if moat_score >= 30 else "fail",
                [f"observed_moat_score={moat_score:g}"],
            )
        else:
            moat_gate = _gate("durable_position", "卡位是否不可替代且可持续", "data_gap", [], "irreplaceability_metric_missing")

        gates = [chain_gate, demand_gate, moat_gate]
        passed = sum(gate["status"] == "pass" for gate in gates)
        gaps = [str(gate.get("reason")) for gate in gates if gate["status"] == "data_gap" and gate.get("reason")]
        failed = [gate["gate_id"] for gate in gates if gate["status"] == "fail"]
        if chain_gate["status"] == "fail":
            rating, verdict = "not_observed", "AI 产业链暴露未由当前证据支持"
        elif any(gate["status"] == "data_gap" for gate in gates):
            rating, verdict = "evidence_incomplete", "证据不足，不能形成完整 AI 就绪度结论"
        elif passed == 3:
            rating, verdict = "strong", "三道证据门均通过，可将 AI 卡位作为已验证研究维度"
        elif passed == 2:
            rating, verdict = "moderate", "部分证据门通过，仍存在已观察到的弱项"
        else:
            rating, verdict = "weak", "现有证据显示 AI 卡位较弱"
        companies.append(
            {
                "symbol": symbol,
                "name": _company_name(market, symbol),
                "rating": rating,
                "verdict": verdict,
                "gates": gates,
                "gates_passed": passed,
                "gates_failed": failed,
                "leverage_points": leverage_points,
                "observed_ai_keywords": ai_hits[:12],
                "data_gap": gaps,
                "quality_status": "data_gap" if len(gaps) == 3 else "degraded" if gaps else "available",
            }
        )
    gaps = _unique(gap for company in companies for gap in company["data_gap"])
    observed = [company for company in companies if company["quality_status"] != "data_gap"]
    return {
        "methodology_version": UZI_TIER1_METHOD_VERSION,
        "method": "ai_readiness",
        "companies": companies,
        "quality_status": "data_gap" if not observed else "degraded" if gaps else "available",
        "data_gap": gaps or ([] if companies else ["symbols_missing"]),
        "assumption_flags": [],
        "generated_at": _now(),
    }


def build_earnings_preview(
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
) -> dict[str, Any]:
    previews: list[dict[str, Any]] = []
    for symbol in symbols:
        rows = _rows_for_symbol(snapshot_rows, symbol)
        market = _snapshot(rows, "market")
        fundamental = _snapshot(rows, "fundamental")
        research = _snapshot(rows, "research")
        events = _snapshot(rows, "events")
        financials = _financial_abstract(fundamental)
        research_facts = dict(research.get("facts") or {}) if _row_has_observation(research) else {}
        industry = _industry_text(rows, market)
        consensus: list[dict[str, Any]] = []
        data_gap: list[str] = []
        for metric, aliases, unit in (
            ("revenue", ("consensus_revenue", "consensus_rev_yi", "revenue_consensus"), "CNY_100M"),
            ("eps", ("consensus_eps", "consensus_eps_2026", "eps_consensus"), "CNY_per_share"),
            ("net_profit", ("consensus_net_profit", "net_profit_consensus"), "CNY_100M"),
            ("target_price", ("target_price_avg", "average_target_price", "target_price"), "CNY"),
        ):
            value = _find_number(research_facts, aliases)
            consensus.append({"metric": metric, "value": value, "unit": unit, "status": "available" if value is not None else "data_gap"})
            if value is None:
                data_gap.append(f"{symbol}:consensus_{metric}_missing")
        baseline_revenue = _find_number(financials, ("revenue", "营业总收入", "营业收入"))
        baseline_eps = _find_number(financials, ("eps", "基本每股收益"))
        baseline_profit = _find_number(financials, ("net_profit", "归母净利润", "净利润"))
        baseline = {"revenue": baseline_revenue, "eps": baseline_eps, "net_profit": baseline_profit}
        observed_baseline = any(value is not None for value in baseline.values())
        if not observed_baseline:
            data_gap.append(f"{symbol}:financial_baseline_missing")

        scenario_anchor = {
            "revenue": _consensus_value(consensus, "revenue") or baseline_revenue,
            "eps": _consensus_value(consensus, "eps") or baseline_eps,
            "net_profit": _consensus_value(consensus, "net_profit") or baseline_profit,
        }
        scenarios: list[dict[str, Any]] = []
        if observed_baseline:
            for scenario, factor in (("bear", 0.95), ("base", 1.0), ("bull", 1.05)):
                scenarios.append(
                    {
                        "scenario": scenario,
                        "status": "assumption_only",
                        "assumption": f"observed_or_consensus_anchor_x_{factor:.2f}",
                        "values": {
                            key: round(value * factor, 4) if value is not None else None
                            for key, value in scenario_anchor.items()
                        },
                    }
                )
        else:
            scenarios.append({"scenario": "unavailable", "status": "data_gap", "reason": "financial_baseline_missing"})

        event_items = _event_items(events)
        watch_metrics = _watch_metrics(industry, _company_name(market, symbol))
        if not event_items:
            data_gap.append(f"{symbol}:earnings_event_or_catalyst_missing")
        data_gap.extend(
            [
                f"{symbol}:options_implied_volatility_not_collected",
                f"{symbol}:earnings_date_aligned_price_series_not_collected",
            ]
        )
        previews.append(
            {
                "symbol": symbol,
                "name": _company_name(market, symbol),
                "industry": industry or None,
                "consensus_table": consensus,
                "latest_reported_baseline": baseline,
                "watch_metrics": list(watch_metrics),
                "scenarios": scenarios,
                "catalyst_checklist": event_items[:12],
                "implied_move": {"value": None, "status": "data_gap", "reason": "options_implied_volatility_not_collected"},
                "historical_earnings_move": {"value": None, "status": "data_gap", "reason": "earnings_date_aligned_price_series_not_collected"},
                "quality_status": "data_gap" if not observed_baseline and not research_facts else "degraded" if data_gap else "available",
                "data_gap": _unique(data_gap),
                "assumption_flags": ["scenario_band_plus_minus_5pct"] if observed_baseline else [],
            }
        )
    gaps = _unique(gap for preview in previews for gap in preview["data_gap"])
    assumptions = _unique(flag for preview in previews for flag in preview["assumption_flags"])
    usable = [preview for preview in previews if preview["quality_status"] != "data_gap"]
    return {
        "methodology_version": UZI_TIER1_METHOD_VERSION,
        "method": "earnings_preview",
        "previews": previews,
        "quality_status": "data_gap" if not usable else "degraded" if gaps or assumptions else "available",
        "data_gap": gaps or ([] if previews else ["symbols_missing"]),
        "assumption_flags": assumptions,
        "generated_at": _now(),
    }


def build_model_update(
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    updates: Mapping[str, Any] | None,
    valuation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    supplied = dict(updates or {})
    if not supplied:
        return {
            "methodology_version": UZI_TIER1_METHOD_VERSION,
            "method": "model_update",
            "updates": [],
            "impacts": [],
            "quality_status": "data_gap",
            "data_gap": ["model_updates_missing"],
            "assumption_flags": [],
            "generated_at": _now(),
        }
    unknown = sorted(set(supplied) - set(_MODEL_UPDATE_SPECS))
    rows: list[dict[str, Any]] = []
    data_gap: list[str] = [f"unsupported_model_update:{key}" for key in unknown]
    for symbol in symbols or [""]:
        symbol_rows = _rows_for_symbol(snapshot_rows, symbol) if symbol else snapshot_rows
        market = _snapshot(symbol_rows, "market")
        fundamental = _snapshot(symbol_rows, "fundamental")
        features: dict[str, Any] = {
            **_market_overview(market),
            **_financial_abstract(fundamental),
        }
        valuation_model = _valuation_model_for_symbol(valuation, symbol)
        valuation_assumptions = dict(valuation_model.get("assumptions") or {})
        for key, after_raw in supplied.items():
            spec = _MODEL_UPDATE_SPECS.get(key)
            if spec is None:
                continue
            after = _number(after_raw)
            before = _find_number({**features, **valuation_assumptions}, spec["aliases"])
            if after is None:
                data_gap.append(f"{symbol or 'portfolio'}:{key}:after_value_invalid")
                continue
            if before is None:
                data_gap.append(f"{symbol or 'portfolio'}:{key}:baseline_missing")
            delta = None if before is None else round(after - before, 6)
            direction = "unknown" if delta is None else "up" if delta > 0 else "down" if delta < 0 else "flat"
            favorable = None if delta is None or delta == 0 else (delta > 0) == bool(spec["positive"])
            rows.append(
                {
                    "symbol": symbol or None,
                    "key": key,
                    "label": spec["label"],
                    "unit": spec["unit"],
                    "before": before,
                    "after": after,
                    "delta": delta,
                    "direction": direction,
                    "first_order_impact": "favorable" if favorable is True else "unfavorable" if favorable is False else "neutral_or_unknown",
                    "input_status": "user_supplied",
                    "baseline_status": "observed_or_model_assumption" if before is not None else "data_gap",
                }
            )
    impacts = _model_update_impacts(rows, valuation)
    if not symbols:
        data_gap.append("symbols_missing")
    return {
        "methodology_version": UZI_TIER1_METHOD_VERSION,
        "method": "model_update",
        "updates": rows,
        "impacts": impacts,
        "quality_status": "data_gap" if not rows else "assumption_only" if not data_gap else "degraded",
        "data_gap": _unique(data_gap),
        "assumption_flags": ["first_order_directional_impact_only"],
        "generated_at": _now(),
    }


def build_returns_attribution(
    holdings: list[Mapping[str, Any]],
    benchmark_return: Any = None,
) -> dict[str, Any]:
    if not holdings:
        return _portfolio_gap("returns_attribution", "portfolio_holdings_missing")
    normalized, errors = _normalize_holdings(holdings)
    if errors:
        return _portfolio_gap("returns_attribution", *errors)
    contribution_rows: list[dict[str, Any]] = []
    observed_weight = 0.0
    observed_contribution = 0.0
    missing_returns: list[str] = []
    for holding in normalized:
        interval_return = _number(holding.get("return_pct"))
        if interval_return is None:
            missing_returns.append(str(holding["ticker"]))
            contribution = None
        else:
            contribution = holding["normalized_weight"] * interval_return
            observed_weight += holding["normalized_weight"]
            observed_contribution += contribution
        contribution_rows.append(
            {
                **holding,
                "return_pct": interval_return,
                "contribution_pct": round(contribution, 6) if contribution is not None else None,
                "status": "available" if interval_return is not None else "data_gap",
            }
        )
    complete = observed_weight >= 1.0 - 1e-9
    total_return = round(observed_contribution, 6) if complete else None
    benchmark = _number(benchmark_return)
    benchmark_view = {
        "benchmark_return_pct": benchmark,
        "excess_return_pct": round(total_return - benchmark, 6) if total_return is not None and benchmark is not None else None,
        "status": "available" if total_return is not None and benchmark is not None else "data_gap",
    }
    groups = {
        "industry": _group_contributions(contribution_rows, "industry"),
        "market": _group_contributions(contribution_rows, "market"),
    }
    data_gap = [f"return_pct_missing:{ticker}" for ticker in missing_returns]
    if benchmark is None:
        data_gap.append("benchmark_return_missing")
    return {
        "methodology_version": UZI_TIER1_METHOD_VERSION,
        "method": "returns_attribution",
        "holdings": contribution_rows,
        "observed_weight_coverage": round(observed_weight, 6),
        "observed_contribution_pct": round(observed_contribution, 6),
        "total_return_pct": total_return,
        "groups": groups,
        "benchmark": benchmark_view,
        "quality_status": "available" if complete and benchmark is not None else "degraded",
        "data_gap": data_gap,
        "assumption_flags": [],
        "generated_at": _now(),
    }


def build_holding_rebalance(
    holdings: list[Mapping[str, Any]],
    targets: Mapping[str, Any] | None = None,
    drift_threshold: Any = 5.0,
    fee_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not holdings:
        return _portfolio_gap("holding_rebalance", "portfolio_holdings_missing")
    normalized, errors = _normalize_holdings(holdings)
    if errors:
        return _portfolio_gap("holding_rebalance", *errors)
    threshold = _number(drift_threshold)
    if threshold is None or threshold < 0:
        return _portfolio_gap("holding_rebalance", "drift_threshold_invalid")
    tickers = [str(holding["ticker"]) for holding in normalized]
    assumptions: list[str] = []
    data_gap: list[str] = []
    if targets is None:
        resolved_targets = {ticker: 1.0 / len(tickers) for ticker in tickers}
        target_mode = "equal_weight_policy_assumption"
        assumptions.append("equal_weight_target_not_observed_market_data")
    else:
        raw_targets = {str(key): _number(value) for key, value in dict(targets).items()}
        if set(raw_targets) != set(tickers) or any(value is None or value < 0 for value in raw_targets.values()):
            return _portfolio_gap("holding_rebalance", "rebalance_targets_must_cover_each_holding")
        total_target = sum(float(value) for value in raw_targets.values() if value is not None)
        if total_target <= 0:
            return _portfolio_gap("holding_rebalance", "rebalance_targets_total_must_be_positive")
        resolved_targets = {ticker: float(raw_targets[ticker]) / total_target for ticker in tickers}
        target_mode = "explicit_user_targets"
    total_value, value_status = _portfolio_total_value(normalized)
    if value_status:
        data_gap.append(value_status)
    drift_table: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    estimated_cost_total = 0.0
    estimated_cost_complete = True
    for holding in normalized:
        ticker = str(holding["ticker"])
        target_weight = resolved_targets[ticker]
        drift_pp = (holding["normalized_weight"] - target_weight) * 100.0
        breached = abs(drift_pp) > threshold
        delta_weight = target_weight - holding["normalized_weight"]
        amount = round(abs(delta_weight) * total_value, 4) if total_value is not None else None
        action = "buy" if delta_weight > 0 else "sell" if delta_weight < 0 else "hold"
        market = str(holding.get("market") or "").strip().upper() or None
        cost = None
        if breached and amount is not None:
            rate = _trade_fee_rate(fee_model, market, action)
            if rate is None:
                estimated_cost_complete = False
                data_gap.append(f"fee_model_missing:{market or ticker}:{action}")
            else:
                cost = round(amount * rate, 4)
                estimated_cost_total += cost
        price = _number(holding.get("price"))
        estimated_shares = None
        if breached and amount is not None and price is not None and price > 0:
            estimated_shares = math.floor(amount / price)
            if market in {"A", "CN", "SH", "SZ", "BJ"}:
                estimated_shares = estimated_shares // 100 * 100
        elif breached and amount is not None:
            data_gap.append(f"price_missing:{ticker}")
        row = {
            **holding,
            "target_weight": round(target_weight, 8),
            "drift_pp": round(drift_pp, 6),
            "breached": breached,
            "action": action if breached else "hold",
            "trade_amount": amount if breached else None,
            "estimated_shares": estimated_shares,
            "estimated_cost": cost,
        }
        drift_table.append(row)
        if breached:
            trades.append({key: row[key] for key in ("ticker", "action", "trade_amount", "estimated_shares", "estimated_cost", "drift_pp")})
    concentration = {
        "before_hhi": round(sum(item["normalized_weight"] ** 2 for item in normalized), 8),
        "after_hhi": round(sum(weight ** 2 for weight in resolved_targets.values()), 8),
        "before_top3_weight": round(sum(sorted((item["normalized_weight"] for item in normalized), reverse=True)[:3]), 8),
        "after_top3_weight": round(sum(sorted(resolved_targets.values(), reverse=True)[:3]), 8),
    }
    quality = "degraded" if data_gap else "assumption_only" if assumptions else "available"
    return {
        "methodology_version": UZI_TIER1_METHOD_VERSION,
        "method": "holding_rebalance",
        "target_mode": target_mode,
        "drift_threshold_pp": threshold,
        "drift_table": drift_table,
        "trades": trades,
        "concentration": concentration,
        "estimated_cost": (
            0.0
            if not trades
            else round(estimated_cost_total, 4)
            if estimated_cost_complete and total_value is not None
            else None
        ),
        "quality_status": quality,
        "data_gap": _unique(data_gap),
        "assumption_flags": assumptions,
        "generated_at": _now(),
    }


def build_portfolio_rebalance(
    holdings: list[Mapping[str, Any]],
    asset_class_targets: Mapping[str, Any] | None,
    drift_threshold: Any = 5.0,
) -> dict[str, Any]:
    """Rebalance at asset-class level using only explicit policy targets.

    Unlike holding-level equal-weight analysis, this method never invents a
    strategic asset allocation. Missing classifications or targets stop the
    calculation and are returned as data gaps.
    """

    if not holdings:
        return _portfolio_gap("portfolio_rebalance", "portfolio_holdings_missing")
    normalized, errors = _normalize_holdings(holdings)
    if errors:
        return _portfolio_gap("portfolio_rebalance", *errors)
    threshold = _number(drift_threshold)
    if threshold is None or threshold < 0:
        return _portfolio_gap("portfolio_rebalance", "drift_threshold_invalid")
    missing_classes = [
        str(row["ticker"])
        for row in normalized
        if not str(row.get("asset_class") or "").strip()
    ]
    if missing_classes:
        return _portfolio_gap(
            "portfolio_rebalance",
            *(f"asset_class_missing:{ticker}" for ticker in missing_classes),
        )
    if not asset_class_targets:
        return _portfolio_gap("portfolio_rebalance", "asset_class_targets_missing")

    classes = {str(row.get("asset_class") or "").strip() for row in normalized}
    raw_targets = {
        str(key).strip(): _number(value)
        for key, value in dict(asset_class_targets).items()
        if str(key).strip()
    }
    if set(raw_targets) != classes:
        return _portfolio_gap(
            "portfolio_rebalance",
            "asset_class_targets_must_cover_observed_classes_exactly",
        )
    if any(value is None or value < 0 for value in raw_targets.values()):
        return _portfolio_gap("portfolio_rebalance", "asset_class_targets_invalid")
    target_total = sum(float(value) for value in raw_targets.values() if value is not None)
    if target_total <= 0:
        return _portfolio_gap("portfolio_rebalance", "asset_class_targets_total_must_be_positive")
    targets = {key: float(value) / target_total for key, value in raw_targets.items() if value is not None}

    current: dict[str, float] = dict.fromkeys(classes, 0.0)
    for holding in normalized:
        current[str(holding["asset_class"]).strip()] += float(holding["normalized_weight"])
    total_value, value_gap = _portfolio_total_value(normalized)
    rows: list[dict[str, Any]] = []
    for asset_class in sorted(classes):
        current_weight = current[asset_class]
        target_weight = targets[asset_class]
        drift_pp = (current_weight - target_weight) * 100
        delta_weight = target_weight - current_weight
        breached = abs(drift_pp) > threshold
        rows.append(
            {
                "asset_class": asset_class,
                "current_weight": round(current_weight, 8),
                "target_weight": round(target_weight, 8),
                "drift_pp": round(drift_pp, 6),
                "breached": breached,
                "action": "increase" if breached and delta_weight > 0 else "decrease" if breached and delta_weight < 0 else "hold",
                "suggested_value_change": round(delta_weight * total_value, 4) if breached and total_value is not None else None,
            }
        )
    data_gap = [value_gap] if value_gap else []
    return {
        "methodology_version": UZI_TIER1_METHOD_VERSION,
        "method": "portfolio_rebalance",
        "target_mode": "explicit_asset_class_targets",
        "drift_threshold_pp": threshold,
        "asset_classes": rows,
        "portfolio_value": total_value,
        "quality_status": "degraded" if data_gap else "available",
        "data_gap": data_gap,
        "assumption_flags": ["user_supplied_strategic_asset_allocation"],
        "generated_at": _now(),
    }


def _portfolio_gap(method: str, *reasons: str) -> dict[str, Any]:
    return {
        "methodology_version": UZI_TIER1_METHOD_VERSION,
        "method": method,
        "quality_status": "data_gap",
        "data_gap": _unique(reasons),
        "assumption_flags": [],
        "generated_at": _now(),
    }


def _normalize_holdings(holdings: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, holding in enumerate(holdings):
        ticker = str(holding.get("ticker") or holding.get("symbol") or "").strip()
        weight = _number(holding.get("weight"))
        if not ticker:
            errors.append(f"holding_{index}:ticker_missing")
        if weight is None or weight < 0:
            errors.append(f"{ticker or f'holding_{index}'}:weight_missing_or_invalid")
        rows.append({**dict(holding), "ticker": ticker, "weight": weight})
    if errors:
        return [], errors
    if len({row["ticker"] for row in rows}) != len(rows):
        return [], ["portfolio_holdings_duplicate_ticker"]
    total = sum(float(row["weight"]) for row in rows)
    if total <= 0:
        return [], ["portfolio_weight_total_must_be_positive"]
    for row in rows:
        row["normalized_weight"] = round(float(row["weight"]) / total, 12)
    return rows, []


def _portfolio_total_value(holdings: list[dict[str, Any]]) -> tuple[float | None, str | None]:
    explicit_values = [_number(item.get("value") if item.get("value") is not None else item.get("market_value")) for item in holdings]
    if all(value is not None and value >= 0 for value in explicit_values):
        total = sum(float(value) for value in explicit_values if value is not None)
        return (total, None) if total > 0 else (None, "portfolio_value_missing")
    totals = []
    for item in holdings:
        quantity = _number(item.get("quantity"))
        price = _number(item.get("price"))
        if quantity is None or price is None or quantity < 0 or price < 0:
            return None, "portfolio_value_missing"
        totals.append(quantity * price)
    total = sum(totals)
    return (total, None) if total > 0 else (None, "portfolio_value_missing")


def _trade_fee_rate(fee_model: Mapping[str, Any] | None, market: str | None, action: str) -> float | None:
    if not fee_model:
        return None
    candidates = [market, "default"] if market else ["default"]
    spec: Mapping[str, Any] | None = None
    for candidate in candidates:
        value = fee_model.get(candidate) if candidate else None
        if isinstance(value, Mapping):
            spec = value
            break
    if spec is None:
        return None
    commission = _number(spec.get("commission_rate"))
    stamp = _number(spec.get("buy_stamp_rate" if action == "buy" else "sell_stamp_rate"))
    if commission is None and stamp is None:
        return None
    return max(0.0, commission or 0.0) + max(0.0, stamp or 0.0)


def _group_contributions(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(row.get(key) or "未分类")
        bucket = groups.setdefault(label, {key: label, "weight": 0.0, "observed_weight": 0.0, "contribution_pct": 0.0, "missing_tickers": []})
        bucket["weight"] += row["normalized_weight"]
        if row["contribution_pct"] is None:
            bucket["missing_tickers"].append(row["ticker"])
        else:
            bucket["observed_weight"] += row["normalized_weight"]
            bucket["contribution_pct"] += row["contribution_pct"]
    output = [
        {
                **bucket,
                "weight": round(bucket["weight"], 8),
                "observed_weight": round(bucket["observed_weight"], 8),
                "contribution_pct": round(bucket["contribution_pct"], 8),
                "status": "available" if not bucket["missing_tickers"] else "degraded",
        }
        for bucket in groups.values()
    ]
    return sorted(output, key=lambda row: row["contribution_pct"], reverse=True)


def _model_update_impacts(rows: list[dict[str, Any]], valuation: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    models = [item for item in list((valuation or {}).get("models") or []) if isinstance(item, Mapping)]
    impacts: list[dict[str, Any]] = []
    for symbol in _unique(str(row.get("symbol") or "") for row in rows):
        changes = [row for row in rows if str(row.get("symbol") or "") == symbol]
        favorable = sum(row["first_order_impact"] == "favorable" for row in changes)
        unfavorable = sum(row["first_order_impact"] == "unfavorable" for row in changes)
        model = next((dict(item) for item in models if str(item.get("symbol") or "") == symbol), {})
        impacts.append(
            {
                "symbol": symbol or None,
                "direction": "positive" if favorable > unfavorable else "negative" if unfavorable > favorable else "mixed_or_neutral",
                "fair_value_before": _number(model.get("fair_value")),
                "fair_value_after": None,
                "status": "assumption_only",
                "reason": "directional_first_order_review_requires_full_model_recalculation",
            }
        )
    return impacts


def _valuation_model_for_symbol(valuation: Mapping[str, Any] | None, symbol: str) -> dict[str, Any]:
    for model in list((valuation or {}).get("models") or []):
        if isinstance(model, Mapping) and str(model.get("symbol") or "") == symbol:
            return dict(model)
    return {}


def _rows_for_symbol(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("symbol") or "") in {"", symbol}]


def _snapshot(rows: list[dict[str, Any]], snapshot_type: str) -> dict[str, Any]:
    return next((row for row in reversed(rows) if str(row.get("snapshot_type") or "") == snapshot_type), {})


def _row_has_observation(row: Mapping[str, Any] | None) -> bool:
    if not row:
        return False
    quality = str(row.get("quality_status") or row.get("status") or "available").strip().lower()
    if quality not in _QUALITY_STATUSES or quality == "data_gap":
        return False
    facts = row.get("facts")
    if not isinstance(facts, Mapping) or not facts:
        return False
    return str(facts.get("availability") or "").lower() != "data_gap"


def _financial_abstract(row: Mapping[str, Any]) -> dict[str, Any]:
    if not _row_has_observation(row):
        return {}
    facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
    abstract = facts.get("financial_abstract")
    return dict(abstract) if isinstance(abstract, Mapping) else {}


def _market_overview(row: Mapping[str, Any]) -> dict[str, Any]:
    if not _row_has_observation(row):
        return {}
    facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
    overview = facts.get("market_overview")
    return dict(overview) if isinstance(overview, Mapping) else {}


def _company_name(market: Mapping[str, Any], symbol: str) -> str:
    overview = _market_overview(market)
    return str(overview.get("name") or overview.get("名称") or symbol)


def _industry_text(rows: list[dict[str, Any]], market: Mapping[str, Any]) -> str:
    overview = _market_overview(market)
    direct = str(overview.get("industry") or overview.get("sector") or "").strip()
    industry = _snapshot(rows, "industry")
    if direct:
        return direct
    if _row_has_observation(industry):
        return _text(industry.get("facts"))[:240]
    return ""


def _event_items(events: Mapping[str, Any]) -> list[str]:
    if not _row_has_observation(events):
        return []
    facts = events.get("facts") if isinstance(events.get("facts"), Mapping) else {}
    candidates = facts.get("events") or facts.get("event_timeline") or facts.get("items") or []
    if isinstance(candidates, list):
        return _unique(_text(item)[:300] for item in candidates if _text(item).strip())
    text = _text(candidates).strip()
    return [text[:300]] if text else []


def _watch_metrics(industry: str, name: str) -> tuple[str, ...]:
    blob = f"{industry} {name}".casefold()
    for keywords, metrics in _SECTOR_WATCH_METRICS:
        if any(keyword.casefold() in blob for keyword in keywords):
            return metrics
    return _DEFAULT_WATCH_METRICS


def _consensus_value(rows: list[dict[str, Any]], metric: str) -> float | None:
    row = next((item for item in rows if item.get("metric") == metric), {})
    return _number(row.get("value"))


def _gate(gate_id: str, label: str, status: str, evidence: list[str], reason: str | None = None) -> dict[str, Any]:
    return {"gate_id": gate_id, "label": label, "status": status, "evidence": evidence, "reason": reason}


def _leverage_points(text: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for category, keywords in _AI_LEVERAGE_PATTERNS:
        hits = [keyword for keyword in keywords if keyword.casefold() in text]
        if hits:
            points.append({"category": category, "keywords": hits[:5], "status": "observed_text_match"})
    return points[:3]


def _find_number(value: Any, aliases: Iterable[str]) -> float | None:
    alias_set = {str(alias).casefold() for alias in aliases}
    for key, item in _walk_mapping(value):
        if key.casefold() in alias_set:
            number = _number(item)
            if number is not None:
                return number
    return None


def _find_bool(value: Any, aliases: Iterable[str]) -> bool | None:
    alias_set = {str(alias).casefold() for alias in aliases}
    for key, item in _walk_mapping(value):
        if key.casefold() not in alias_set:
            continue
        if isinstance(item, bool):
            return item
        normalized = str(item).strip().casefold()
        if normalized in {"true", "yes", "1", "是"}:
            return True
        if normalized in {"false", "no", "0", "否"}:
            return False
    return None


def _walk_mapping(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key), item
            yield from _walk_mapping(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_mapping(item)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value or "")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = str(value).replace(",", "").replace("%", "").replace("¥", "").strip()
        number = float(normalized)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unique(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "UZI_TIER1_METHOD_VERSION",
    "build_ai_readiness",
    "build_earnings_preview",
    "build_holding_rebalance",
    "build_model_update",
    "build_portfolio_rebalance",
    "build_returns_attribution",
]
