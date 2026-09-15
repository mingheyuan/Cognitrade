from __future__ import annotations

from importlib import import_module
from datetime import UTC, date, datetime
import math
from typing import Any

from research_system.adapters.vendors.akshare_utils import normalize_value


class YFinanceFundamentalAdapter:
    vendor_name = "yfinance"

    def get_fundamental_snapshot(
        self, *, symbol: str, as_of_date: date | None = None
    ) -> dict[str, Any]:
        yf = import_module("yfinance")
        ticker_symbol = self._ticker(symbol)
        ticker = yf.Ticker(ticker_symbol)
        info = self._safe_info(ticker) if as_of_date is None else {}
        income_frame = self._filter_frame_as_of(
            getattr(ticker, "financials", None), as_of_date
        )
        quarterly_income = self._filter_frame_as_of(
            getattr(ticker, "quarterly_financials", None), as_of_date
        )
        balance_frame = self._filter_frame_as_of(
            getattr(ticker, "balance_sheet", None), as_of_date
        )
        cashflow_frame = self._filter_frame_as_of(
            getattr(ticker, "cashflow", None), as_of_date
        )
        annual_history, financial_period, financial_basis = self._financial_history(
            income_frame,
            quarterly_income,
        )
        financial_health = self._financial_health(balance_frame, info)
        statements = {
            "income": self._records(income_frame),
            "quarterly_income": self._records(quarterly_income),
            "balance": self._records(balance_frame),
            "cash_flow": self._records(cashflow_frame),
        }
        if not info and not any(statements.values()):
            raise ValueError(f"no fundamental data returned for symbol {symbol}")
        financial_abstract = {
            "market_cap": info.get("marketCap"),
            "revenue": info.get("totalRevenue"),
            "net_income": info.get("netIncomeToCommon"),
            "operating_cash_flow": info.get("operatingCashflow"),
            "free_cash_flow": info.get("freeCashflow"),
        }
        if annual_history:
            latest = annual_history[-1]
            financial_abstract["revenue"] = financial_abstract.get("revenue") or latest.get("revenue")
            financial_abstract["net_income"] = financial_abstract.get("net_income") or latest.get("net_income")
        result = {
            "symbol": symbol,
            "info": info,
            "financial_abstract": financial_abstract,
            "financial_abstract_history": annual_history,
            "financial_period": financial_period,
            "financial_basis": financial_basis,
            "financial_health": financial_health,
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
        staleness_days = self._staleness_days(financial_period)
        if staleness_days is not None and staleness_days > 180:
            result["financial_staleness_days"] = staleness_days
            result["data_gap"] = ["financial_statements_stale"]
        else:
            result["data_gap"] = []
        return result

    @staticmethod
    def _safe_info(ticker) -> dict[str, Any]:
        try:
            return {str(key): normalize_value(value) for key, value in dict(ticker.info or {}).items()}
        except Exception:
            return {}

    @staticmethod
    def _filter_frame_as_of(frame, as_of_date: date | None):
        if frame is None or getattr(frame, "empty", True) or as_of_date is None:
            return frame
        keep = []
        for column in frame.columns:
            try:
                period = column.date() if hasattr(column, "date") else date.fromisoformat(str(column)[:10])
            except (TypeError, ValueError):
                continue
            if period <= as_of_date:
                keep.append(column)
        return frame.loc[:, keep]

    @staticmethod
    def _records(frame) -> list[dict[str, Any]]:
        if frame is None or getattr(frame, "empty", True):
            return []
        transposed = frame.T.reset_index()
        return [
            {str(key): normalize_value(value) for key, value in row.items()}
            for row in transposed.to_dict("records")
        ]

    @classmethod
    def _financial_history(cls, annual_frame, quarterly_frame) -> tuple[list[dict[str, Any]], str | None, str | None]:
        annual = cls._statement_points(
            annual_frame,
            {
                "revenue": ("Total Revenue", "TotalRevenue"),
                "net_income": ("Net Income", "NetIncome", "Net Income Common Stockholders"),
            },
        )
        history = [
            {"report_date": period, "report_basis": "annual", **facts}
            for period, facts in sorted(annual.items())
        ]
        quarterly = cls._statement_points(
            quarterly_frame,
            {
                "revenue": ("Total Revenue", "TotalRevenue"),
                "net_income": ("Net Income", "NetIncome", "Net Income Common Stockholders"),
            },
        )
        quarter_periods = sorted(quarterly, reverse=True)[:4]
        latest_period = history[-1]["report_date"] if history else None
        basis = "annual" if latest_period else None
        if len(quarter_periods) == 4:
            ttm: dict[str, Any] = {}
            for metric in ("revenue", "net_income"):
                values = [quarterly[period].get(metric) for period in quarter_periods]
                if all(value is not None for value in values):
                    ttm[metric] = sum(float(value) for value in values)
            if ttm:
                quarter_latest = quarter_periods[0]
                if latest_period is None or quarter_latest > str(latest_period):
                    history.append({"report_date": quarter_latest, "report_basis": "TTM", **ttm})
                    latest_period = quarter_latest
                    basis = "TTM"
        return history, str(latest_period) if latest_period else None, basis

    @classmethod
    def _statement_points(
        cls,
        frame,
        metrics: dict[str, tuple[str, ...]],
    ) -> dict[str, dict[str, float]]:
        if frame is None or getattr(frame, "empty", True):
            return {}
        result: dict[str, dict[str, float]] = {}
        for metric, candidates in metrics.items():
            row_name = next((candidate for candidate in candidates if candidate in frame.index), None)
            if row_name is None:
                continue
            series = frame.loc[row_name]
            for column, raw in series.items():
                value = cls._number(raw)
                if value is None:
                    continue
                period = str(column)[:10]
                result.setdefault(period, {})[metric] = value
        return result

    @classmethod
    def _financial_health(cls, balance_frame, info: dict[str, Any]) -> dict[str, Any]:
        fields = {
            "total_debt": ("Total Debt", "TotalDebt", "Long Term Debt And Capital Lease Obligation"),
            "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
            "equity": ("Stockholders Equity", "Total Stockholder Equity"),
            "total_assets": ("Total Assets", "TotalAssets"),
            "total_liabilities": ("Total Liabilities Net Minority Interest", "Total Liabilities"),
        }
        result: dict[str, Any] = {}
        if balance_frame is not None and not getattr(balance_frame, "empty", True):
            for target, candidates in fields.items():
                row_name = next((candidate for candidate in candidates if candidate in balance_frame.index), None)
                if row_name is None:
                    continue
                # Use label-based row lookup. Positional .iloc previously read
                # unrelated facts when Yahoo changed statement row ordering.
                for raw in balance_frame.loc[row_name]:
                    value = cls._number(raw)
                    if value is not None:
                        result[target] = value
                        break
        result["total_debt"] = result.get("total_debt") or cls._number(info.get("totalDebt"))
        result["cash"] = result.get("cash") or cls._number(info.get("totalCash"))
        result["source"] = "yfinance.balance_sheet"
        return result

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _staleness_days(period: str | None) -> int | None:
        if not period:
            return None
        try:
            parsed = datetime.fromisoformat(str(period)[:10]).replace(tzinfo=UTC)
        except ValueError:
            return None
        return max(0, (datetime.now(UTC) - parsed).days)

    @staticmethod
    def _ticker(symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        if raw.startswith("HK") and raw[2:].isdigit():
            return f"{str(int(raw[2:])).zfill(4)}.HK"
        if raw.endswith(".HK"):
            return f"{str(int(raw.removesuffix('.HK'))).zfill(4)}.HK"
        return raw
