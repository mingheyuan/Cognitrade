from __future__ import annotations

from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_ashare_symbol, normalize_value


class AkShareMarketAdapter:
    vendor_name = "akshare"

    def get_market_snapshot(self, *, symbol: str, start_date: str, end_date: str, adjust: str = "") -> dict[str, Any]:
        normalized_symbol = normalize_ashare_symbol(symbol)
        hist_df = call_with_retry(
            lambda: ak.stock_zh_a_daily(
                symbol=normalized_symbol,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        )
        if hist_df.empty:
            raise ValueError(f"no market data returned for symbol {symbol}")
        latest = hist_df.iloc[-1].to_dict()
        return {
            "symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
            "latest_bar": {str(k): normalize_value(v) for k, v in latest.items()},
            "bar_count": int(len(hist_df)),
        }

    def get_market_overview(self, *, symbol: str) -> dict[str, Any]:
        normalized_symbol = normalize_ashare_symbol(symbol)
        spot_df = call_with_retry(ak.stock_zh_a_spot)
        if spot_df.empty:
            raise ValueError("no spot data returned")
        symbol_column = "代码" if "代码" in spot_df.columns else spot_df.columns[0]
        symbol_series = spot_df[symbol_column].astype(str).str.lower()
        row = spot_df[symbol_series == normalized_symbol]
        if row.empty:
            raw_symbol = normalized_symbol[2:] if len(normalized_symbol) > 2 else normalized_symbol
            row = spot_df[symbol_series == raw_symbol]
        if row.empty:
            return {"symbol": symbol, "normalized_symbol": normalized_symbol, "spot_found": False}
        latest = row.iloc[0].to_dict()
        return {
            "symbol": symbol,
            "normalized_symbol": normalized_symbol,
            "spot_found": True,
            "spot": {str(k): normalize_value(v) for k, v in latest.items()},
        }
