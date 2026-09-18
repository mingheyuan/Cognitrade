"""Numeric units at the host-provider → pinned UZI boundary, not model formulas."""
from collections.abc import Mapping
from copy import deepcopy
import math


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def project_host_numbers(data, dimension, *, include_display_aliases=False):
    """Preserve source values; convert only fields with a known provider unit.

    UZI stock_features reads market_cap[_yi], not market_cap_raw, and expects
    financial_health monetary fields in 亿. Our generic providers use base
    currency units. Never guess units from magnitude or convert ratios/dates.
    The explicit health marker makes newly collected inputs idempotent.
    """
    result = deepcopy(dict(data))
    if dimension == "0_basic":
        for field in ("market_cap", "circulating_cap"):
            value = _number(result.get(f"{field}_raw"))
            if (value is not None and value >= 0
                    and result.get(field) in (None, "")
                    and result.get(f"{field}_yi") in (None, "")):
                result[f"{field}_yi"] = value / 1e8
            if include_display_aliases and result.get(field) in (None, ""):
                native_yi = _number(result.get(f"{field}_yi"))
                if native_yi is not None and native_yi >= 0:
                    # Native integrity checks also read the display alias,
                    # while stock_features prefers the unrounded _yi number.
                    # Match data_sources' base-currency → '亿' formatting.
                    result[field] = f"{round(native_yi, 1)}亿"
    elif dimension == "1_financials":
        health = result.get("financial_health")
        if (isinstance(health, Mapping)
                and health.get("source") == "akshare.stock_balance_sheet_by_report_em"
                and health.get("_monetary_unit", "base_currency") == "base_currency"):
            health = dict(health)
            for field in ("cash", "total_debt", "equity", "total_liabilities", "total_assets"):
                value = _number(health.get(field))
                if value is not None:
                    # Identical scaling/precision to pinned fetch_financials.
                    health[field] = round(value / 1e8, 2)
            health["_monetary_unit"] = "yi"
            result["financial_health"] = health
    return result
