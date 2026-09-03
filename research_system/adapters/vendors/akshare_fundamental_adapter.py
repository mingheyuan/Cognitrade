from __future__ import annotations

from datetime import date, datetime
from typing import Any

import akshare as ak
import pandas as pd
import requests

from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    coerce_numeric,
    normalize_ashare_symbol,
    normalize_value,
)


class AkShareFundamentalAdapter:
    vendor_name = "akshare"
    _COMPARISON_CODE_LABEL = "\u4ee3\u7801"
    _COMPARISON_NAME_LABEL = "\u7b80\u79f0"
    _INDUSTRY_AVERAGE_LABEL = "\u884c\u4e1a\u5e73\u5747"
    _INDUSTRY_MEDIAN_LABEL = "\u884c\u4e1a\u4e2d\u503c"
    _PEER_VALUATION_RANK_LABEL = "\u5e02\u51c0\u7387MRQ\u6392\u540d"
    _PEER_VALUATION_PB_MRQ_LABEL = "\u5e02\u51c0\u7387MRQ"
    _PEER_VALUATION_PE_TTM_LABEL = "\u5e02\u76c8\u7387TTM"

    def get_fundamental_snapshot(
        self, *, symbol: str, as_of_date: date | None = None
    ) -> dict[str, Any]:
        abstract_df = call_with_retry(
            lambda: ak.stock_financial_abstract_ths(
                symbol=symbol, indicator="鎸夋姤鍛婃湡"
            )
        )
        abstract_df = self._filter_frame_as_of(abstract_df, as_of_date)
        analysis_df = self._filter_frame_as_of(
            self._try_get_analysis_indicator(symbol, as_of_date=as_of_date),
            as_of_date,
        )
        abstract_new_df = self._filter_frame_as_of(
            self._try_get_new_metric_frame("stock_financial_abstract_new_ths", symbol),
            as_of_date,
        )
        benefit_new_df = self._filter_frame_as_of(
            self._try_get_new_metric_frame("stock_financial_benefit_new_ths", symbol),
            as_of_date,
        )
        debt_new_df = self._filter_frame_as_of(
            self._try_get_new_metric_frame("stock_financial_debt_new_ths", symbol),
            as_of_date,
        )
        cash_new_df = self._filter_frame_as_of(
            self._try_get_new_metric_frame("stock_financial_cash_new_ths", symbol),
            as_of_date,
        )
        balance_sheet_payload, balance_sheet_gap = self._try_get_balance_sheet(
            symbol, as_of_date=as_of_date
        )

        abstract_payload: dict[str, Any] = {}
        abstract_history: list[dict[str, Any]] = []
        if not abstract_df.empty:
            latest = abstract_df.iloc[-1].to_dict()
            abstract_payload = {str(k): normalize_value(v) for k, v in latest.items()}
            abstract_history = [
                {str(k): normalize_value(v) for k, v in row.items()}
                for row in abstract_df.to_dict("records")
            ]

        info_payload, info_gap = self._try_get_individual_info(symbol)
        analysis_payload = self._latest_record(analysis_df)
        abstract_new_payload = self._latest_metric_values(abstract_new_df)
        benefit_new_payload = self._latest_metric_values(benefit_new_df)
        debt_new_payload = self._latest_metric_values(debt_new_df)
        cash_new_payload = self._latest_metric_values(cash_new_df)
        banking_metrics = self._extract_banking_metrics(
            abstract_payload=abstract_payload,
            analysis_payload=analysis_payload,
            abstract_new_payload=abstract_new_payload,
            benefit_new_payload=benefit_new_payload,
            debt_new_payload=debt_new_payload,
            cash_new_payload=cash_new_payload,
        )
        bank_peer_comparison: dict[str, Any] = {}
        if banking_metrics.get("is_bank_like") and as_of_date is None:
            growth_comparison_df = self._try_get_bank_peer_growth_comparison(symbol)
            dupont_comparison_df = self._try_get_bank_peer_dupont_comparison(symbol)
            valuation_comparison_df = self._try_get_bank_peer_valuation_comparison(
                symbol
            )
            bank_special_indicators = self._try_get_bank_special_indicators(symbol)
            banking_metrics.update(
                {
                    key: value
                    for key, value in dict(bank_special_indicators or {}).items()
                    if value is not None
                }
            )
            bank_peer_comparison = self._extract_bank_peer_comparison(
                symbol=symbol,
                growth_frame=growth_comparison_df,
                dupont_frame=dupont_comparison_df,
                valuation_frame=valuation_comparison_df,
            )
            banking_metrics.update(
                {
                    key: value
                    for key, value in dict(
                        bank_peer_comparison.get("metrics") or {}
                    ).items()
                    if value is not None
                }
            )

        if not info_payload and not abstract_payload and not analysis_payload:
            raise ValueError(f"no fundamental data returned for symbol {symbol}")

        return {
            "symbol": symbol,
            "info": info_payload,
            "financial_abstract": abstract_payload,
            "financial_abstract_history": abstract_history,
            "financial_analysis": analysis_payload,
            "financial_abstract_new": abstract_new_payload,
            "financial_benefit_new": benefit_new_payload,
            "financial_debt_new": debt_new_payload,
            "financial_cash_new": cash_new_payload,
            "financial_health": balance_sheet_payload,
            "banking_metrics": banking_metrics,
            "bank_peer_comparison": dict(bank_peer_comparison.get("comparison") or {}),
            "data_gap": [gap for gap in (info_gap, balance_sheet_gap) if gap],
        }

    def _try_get_balance_sheet(
        self, symbol: str, *, as_of_date: date | None = None
    ) -> tuple[dict[str, Any], str | None]:
        fetcher = getattr(ak, "stock_balance_sheet_by_report_em", None)
        if not callable(fetcher):
            return {}, "balance_sheet_provider_missing"
        try:
            frame = call_with_retry(
                lambda: fetcher(symbol=self._comparison_symbol(symbol)),
                retries=1,
                delay_seconds=0.5,
            )
        except Exception:
            return {}, "balance_sheet_unavailable"
        if frame is None or getattr(frame, "empty", True):
            return {}, "balance_sheet_missing"
        ordered = self._filter_frame_as_of(frame, as_of_date)
        if ordered is None or ordered.empty:
            return {}, "balance_sheet_not_published_by_cutoff"
        for date_column in ("REPORT_DATE", "REPORTDATE", "NOTICE_DATE"):
            if date_column in ordered.columns:
                ordered = ordered.sort_values(date_column)
                break
        latest = ordered.iloc[-1]
        mapping = {
            "MONETARYFUNDS": "cash",
            "TOTAL_LIABILITIES": "total_liabilities",
            "TOTAL_PARENT_EQUITY": "equity",
            "TOTAL_ASSETS": "total_assets",
            "TOTAL_DEBT": "total_debt",
        }
        result: dict[str, Any] = {}
        for source_key, target_key in mapping.items():
            if source_key not in ordered.columns:
                continue
            value = coerce_numeric(latest.get(source_key))
            if value is not None:
                result[target_key] = value
        # UZI's latest upstream bridge uses TOTAL_LIABILITIES when the vendor
        # does not publish a dedicated interest-bearing TOTAL_DEBT field. Keep
        # the source fact and disclose the proxy instead of silently treating
        # missing debt as zero.
        if (
            result.get("total_debt") is None
            and result.get("total_liabilities") is not None
        ):
            result["total_debt"] = result["total_liabilities"]
            result["total_debt_basis"] = "total_liabilities_proxy"
        report_date = next(
            (
                normalize_value(latest.get(key))
                for key in ("REPORT_DATE", "REPORTDATE", "NOTICE_DATE")
                if key in ordered.columns
            ),
            None,
        )
        if report_date:
            result["report_date"] = report_date
        result["source"] = "akshare.stock_balance_sheet_by_report_em"
        return result, None if result else "balance_sheet_fields_missing"

    def _try_get_individual_info(
        self, symbol: str
    ) -> tuple[dict[str, Any], str | None]:
        try:
            info_df = call_with_retry(
                lambda: ak.stock_individual_info_em(symbol=symbol),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception:
            return {}, "individual_info_unavailable"

        info_payload: dict[str, Any] = {}
        if info_df.empty:
            return info_payload, "individual_info_missing"

        item_column = "item" if "item" in info_df.columns else info_df.columns[0]
        value_column = "value" if "value" in info_df.columns else info_df.columns[-1]
        for _, row in info_df.iterrows():
            key = str(row[item_column])
            value = row[value_column]
            info_payload[key] = normalize_value(value)
        return info_payload, None

    def _try_get_analysis_indicator(
        self, symbol: str, *, as_of_date: date | None = None
    ):
        try:
            return call_with_retry(
                lambda: ak.stock_financial_analysis_indicator(
                    symbol=symbol,
                    start_year=str((as_of_date or date.today()).year - 6),
                ),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception:
            return None

    def _try_get_new_metric_frame(self, func_name: str, symbol: str):
        func = getattr(ak, func_name, None)
        if func is None:
            return None
        try:
            return call_with_retry(
                lambda: func(symbol=symbol, indicator="按报告期"),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception:
            return None

    def _try_get_bank_peer_growth_comparison(self, symbol: str):
        try:
            return call_with_retry(
                lambda: ak.stock_zh_growth_comparison_em(
                    symbol=self._comparison_symbol(symbol)
                ),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception:
            return None

    def _try_get_bank_peer_dupont_comparison(self, symbol: str):
        try:
            return call_with_retry(
                lambda: ak.stock_zh_dupont_comparison_em(
                    symbol=self._comparison_symbol(symbol)
                ),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception:
            return None

    def _try_get_bank_peer_valuation_comparison(self, symbol: str):
        try:
            return call_with_retry(
                lambda: self._fetch_bank_peer_valuation_comparison(symbol),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception:
            return None

    def _try_get_bank_special_indicators(self, symbol: str) -> dict[str, Any]:
        try:
            return call_with_retry(
                lambda: self._fetch_bank_special_indicators(symbol),
                retries=2,
                delay_seconds=0.5,
            )
        except Exception:
            return {}

    def _fetch_bank_peer_valuation_comparison(self, symbol: str):
        comparison_symbol = self._comparison_symbol(symbol)
        url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
        params = {
            "reportName": "RPT_PCF10_INDUSTRY_CVALUE",
            "columns": "ALL",
            "quoteColumns": "",
            "filter": f'(SECUCODE="{comparison_symbol[2:]}.{comparison_symbol[:2]}")',
            "pageNumber": "",
            "pageSize": "",
            "sortTypes": "1",
            "sortColumns": "PAIMING",
            "source": "HSF10",
            "client": "PC",
        }
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        rows = response.json().get("result", {}).get("data") or []
        normalized_rows = [
            {
                self._COMPARISON_CODE_LABEL: normalize_value(
                    row.get("CORRE_SECURITY_CODE")
                ),
                self._COMPARISON_NAME_LABEL: normalize_value(
                    row.get("CORRE_SECURITY_NAME")
                ),
                self._PEER_VALUATION_RANK_LABEL: normalize_value(row.get("PAIMING")),
                self._PEER_VALUATION_PB_MRQ_LABEL: normalize_value(row.get("PB_MRQ")),
                self._PEER_VALUATION_PE_TTM_LABEL: normalize_value(row.get("PE_TTM")),
                "PEG": normalize_value(row.get("PEG")),
                "PB_24A": normalize_value(row.get("PB")),
                "TOTAL_COUNT": normalize_value(row.get("TOTAL_COUNT")),
            }
            for row in rows
        ]
        return pd.DataFrame(normalized_rows)

    def _fetch_bank_special_indicators(self, symbol: str) -> dict[str, Any]:
        comparison_symbol = self._comparison_symbol(symbol)
        url = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew"
        response = requests.get(
            url, params={"code": comparison_symbol, "type": "1"}, timeout=20
        )
        response.raise_for_status()
        rows = response.json().get("data") or []
        if not rows:
            return {}
        row = rows[0]
        return {
            "loan_to_deposit_ratio": self._comparison_numeric(row, "LTDRR"),
            "capital_adequacy_ratio": self._comparison_ratio(row, "NEWCAPITALADER"),
            "core_tier1_capital_adequacy_ratio": self._comparison_ratio(
                row, "HXYJBCZL"
            ),
            "nonperforming_loan_ratio": self._comparison_ratio(row, "NONPERLOAN"),
            "provision_coverage_ratio": self._comparison_ratio(row, "BLDKBBL"),
            "net_interest_spread": self._comparison_ratio(row, "NET_INTEREST_SPREAD"),
            "net_interest_margin": self._comparison_ratio(row, "NET_INTEREST_MARGIN"),
            "total_deposits_balance": self._comparison_numeric(row, "TOTALDEPOSITS"),
            "gross_loans_balance": self._comparison_numeric(row, "GROSSLOANS"),
            "roe_report_period": self._comparison_ratio(row, "ROEJQ"),
        }

    def _latest_record(self, frame) -> dict[str, Any]:
        if frame is None or getattr(frame, "empty", True):
            return {}
        latest = frame.iloc[-1].to_dict()
        return {str(k): normalize_value(v) for k, v in latest.items()}

    def _latest_metric_values(self, frame) -> dict[str, Any]:
        if frame is None or getattr(frame, "empty", True):
            return {}
        ordered = frame
        if "report_date" in ordered.columns:
            ordered = ordered.sort_values(
                by=["report_date", "quarter_name"], ascending=[True, True]
            )
        latest_by_metric: dict[str, Any] = {}
        for _, row in ordered.iterrows():
            metric_name = str(row.get("metric_name") or "").strip()
            if not metric_name:
                continue
            latest_by_metric[metric_name] = {
                "value": normalize_value(row.get("value")),
                "single": normalize_value(row.get("single")),
                "yoy": normalize_value(row.get("yoy")),
                "mom": normalize_value(row.get("mom")),
                "single_yoy": normalize_value(row.get("single_yoy")),
                "report_date": normalize_value(row.get("report_date")),
                "quarter_name": normalize_value(row.get("quarter_name")),
            }
        return latest_by_metric

    @classmethod
    def _filter_frame_as_of(cls, frame, as_of_date: date | None):
        """Keep only facts visible by the requested point in time."""

        if frame is None or getattr(frame, "empty", True) or as_of_date is None:
            return frame
        filtered = frame.copy()
        publication_columns = (
            "NOTICE_DATE",
            "ANN_DATE",
            "ann_date",
            "公告日期",
            "发布日期",
            "pubDate",
        )
        report_columns = (
            "REPORT_DATE",
            "REPORTDATE",
            "report_date",
            "end_date",
            "statDate",
            "报告期",
            "报告日期",
        )
        selected_column = next(
            (column for column in publication_columns if column in filtered.columns),
            None,
        ) or next(
            (column for column in report_columns if column in filtered.columns),
            None,
        )
        if selected_column is not None:
            publication_date = selected_column in publication_columns
            parsed = filtered[selected_column].map(
                lambda value: cls._visible_date(
                    value, publication_date=publication_date
                )
            )
            filtered = filtered[
                parsed.map(
                    lambda value: value is not None and value <= as_of_date
                )
            ]

        keep_columns: list[Any] = []
        for column in filtered.columns:
            parsed_column = cls._calendar_date(column)
            if parsed_column is None or parsed_column <= as_of_date:
                keep_columns.append(column)
        return filtered.loc[:, keep_columns]

    @staticmethod
    def _calendar_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip().replace("/", "-")
        for fmt, width in (
            ("%Y-%m-%d", 10),
            ("%Y%m%d", 8),
            ("%Y-%m", 7),
            ("%Y", 4),
        ):
            try:
                return datetime.strptime(text[:width], fmt).date()
            except ValueError:
                continue
        return None

    @classmethod
    def _visible_date(
        cls, value: Any, *, publication_date: bool
    ) -> date | None:
        text = str(value or "").strip()
        if text.isdigit() and len(text) == 4:
            year = int(text)
            return date(year, 12, 31) if publication_date else date(year + 1, 4, 30)
        resolved = cls._calendar_date(value)
        if resolved is None or publication_date:
            return resolved
        month_day = (resolved.month, resolved.day)
        if month_day <= (3, 31):
            return date(resolved.year, 4, 30)
        if month_day <= (6, 30):
            return date(resolved.year, 8, 31)
        if month_day <= (9, 30):
            return date(resolved.year, 10, 31)
        return date(resolved.year + 1, 4, 30)

    def _extract_bank_peer_comparison(
        self, *, symbol: str, growth_frame, dupont_frame, valuation_frame
    ) -> dict[str, Any]:
        growth_target = self._comparison_row_by_code(growth_frame, symbol)
        growth_average = self._comparison_row_by_code(
            growth_frame, self._INDUSTRY_AVERAGE_LABEL
        )
        growth_median = self._comparison_row_by_code(
            growth_frame, self._INDUSTRY_MEDIAN_LABEL
        )
        dupont_target = self._comparison_row_by_code(dupont_frame, symbol)
        dupont_average = self._comparison_row_by_code(
            dupont_frame, self._INDUSTRY_AVERAGE_LABEL
        )
        dupont_median = self._comparison_row_by_code(
            dupont_frame, self._INDUSTRY_MEDIAN_LABEL
        )
        valuation_target = self._comparison_row_by_code(valuation_frame, symbol)
        valuation_average = self._comparison_row_by_code(
            valuation_frame, self._INDUSTRY_AVERAGE_LABEL
        )
        valuation_median = self._comparison_row_by_code(
            valuation_frame, self._INDUSTRY_MEDIAN_LABEL
        )

        metrics: dict[str, Any] = {
            "peer_eps_growth_rank": self._comparison_numeric(
                growth_target,
                "\u57fa\u672c\u6bcf\u80a1\u6536\u76ca\u589e\u957f\u7387-3\u5e74\u590d\u5408\u6392\u540d",
            ),
            "peer_revenue_growth_ttm": self._comparison_ratio(
                growth_target,
                "\u8425\u4e1a\u6536\u5165\u589e\u957f\u7387-TTM",
            ),
            "peer_net_profit_growth_ttm": self._comparison_ratio(
                growth_target,
                "\u51c0\u5229\u6da6\u589e\u957f\u7387-TTM",
            ),
            "peer_roe_rank": self._comparison_numeric(
                dupont_target,
                "ROE-3\u5e74\u5e73\u5747\u6392\u540d",
            ),
            "peer_roe_3y_avg": self._comparison_ratio(
                dupont_target,
                "ROE-3\u5e74\u5e73\u5747",
            ),
            "peer_net_margin_3y_avg": self._comparison_ratio(
                dupont_target,
                "\u51c0\u5229\u7387-3\u5e74\u5e73\u5747",
            ),
            "peer_total_asset_turnover_3y_avg": self._comparison_numeric(
                dupont_target,
                "\u603b\u8d44\u4ea7\u5468\u8f6c\u7387-3\u5e74\u5e73\u5747",
            ),
            "peer_pb_rank": self._comparison_numeric(
                valuation_target,
                "\u5e02\u51c0\u7387MRQ\u6392\u540d",
            ),
            "peer_pb_mrq": self._comparison_numeric(
                valuation_target,
                "\u5e02\u51c0\u7387MRQ",
            ),
            "peer_pe_ttm": self._comparison_numeric(
                valuation_target,
                "\u5e02\u76c8\u7387TTM",
            ),
        }
        revenue_growth_avg = self._comparison_ratio(
            growth_average, "\u8425\u4e1a\u6536\u5165\u589e\u957f\u7387-TTM"
        )
        net_profit_growth_avg = self._comparison_ratio(
            growth_average, "\u51c0\u5229\u6da6\u589e\u957f\u7387-TTM"
        )
        roe_avg = self._comparison_ratio(dupont_average, "ROE-3\u5e74\u5e73\u5747")
        pb_avg = self._comparison_numeric(valuation_average, "\u5e02\u51c0\u7387MRQ")
        if (
            metrics.get("peer_revenue_growth_ttm") is not None
            and revenue_growth_avg is not None
        ):
            metrics["peer_revenue_growth_ttm_vs_industry_avg"] = (
                metrics["peer_revenue_growth_ttm"] - revenue_growth_avg
            )
        if (
            metrics.get("peer_net_profit_growth_ttm") is not None
            and net_profit_growth_avg is not None
        ):
            metrics["peer_net_profit_growth_ttm_vs_industry_avg"] = (
                metrics["peer_net_profit_growth_ttm"] - net_profit_growth_avg
            )
        if metrics.get("peer_roe_3y_avg") is not None and roe_avg is not None:
            metrics["peer_roe_3y_avg_vs_industry_avg"] = (
                metrics["peer_roe_3y_avg"] - roe_avg
            )
        if metrics.get("peer_pb_mrq") is not None and pb_avg is not None:
            metrics["peer_pb_mrq_vs_industry_avg"] = metrics["peer_pb_mrq"] - pb_avg

        return {
            "metrics": metrics,
            "comparison": {
                "growth": {
                    "target": growth_target,
                    "industry_average": growth_average,
                    "industry_median": growth_median,
                },
                "dupont": {
                    "target": dupont_target,
                    "industry_average": dupont_average,
                    "industry_median": dupont_median,
                },
                "valuation": {
                    "target": valuation_target,
                    "industry_average": valuation_average,
                    "industry_median": valuation_median,
                },
            },
        }

    def _extract_banking_metrics(
        self,
        *,
        abstract_payload: dict[str, Any],
        analysis_payload: dict[str, Any],
        abstract_new_payload: dict[str, Any],
        benefit_new_payload: dict[str, Any],
        debt_new_payload: dict[str, Any],
        cash_new_payload: dict[str, Any],
    ) -> dict[str, Any]:
        metrics = {
            "roe": self._first_numeric(
                abstract_payload.get("净资产收益率"),
                abstract_payload.get("净资产收益率-摊薄"),
                analysis_payload.get("净资产收益率(%)"),
                analysis_payload.get("加权净资产收益率(%)"),
                self._metric_value(abstract_new_payload, "index_weighted_avg_roe"),
                self._metric_value(abstract_new_payload, "index_full_diluted_roe"),
            ),
            "asset_liability_ratio": self._first_numeric(
                abstract_payload.get("资产负债率"),
                analysis_payload.get("资产负债率(%)"),
            ),
            "dividend_payout_ratio": self._first_numeric(
                analysis_payload.get("股息发放率(%)"),
            ),
            "revenue_yoy": self._first_numeric(
                abstract_payload.get("营业总收入同比增长率"),
                analysis_payload.get("主营业务收入增长率(%)"),
                self._metric_yoy(
                    abstract_new_payload,
                    "calculate_operating_income_total_yoy_growth_ratio",
                ),
            ),
            "net_profit_yoy": self._first_numeric(
                abstract_payload.get("净利润同比增长率"),
                analysis_payload.get("净利润增长率(%)"),
                self._metric_yoy(
                    abstract_new_payload,
                    "calculate_parent_holder_net_profit_yoy_growth_ratio",
                ),
            ),
            "sales_net_interest_ratio": self._first_numeric(
                self._metric_value(abstract_new_payload, "sale_net_interest_ratio"),
                abstract_payload.get("销售净利率"),
                analysis_payload.get("销售净利率(%)"),
            ),
            "interest_margin_proxy": self._first_numeric(
                self._metric_value(abstract_new_payload, "sale_net_interest_ratio"),
                abstract_payload.get("销售净利率"),
                analysis_payload.get("销售净利率(%)"),
            ),
            "interest_income": self._first_numeric(
                self._metric_value(benefit_new_payload, "interest_income"),
                self._metric_value(benefit_new_payload, "financial_interest_income"),
            ),
            "interest_expenses": self._first_numeric(
                self._metric_value(benefit_new_payload, "interest_expenses"),
                self._metric_value(benefit_new_payload, "financial_interest_expenses"),
            ),
            "credit_impairment_loss": self._first_numeric(
                self._metric_value(
                    benefit_new_payload, "benefit_credit_impairment_loss"
                ),
                self._metric_value(benefit_new_payload, "assets_impairment_loss"),
            ),
            "general_risk_preparation": self._first_numeric(
                self._metric_value(debt_new_payload, "general_risk_preparation"),
            ),
            "capital_reserve": self._first_numeric(
                self._metric_value(debt_new_payload, "capital_reserve"),
            ),
            "settle_provision": self._first_numeric(
                self._metric_value(debt_new_payload, "settle_provision"),
            ),
            "deposits_and_deposits": self._first_numeric(
                self._metric_value(debt_new_payload, "deposits_and_deposits"),
            ),
            "customer_loan_advance_net_addition": self._first_numeric(
                self._metric_value(
                    cash_new_payload, "customer_loan_advance_net_addition"
                ),
            ),
            "customer_and_interbank_deposits_addition": self._first_numeric(
                self._metric_value(
                    cash_new_payload, "customer_and_interbank_deposits_addition"
                ),
                self._metric_value(cash_new_payload, "deposits_and_funds_net_addition"),
            ),
            "receive_interest_fee_and_commission_cash": self._first_numeric(
                self._metric_value(
                    cash_new_payload, "receive_interest_fee_and_commission_cash"
                ),
            ),
            "pay_interest_fee_and_commission_cash": self._first_numeric(
                self._metric_value(
                    cash_new_payload, "pay_interest_fee_and_commission_cash"
                ),
            ),
            "pay_dividends_profits_interest_cash": self._first_numeric(
                self._metric_value(
                    cash_new_payload, "pay_dividends_profits_interest_cash"
                ),
            ),
        }
        interest_income = metrics.get("interest_income")
        interest_expenses = metrics.get("interest_expenses")
        deposits = metrics.get("deposits_and_deposits")
        loan_addition = metrics.get("customer_loan_advance_net_addition")
        deposit_addition = metrics.get("customer_and_interbank_deposits_addition")
        receive_interest_cash = metrics.get("receive_interest_fee_and_commission_cash")
        if (
            interest_income is not None
            and interest_expenses is not None
            and deposits not in (None, 0)
        ):
            metrics["net_interest_spread_proxy"] = (
                interest_income - interest_expenses
            ) / deposits
        elif (
            interest_income is not None
            and interest_expenses is not None
            and interest_income not in (None, 0)
        ):
            metrics["net_interest_spread_proxy"] = (
                interest_income - interest_expenses
            ) / interest_income
        else:
            metrics["net_interest_spread_proxy"] = None
        if deposits not in (None, 0) and loan_addition is not None:
            metrics["loan_to_deposit_proxy"] = loan_addition / deposits
        elif loan_addition is not None and deposit_addition not in (None, 0):
            metrics["loan_to_deposit_proxy"] = loan_addition / deposit_addition
        else:
            metrics["loan_to_deposit_proxy"] = None
        if deposits not in (None, 0) and deposit_addition is not None:
            metrics["deposit_growth_proxy"] = deposit_addition / deposits
        elif deposit_addition is not None and receive_interest_cash not in (None, 0):
            metrics["deposit_growth_proxy"] = deposit_addition / receive_interest_cash
        else:
            metrics["deposit_growth_proxy"] = None
        if metrics.get("general_risk_preparation") not in (None, 0) and metrics.get(
            "credit_impairment_loss"
        ) not in (None, 0):
            metrics["provision_coverage_proxy"] = metrics[
                "general_risk_preparation"
            ] / abs(metrics["credit_impairment_loss"])
        else:
            metrics["provision_coverage_proxy"] = None
        if metrics.get("dividend_payout_ratio") is None and metrics.get(
            "pay_dividends_profits_interest_cash"
        ) not in (None, 0):
            net_profit = self._first_numeric(
                abstract_payload.get("净利润"),
                analysis_payload.get("扣除非经常性损益后的净利润(元)"),
            )
            if net_profit not in (None, 0):
                metrics["dividend_payout_ratio"] = (
                    metrics["pay_dividends_profits_interest_cash"] / net_profit
                )
        available_fields = sorted(
            key for key, value in metrics.items() if value is not None
        )
        metrics["available_fields"] = available_fields
        metrics["is_bank_like"] = len(available_fields) >= 6 and any(
            metrics.get(key) is not None
            for key in (
                "deposits_and_deposits",
                "customer_loan_advance_net_addition",
                "customer_and_interbank_deposits_addition",
            )
        )
        return metrics

    def _comparison_symbol(self, symbol: str) -> str:
        normalized = normalize_ashare_symbol(symbol)
        if normalized.startswith(("sh", "sz", "bj")) and len(normalized) > 2:
            return f"{normalized[:2].upper()}{normalized[2:]}"
        return str(symbol).strip().upper()

    def _comparison_row_by_code(self, frame, code: str) -> dict[str, Any]:
        if frame is None or getattr(frame, "empty", True):
            return {}
        target = str(code or "").strip()
        for _, row in frame.iterrows():
            row_dict = {
                str(key): normalize_value(value) for key, value in row.to_dict().items()
            }
            row_code = str(row_dict.get(self._COMPARISON_CODE_LABEL) or "").strip()
            row_name = str(row_dict.get(self._COMPARISON_NAME_LABEL) or "").strip()
            if row_code == target or row_name == target:
                return row_dict
        return {}

    def _comparison_numeric(self, row: dict[str, Any], key: str) -> float | None:
        if not row:
            return None
        return self._first_numeric(row.get(key))

    def _comparison_ratio(self, row: dict[str, Any], key: str) -> float | None:
        value = self._comparison_numeric(row, key)
        if value is None:
            return None
        return value / 100.0

    def _metric_value(self, payload: dict[str, Any], key: str) -> Any:
        item = payload.get(key)
        if isinstance(item, dict):
            return item.get("value")
        return None

    def _metric_yoy(self, payload: dict[str, Any], key: str) -> Any:
        item = payload.get(key)
        if isinstance(item, dict):
            value = item.get("yoy")
            return value if value not in (None, "", False) else item.get("single_yoy")
        return None

    def _first_numeric(self, *values: Any) -> float | None:
        for value in values:
            parsed = coerce_numeric(value)
            if parsed is not None:
                return parsed
        return None
