from __future__ import annotations

from datetime import date, datetime
from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import (
    coerce_numeric,
    normalize_value,
)


class EFinanceCapitalFlowAdapter:
    """A-share individual capital-flow fallback backed by eFinance."""

    vendor_name = "efinance"

    def get_capital_flow_snapshot(self, *, symbol: str) -> dict[str, Any]:
        provider_symbol = self._provider_symbol(symbol)
        ef = import_module("efinance")
        history = ef.stock.get_history_bill(provider_symbol)
        if history is None or getattr(history, "empty", True):
            raise ValueError(f"no capital flow data returned for symbol {symbol}")

        rows = [self._normalize_row(row) for row in history.to_dict("records")]
        rows.sort(key=self._date_sort_key)
        main_fund_flow_20d = rows[-20:]
        recent_flows = list(reversed(main_fund_flow_20d))
        if not recent_flows:
            raise ValueError(f"no capital flow data returned for symbol {symbol}")

        return {
            "symbol": symbol,
            "normalized_symbol": provider_symbol,
            "latest_flow": dict(recent_flows[0]),
            "recent_flows": recent_flows,
            # Preserve the native UZI field so report dimensions can consume
            # this fallback without inventing a second capital-flow contract.
            "main_fund_flow_20d": main_fund_flow_20d,
            "main_20d": self._summary(main_fund_flow_20d),
            "main_5d": self._summary(recent_flows[:5]),
            "main_flow_rank": None,
            "main_flow_rank_status": "not_provided_by_efinance_history_bill",
        }

    @staticmethod
    def _provider_symbol(symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        for suffix in (".SH", ".SZ", ".BJ"):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
                break
        if raw.startswith(("SH", "SZ", "BJ")):
            raw = raw[2:]
        if len(raw) != 6 or not raw.isdigit():
            raise ValueError(
                "efinance capital-flow adapter only supports A-share symbols"
            )
        return raw

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        aliases = {
            "日期": "date",
            "股票名称": "name",
            "股票代码": "symbol",
            "收盘价": "close",
            "收盘": "close",
            "涨跌幅": "pct_change",
            "主力净流入": "main_net_inflow",
            "主力净流入占比": "main_net_inflow_ratio",
            "超大单净流入": "super_large_net_inflow",
            "超大单净流入占比": "super_large_net_inflow_ratio",
            "大单净流入": "large_net_inflow",
            "大单净流入占比": "large_net_inflow_ratio",
            "中单净流入": "medium_net_inflow",
            "中单净流入占比": "medium_net_inflow_ratio",
            "小单净流入": "small_net_inflow",
            "小单净流入占比": "small_net_inflow_ratio",
        }
        normalized = {str(key): normalize_value(value) for key, value in row.items()}
        for source, target in aliases.items():
            if source not in normalized:
                continue
            value = normalized[source]
            if target == "date":
                normalized[target] = value
            elif target in {"name", "symbol"}:
                normalized[target] = str(value or "")
            else:
                normalized[target] = coerce_numeric(value)
        return normalized

    @staticmethod
    def _date_sort_key(row: dict[str, Any]) -> str:
        value = row.get("date") or row.get("日期") or ""
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _summary(rows: list[dict[str, Any]]) -> str:
        values = [coerce_numeric(row.get("main_net_inflow")) for row in rows]
        total = sum(value for value in values if value is not None)
        if not any(value is not None for value in values):
            return "—"
        if abs(total) >= 100_000_000:
            return f"{total / 100_000_000:+.1f}亿"
        return f"{total / 10_000:+.1f}万"
