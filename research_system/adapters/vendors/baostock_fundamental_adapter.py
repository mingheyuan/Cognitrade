from __future__ import annotations

from datetime import date
from typing import Any, Callable

from research_system.adapters.vendors.akshare_utils import normalize_value
from research_system.adapters.vendors.baostock_socket_safety import (
    baostock_session,
)


class BaoStockFundamentalAdapter:
    """BaoStock financial-statement fallback used by UZI's A-share chain."""

    vendor_name = "baostock"

    def get_fundamental_snapshot(
        self, *, symbol: str, as_of_date: date | None = None
    ) -> dict[str, Any]:
        code = self._code(symbol)
        with baostock_session() as bs:
            sections = {
                "profit": self._quarterly(
                    bs.query_profit_data, code, as_of_date=as_of_date
                ),
                "growth": self._quarterly(
                    bs.query_growth_data, code, as_of_date=as_of_date
                ),
                "dupont": self._quarterly(
                    bs.query_dupont_data, code, as_of_date=as_of_date
                ),
                "balance": self._quarterly(
                    bs.query_balance_data, code, as_of_date=as_of_date
                ),
                "cash_flow": self._quarterly(
                    bs.query_cash_flow_data, code, as_of_date=as_of_date
                ),
                "operation": self._quarterly(
                    bs.query_operation_data, code, as_of_date=as_of_date
                ),
            }
        populated = {key: value for key, value in sections.items() if value}
        if not populated:
            raise ValueError(f"no fundamental data returned for symbol {symbol}")
        latest = {key: rows[-1] for key, rows in populated.items() if rows}
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
    def _quarterly(
        loader: Callable[..., Any],
        code: str,
        years: int = 5,
        *,
        as_of_date: date | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        current_year = (as_of_date or date.today()).year
        for year in range(current_year - years, current_year + 1):
            for quarter in range(1, 5):
                result = loader(code=code, year=year, quarter=quarter)
                if str(getattr(result, "error_code", "0")) != "0":
                    continue
                fields = list(getattr(result, "fields", []))
                while result.next():
                    row = {
                        str(key): normalize_value(value)
                        for key, value in zip(fields, result.get_row_data())
                    }
                    visible_at = BaoStockFundamentalAdapter._row_date(
                        row.get("pubDate") or row.get("statDate")
                    )
                    if as_of_date is None or (
                        visible_at is not None and visible_at <= as_of_date
                    ):
                        rows.append(row)
        rows.sort(
            key=lambda row: (
                str(row.get("statDate") or ""),
                str(row.get("pubDate") or ""),
            )
        )
        return rows

    @staticmethod
    def _row_date(value: Any) -> date | None:
        try:
            return date.fromisoformat(str(value or "")[:10])
        except ValueError:
            return None

    @staticmethod
    def _code(symbol: str) -> str:
        raw = "".join(char for char in str(symbol) if char.isdigit())[-6:]
        prefix = (
            "sh"
            if raw.startswith(("5", "6", "9"))
            else "bj"
            if raw.startswith(("4", "8"))
            else "sz"
        )
        return f"{prefix}.{raw}"
