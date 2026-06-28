from __future__ import annotations

from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value


class AkShareFundamentalAdapter:
    vendor_name = "akshare"

    def get_fundamental_snapshot(self, *, symbol: str) -> dict[str, Any]:
        abstract_df = call_with_retry(lambda: ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期"))

        abstract_payload: dict[str, Any] = {}
        if not abstract_df.empty:
            latest = abstract_df.iloc[-1].to_dict()
            abstract_payload = {str(k): normalize_value(v) for k, v in latest.items()}

        info_payload = self._try_get_individual_info(symbol)

        if not info_payload and not abstract_payload:
            raise ValueError(f"no fundamental data returned for symbol {symbol}")

        return {
            "symbol": symbol,
            "info": info_payload,
            "financial_abstract": abstract_payload,
        }

    def _try_get_individual_info(self, symbol: str) -> dict[str, Any]:
        try:
            info_df = call_with_retry(lambda: ak.stock_individual_info_em(symbol=symbol), retries=2, delay_seconds=0.5)
        except Exception as exc:
            return {"availability": "degraded", "note": f"individual_info_unavailable: {exc}"}

        info_payload: dict[str, Any] = {}
        if info_df.empty:
            return info_payload

        item_column = "item" if "item" in info_df.columns else info_df.columns[0]
        value_column = "value" if "value" in info_df.columns else info_df.columns[-1]
        for _, row in info_df.iterrows():
            key = str(row[item_column])
            value = row[value_column]
            info_payload[key] = normalize_value(value)
        return info_payload
