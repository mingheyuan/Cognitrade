from __future__ import annotations

from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import coerce_numeric, normalize_value


class TuShareMarketAdapter:
    vendor_name = "tushare"

    def __init__(self, token: str) -> None:
        self.token = str(token or "").strip()
        if not self.token:
            raise ValueError("tushare token is required")

    def get_market_snapshot(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
    ) -> dict[str, Any]:
        ts = import_module("tushare")
        pro = ts.pro_api(self.token)
        ts_code = self._ts_code(symbol)
        frame = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if frame is None or frame.empty:
            raise ValueError(f"no market data returned for symbol {symbol}")
        if adjust in {"qfq", "hfq"}:
            frame = self._apply_adjustment(pro, frame, ts_code, start_date, end_date, adjust)
        bars = []
        for row in frame.sort_values("trade_date").to_dict("records"):
            bars.append(
                {
                    "date": self._date(row.get("trade_date")),
                    "open": normalize_value(row.get("open")),
                    "high": normalize_value(row.get("high")),
                    "low": normalize_value(row.get("low")),
                    "close": normalize_value(row.get("close")),
                    "pre_close": normalize_value(row.get("pre_close")),
                    "volume": normalize_value(row.get("vol")),
                    "amount": normalize_value(row.get("amount")),
                    "pct_change": normalize_value(row.get("pct_chg")),
                }
            )
        return {
            "symbol": symbol,
            "normalized_symbol": ts_code,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
            "bars": bars,
            "latest_bar": dict(bars[-1]),
            "bar_count": len(bars),
        }

    def get_market_overview(self, *, symbol: str, latest_bar: dict[str, Any] | None = None) -> dict[str, Any]:
        bar = dict(latest_bar or {})
        return {
            "symbol": symbol,
            "normalized_symbol": self._ts_code(symbol),
            "latest_price": coerce_numeric(bar.get("close")),
            "pct_change": coerce_numeric(bar.get("pct_change")),
            "amount": coerce_numeric(bar.get("amount")),
            "spot": bar,
            "valuation": {},
            "spot_found": bool(bar),
            "valuation_found": False,
        }

    @staticmethod
    def _apply_adjustment(pro, frame, ts_code: str, start_date: str, end_date: str, adjust: str):
        factors = pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if factors is None or factors.empty:
            return frame
        merged = frame.merge(factors[["trade_date", "adj_factor"]], on="trade_date", how="left")
        anchor = float(merged["adj_factor"].iloc[0 if adjust == "qfq" else -1])
        if not anchor:
            return frame
        ratio = merged["adj_factor"] / anchor
        for column in ("open", "high", "low", "close", "pre_close"):
            if column in merged:
                merged[column] = merged[column] * ratio
        return merged

    @staticmethod
    def _ts_code(symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        if "." in raw:
            code, suffix = raw.split(".", 1)
            if suffix in {"SH", "SZ", "BJ"}:
                return f"{code[-6:]}.{suffix}"
        code = "".join(char for char in raw if char.isdigit())[-6:]
        suffix = "BJ" if code.startswith(("4", "8")) else "SH" if code.startswith(("5", "6", "9")) else "SZ"
        return f"{code}.{suffix}"

    @staticmethod
    def _date(value: Any) -> str:
        text = str(value or "")
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else text
