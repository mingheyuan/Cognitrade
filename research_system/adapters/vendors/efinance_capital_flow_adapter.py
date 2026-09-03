from __future__ import annotations

from datetime import date, datetime
from importlib import import_module
from typing import Any

import requests

from research_system.adapters.vendors.akshare_utils import (
    coerce_numeric,
    normalize_value,
)


class EFinanceCapitalFlowAdapter:
    """A-share individual capital-flow fallback backed by eFinance."""

    vendor_name = "efinance"

    def get_capital_flow_snapshot(
        self, *, symbol: str, as_of_date: date | None = None
    ) -> dict[str, Any]:
        provider_symbol = self._provider_symbol(symbol)
        bounded_error: Exception | None = None
        try:
            rows = self._bounded_history_rows(
                provider_symbol,
                limit=420 if as_of_date is not None else 60,
            )
        except Exception as exc:
            bounded_error = exc
            rows = []
        if not rows and as_of_date is None:
            # Retain the installed efinance SDK as a compatibility fallback.
            # Current efinance versions request the complete history with
            # lmt=100000, which EastMoney may disconnect before returning.  A
            # bounded 60-row request is sufficient for the strict 20-day
            # contract and avoids turning a target-stock retry into an
            # unbounded source call.
            try:
                ef = import_module("efinance")
                history = ef.stock.get_history_bill(provider_symbol)
            except Exception:
                if bounded_error is not None:
                    raise bounded_error
                raise
            if history is None or getattr(history, "empty", True):
                raise ValueError(f"no capital flow data returned for symbol {symbol}")
            rows = list(history.to_dict("records"))

        rows = [self._normalize_row(row) for row in rows]
        if as_of_date is not None:
            rows = [
                row
                for row in rows
                if self._date_value(row) is not None
                and self._date_value(row) <= as_of_date
            ]
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

    @classmethod
    def _date_value(cls, row: dict[str, Any]) -> date | None:
        normalized = cls._date_sort_key(row).replace("/", "-")
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(normalized[:10], fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _bounded_history_rows(
        provider_symbol: str, *, limit: int = 60
    ) -> list[dict[str, Any]]:
        market = "1" if provider_symbol.startswith(("5", "6", "9")) else "0"
        fields = {
            "f51": "日期",
            "f52": "主力净流入",
            "f53": "小单净流入",
            "f54": "中单净流入",
            "f55": "大单净流入",
            "f56": "超大单净流入",
            "f57": "主力净流入占比",
            "f58": "小单流入净占比",
            "f59": "中单流入净占比",
            "f60": "大单流入净占比",
            "f61": "超大单流入净占比",
            "f62": "收盘价",
            "f63": "涨跌幅",
        }
        response = requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params={
                "lmt": str(max(20, min(int(limit), 500))),
                "klt": "101",
                "secid": f"{market}.{provider_symbol}",
                "fields1": "f1,f2,f3,f7",
                "fields2": ",".join(fields),
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/138 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
                "Connection": "close",
            },
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        klines = data.get("klines") if isinstance(data, dict) else None
        if not isinstance(klines, list) or not klines:
            return []
        columns = list(fields.values())
        name = str(data.get("name") or "")
        rows: list[dict[str, Any]] = []
        for encoded in klines:
            values = str(encoded or "").split(",")
            if len(values) != len(columns):
                continue
            row = dict(zip(columns, values, strict=True))
            row.update({"股票代码": provider_symbol, "股票名称": name})
            rows.append(row)
        return rows

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
