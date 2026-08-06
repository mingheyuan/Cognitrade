from __future__ import annotations

from importlib import import_module
from typing import Any

from research_system.adapters.vendors.akshare_utils import normalize_value


class YFinanceFundamentalAdapter:
    vendor_name = "yfinance"

    def get_fundamental_snapshot(self, *, symbol: str) -> dict[str, Any]:
        yf = import_module("yfinance")
        ticker_symbol = self._ticker(symbol)
        ticker = yf.Ticker(ticker_symbol)
        info = self._safe_info(ticker)
        statements = {
            "income": self._records(getattr(ticker, "financials", None)),
            "balance": self._records(getattr(ticker, "balance_sheet", None)),
            "cash_flow": self._records(getattr(ticker, "cashflow", None)),
        }
        if not info and not any(statements.values()):
            raise ValueError(f"no fundamental data returned for symbol {symbol}")
        return {
            "symbol": symbol,
            "info": info,
            "financial_abstract": {
                "market_cap": info.get("marketCap"),
                "revenue": info.get("totalRevenue"),
                "net_income": info.get("netIncomeToCommon"),
                "operating_cash_flow": info.get("operatingCashflow"),
                "free_cash_flow": info.get("freeCashflow"),
            },
            "financial_analysis": {
                "return_on_equity": info.get("returnOnEquity"),
                "debt_to_equity": info.get("debtToEquity"),
                "profit_margin": info.get("profitMargins"),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
            },
            "market_valuation": {
                "pe_ttm": info.get("trailingPE"),
                "pe_forward": info.get("forwardPE"),
                "pb": info.get("priceToBook"),
                "market_cap": info.get("marketCap"),
            },
            "financial_statements": statements,
        }

    @staticmethod
    def _safe_info(ticker) -> dict[str, Any]:
        try:
            return {str(key): normalize_value(value) for key, value in dict(ticker.info or {}).items()}
        except Exception:
            return {}

    @staticmethod
    def _records(frame) -> list[dict[str, Any]]:
        if frame is None or getattr(frame, "empty", True):
            return []
        transposed = frame.T.reset_index()
        return [
            {str(key): normalize_value(value) for key, value in row.items()}
            for row in transposed.to_dict("records")
        ]

    @staticmethod
    def _ticker(symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        if raw.startswith("HK") and raw[2:].isdigit():
            return f"{str(int(raw[2:])).zfill(4)}.HK"
        if raw.endswith(".HK"):
            return f"{str(int(raw.removesuffix('.HK'))).zfill(4)}.HK"
        return raw
