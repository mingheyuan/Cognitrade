from __future__ import annotations

from datetime import date, timedelta
from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import normalize_value


class TuShareCapitalFlowAdapter:
    vendor_name = "tushare"

    def __init__(self, token: str) -> None:
        self.token = str(token or "").strip()

    def get_capital_flow_snapshot(self, *, symbol: str) -> dict[str, Any]:
        pro = import_module("tushare").pro_api(self.token)
        frame = pro.moneyflow(
            ts_code=_ts_code(symbol),
            start_date=(date.today() - timedelta(days=45)).strftime("%Y%m%d"),
            end_date=date.today().strftime("%Y%m%d"),
        )
        rows = _records(frame)
        if not rows:
            raise ValueError(f"no capital flow data returned for symbol {symbol}")
        return {
            "symbol": symbol,
            "latest_flow": rows[0],
            "recent_flows": rows[:10],
            "main_flow_rank": None,
            "main_flow_rank_status": "not_provided_by_tushare_moneyflow",
        }


class TuShareLHBAdapter:
    vendor_name = "tushare"

    def __init__(self, token: str) -> None:
        self.token = str(token or "").strip()

    def get_lhb_snapshot(self, *, symbol: str) -> dict[str, Any]:
        pro = import_module("tushare").pro_api(self.token)
        frame = pro.top_list(
            ts_code=_ts_code(symbol),
            start_date=(date.today() - timedelta(days=30)).strftime("%Y%m%d"),
            end_date=date.today().strftime("%Y%m%d"),
        )
        rows = _records(frame)
        if not rows:
            return {
                "symbol": symbol,
                "latest_lhb_date": None,
                "buy_detail": [],
                "sell_detail": [],
                "lhb_statistics": [],
                "lhb_count_30d": 0,
                "buy_top_total": None,
                "sell_top_total": None,
                "observation_status": "verified_empty",
                "observation_window_days": 30,
            }
        latest_date = str(rows[0].get("trade_date") or "")
        institutions: list[dict[str, Any]] = []
        if latest_date:
            try:
                institutions = _records(pro.top_inst(ts_code=_ts_code(symbol), trade_date=latest_date))
            except Exception:
                institutions = []
        return {
            "symbol": symbol,
            "latest_lhb_date": latest_date,
            "buy_detail": institutions,
            "sell_detail": institutions,
            "lhb_statistics": rows[:10],
            "lhb_count_30d": len(
                {str(row.get("trade_date")) for row in rows if row.get("trade_date")}
            ) or 1,
            "buy_top_total": rows[0].get("l_buy"),
            "sell_top_total": rows[0].get("l_sell"),
            "observation_status": "verified_non_empty",
            "observation_window_days": 30,
        }


def _ts_code(symbol: str) -> str:
    raw = str(symbol).lower().replace("sh", "").replace("sz", "").replace("bj", "").replace(".", "")[-6:]
    exchange = "BJ" if raw.startswith(("4", "8")) else "SH" if raw.startswith(("5", "6", "9")) else "SZ"
    return f"{raw}.{exchange}"


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return [
        {str(key): normalize_value(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]
