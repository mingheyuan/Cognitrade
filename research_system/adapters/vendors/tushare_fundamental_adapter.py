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
        basic = pro.stock_basic(ts_code=ts_code, fields="ts_code,symbol,name,area,industry,market,list_date")
        indicator = pro.fina_indicator(ts_code=ts_code, limit=1)
        daily_basic = pro.daily_basic(
            ts_code=ts_code,
            fields="ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,total_mv,circ_mv",
            limit=1,
        )
        if basic.empty and indicator.empty and daily_basic.empty:
            raise ValueError(f"no fundamental data returned for symbol {symbol}")
        return {
            "symbol": symbol,
            "info": self._first_record(basic),
            "financial_abstract": {},
            "financial_analysis": self._first_record(indicator),
            "market_valuation": self._first_record(daily_basic),
        }

    @staticmethod
    def _first_record(frame) -> dict[str, Any]:
        if frame is None or getattr(frame, "empty", True):
            return {}
        return {str(key): normalize_value(value) for key, value in frame.iloc[0].to_dict().items()}

    @staticmethod
    def _ts_code(symbol: str) -> str:
        raw = str(symbol).lower().replace("sh", "").replace("sz", "").replace(".", "")[-6:]
        exchange = "SH" if raw.startswith(("5", "6", "9")) else "SZ"
        return f"{raw}.{exchange}"
