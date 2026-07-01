from __future__ import annotations

from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_ashare_symbol, normalize_value


class AkShareCapitalFlowAdapter:
    vendor_name = "akshare"

    def get_capital_flow_snapshot(self, *, symbol: str) -> dict[str, Any]:
        market = self._market_code(symbol)
        individual_df = call_with_retry(
            lambda: ak.stock_individual_fund_flow(stock=symbol, market=market),
            retries=2,
            delay_seconds=0.5,
        )
        if individual_df.empty:
            raise ValueError(f"no capital flow data returned for symbol {symbol}")

        main_rank_df = call_with_retry(lambda: ak.stock_main_fund_flow(symbol="全部股票"), retries=2, delay_seconds=0.5)
        normalized_symbol = normalize_ashare_symbol(symbol)
        rank_row = None
        if not main_rank_df.empty:
            for row in main_rank_df.to_dict("records"):
                code = str(row.get("代码") or row.get("股票代码") or row.get("代码 ") or "").strip().lower()
                code = code[-6:] if len(code) >= 6 else code
                if code == symbol or code == normalized_symbol[-6:]:
                    rank_row = self._normalize_row(row)
                    break

        latest = self._normalize_row(individual_df.iloc[0].to_dict())
        recent = [self._normalize_row(item) for item in individual_df.head(5).to_dict("records")]
        return {
            "symbol": symbol,
            "market": market,
            "latest_flow": latest,
            "recent_flows": recent,
            "main_flow_rank": rank_row,
        }

    def _market_code(self, symbol: str) -> str:
        normalized = normalize_ashare_symbol(symbol)
        if normalized.startswith("sh"):
            return "sh"
        if normalized.startswith("bj"):
            return "bj"
        return "sz"

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {str(key): normalize_value(value) for key, value in row.items()}
