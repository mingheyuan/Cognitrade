"""Finance Pack-owned implementations of the UZI research semantics.

The source project is used as a methodology reference only.  This module has
no import or runtime dependency on the temporary UZI checkout or its CLI.
"""

from __future__ import annotations

from datetime import date
import json
import math
import re
import statistics
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_panel_registry import (
    PANEL_REGISTRY,
)
from domain_packs.finance_ashare.subagents.uzi_panel_rules import evaluate_investor
from research_system.adapters.vendors.market_router import MarketRouter


UZI_METHOD_VERSION = "3.9.4-adapted"
UZI_SOURCE_COMMIT = "0acf25122cabf493e7bc442faff62ceceb7f2f02"

_PROFILES: dict[str, dict[str, Any]] = {
    "lite": {
        "depth": "lite",
        "label_cn": "速判模式",
        "estimated_minutes": "1-2 分钟",
        "estimated_duration_seconds": 120,
        "deadline_policy": "unbounded",
        "ddg_budget": 0,
        "industry_dynamic_lookup": False,
        "playwright_mode": "off",
        "playwright_dimensions": [],
        "investors_count": 10,
        "enable_bull_bear_debate": False,
        "institutional_methods": ["dcf"],
        "methods": ["dcf"],
        "enable_segmental_model": False,
        "enable_owner_earnings": False,
        "enable_narrative_gap": False,
        "fund_stats_top_n": 5,
        "fund_lite_list_enabled": False,
        "require_qualitative_deep_dive": False,
        "self_review_block_warnings": False,
        "fetchers_enabled": [
            "0_basic", "1_financials", "2_kline", "10_valuation",
            "11_governance", "15_events", "16_lhb",
        ],
        "snapshot_types": [
            "market",
            "fundamental",
            "kline",
            "valuation",
            "governance",
            "events",
            "lhb",
        ],
        "allowed_capability_tags": ["profile:lite"],
    },
    "medium": {
        "depth": "medium",
        "label_cn": "标准分析",
        "estimated_minutes": "5-8 分钟",
        "estimated_duration_seconds": 480,
        "deadline_policy": "unbounded",
        "ddg_budget": 30,
        "industry_dynamic_lookup": True,
        "playwright_mode": "opt-in",
        "playwright_dimensions": ["peers", "materials", "events", "sentiment", "industry", "moat"],
        "investors_count": 51,
        "enable_bull_bear_debate": False,
        "institutional_methods": [
            "dcf", "comps", "lbo", "three_statement", "merger",
            "initiating", "earnings", "catalysts", "thesis", "morning",
            "screen", "sector", "ic_memo", "porter_bcg", "dd",
            "unit_economics", "portfolio_rebalance",
        ],
        "methods": [
            "dcf", "comps", "quick_lbo", "three_statement", "accretion_dilution",
            "initiation_report", "earnings_review", "catalyst_calendar", "thesis_tracker",
            "morning_note", "stock_screen", "sector_research", "ic_memo", "porter_bcg",
            "dd_checklist", "unit_economics", "portfolio_rebalance",
        ],
        "enable_segmental_model": False,
        "enable_owner_earnings": False,
        "enable_narrative_gap": False,
        "fund_stats_top_n": 20,
        "fund_lite_list_enabled": True,
        "require_qualitative_deep_dive": True,
        "self_review_block_warnings": False,
        "fetchers_enabled": [
            "0_basic", "1_financials", "2_kline", "3_macro", "4_peers", "5_chain",
            "6_research", "7_industry", "8_materials", "9_futures", "10_valuation",
            "11_governance", "12_capital_flow", "13_policy", "14_moat", "15_events",
            "16_lhb", "17_sentiment", "18_trap", "19_contests",
        ],
        "snapshot_types": [
            "market",
            "fundamental",
            "kline",
            "macro",
            "peers",
            "chain",
            "research",
            "industry",
            "materials",
            "futures",
            "valuation",
            "governance",
            "capital_flow",
            "policy",
            "moat",
            "events",
            "lhb",
            "sentiment",
            "trap",
            "contests",
        ],
        "allowed_capability_tags": ["profile:lite", "profile:medium"],
    },
    "deep": {
        "depth": "deep",
        "label_cn": "深度研究",
        "estimated_minutes": "15-20 分钟",
        "estimated_duration_seconds": 1200,
        "deadline_policy": "unbounded",
        "ddg_budget": 60,
        "industry_dynamic_lookup": True,
        "playwright_mode": "default_subject_to_sensitive_source_policy",
        "playwright_dimensions": ["peers", "materials", "events", "sentiment", "industry", "moat", "macro", "policy", "trap", "contests"],
        "investors_count": 66,
        "enable_bull_bear_debate": True,
        "institutional_methods": [
            "dcf", "comps", "lbo", "three_statement", "merger",
            "initiating", "earnings", "catalysts", "thesis", "morning",
            "screen", "sector", "ic_memo", "porter_bcg", "dd",
            "unit_economics", "portfolio_rebalance", "segmental",
        ],
        "methods": [
            "dcf",
            "comps",
            "three_statement",
            "quick_lbo",
            "accretion_dilution",
            "initiation_report",
            "earnings_review",
            "catalyst_calendar",
            "thesis_tracker",
            "morning_note",
            "stock_screen",
            "sector_research",
            "ic_memo",
            "porter_bcg",
            "dd_checklist",
            "unit_economics",
            "portfolio_rebalance",
            "segmental",
        ],
        "enable_segmental_model": True,
        "enable_owner_earnings": True,
        "enable_narrative_gap": True,
        "fund_stats_top_n": 100,
        "fund_lite_list_enabled": True,
        "require_qualitative_deep_dive": True,
        "self_review_block_warnings": True,
        "fetchers_enabled": [
            "0_basic", "1_financials", "2_kline", "3_macro", "4_peers", "5_chain",
            "6_research", "7_industry", "8_materials", "9_futures", "10_valuation",
            "11_governance", "12_capital_flow", "13_policy", "14_moat", "15_events",
            "16_lhb", "17_sentiment", "18_trap", "19_contests",
        ],
        "snapshot_types": [
            "market",
            "fundamental",
            "kline",
            "macro",
            "peers",
            "chain",
            "research",
            "industry",
            "materials",
            "futures",
            "valuation",
            "governance",
            "capital_flow",
            "policy",
            "moat",
            "events",
            "lhb",
            "sentiment",
            "trap",
            "contests",
        ],
        "allowed_capability_tags": ["profile:lite", "profile:medium", "profile:deep"],
    },
}

_PANEL_PERSPECTIVES = (
    ("value", "价值视角", "关注价格、安全边际和可验证现金流", ("pe", "pb", "fcf")),
    ("quality", "质量视角", "关注盈利质量、现金流和负债约束", ("roe", "fcf", "debt_ratio")),
    ("growth", "成长视角", "关注收入增长和利润扩张证据", ("revenue_growth", "profit_growth", "net_margin")),
    ("trend", "趋势视角", "关注价格动量和市场确认", ("pct_change", "amount", "price")),
    ("macro", "宏观视角", "关注周期暴露和估值承受力", ("pe", "revenue_growth", "debt_ratio")),
    ("risk", "风险视角", "优先识别数据缺口和下行约束", ("debt_ratio", "fcf", "notice_count")),
    ("china_value", "中国价投视角", "结合本土行业和治理证据审视长期质量", ("roe", "net_margin", "governance")),
    ("quant", "量化视角", "关注可重复的指标组合而非单点叙事", ("pe", "pct_change", "amount")),
    ("industry", "产业链视角", "关注行业位置和经营数据的相互验证", ("revenue_growth", "margin", "sector")),
    ("contrarian", "逆向视角", "寻找市场价格与基本面之间的可解释偏差", ("pe", "pb", "risk")),
)

_AI_CHAIN_KEYWORDS = (
    "光模块", "光芯片", "cpo", "光引擎", "硅光", "光通信", "光器件", "激光器", "eml", "vcsel",
    "hbm", "cowos", "先进封装", "封装基板", "abf", "载板", "inp", "磷化铟", "砷化镓", "化合物半导体", "衬底", "外延",
    "pcb", "高速铜", "铜连接", "铜缆", "连接器", "液冷", "散热", "电源", "bbu", "pdu", "交换机", "算力",
    "ai 芯片", "asic", "gpu", "risc-v", "存储", "ddr", "ai server", "ai 服务器", "数据中心", "data center",
    "半导体设备", "光刻", "刻蚀", "薄膜沉积", "量测", "测试机", "分选机",
    "光学", "光电子", "光学元件", "光波导", "摄像模组", "增强现实", "虚拟现实", "ar眼镜", "micro-led",
    "人形机器人", "具身智能", "humanoid", "机器人", "robot", "谐波减速器", "rv减速器", "精密减速器",
    "行星滚柱丝杠", "滚柱丝杠", "灵巧手", "机械臂", "关节模组", "执行器", "actuator", "空心杯电机",
    "六维力", "力传感器", "触觉传感器", "电子皮肤",
)


def select_profile(inputs: dict[str, Any]) -> dict[str, Any]:
    supplied = next(
        (
            inputs.get(key)
            for key in ("research_profile", "depth", "profile")
            if str(inputs.get(key) or "").strip()
        ),
        None,
    )
    if supplied is None:
        raise ValueError("research_profile is required; choose lite, medium or deep")
    requested = str(supplied).strip().lower()
    if requested not in _PROFILES:
        raise ValueError("research_profile must be lite, medium or deep")
    selected = requested
    profile = dict(_PROFILES[selected])
    profile["requested"] = requested
    profile["selected"] = selected
    profile["selection_reason"] = "用户显式指定研究深度"
    profile["source_commit"] = UZI_SOURCE_COMMIT
    return profile


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _first_number(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number(mapping.get(key))
        if value is not None:
            return value
    return None


def _metric_number(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Read both flat financial fields and the nested metric payloads."""

    for key in keys:
        value = mapping.get(key)
        if isinstance(value, dict):
            for nested_key in ("value", "single", "latest", "current"):
                number = _number(value.get(nested_key))
                if number is not None:
                    return number
        number = _number(value)
        if number is not None:
            return number
    return None


def _amount_yi(value: Any) -> float | None:
    """Normalize financial amounts to the Pack's 亿元 unit when needed."""

    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("人民币", "")
    if not text or text.lower() in {"-", "--", "—", "none", "nan", "n/a"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    number = _number(match.group(0)) if match else None
    if number is None:
        return None
    if "万亿" in text:
        return number * 10_000
    if "亿" in text:
        return number
    if "万" in text:
        return number / 10_000
    return number / 100_000_000 if abs(number) >= 1_000_000 else number


def _currency_symbol(currency: str | None, market_code: str | None = None) -> str:
    normalized = str(currency or "").strip().upper()
    symbols = {
        "CNY": "¥", "HKD": "HK$", "USD": "$", "EUR": "€", "GBP": "£",
        "JPY": "JP¥", "KRW": "₩", "TWD": "NT$", "SGD": "S$", "CAD": "C$",
        "AUD": "A$", "CHF": "CHF ", "SEK": "SEK ", "NOK": "NOK ",
        "DKK": "DKK ", "BRL": "R$", "MXN": "MX$", "INR": "₹", "IDR": "Rp",
        "MYR": "RM", "NZD": "NZ$", "ZAR": "R", "ILS": "₪", "PLN": "zł",
        "CZK": "Kč", "HUF": "Ft", "THB": "฿",
    }
    if normalized in symbols:
        return symbols[normalized]
    return {"A": "¥", "H": "HK$", "U": "$"}.get(str(market_code or "").upper(), f"{normalized} " if normalized else "")


def _metric_amount_yi(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, dict):
            for nested_key in ("value", "single", "latest", "current"):
                amount = _amount_yi(value.get(nested_key))
                if amount is not None:
                    return amount
            continue
        amount = _amount_yi(value)
        if amount is not None:
            return amount
    return None


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) >= 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def _row_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(row.get(key))
        if number is not None:
            return number
    return None


def _moving_average(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _kline_rule_features(
    facts: dict[str, Any],
    *,
    symbol: str,
    name: str,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Derive UZI rule inputs from real OHLCV bars, without default values."""

    history = facts.get("history") or facts.get("market_hist") or facts
    bars = list(history.get("bars") or []) if isinstance(history, dict) else []
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(bars):
        if not isinstance(raw, dict):
            continue
        close = _row_number(raw, "close", "收盘", "Close")
        high = _row_number(raw, "high", "最高", "High")
        low = _row_number(raw, "low", "最低", "Low")
        if close is None or close <= 0 or high is None or low is None:
            continue
        normalized.append(
            {
                "index": index,
                "date": _parse_date(raw.get("date") or raw.get("日期") or raw.get("Date")),
                "close": close,
                "high": high,
                "low": low,
                "volume": _row_number(raw, "volume", "成交量", "Volume"),
            }
        )
    normalized.sort(key=lambda row: (row["date"] or date.min, row["index"]))
    closes = [float(row["close"]) for row in normalized]
    highs = [float(row["high"]) for row in normalized]
    volumes = [row["volume"] for row in normalized]
    features: dict[str, Any] = {}
    derivations: list[str] = []
    if not closes:
        return features, {}, derivations

    last = closes[-1]
    ma_values = {period: _moving_average(closes, period) for period in (5, 10, 20, 60, 120, 200)}
    if all(ma_values[period] is not None for period in (5, 10, 20, 60, 120)):
        features["ma_bull_aligned"] = bool(
            ma_values[5] > ma_values[10] > ma_values[20] > ma_values[60] > ma_values[120]
        )
        derivations.append("ma_bull_aligned=MA5>MA10>MA20>MA60>MA120")
    if ma_values[200] is not None:
        features["price_above_ma200"] = bool(last > ma_values[200])
        derivations.append("price_above_ma200=latest_close>MA200")
    # Weinstein stage needs a comparable 200-day average from 60 sessions ago.
    if len(closes) >= 260 and ma_values[200] is not None:
        ma200_60_sessions_ago = sum(closes[-260:-60]) / 200
        above = last > ma_values[200]
        rising = ma_values[200] > ma200_60_sessions_ago
        features["stage_num"] = 2 if above and rising else 1 if not above and rising else 3 if above else 4
        derivations.append("stage_num=Weinstein(latest_close,MA200,MA200_60_sessions_ago)")

    if len(closes) >= 15:
        changes = [closes[index] - closes[index - 1] for index in range(len(closes) - 14, len(closes))]
        average_gain = sum(max(change, 0.0) for change in changes) / 14
        average_loss = sum(max(-change, 0.0) for change in changes) / 14
        rsi = 100.0 if average_loss == 0 and average_gain > 0 else 50.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
        features["rsi"] = round(rsi, 4)
        features["rsi_overbought"] = rsi > 70
        derivations.append("rsi=14_session_simple_RSI")

    if len(closes) >= 60:
        high_60 = max(highs[-60:])
        if high_60 > 0:
            features["pct_from_60d_high"] = round((last - high_60) / high_60 * 100, 4)
            derivations.append("pct_from_60d_high=(latest_close/max_high_60-1)*100")
    if len(closes) >= 200:
        year_window = min(250, len(closes))
        year_high = max(highs[-year_window:])
        if year_high > 0:
            features["pct_from_year_high"] = round((last - year_high) / year_high * 100, 4)
            derivations.append(f"pct_from_year_high=(latest_close/max_high_{year_window}-1)*100")

    if len(closes) >= 20:
        return_window = closes[-253:] if len(closes) >= 253 else closes
        returns = [return_window[index] / return_window[index - 1] - 1 for index in range(1, len(return_window))]
        if len(returns) >= 2:
            features["volatility_1y"] = round(statistics.stdev(returns) * math.sqrt(252) * 100, 4)
            derivations.append("volatility_1y=stdev(daily_returns)*sqrt(252)*100")
        drawdown_window = closes[-252:] if len(closes) >= 252 else closes
        peak = drawdown_window[0]
        max_drawdown = 0.0
        for close in drawdown_window:
            peak = max(peak, close)
            max_drawdown = min(max_drawdown, (close - peak) / peak)
        features["max_drawdown_1y"] = round(max_drawdown * 100, 4)
        derivations.append("max_drawdown_1y=min(close/running_peak-1)*100")

    dated = [(row["date"], float(row["close"])) for row in normalized if row["date"] is not None]
    if dated:
        latest_year = dated[-1][0].year
        year_rows = [(row_date, close) for row_date, close in dated if row_date.year == latest_year]
        if len(year_rows) >= 2 and year_rows[0][1] > 0:
            features["ytd_return"] = round((year_rows[-1][1] / year_rows[0][1] - 1) * 100, 4)
            derivations.append("ytd_return=(latest_close/first_trading_close_of_year-1)*100")

    numeric_volumes = [float(value) for value in volumes if value is not None and value >= 0]
    if len(numeric_volumes) == len(volumes) and len(numeric_volumes) >= 20:
        average_5 = sum(numeric_volumes[-5:]) / 5
        average_20 = sum(numeric_volumes[-20:]) / 20
        if average_20 > 0:
            features["vol_amplified"] = average_5 > average_20
            derivations.append("vol_amplified=average_volume_5>average_volume_20")

    if len(closes) >= 2:
        raw_code = re.sub(r"\D", "", symbol)[-6:]
        limit_threshold = 29.5 if raw_code.startswith(("4", "8")) else 19.5 if raw_code.startswith(("300", "301", "688", "689")) else 4.8 if name.upper().startswith(("ST", "*ST")) else 9.5
        recent = closes[-31:]
        features["has_limit_up_recent"] = any(
            previous > 0 and (current / previous - 1) * 100 >= limit_threshold
            for previous, current in zip(recent, recent[1:])
        )
        derivations.append(f"has_limit_up_recent=30_session_return>={limit_threshold}%")

    sources = {key: "kline_snapshot.history.bars" for key, value in features.items() if value is not None}
    return features, sources, derivations


def _item_value_map(rows: Any) -> dict[str, Any]:
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("item")): row.get("value")
        for row in rows
        if isinstance(row, dict) and str(row.get("item") or "").strip()
    }


def _facts(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    value = snapshot.get("facts")
    return value if isinstance(value, dict) else {}


def _section(facts: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = facts.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _snapshot_for_symbol(snapshots: list[dict[str, Any]], symbol: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in snapshots:
        if not isinstance(row, dict) or str(row.get("symbol") or "") != symbol:
            continue
        snapshot_type = str(row.get("snapshot_type") or "unknown")
        result[snapshot_type] = row
    return result


def _snapshot_identity(snapshots: list[dict[str, Any]], symbol: str) -> tuple[str, str]:
    """Resolve display identity from source snapshots without inventing company data."""

    name = symbol
    dates: list[str] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or str(snapshot.get("symbol") or "") != symbol:
            continue
        snapshot_date = str(snapshot.get("snapshot_date") or snapshot.get("data_period") or "").strip()
        if snapshot_date:
            dates.append(snapshot_date)
        facts = _facts(snapshot)
        info = _section(facts, "info", "company_info")
        for candidate in (
            snapshot.get("name"),
            snapshot.get("company_name"),
            info.get("name"),
            info.get("company_name"),
            info.get("股票简称"),
            info.get("名称"),
            info.get("longName"),
            info.get("shortName"),
        ):
            if str(candidate or "").strip():
                name = str(candidate).strip()
                break
        if name != symbol:
            break
    return name, max(dates) if dates else ""


def build_features(snapshot_rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    grouped = _snapshot_for_symbol(snapshot_rows, symbol)
    name, data_period = _snapshot_identity(snapshot_rows, symbol)
    market_facts = _facts(grouped.get("market"))
    market = _section(market_facts, "market_overview", "spot")
    fundamental_facts = _facts(grouped.get("fundamental"))
    financial = _section(fundamental_facts, "financial_abstract", "financials")
    analysis = _section(fundamental_facts, "financial_analysis", "financial_indicator")
    info = _section(fundamental_facts, "info", "company_info")
    banking = _section(fundamental_facts, "banking_metrics")
    valuation_facts = _facts(grouped.get("valuation"))
    metric_sections = [
        # Prefer directly reported market/basic metrics for price-derived fields
        # such as dividend yield before falling back to financial statements.
        ("market_overview", market),
        ("financial_abstract", financial),
        ("financial_analysis", analysis),
        ("info", info),
        ("banking_metrics", banking),
        ("financial_abstract_new", fundamental_facts.get("financial_abstract_new") or {}),
        ("financial_benefit_new", fundamental_facts.get("financial_benefit_new") or {}),
        ("financial_debt_new", fundamental_facts.get("financial_debt_new") or {}),
        ("financial_cash_new", fundamental_facts.get("financial_cash_new") or {}),
        ("financial_health", fundamental_facts.get("financial_health") or {}),
        ("valuation", valuation_facts),
    ]

    def first_number_with_source(keys: tuple[str, ...]) -> tuple[float | None, str | None]:
        for section_name, section in metric_sections:
            if not isinstance(section, dict):
                continue
            value = _metric_number(section, keys)
            if value is not None:
                return value, section_name
        return None, None

    def first_amount_with_source(keys: tuple[str, ...]) -> tuple[float | None, str | None]:
        for section_name, section in metric_sections:
            if not isinstance(section, dict):
                continue
            value = _metric_amount_yi(section, keys)
            if value is not None:
                return value, section_name
        return None, None
    notices = _facts(grouped.get("notice"))
    capital = _facts(grouped.get("capital_flow"))
    sentiment = _facts(grouped.get("sentiment"))
    events = _facts(grouped.get("events"))
    lhb = _facts(grouped.get("lhb"))
    kline = _facts(grouped.get("kline"))
    research = _facts(grouped.get("research"))
    industry_facts = _facts(grouped.get("industry"))
    chain_facts = _facts(grouped.get("chain"))
    fund_holders = _facts(grouped.get("fund_holders"))
    price = _first_number(market, ("latest_price", "price", "spot", "收盘", "close"))
    market_cap = _amount_yi(_first_number(market, ("market_cap", "market_cap_yi", "总市值")))
    shares = _first_number(market, ("shares_outstanding_yi", "shares", "total_shares", "总股本"))
    if shares is not None and abs(shares) >= 1_000_000:
        shares /= 100_000_000
    revenue, revenue_source = first_amount_with_source(
        (
            "revenue",
            "revenue_latest_yi",
            "营业收入",
            "营业总收入",
            "主营业务收入",
            "operating_revenue",
            "operating_income_total",
        )
    )
    net_profit, net_profit_source = first_amount_with_source(
        ("net_profit", "net_profit_latest_yi", "归母净利润", "净利润", "net_income")
    )
    fcf, fcf_source = first_amount_with_source(
        (
            "fcf_latest_yi",
            "free_cash_flow",
            "free_cash_flow_yi",
            "自由现金流",
        )
    )
    operating_cash_flow, operating_cash_flow_source = first_amount_with_source(
        (
            "operating_cash_flow_yi",
            "operating_cash_flow",
            "ocf",
            "经营活动现金流净额",
            "经营活动产生的现金流量净额",
            "act_cash_flow_net",
        )
    )
    ocf_to_net_income_ratio, ocf_ratio_source = first_number_with_source(
        (
            "ocf_to_net_income_ratio",
            "经营现金流净额/净利润",
            "经营现金净流量与净利润的比率(%)",
        )
    )
    ocf_ratio_derivation = None
    if (
        ocf_to_net_income_ratio is None
        and operating_cash_flow is not None
        and net_profit not in (None, 0)
    ):
        ocf_to_net_income_ratio = operating_cash_flow / net_profit
        ocf_ratio_source = "+".join(
            item for item in (operating_cash_flow_source, net_profit_source) if item
        )
        ocf_ratio_derivation = "operating_cash_flow_yi / net_profit_latest_yi"
    total_assets, total_assets_source = first_amount_with_source(
        ("total_assets_yi", "total_assets", "assets_total", "debt_and_equity_total", "资产总计", "资产总额")
    )
    total_liabilities, total_liabilities_source = first_amount_with_source(
        ("total_liabilities_yi", "total_liabilities", "负债合计", "负债总额")
    )
    cash, cash_source = first_amount_with_source(
        ("cash_yi", "cash", "货币资金", "现金及现金等价物", "cash_and_equivalents")
    )
    equity, equity_source = first_amount_with_source(
        ("equity_yi", "equity", "total_parent_equity", "归属母公司股东权益", "所有者权益合计")
    )
    direct_debt, debt_source = first_amount_with_source(
        ("total_debt_yi", "total_debt", "interest_bearing_debt", "有息负债", "债务合计")
    )
    short_debt, short_debt_source = first_amount_with_source(
        ("short_term_debt", "short_term_borrowings", "short_term_loan", "短期借款")
    )
    long_debt, long_debt_source = first_amount_with_source(
        ("long_term_debt", "long_term_borrowings", "long_term_loan", "长期借款")
    )
    current_long_debt, current_long_debt_source = first_amount_with_source(
        ("year_non_current_debt", "current_portion_long_term_debt", "一年内到期的非流动负债")
    )
    bonds_debt, bonds_debt_source = first_amount_with_source(
        ("bonds_payable", "payable_short_term_bonds", "应付债券")
    )
    lease_debt, lease_debt_source = first_amount_with_source(("lease_debt", "lease_liabilities", "租赁负债"))
    debt_derivation = None
    if direct_debt is not None:
        total_debt = direct_debt
    elif any(item is not None for item in (short_debt, long_debt, current_long_debt, bonds_debt, lease_debt)):
        total_debt = sum(item or 0.0 for item in (short_debt, long_debt, current_long_debt, bonds_debt, lease_debt))
        debt_source = "+".join(
            dict.fromkeys(
                item
                for item in (
                    short_debt_source,
                    long_debt_source,
                    current_long_debt_source,
                    bonds_debt_source,
                    lease_debt_source,
                )
                if item
            )
        )
        debt_derivation = "short_term_debt + long_term_debt + current_portion + bonds + lease_liabilities"
    else:
        total_debt = None
    ebitda, ebitda_source = first_amount_with_source(
        ("ebitda_yi", "ebitda", "EBITDA", "息税折旧摊销前利润")
    )
    eps, eps_source = first_number_with_source(("eps", "EPS", "每股收益", "基本每股收益"))
    if shares is None and market_cap is not None and price and price > 0:
        shares = market_cap / price
    net_margin_source = "financial_abstract" if _first_number(financial, ("net_margin", "净利率")) is not None else None
    net_margin = _first_number(financial, ("net_margin", "净利率"))
    if net_margin is None:
        net_margin, net_margin_source = first_number_with_source(
            ("net_margin", "净利率", "销售净利率", "销售净利率(%)", "sale_net_interest_ratio")
        )
    if net_margin is None and revenue and revenue != 0 and net_profit is not None:
        net_margin = net_profit / revenue * 100
        net_margin_source = "+".join(item for item in (net_profit_source, revenue_source) if item)
    elif net_margin is not None and abs(net_margin) <= 1:
        net_margin *= 100
    pe = _first_number(market, ("pe_ttm", "pe", "市盈率"))
    pb = _first_number(market, ("pb", "市净率"))
    ps = _first_number(market, ("ps", "市销率"))
    peg = _first_number(market, ("peg", "PEG值", "PEG"))
    dividend_yield, dividend_yield_source = first_number_with_source(
        ("dividend_yield", "dividend_yield_ttm", "股息率", "股息率TTM")
    )
    if dividend_yield is not None and 0 < abs(dividend_yield) <= 1:
        dividend_yield *= 100
    roe = _first_number(banking, ("roe",))
    if roe is None:
        roe = _first_number(financial, ("roe", "ROE"))
    roe_source = "banking_metrics" if _first_number(banking, ("roe",)) is not None else "financial_abstract" if _first_number(financial, ("roe", "ROE")) is not None else None
    if roe is None:
        roe, roe_source = first_number_with_source(
            (
                "roe",
                "ROE",
                "净资产收益率",
                "净资产收益率(%)",
                "加权净资产收益率(%)",
                "index_weighted_avg_roe",
                "index_full_diluted_roe",
            )
        )
    debt_ratio = _first_number(
        financial,
        ("debt_ratio", "asset_liability_ratio", "debt_to_assets", "资产负债率"),
    )
    if debt_ratio is None:
        debt_ratio, debt_ratio_source = first_number_with_source(
            ("debt_ratio", "asset_liability_ratio", "assets_debt_ratio", "资产负债率", "资产负债率(%)")
        )
    else:
        debt_ratio_source = "financial_abstract"
    if roe is not None and abs(roe) <= 1:
        roe *= 100
    if debt_ratio is not None and abs(debt_ratio) <= 1:
        debt_ratio *= 100
    pct_change = _first_number(market, ("pct_change", "change_pct", "涨跌幅"))
    revenue_growth = _first_number(financial, ("revenue_growth", "revenue_yoy", "营收同比"))
    revenue_growth_source = "financial_abstract" if revenue_growth is not None else None
    if revenue_growth is None:
        revenue_growth, revenue_growth_source = first_number_with_source(
            (
                "revenue_growth",
                "revenue_yoy",
                "营收同比",
                "营业总收入同比增长率",
                "主营业务收入增长率(%)",
                "calculate_operating_income_total_yoy_growth_ratio",
            )
        )
    profit_growth = _first_number(financial, ("profit_growth", "net_profit_yoy", "净利润同比"))
    profit_growth_source = "financial_abstract" if profit_growth is not None else None
    if profit_growth is None:
        profit_growth, profit_growth_source = first_number_with_source(
            (
                "profit_growth",
                "net_profit_yoy",
                "净利润同比",
                "净利润同比增长率",
                "净利润增长率(%)",
                "calculate_parent_holder_net_profit_yoy_growth_ratio",
            )
        )
    amount = _first_number(market, ("amount", "成交额"))
    if revenue_growth is not None and abs(revenue_growth) <= 1:
        revenue_growth *= 100
    if profit_growth is not None and abs(profit_growth) <= 1:
        profit_growth *= 100
    gross_margin, gross_margin_source = first_number_with_source(
        ("gross_margin", "销售毛利率", "销售毛利率(%)", "主营业务利润率(%)", "sale_gross_margin")
    )
    if gross_margin is not None and abs(gross_margin) <= 1:
        gross_margin *= 100
    current_ratio, current_ratio_source = first_number_with_source(("current_ratio", "流动比率"))

    latest_period_revenue, latest_period_revenue_source = first_amount_with_source(("operating_income_total",))
    research_expense, research_expense_source = first_amount_with_source(
        ("research_and_development_expenses", "research_expense", "研发费用")
    )
    rd_intensity = (
        research_expense / latest_period_revenue * 100
        if research_expense is not None and latest_period_revenue not in (None, 0)
        else None
    )
    capital_expenditure, capital_expenditure_source = first_amount_with_source(
        ("pay_fixed_assets_etc_cash", "capital_expenditure", "capex", "购建固定资产支付的现金")
    )
    panel_fcf = (
        operating_cash_flow - capital_expenditure
        if operating_cash_flow is not None and capital_expenditure is not None
        else fcf
    )
    panel_fcf_source = (
        "+".join(item for item in (operating_cash_flow_source, capital_expenditure_source) if item)
        if operating_cash_flow is not None and capital_expenditure is not None
        else fcf_source
    )
    fcf_margin = (
        panel_fcf / latest_period_revenue * 100
        if panel_fcf is not None and latest_period_revenue not in (None, 0)
        else None
    )

    annual_history = [
        row
        for row in list(fundamental_facts.get("financial_abstract_history") or [])
        if isinstance(row, dict)
    ]
    annual_history.sort(
        key=lambda row: int(str(row.get("报告期") or row.get("report_date") or "0")[:4])
        if str(row.get("报告期") or row.get("report_date") or "")[:4].isdigit()
        else 0
    )
    annual_revenues = [
        value
        for row in annual_history
        if (value := _metric_amount_yi(row, ("营业总收入", "营业收入", "revenue"))) is not None
    ]
    annual_profits = [
        value
        for row in annual_history
        if (value := _metric_amount_yi(row, ("净利润", "归母净利润", "net_profit"))) is not None
    ]
    annual_roes = [
        value
        for row in annual_history
        if (value := _metric_number(row, ("净资产收益率", "ROE", "roe"))) is not None
    ]
    revenue_growth_3y_cagr = (
        ((annual_revenues[-1] / annual_revenues[-4]) ** (1 / 3) - 1) * 100
        if len(annual_revenues) >= 4 and annual_revenues[-1] > 0 and annual_revenues[-4] > 0
        else None
    )
    net_profit_growth_3y = (
        ((annual_profits[-1] / annual_profits[-4]) ** (1 / 3) - 1) * 100
        if len(annual_profits) >= 4 and annual_profits[-1] > 0 and annual_profits[-4] > 0
        else None
    )
    roe_5y_min = min(annual_roes[-5:]) if len(annual_roes) >= 5 else None
    roe_5y_above_15 = sum(value > 15 for value in annual_roes[-5:]) if len(annual_roes) >= 5 else None
    consecutive_profit_years: int | None = None
    if annual_profits:
        consecutive_profit_years = 0
        for value in reversed(annual_profits):
            if value <= 0:
                break
            consecutive_profit_years += 1

    valuation_history = [
        row
        for row in list(market.get("valuation_history") or [])
        if isinstance(row, dict)
    ]
    historical_pe = [
        value
        for row in valuation_history
        if (value := _row_number(row, "PE(TTM)", "pe_ttm", "pe")) is not None and value > 0
    ]
    pe_quantile_5y = (
        sum(value <= pe for value in historical_pe) / len(historical_pe) * 100
        if pe is not None and pe > 0 and len(historical_pe) >= 250
        else None
    )
    segments = financial.get("segments") or financial.get("segment_revenue")
    if not isinstance(segments, list):
        segments = None

    reports = [row for row in list(research.get("reports") or []) if isinstance(row, dict)]
    if name == symbol:
        reported_name = next(
            (
                str(row.get("股票简称") or row.get("name") or "").strip()
                for row in reports
                if str(row.get("股票简称") or row.get("name") or "").strip()
            ),
            "",
        )
        if reported_name:
            name = reported_name
    brokers = {
        str(row.get("机构") or row.get("broker") or "").strip()
        for row in reports
        if str(row.get("机构") or row.get("broker") or "").strip()
    }
    ratings = [
        str(row.get("东财评级") or row.get("评级") or row.get("rating") or "").strip()
        for row in reports
        if str(row.get("东财评级") or row.get("评级") or row.get("rating") or "").strip()
    ]
    research_coverage = len(brokers) if reports else None
    buy_rating_pct = (
        sum(1 for rating in ratings if "买入" in rating or "增持" in rating) / len(ratings) * 100
        if ratings
        else None
    )
    consensus_eps_2026_values = [
        value
        for row in reports
        if (value := _number(row.get("2026-盈利预测-收益") or row.get("consensus_eps_2026"))) is not None
    ]
    consensus_eps_2026 = (
        sum(consensus_eps_2026_values) / len(consensus_eps_2026_values)
        if consensus_eps_2026_values
        else None
    )
    consensus_growth_to_2026 = (
        (consensus_eps_2026 / eps - 1) * 100
        if consensus_eps_2026 is not None and eps not in (None, 0)
        else None
    )

    industry = str(
        industry_facts.get("industry")
        or (industry_facts.get("resolved_sector") or {}).get("sector_name")
        or next((row.get("行业") for row in reports if row.get("行业")), "")
        or info.get("所属行业")
        or ""
    ).strip()

    sentiment_items = _item_value_map(sentiment.get("hot_rank_latest"))
    sentiment_rank = _number(sentiment_items.get("rank"))
    sentiment_universe = _number(sentiment_items.get("marketAllCount"))
    sentiment_calc_date = _parse_date(sentiment_items.get("calcTime"))
    sentiment_snapshot_date = _parse_date((grouped.get("sentiment") or {}).get("snapshot_date"))
    sentiment_is_fresh = (
        sentiment_calc_date is not None
        and (sentiment_snapshot_date is None or abs((sentiment_snapshot_date - sentiment_calc_date).days) <= 7)
    )
    sentiment_heat = (
        max(0.0, min(100.0, (sentiment_universe - sentiment_rank) / max(sentiment_universe - 1, 1) * 100))
        if sentiment_is_fresh
        and sentiment_rank is not None
        and sentiment_universe is not None
        and 1 <= sentiment_rank <= sentiment_universe
        else None
    )

    lhb_statistics = [row for row in list(lhb.get("lhb_statistics") or []) if isinstance(row, dict)]
    lhb_count_values = [
        value
        for row in lhb_statistics
        if (value := _row_number(row, "lhb_count_30d", "上榜次数", "count")) is not None
    ]
    lhb_count_30d: float | None = max(lhb_count_values) if lhb_count_values else None
    if lhb_count_30d is None and lhb_statistics:
        snapshot_day = _parse_date((grouped.get("lhb") or {}).get("snapshot_date")) or date.today()
        parsed_dates = [
            row_date
            for row in lhb_statistics
            if (row_date := _parse_date(row.get("trade_date") or row.get("上榜日期") or row.get("日期")))
        ]
        lhb_count_30d = float(
            sum(0 <= (snapshot_day - row_date).days <= 30 for row_date in parsed_dates)
            if parsed_dates
            else len(lhb_statistics)
        )
    lhb_snapshot = grouped.get("lhb") or {}
    lhb_available = (
        isinstance(lhb_snapshot, dict)
        and not lhb_snapshot.get("degraded")
        and str(lhb.get("availability") or "available") == "available"
    )
    if lhb_count_30d is None and lhb_available and "lhb_statistics" in lhb:
        latest_lhb_day = _parse_date(lhb.get("latest_lhb_date"))
        snapshot_day = _parse_date(lhb_snapshot.get("snapshot_date")) or date.today()
        lhb_count_30d = 1.0 if latest_lhb_day and 0 <= (snapshot_day - latest_lhb_day).days <= 30 else 0.0

    event_rows = [
        row
        for row in [
            *list(events.get("event_notices") or []),
            *list(notices.get("recent_notices") or []),
        ]
        if isinstance(row, dict)
    ]
    event_titles = list(
        dict.fromkeys(
            str(row.get("公告标题") or row.get("标题") or row.get("title") or "").strip()
            for row in event_rows
            if str(row.get("公告标题") or row.get("标题") or row.get("title") or "").strip()
        )
    )
    recent_events_count = len(event_titles) if event_rows else None
    positive_catalyst_keywords = ("预告", "增长", "大订单", "中标", "新品", "合作", "并购", "增持", "回购")
    has_positive_catalyst = (
        any(keyword in title for title in event_titles for keyword in positive_catalyst_keywords)
        if event_rows
        else None
    )

    recent_flows = [row for row in list(capital.get("recent_flows") or []) if isinstance(row, dict)]
    main_flow_values = [
        value
        for row in recent_flows[:5]
        if (value := _metric_amount_yi(row, ("主力净流入-净额", "main_net_inflow", "net_mf_amount"))) is not None
    ]
    main_fund_5d_net_yi = sum(main_flow_values) if main_flow_values else None

    technical_values, technical_sources, technical_derivations = _kline_rule_features(
        kline,
        symbol=symbol,
        name=name,
    )
    # `stock_fund_stock_holder` returns fund products, not named managers.
    # Do not mislabel its row count as the UZI `fund_manager_count` input.
    fund_managers = fund_holders.get("fund_managers")
    fund_manager_count = float(len(fund_managers)) if isinstance(fund_managers, list) else None

    pe_x_pb = pe * pb if pe is not None and pb is not None else None
    if ps is None and market_cap is not None and revenue not in (None, 0):
        ps = market_cap / revenue
    revenue_b = revenue / 10 if revenue is not None else None
    cash_to_marketcap_ratio = cash / market_cap if cash is not None and market_cap not in (None, 0) else None
    market_route = MarketRouter().classify(symbol=symbol, query=name)
    normalized_market = market_route.market_code or None
    country = str(
        market_facts.get("country")
        or fundamental_facts.get("country")
        or market_route.country
        or ""
    ).strip().upper() or None
    currency = str(
        market_facts.get("currency")
        or fundamental_facts.get("currency")
        or info.get("currency")
        or market_route.currency
        or ""
    ).strip().upper() or None
    currency_symbol = _currency_symbol(currency, normalized_market)
    matched_youzi = [
        str(item)
        for item in list(lhb.get("matched_youzi") or [])
        if str(item).strip()
    ]
    ai_evidence_text = " ".join(
        (
            industry,
            name,
            " ".join(event_titles),
            json.dumps(chain_facts, ensure_ascii=False, default=str) if chain_facts else "",
        )
    ).lower()
    ai_chain_keywords = [keyword for keyword in _AI_CHAIN_KEYWORDS if keyword.lower() in ai_evidence_text]
    ai_chain_hit = bool(ai_chain_keywords) if ai_evidence_text.strip() else None
    ai_smallcap = 0 < market_cap < 300 if market_cap is not None else None
    values = {
        "price": price,
        "market_cap_yi": market_cap,
        "market_cap": market_cap * 100_000_000 if market_cap is not None else None,
        "shares_outstanding_yi": shares,
        "revenue_latest_yi": revenue,
        "net_profit_latest_yi": net_profit,
        "fcf_latest_yi": fcf,
        "operating_cash_flow_yi": operating_cash_flow,
        "ocf_to_net_income_ratio": ocf_to_net_income_ratio,
        "total_assets_yi": total_assets,
        "total_liabilities_yi": total_liabilities,
        "cash_yi": cash,
        "equity_yi": equity,
        "total_debt_yi": total_debt,
        "ebitda_yi": ebitda,
        "eps": eps,
        "net_margin": net_margin,
        "pe": pe,
        "pe_ttm": pe,
        "pb": pb,
        "ps": ps,
        "peg": peg,
        "dividend_yield": dividend_yield,
        "pe_x_pb": pe_x_pb,
        "roe": roe,
        "roe_latest": roe,
        "debt_ratio": debt_ratio,
        "revenue_growth": revenue_growth,
        "revenue_growth_latest": revenue_growth,
        "rev_growth_yoy": revenue_growth,
        "revenue_growth_3y_cagr": revenue_growth_3y_cagr,
        "rev_growth_3y": revenue_growth_3y_cagr,
        "rev_growth_3y_pct": revenue_growth_3y_cagr,
        "profit_growth": profit_growth,
        "net_profit_growth_latest": profit_growth,
        "net_profit_growth_3y": net_profit_growth_3y,
        "gross_margin": gross_margin,
        "current_ratio": current_ratio,
        "rd_intensity": rd_intensity,
        "fcf_margin": fcf_margin,
        "fcf_positive": panel_fcf > 0 if panel_fcf is not None else None,
        "revenue_b": revenue_b,
        "rev": revenue_b,
        "cash_to_marketcap_ratio": cash_to_marketcap_ratio,
        "industry": industry or None,
        "research_coverage": research_coverage,
        "buy_rating_pct": buy_rating_pct,
        "consensus_growth_to_2026": consensus_growth_to_2026,
        "sentiment_heat": sentiment_heat,
        "lhb_30d_count": lhb_count_30d,
        "recent_events_count": recent_events_count,
        "has_positive_catalyst": has_positive_catalyst,
        "main_fund_5d_net_yi": main_fund_5d_net_yi,
        "fund_manager_count": fund_manager_count,
        "roe_5y_min": roe_5y_min,
        "roe_5y_above_15": roe_5y_above_15,
        "consecutive_profit_years": consecutive_profit_years,
        "pe_quantile_5y": pe_quantile_5y,
        "market": normalized_market,
        "country": country,
        "currency": currency,
        "currency_symbol": currency_symbol or None,
        "matched_youzi": matched_youzi,
        "ai_chain_hit": ai_chain_hit,
        "ai_chain_keywords": ai_chain_keywords if ai_chain_hit is not None else None,
        "ai_smallcap": ai_smallcap,
        # Latest UZI marks source-less sole-dependency features as explicit
        # missing values so affected rules are skipped rather than failed.
        "founder_active": None,
        "founder_ownership_pct": None,
        "ev_to_revenue": None,
        "governance_score": None,
        "insider_selling_recent": None,
        "retail_holding_pct": None,
        "ceo_promotional_score": None,
        "audit_qualified": None,
        "off_balance_debt_ratio": None,
        "capex_growth_yoy": None,
        "btc_holdings_b": None,
        **technical_values,
    }
    market_source = (
        dict(grouped.get("market", {}).get("source") or {})
        if isinstance(grouped.get("market"), dict)
        else {}
    )
    field_sources = {
        key: source
        for key, source in {
            "revenue_latest_yi": revenue_source,
            "net_profit_latest_yi": net_profit_source,
            "fcf_latest_yi": fcf_source,
            "operating_cash_flow_yi": operating_cash_flow_source,
            "ocf_to_net_income_ratio": ocf_ratio_source,
            "total_assets_yi": total_assets_source,
            "total_liabilities_yi": total_liabilities_source,
            "cash_yi": cash_source,
            "equity_yi": equity_source,
            "total_debt_yi": debt_source,
            "ebitda_yi": ebitda_source,
            "eps": eps_source,
            "net_margin": net_margin_source,
            "roe": roe_source,
            "roe_latest": roe_source,
            "debt_ratio": debt_ratio_source,
            "revenue_growth": revenue_growth_source,
            "revenue_growth_latest": revenue_growth_source,
            "rev_growth_yoy": revenue_growth_source,
            "profit_growth": profit_growth_source,
            "net_profit_growth_latest": profit_growth_source,
            "gross_margin": gross_margin_source,
            "current_ratio": current_ratio_source,
            "rd_intensity": "+".join(item for item in (research_expense_source, latest_period_revenue_source) if item),
            "fcf_margin": panel_fcf_source,
            "fcf_positive": panel_fcf_source,
            "revenue_growth_3y_cagr": "financial_abstract_history" if revenue_growth_3y_cagr is not None else None,
            "rev_growth_3y": "financial_abstract_history" if revenue_growth_3y_cagr is not None else None,
            "rev_growth_3y_pct": "financial_abstract_history" if revenue_growth_3y_cagr is not None else None,
            "net_profit_growth_3y": "financial_abstract_history" if net_profit_growth_3y is not None else None,
            "roe_5y_min": "financial_abstract_history" if roe_5y_min is not None else None,
            "roe_5y_above_15": "financial_abstract_history" if roe_5y_above_15 is not None else None,
            "consecutive_profit_years": "financial_abstract_history" if consecutive_profit_years is not None else None,
            "pe_quantile_5y": "market_snapshot.market_overview.valuation_history" if pe_quantile_5y is not None else None,
            "dividend_yield": dividend_yield_source,
        }.items()
        if source
    }
    if market_source:
        field_sources.update({
            "price": "market_snapshot",
            "pe": "market_snapshot",
            "pb": "market_snapshot",
            "market_cap_yi": "market_snapshot",
            "pct_change": "market_snapshot",
            "pe_ttm": "market_snapshot",
            "ps": "market_snapshot" if _first_number(market, ("ps", "市销率")) is not None else "+".join(item for item in ("market_snapshot", revenue_source) if item),
            "peg": "market_snapshot",
            "pe_x_pb": "market_snapshot",
            "cash_to_marketcap_ratio": "+".join(item for item in (cash_source, "market_snapshot") if item),
        })
    field_sources.update(technical_sources)
    field_sources.update({
        key: source
        for key, source in {
        "industry": "industry_snapshot" if industry_facts.get("industry") else "research_snapshot" if reports else None,
        "research_coverage": "research_snapshot.reports" if reports else None,
        "buy_rating_pct": "research_snapshot.reports" if ratings else None,
        "consensus_growth_to_2026": "research_snapshot.reports+fundamental_snapshot" if consensus_growth_to_2026 is not None else None,
        "sentiment_heat": "sentiment_snapshot.hot_rank_latest" if sentiment_heat is not None else None,
        "lhb_30d_count": "lhb_snapshot.lhb_statistics" if lhb_count_30d is not None else None,
        "recent_events_count": "events_snapshot+notice_snapshot" if recent_events_count is not None else None,
        "has_positive_catalyst": "events_snapshot+notice_snapshot" if has_positive_catalyst is not None else None,
        "main_fund_5d_net_yi": "capital_flow_snapshot.recent_flows" if main_fund_5d_net_yi is not None else None,
        "fund_manager_count": "fund_holders_snapshot.holders" if fund_manager_count is not None else None,
        "market": "symbol_identity" if normalized_market else None,
        "country": "symbol_identity" if normalized_market else None,
        "currency": "market_snapshot+fundamental_snapshot+symbol_identity" if currency else None,
        "currency_symbol": "currency_mapping" if currency_symbol else None,
        "revenue_b": revenue_source,
        "rev": revenue_source,
        "ai_chain_hit": "industry_snapshot+chain_snapshot+events_snapshot" if ai_chain_hit is not None else None,
        "ai_chain_keywords": "industry_snapshot+chain_snapshot+events_snapshot" if ai_chain_hit is not None else None,
        "ai_smallcap": "market_snapshot" if ai_smallcap is not None else None,
        }.items()
        if source
    })
    assumptions = [
        item
        for item in (debt_derivation, ocf_ratio_derivation)
        if item
    ]
    feature_derivations = [
        *technical_derivations,
        *(
            ["sentiment_heat=current_rank_percentile_within_market_universe"]
            if sentiment_heat is not None
            else []
        ),
        *(
            ["fcf_margin=(operating_cash_flow-capital_expenditure)/same_period_revenue*100"]
            if fcf_margin is not None and capital_expenditure is not None
            else []
        ),
        *(
            ["rd_intensity=research_and_development_expenses/same_period_revenue*100"]
            if rd_intensity is not None
            else []
        ),
        *(["pe_x_pb=pe_ttm*pb"] if pe_x_pb is not None else []),
        *(["ps=market_cap/revenue"] if ps is not None and _first_number(market, ("ps", "市销率")) is None else []),
        *(["cash_to_marketcap_ratio=cash/market_cap"] if cash_to_marketcap_ratio is not None else []),
        *(["revenue_growth_3y_cagr=(revenue_t/revenue_t_minus_3)^(1/3)-1"] if revenue_growth_3y_cagr is not None else []),
        *(["net_profit_growth_3y=(profit_t/profit_t_minus_3)^(1/3)-1"] if net_profit_growth_3y is not None else []),
        *(["pe_quantile_5y=empirical_percentile_of_positive_daily_pe_ttm"] if pe_quantile_5y is not None else []),
        *(["ai_chain_hit=keyword_matches_in_verified_industry_chain_and_event_evidence"] if ai_chain_hit is not None else []),
    ]
    return {
        "symbol": symbol,
        "ticker": symbol,
        "name": name,
        "data_period": data_period,
        **values,
        "pct_change": pct_change,
        "amount": amount,
        "notice_count": _first_number(notices, ("notice_count", "disclosures_count")),
        "capital_flow": capital,
        "sentiment": sentiment,
        "segments": segments,
        "field_sources": field_sources,
        "feature_derivations": feature_derivations,
        "assumptions": assumptions,
        "data_gap": [key for key, value in values.items() if value is None],
        "sector": str(
            _facts(grouped.get("sector")).get("sector")
            or _facts(grouped.get("sector")).get("name")
            or industry
            or ""
        ),
        "available_fields": [key for key, value in values.items() if value is not None],
    }


UZI_QUICK_SCAN_INVESTOR_IDS: tuple[str, ...] = (
    "buffett",
    "duan",
    "lynch",
    "oneill",
    "wood",
    "marks",
    "minervini",
    "zhang_mz",
    "zhao_lg",
    "chen_xq",
)
UZI_QUICK_SCAN_DIMENSIONS: tuple[str, ...] = (
    "1_financials",
    "2_kline",
    "10_valuation",
    "18_trap",
)


def _quick_scan_dimension_data(
    snapshot_rows: list[dict[str, Any]],
    symbol: str,
    snapshot_type: str,
) -> tuple[dict[str, Any], str]:
    rows = [
        row
        for row in snapshot_rows
        if isinstance(row, dict)
        and str(row.get("symbol") or "") == symbol
        and str(row.get("snapshot_type") or "") == snapshot_type
    ]
    if not rows:
        return {}, "data_gap"
    row = rows[-1]
    facts = _facts(row)
    raw = facts.get("_uzi_raw_result")
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        data = {
            key: value
            for key, value in facts.items()
            if not str(key).startswith("_key")
            and not str(key).startswith("_uzi_")
            and key not in {"availability", "market_overview", "financial_abstract", "history"}
        }
    quality = str(row.get("quality_status") or facts.get("availability") or "").strip().lower()
    if not data or quality == "data_gap":
        return {}, "data_gap"
    return dict(data), quality or "available"


def build_quick_scan(snapshot_rows: list[dict[str, Any]], symbols: list[str]) -> dict[str, Any]:
    """Implement UZI 3.9.4 `/quick-scan` as its own delivery contract.

    The command is deliberately not an alias for Cognitrade's historical
    ``lite`` profile: it consumes exactly dimensions 1/2/10/18, evaluates the
    exact ten seats named by upstream, and emits Markdown rather than HTML.
    """

    symbol = str((symbols or [""])[0] or "").strip()
    snapshot_types = ("fundamental", "kline", "valuation", "trap")
    dimension_status: dict[str, str] = {}
    for dimension_id, snapshot_type in zip(UZI_QUICK_SCAN_DIMENSIONS, snapshot_types):
        _data, status = _quick_scan_dimension_data(snapshot_rows, symbol, snapshot_type)
        dimension_status[dimension_id] = status

    features = build_features(snapshot_rows, symbol) if symbol else {}
    trap_data, trap_status = _quick_scan_dimension_data(snapshot_rows, symbol, "trap")
    trap_level = str(trap_data.get("trap_level") or "").strip() or None
    # Upstream stock_features defaults an absent trap payload to safe.  The
    # product policy is stricter: only a real dimension-18 observation can
    # establish safety.
    features["trap_level"] = trap_level
    features["is_safe"] = ("安全" in trap_level) if trap_level else None

    seats_by_id = {str(row.get("id") or ""): row for row in PANEL_REGISTRY}
    votes = [
        {"symbol": symbol, **_registered_panel_vote(dict(seats_by_id[investor_id]), features)}
        for investor_id in UZI_QUICK_SCAN_INVESTOR_IDS
    ]
    active_votes = [
        vote
        for vote in votes
        if vote.get("signal") != "skip"
        and int(vote.get("pass_count") or 0) + int(vote.get("fail_count") or 0) > 0
    ]
    scores = [
        float(vote["score"])
        for vote in active_votes
        if isinstance(vote.get("score"), (int, float)) and float(vote["score"]) >= 0
    ]
    aggregate_score = round(sum(scores) / len(scores), 1) if scores else None
    if aggregate_score is None:
        verdict = "核心维度证据不足，暂不能形成速判结论"
    elif aggregate_score >= 65:
        verdict = "核心四维与方法论投票整体偏积极，但仍需核对风险与缺口"
    elif aggregate_score < 35:
        verdict = "核心四维与方法论投票整体偏谨慎，不宜忽略已识别风险"
    else:
        verdict = "核心四维与方法论投票分歧较大，当前以中性观察为宜"

    risks: list[str] = []
    for detail in list(trap_data.get("signals_hit_detail") or []):
        if not isinstance(detail, dict):
            continue
        name = str(detail.get("name") or "").strip()
        evidence = "、".join(str(item) for item in list(detail.get("evidence_kws") or []) if str(item).strip())
        if name:
            risks.append(f"{name}{f'（{evidence}）' if evidence else ''}")
    missing_dimensions = [key for key, status in dimension_status.items() if status == "data_gap"]
    if trap_status == "data_gap":
        risks.append("杀猪盘检测没有取得真实来源结果，安全等级未知")
    if missing_dimensions:
        risks.append(f"数据维度缺口：{', '.join(missing_dimensions)}")
    risks = list(dict.fromkeys(risks))[:2]
    if not risks:
        risks = ["未命中明确推广风险，但速判只覆盖四个核心维度"]

    signal_labels = {
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
        "skip": "跳过",
    }
    vote_lines = [
        (
            f"- {vote['perspective_label']}：{signal_labels.get(str(vote.get('signal') or ''), str(vote.get('signal') or '未知'))}"
            + (
                f" · {float(vote['score']):.1f} 分"
                if isinstance(vote.get("score"), (int, float)) and float(vote["score"]) >= 0
                else ""
            )
            + f" · {vote.get('headline') or vote.get('reasoning') or '无有效规则命中'}"
        )
        for vote in votes
    ]
    markdown = "\n".join(
        [
            f"# {symbol or '未知标的'} · UZI 速判",
            "",
            f"**一句话定调：** {verdict}。",
            "",
            f"**综合评分：** {f'{aggregate_score:.1f}/100' if aggregate_score is not None else '数据不足，未评分'}",
            "",
            "## 10 位方法论投票",
            "",
            *vote_lines,
            "",
            f"**杀猪盘安全等级：** {trap_level or '数据缺口，无法判断'}",
            "",
            "## 关键风险",
            "",
            *[f"- {risk}" for risk in risks],
            "",
            "> 仅为基于当前真实来源的研究辅助信息，不构成投资建议。",
        ]
    )
    available_count = sum(status != "data_gap" for status in dimension_status.values())
    quality_status = (
        "available"
        if available_count == len(UZI_QUICK_SCAN_DIMENSIONS) and aggregate_score is not None
        else "degraded"
        if available_count
        else "data_gap"
    )
    confidence = (
        round(sum(float(vote.get("confidence") or 0.0) for vote in active_votes) / len(active_votes), 4)
        if active_votes
        else 0.0
    )
    return {
        "command": "quick-scan",
        "methodology_version": UZI_METHOD_VERSION,
        "source_commit": UZI_SOURCE_COMMIT,
        "symbol": symbol or None,
        "required_dimensions": list(UZI_QUICK_SCAN_DIMENSIONS),
        "dimension_status": dimension_status,
        "investor_ids": list(UZI_QUICK_SCAN_INVESTOR_IDS),
        "verdict": verdict,
        "aggregate_score": aggregate_score,
        "votes": votes,
        "trap_level": trap_level,
        "trap_signals_hit": trap_data.get("signals_hit_count"),
        "risks": risks,
        "markdown": markdown,
        "output_format": "markdown",
        "html_generated": False,
        "quality_status": quality_status,
        "confidence": confidence,
        "data_gap": missing_dimensions,
        "risk_flags": [
            "decision_support_only",
            *(('trap_status_unknown',) if trap_status == "data_gap" else ()),
        ],
    }


_PANEL_FIELD_FEATURES: dict[str, tuple[str, ...]] = {
    "1_financials": ("revenue_latest_yi", "net_profit_latest_yi", "operating_cash_flow_yi", "roe", "net_margin"),
    "2_kline": ("stage_num", "ma_bull_aligned", "rsi", "volatility_1y"),
    "3_macro": ("macro_rate_cycle", "macro_rate_easing"),
    "4_peers": ("vs_peer_avg_pe", "industry_rank"),
    "5_chain": ("ai_chain_hit", "ai_chain_keywords"),
    "7_industry": ("industry", "industry_growth", "industry_lifecycle"),
    "9_futures": ("macro_commodity",),
    "10_valuation": ("pe", "pb", "market_cap_yi"),
    "11_governance": ("governance_score", "no_violations", "has_pledge_issue"),
    "12_capital_flow": ("main_fund_5d_net_yi",),
    "13_policy": ("policy_supportive",),
    "14_moat": ("moat_total", "moat_intangible", "moat_network", "moat_scale"),
    "15_events": ("recent_events_count", "has_positive_catalyst"),
    "16_lhb": ("lhb_30d_count",),
    "17_sentiment": ("sentiment_heat",),
    "18_trap": ("is_safe",),
}


def _registered_panel_vote(investor: dict[str, object], features: dict[str, Any]) -> dict[str, Any]:
    fields = [str(item) for item in list(investor.get("fields") or [])]
    checks: list[dict[str, Any]] = []
    for field in fields:
        feature_names = _PANEL_FIELD_FEATURES.get(field, ())
        present = any(features.get(name) is not None for name in feature_names)
        checks.append(
            {
                "dimension": field,
                "status": "available" if present else "data_gap",
                "feature_names": list(feature_names),
            }
        )
    # Preserve explicit ``None`` observations.  UZI main (22e65f2) relies on
    # those values to distinguish "source missing" (rule skipped) from a real
    # observation that fails the threshold.  Removing them would activate
    # many ``dict.get(..., default)`` fallbacks and silently recreate the old
    # "missing data = failed rule" behaviour.
    rule_result = evaluate_investor(str(investor.get("id") or ""), features)
    available = [item for item in checks if item["status"] == "available"]
    passed = len(available)
    total = len(checks)
    score = float(rule_result.get("score") or 0.0)
    uzi_confidence = float(rule_result.get("confidence") or 0.0)
    confidence = max(0.0, min(1.0, uzi_confidence / 100.0))
    signal = str(rule_result.get("signal") or "neutral")
    label = str(investor.get("name") or investor.get("id") or "方法论席位")
    return {
        "investor_id": str(investor.get("id") or ""),
        "perspective_id": str(investor.get("id") or ""),
        "perspective_label": label,
        "investor_name_en": investor.get("en"),
        "methodology_source": investor.get("source"),
        "avatar_seed": investor.get("avatar_seed"),
        "tier": investor.get("tier"),
        "school": str(investor.get("group") or ""),
        "required_dimensions": fields,
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "uzi_confidence": uzi_confidence,
        "reasoning": str(rule_result.get("rationale") or f"{label}规则覆盖 {passed}/{total} 个声明维度。"),
        "headline": str(rule_result.get("headline") or "数据不足，暂无判断。"),
        "pass_rules": list(rule_result.get("pass_rules") or []),
        "fail_rules": list(rule_result.get("fail_rules") or []),
        "pass_count": int(rule_result.get("pass_count") or 0),
        "fail_count": int(rule_result.get("fail_count") or 0),
        "skipped_rule_count": int(rule_result.get("skipped_rule_count") or 0),
        "weight_pass": int(rule_result.get("weight_pass") or 0),
        "weight_total": int(rule_result.get("weight_total") or 0),
        "rule_engine_version": str(rule_result.get("rule_engine_version") or ""),
        "skip_reason": rule_result.get("skip_reason"),
        "time_horizon": rule_result.get("time_horizon"),
        "position_sizing": rule_result.get("position_sizing"),
        "what_would_change_my_mind": rule_result.get("what_would_change_my_mind"),
        "checks": checks,
        "is_methodology_perspective": True,
        "is_real_person_advice": False,
        "rule_mode": "uzi_3_9_2_source_parity",
    }



__all__ = [
    "UZI_METHOD_VERSION",
    "UZI_SOURCE_COMMIT",
    "build_features",
    "build_quick_scan",
    "select_profile",
    "UZI_QUICK_SCAN_DIMENSIONS",
    "UZI_QUICK_SCAN_INVESTOR_IDS",
]
