from __future__ import annotations

from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import normalize_value


class TuShareFundamentalAdapter:
    vendor_name = "tushare"

    def __init__(self, token: str) -> None:
        self.token = str(token or "").strip()
        if not self.token:
            raise ValueError("tushare token is required")

    def get_fundamental_snapshot(self, *, symbol: str) -> dict[str, Any]:
        ts = import_module("tushare")
        pro = ts.pro_api(self.token)
        ts_code = self._ts_code(symbol)
        frames: dict[str, Any] = {}
        unavailable: list[str] = []
        calls = {
            "basic": lambda: pro.stock_basic(
                ts_code=ts_code,
                fields="ts_code,symbol,name,area,industry,market,list_date",
            ),
            "indicator": lambda: pro.fina_indicator(ts_code=ts_code, limit=20),
            "daily_basic": lambda: pro.daily_basic(
                ts_code=ts_code,
                fields="ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,total_mv,circ_mv",
                limit=20,
            ),
            "income": lambda: pro.income(ts_code=ts_code, limit=20),
            "balance": lambda: pro.balancesheet(ts_code=ts_code, limit=20),
            "cash_flow": lambda: pro.cashflow(ts_code=ts_code, limit=20),
        }
        for name, call in calls.items():
            try:
                frames[name] = call()
            except Exception:
                frames[name] = None
                unavailable.append(name)
        if not any(frame is not None and not getattr(frame, "empty", True) for frame in frames.values()):
            if unavailable:
                raise PermissionError("tushare token has no accessible fundamental interfaces")
            raise ValueError(f"no fundamental data returned for symbol {symbol}")
        income = self._records(frames["income"])
        balance = self._records(frames["balance"])
        cash_flow = self._records(frames["cash_flow"])
        return {
            "symbol": symbol,
            "info": self._first_record(frames["basic"]),
            "financial_abstract": {
                "income": income[0] if income else {},
                "balance": balance[0] if balance else {},
                "cash_flow": cash_flow[0] if cash_flow else {},
            },
            "financial_analysis": self._first_record(frames["indicator"]),
            "market_valuation": self._first_record(frames["daily_basic"]),
            "financial_statements": {
                "income": income,
                "balance": balance,
                "cash_flow": cash_flow,
            },
            "unavailable_interfaces": unavailable,
        }

    @staticmethod
    def _first_record(frame) -> dict[str, Any]:
        if frame is None or getattr(frame, "empty", True):
            return {}
        return {str(key): normalize_value(value) for key, value in frame.iloc[0].to_dict().items()}

    @staticmethod
    def _records(frame) -> list[dict[str, Any]]:
        if frame is None or getattr(frame, "empty", True):
            return []
        return [
            {str(key): normalize_value(value) for key, value in row.items()}
            for row in frame.to_dict("records")
        ]

    @staticmethod
    def _ts_code(symbol: str) -> str:
        raw = str(symbol).lower().replace("sh", "").replace("sz", "").replace("bj", "").replace(".", "")[-6:]
        exchange = "BJ" if raw.startswith(("4", "8")) else "SH" if raw.startswith(("5", "6", "9")) else "SZ"
        return f"{raw}.{exchange}"
