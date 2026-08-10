from __future__ import annotations

from datetime import date
from importlib import import_module
from typing import Any, Callable

from research_system.adapters.vendors.akshare_utils import normalize_value
from research_system.adapters.vendors.baostock_socket_safety import (
    install_baostock_socket_safety,
)


class BaoStockFundamentalAdapter:
    """BaoStock financial-statement fallback used by UZI's A-share chain."""

    vendor_name = "baostock"

    def get_fundamental_snapshot(self, *, symbol: str) -> dict[str, Any]:
        install_baostock_socket_safety()
        bs = import_module("baostock")
        login = bs.login()
        if str(getattr(login, "error_code", "0")) != "0":
            raise ConnectionError("baostock login failed")
        code = self._code(symbol)
        try:
            sections = {
                "profit": self._quarterly(bs.query_profit_data, code),
                "growth": self._quarterly(bs.query_growth_data, code),
                "dupont": self._quarterly(bs.query_dupont_data, code),
                "balance": self._quarterly(bs.query_balance_data, code),
                "cash_flow": self._quarterly(bs.query_cash_flow_data, code),
                "operation": self._quarterly(bs.query_operation_data, code),
            }
        finally:
            bs.logout()
        populated = {key: value for key, value in sections.items() if value}
        if not populated:
            raise ValueError(f"no fundamental data returned for symbol {symbol}")
        latest = {
            key: rows[-1]
            for key, rows in populated.items()
            if rows
        }
        return {
            "symbol": symbol,
            "info": {"code": code},
            "financial_abstract": latest,
            "financial_analysis": {
                **dict(latest.get("profit") or {}),
                **dict(latest.get("growth") or {}),
                **dict(latest.get("dupont") or {}),
            },
            "financial_statements": populated,
        }

    @staticmethod
    def _quarterly(loader: Callable[..., Any], code: str, years: int = 5) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        current_year = date.today().year
        for year in range(current_year - years, current_year + 1):
            for quarter in range(1, 5):
                result = loader(code=code, year=year, quarter=quarter)
                if str(getattr(result, "error_code", "0")) != "0":
                    continue
                fields = list(getattr(result, "fields", []))
                while result.next():
                    rows.append(
                        {
                            str(key): normalize_value(value)
                            for key, value in zip(fields, result.get_row_data())
                        }
                    )
        rows.sort(key=lambda row: (str(row.get("statDate") or ""), str(row.get("pubDate") or "")))
        return rows

    @staticmethod
    def _code(symbol: str) -> str:
        raw = "".join(char for char in str(symbol) if char.isdigit())[-6:]
        prefix = "sh" if raw.startswith(("5", "6", "9")) else "bj" if raw.startswith(("4", "8")) else "sz"
        return f"{prefix}.{raw}"
