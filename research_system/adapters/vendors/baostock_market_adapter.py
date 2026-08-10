from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import (
    coerce_numeric,
    normalize_value,
)
from research_system.adapters.vendors.baostock_socket_safety import (
    install_baostock_socket_safety,
)


class BaoStockMarketAdapter:
    """Independent daily K-line fallback for the A-share market dimension."""

    vendor_name = "baostock"

    def get_market_snapshot(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
    ) -> dict[str, Any]:
        install_baostock_socket_safety()
        bs = import_module("baostock")
        login = bs.login()
        if str(getattr(login, "error_code", "0")) != "0":
            raise ConnectionError("baostock login failed")
        code = self._code(symbol)
        try:
            result = bs.query_history_k_data_plus(
                code,
                "date,code,open,high,low,close,preclose,volume,amount,adjustflag",
                start_date=self._date(start_date),
                end_date=self._date(end_date),
                frequency="d",
                adjustflag={"qfq": "2", "hfq": "1"}.get(adjust, "3"),
            )
            if str(getattr(result, "error_code", "0")) != "0":
                raise ConnectionError("baostock history query failed")
            bars: list[dict[str, Any]] = []
            fields = list(getattr(result, "fields", []))
            while result.next():
                row = dict(zip(fields, result.get_row_data()))
                bars.append({key: normalize_value(value) for key, value in row.items()})
        finally:
            bs.logout()
        if not bars:
            raise ValueError(f"no market data returned for symbol {symbol}")
        return {
            "symbol": symbol,
            "normalized_symbol": code,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
            "bars": bars,
            "latest_bar": dict(bars[-1]),
            "bar_count": len(bars),
        }

    def get_market_overview(self, *, symbol: str, latest_bar: dict[str, Any] | None = None) -> dict[str, Any]:
        bar = dict(latest_bar or {})
        close = coerce_numeric(bar.get("close"))
        previous = coerce_numeric(bar.get("preclose"))
        pct_change = None
        if close is not None and previous not in (None, 0):
            pct_change = (close - previous) / previous * 100
        return {
            "symbol": symbol,
            "normalized_symbol": self._code(symbol),
            "latest_price": close,
            "pct_change": pct_change,
            "amount": coerce_numeric(bar.get("amount")),
            "spot": bar,
            "valuation": {},
            "spot_found": bool(bar),
            "valuation_found": False,
        }

    @staticmethod
    def _code(symbol: str) -> str:
        raw = str(symbol).lower().replace("sh", "").replace("sz", "").replace(".", "")[-6:]
        exchange = "sh" if raw.startswith(("5", "6", "9")) else "sz"
        return f"{exchange}.{raw}"

    @staticmethod
    def _date(value: str) -> str:
        return datetime.strptime(str(value), "%Y%m%d").strftime("%Y-%m-%d")
