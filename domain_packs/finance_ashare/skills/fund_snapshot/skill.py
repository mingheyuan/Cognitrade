from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from io import StringIO
import math
import statistics
from typing import Any

import akshare as ak
import pandas as pd
import requests
from akshare.utils import demjson
from bs4 import BeautifulSoup

from domain_packs.finance_ashare.tools.fund_master import FundSecurityMaster
from research_system.adapters.vendors.akshare_utils import call_with_retry, coerce_numeric, normalize_value


_CATEGORY_ALIASES = {
    "全部": "全部",
    "股票型": "股票型",
    "混合型": "混合型",
    "债券型": "债券型",
    "指数型": "指数型",
    "qdii": "QDII",
    "fof": "FOF",
    "lof": "LOF",
    "etf": "ETF",
}
_RETURN_FIELDS = {
    "daily": "日增长率",
    "week_1": "近1周",
    "month_1": "近1月",
    "month_3": "近3月",
    "month_6": "近6月",
    "year_1": "近1年",
    "year_2": "近2年",
    "year_3": "近3年",
    "year_to_date": "今年来",
    "since_inception": "成立来",
}


class FundSnapshotSkill:
    """Collect real fund identity, ranking, NAV risk and disclosed holdings."""

    skill_name = "fund_snapshot_skill"

    def __init__(
        self,
        *,
        fund_master: FundSecurityMaster | None = None,
        open_rank_loader: Callable[[str], Any] | None = None,
        exchange_rank_loader: Callable[[], Any] | None = None,
        nav_loader: Callable[[str], Any] | None = None,
        holdings_loader: Callable[[str, str], Any] | None = None,
        overview_loader: Callable[[str], Any] | None = None,
        achievement_loader: Callable[[str], Any] | None = None,
        manager_tenure_loader: Callable[[str], Any] | None = None,
        scale_history_loader: Callable[[str], Any] | None = None,
    ) -> None:
        self.fund_master = fund_master or FundSecurityMaster()
        self.open_rank_loader = open_rank_loader or (
            lambda category: call_with_retry(
                lambda: ak.fund_open_fund_rank_em(symbol=category),
                retries=2,
                delay_seconds=0.25,
            )
        )
        self.exchange_rank_loader = exchange_rank_loader or (
            lambda: call_with_retry(ak.fund_exchange_rank_em, retries=2, delay_seconds=0.25)
        )
        self.nav_loader = nav_loader or self._load_nav
        self.holdings_loader = holdings_loader or self._load_holdings
        self.overview_loader = overview_loader or self._load_overview
        self.achievement_loader = achievement_loader or self._load_achievement
        self.manager_tenure_loader = manager_tenure_loader or self._load_manager_tenure
        self.scale_history_loader = scale_history_loader or self._load_scale_history

    def run(
        self,
        *,
        query: str,
        fund_codes: list[str] | None = None,
        fund_category: str | None = None,
        candidate_pool_size: int = 8,
        investment_cycle: str = "mid_term",
    ) -> dict[str, Any]:
        category = self.normalize_category(fund_category)
        requested_codes = self._unique_codes(fund_codes or [])
        limit = max(1, min(int(candidate_pool_size), 20))
        errors: list[dict[str, str]] = []

        try:
            catalog_rows = self.fund_master.rows()
        except Exception as exc:
            catalog_rows = []
            errors.append(self._error("fund_catalog", exc))
        catalog = {str(row.get("fund_code") or ""): row for row in catalog_rows}

        ranking_rows: list[dict[str, Any]] = []
        ranking_sources: list[str] = []
        # Explicit codes can span categories (for example a QDII plus a mixed
        # fund), so query the complete public ranking before filtering by code.
        open_category = "全部" if requested_codes or category == "ETF" else category
        try:
            ranking_rows.extend(self._rows(self.open_rank_loader(open_category)))
            ranking_sources.append(f"akshare.fund_open_fund_rank_em:{open_category}")
        except Exception as exc:
            errors.append(self._error("open_fund_ranking", exc))
        if category in {"ETF", "LOF"} or requested_codes:
            try:
                ranking_rows.extend(self._rows(self.exchange_rank_loader()))
                ranking_sources.append("akshare.fund_exchange_rank_em")
            except Exception as exc:
                errors.append(self._error("exchange_fund_ranking", exc))

        candidates = self._select_candidates(
            ranking_rows,
            catalog=catalog,
            requested_codes=requested_codes,
            category=category,
            limit=limit,
            investment_cycle=investment_cycle,
        )
        if requested_codes:
            ranked_codes = {str(row.get("fund_code") or "") for row in candidates}
            for code in requested_codes:
                if code in ranked_codes:
                    continue
                identity = catalog.get(code) or {}
                candidates.append(
                    {
                        "fund_code": code,
                        "fund_name": identity.get("fund_name") or code,
                        "fund_type": identity.get("fund_type"),
                        "as_of": None,
                        "unit_nav": None,
                        "accumulated_nav": None,
                        "returns_pct": {},
                        "fee_pct": None,
                        "ranking_source": None,
                        "data_gap": ["fund_ranking_missing"],
                    }
                )

        enriched: list[dict[str, Any]] = []
        fund_source_refs: list[dict[str, str]] = []
        for candidate in candidates[:limit]:
            code = str(candidate["fund_code"])
            row = dict(candidate)
            row_errors: list[str] = []
            try:
                nav_rows = self._normalize_nav(self.nav_loader(code))
                row["nav_history"] = nav_rows
                row["risk"] = self._risk_metrics(nav_rows)
                if not nav_rows:
                    row_errors.append("nav_history_missing")
                else:
                    fund_source_refs.append(
                        {
                            "type": "fund_nav",
                            "provider": "eastmoney_via_akshare",
                            "id": f"akshare.fund_open_fund_info_em:{code}",
                        }
                    )
            except Exception as exc:
                row["nav_history"] = []
                row["risk"] = {}
                row_errors.append("nav_history_unavailable")
                errors.append(self._error(f"nav:{code}", exc))
            try:
                holdings_payload = self._latest_holdings(code)
                row.update(holdings_payload)
                if not row.get("top_holdings"):
                    row_errors.append("fund_holdings_missing")
                else:
                    fund_source_refs.append(
                        {
                            "type": "fund_holdings",
                            "provider": "eastmoney",
                            "id": f"eastmoney.fund_archives:{code}:{row.get('holdings_period') or 'latest'}",
                        }
                    )
            except Exception as exc:
                row.update({"top_holdings": [], "holdings_period": None, "top10_concentration_pct": None})
                row_errors.append("fund_holdings_unavailable")
                errors.append(self._error(f"holdings:{code}", exc))
            try:
                overview = self._normalize_overview(self.overview_loader(code))
                row["fund_overview"] = overview
                row["benchmark_definition"] = overview.get("benchmark_definition")
                if not overview:
                    row_errors.append("fund_overview_missing")
                else:
                    fund_source_refs.append(
                        {
                            "type": "fund_overview",
                            "provider": "danjuan_via_akshare",
                            "id": f"akshare.fund_individual_basic_info_xq:{code}",
                        }
                    )
                if not row.get("benchmark_definition"):
                    row_errors.append("benchmark_definition_missing")
            except Exception as exc:
                row.update({"fund_overview": {}, "benchmark_definition": None})
                row_errors.extend(["fund_overview_unavailable", "benchmark_definition_missing"])
                errors.append(self._error(f"overview:{code}", exc))
            try:
                performance = self._normalize_achievement(self.achievement_loader(code))
                row["benchmark_performance"] = performance
                if not performance:
                    row_errors.append("benchmark_performance_missing")
                else:
                    fund_source_refs.append(
                        {
                            "type": "fund_benchmark_performance",
                            "provider": "danjuan_public_http",
                            "id": f"danjuan.fund_achievement:{code}",
                        }
                    )
            except Exception as exc:
                row["benchmark_performance"] = []
                row_errors.append("benchmark_performance_unavailable")
                errors.append(self._error(f"benchmark:{code}", exc))
            try:
                manager_tenures = self._normalize_manager_tenures(
                    self.manager_tenure_loader(code)
                )
                row["manager_tenures"] = manager_tenures
                if not manager_tenures:
                    row_errors.append("manager_tenure_history_missing")
                else:
                    fund_source_refs.append(
                        {
                            "type": "fund_manager_tenure",
                            "provider": "eastmoney",
                            "id": f"eastmoney.fund_manager_tenure:{code}",
                        }
                    )
            except Exception as exc:
                row["manager_tenures"] = []
                row_errors.append("manager_tenure_history_unavailable")
                errors.append(self._error(f"manager_tenure:{code}", exc))
            try:
                scale_history = self._normalize_scale_history(
                    self.scale_history_loader(code)
                )
                row["scale_history"] = scale_history
                row["scale_change"] = self._scale_change(scale_history)
                if not scale_history:
                    row_errors.append("fund_scale_history_missing")
                else:
                    fund_source_refs.append(
                        {
                            "type": "fund_scale_history",
                            "provider": "eastmoney",
                            "id": f"eastmoney.fund_scale_history:{code}",
                        }
                    )
            except Exception as exc:
                row.update({"scale_history": [], "scale_change": {}})
                row_errors.append("fund_scale_history_unavailable")
                errors.append(self._error(f"scale_history:{code}", exc))
            row["data_gap"] = list(dict.fromkeys([*list(row.get("data_gap") or []), *row_errors]))
            row["quality_status"] = "degraded" if row["data_gap"] else "available"
            enriched.append(row)

        available = bool(enriched)
        quality_status = (
            "data_gap"
            if not available
            else "degraded"
            if errors or any(row.get("data_gap") for row in enriched)
            else "available"
        )
        return {
            "query": query,
            "fund_codes": [str(row.get("fund_code") or "") for row in enriched],
            "fund_category": category,
            "investment_cycle": investment_cycle,
            "candidate_pool_size": limit,
            "funds": enriched,
            "quality_status": quality_status,
            "data_gap": ["fund_candidates_unavailable"] if not available else [],
            "provider_errors": errors,
            "source_refs": [
                *(
                    [
                        {
                            "type": "fund_catalog",
                            "provider": "eastmoney_via_akshare",
                            "id": "akshare.fund_name_em",
                        }
                    ]
                    if catalog_rows
                    else []
                ),
                *[
                    {
                        "type": "fund_ranking",
                        "provider": "eastmoney_via_akshare",
                        "id": source,
                    }
                    for source in ranking_sources
                ],
                *fund_source_refs,
            ],
            "retrieved_at": datetime.now(UTC).isoformat(),
        }

    @classmethod
    def normalize_category(cls, value: object) -> str:
        text = str(value or "全部").strip()
        lowered = text.casefold()
        for alias, category in _CATEGORY_ALIASES.items():
            if lowered == alias.casefold() or alias.casefold() in lowered:
                return category
        return "全部"

    def _select_candidates(
        self,
        rows: list[dict[str, Any]],
        *,
        catalog: dict[str, dict[str, Any]],
        requested_codes: list[str],
        category: str,
        limit: int,
        investment_cycle: str,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in rows:
            code = FundSecurityMaster.normalize_code(self._first(raw, ("基金代码", "代码", "fund_code")))
            if not code or code in seen:
                continue
            identity = catalog.get(code) or {}
            fund_type = str(
                identity.get("fund_type")
                or self._first(raw, ("基金类型", "类型", "fund_type"))
                or ""
            ).strip() or None
            name = str(
                identity.get("fund_name")
                or self._first(raw, ("基金简称", "基金名称", "名称", "fund_name"))
                or code
            ).strip()
            if requested_codes and code not in requested_codes:
                continue
            if not requested_codes and not self._category_matches(category, name=name, fund_type=fund_type):
                continue
            seen.add(code)
            returns = {
                key: self._numeric(raw.get(source_key))
                for key, source_key in _RETURN_FIELDS.items()
                if self._numeric(raw.get(source_key)) is not None
            }
            normalized.append(
                {
                    "fund_code": code,
                    "fund_name": name,
                    "fund_type": fund_type,
                    "as_of": self._date_value(self._first(raw, ("日期", "净值日期", "as_of"))),
                    "unit_nav": self._numeric(self._first(raw, ("单位净值", "unit_nav"))),
                    "accumulated_nav": self._numeric(self._first(raw, ("累计净值", "accumulated_nav"))),
                    "returns_pct": returns,
                    "fee_pct": self._percent_value(self._first(raw, ("手续费", "费率", "fee"))),
                    "ranking_source": "eastmoney_fund_ranking_via_akshare",
                    "data_gap": [],
                }
            )
        if requested_codes:
            order = {code: index for index, code in enumerate(requested_codes)}
            normalized.sort(key=lambda row: order.get(str(row["fund_code"]), len(order)))
            return normalized[:limit]

        horizon = {"short_term": "month_3", "mid_term": "year_1", "long_term": "year_3"}.get(
            str(investment_cycle),
            "year_1",
        )
        normalized = [row for row in normalized if row.get("returns_pct", {}).get(horizon) is not None]
        normalized.sort(
            key=lambda row: float(row.get("returns_pct", {}).get(horizon)),
            reverse=True,
        )
        return normalized[:limit]

    @staticmethod
    def _category_matches(category: str, *, name: str, fund_type: str | None) -> bool:
        if category == "全部":
            return True
        haystack = f"{name} {fund_type or ''}".casefold()
        if category == "ETF":
            return "etf" in haystack
        if category == "LOF":
            return "lof" in haystack
        return category.casefold().replace("型", "") in haystack.replace("型", "")

    def _latest_holdings(self, code: str) -> dict[str, Any]:
        errors: list[Exception] = []
        for year in (str(date.today().year), str(date.today().year - 1), str(date.today().year - 2)):
            try:
                payload = self.holdings_loader(code, year)
                rows, period = self._normalize_holdings(payload)
                if rows:
                    top = rows[:10]
                    weights = [row["weight_pct"] for row in top if row.get("weight_pct") is not None]
                    return {
                        "top_holdings": top,
                        "holdings_period": period,
                        "top10_concentration_pct": round(sum(weights), 4) if weights else None,
                        "holdings_source": "eastmoney_fund_archives",
                    }
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[-1]
        return {"top_holdings": [], "holdings_period": None, "top10_concentration_pct": None}

    @staticmethod
    def _load_nav(code: str):
        return call_with_retry(
            lambda: ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势", period="近3年"),
            retries=2,
            delay_seconds=0.25,
        )

    @staticmethod
    def _load_holdings(code: str, year: str):
        try:
            return call_with_retry(
                lambda: ak.fund_portfolio_hold_em(symbol=code, date=year),
                retries=1,
            )
        except Exception:
            url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
            response = requests.get(
                url,
                params={
                    "type": "jjcc",
                    "code": code,
                    "topline": "10000",
                    "year": year,
                    "month": "",
                    "rt": "0.913877030254846",
                },
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html",
                },
                timeout=20,
            )
            response.raise_for_status()
            text = response.text
            start = text.find("{")
            if start < 0:
                raise ValueError("EastMoney holdings response did not contain a data object")
            payload = demjson.decode(text[start:-1])
            content = str(payload.get("content") or "")
            if not content:
                return []
            soup = BeautifulSoup(content, features="lxml")
            periods = [item.get_text(" ", strip=True) for item in soup.find_all("h4", class_="t")]
            frames = pd.read_html(StringIO(content), converters={"股票代码": str})
            return [
                {"period": periods[index] if index < len(periods) else None, "rows": frame.to_dict("records")}
                for index, frame in enumerate(frames)
            ]

    @staticmethod
    def _load_overview(code: str):
        return call_with_retry(
            lambda: ak.fund_individual_basic_info_xq(symbol=code, timeout=15),
            retries=2,
            delay_seconds=0.25,
        )

    @staticmethod
    def _load_achievement(code: str) -> dict[str, Any]:
        response = requests.get(
            f"https://danjuanfunds.com/djapi/fundx/base/fund/achievement/{code}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Cognitrade/1.0)"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _load_manager_tenure(code: str):
        response = requests.get(
            f"https://fundf10.eastmoney.com/jjjl_{code}.html",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Cognitrade/1.0)"},
            timeout=20,
        )
        response.raise_for_status()
        frames = pd.read_html(StringIO(response.text))
        return next(
            (
                frame
                for frame in frames
                if {"起始期", "截止期", "基金经理", "任职期间", "任职回报"}.issubset(
                    {str(column) for column in frame.columns}
                )
            ),
            pd.DataFrame(),
        )

    @staticmethod
    def _load_scale_history(code: str) -> list[dict[str, Any]]:
        response = requests.get(
            "https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
            params={"type": "gmbd", "mode": "0", "code": code, "rt": "0.1"},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Cognitrade/1.0)",
                "Referer": f"https://fundf10.eastmoney.com/gmbd_{code}.html",
            },
            timeout=20,
        )
        response.raise_for_status()
        text = response.text
        start = text.find("{")
        if start < 0:
            return []
        payload = demjson.decode(text[start:-1])
        rows = payload.get("data") if isinstance(payload, dict) else None
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    @classmethod
    def _normalize_overview(cls, payload: Any) -> dict[str, Any]:
        rows = cls._rows(payload)
        values: dict[str, Any] = {}
        if rows and {"item", "value"}.issubset(rows[0]):
            values = {
                str(row.get("item") or "").strip(): normalize_value(row.get("value"))
                for row in rows
                if str(row.get("item") or "").strip()
            }
        elif rows:
            values = {str(key): normalize_value(value) for key, value in rows[0].items()}
        if not values:
            return {}
        normalized = {
            "fund_company": cls._first(values, ("基金公司", "管理人")),
            "fund_manager": cls._first(values, ("基金经理",)),
            "inception_date": cls._date_value(cls._first(values, ("成立时间", "成立日期"))),
            "latest_scale": normalize_value(cls._first(values, ("最新规模", "基金规模"))),
            "custodian": cls._first(values, ("托管银行", "托管人")),
            "investment_objective": cls._first(values, ("投资目标",)),
            "investment_strategy": cls._first(values, ("投资策略",)),
            "benchmark_definition": cls._first(values, ("业绩比较基准",)),
        }
        return normalized if any(
            value is not None and value != "" for value in normalized.values()
        ) else {}

    @classmethod
    def _normalize_achievement(cls, payload: Any) -> list[dict[str, Any]]:
        raw_rows: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            for group, label in (
                ("stage_performance_list", "stage"),
                ("annual_performance_list", "annual"),
            ):
                for item in list(payload.get(group) or []):
                    if isinstance(item, dict):
                        raw_rows.append({**item, "period_group": label})
        else:
            raw_rows = cls._rows(payload)
        output: list[dict[str, Any]] = []
        for raw in raw_rows:
            period = str(
                cls._first(raw, ("period_time", "周期", "period")) or ""
            ).strip()
            fund_return = cls._percent_value(
                cls._first(raw, ("self_nav", "本产品区间收益", "fund_return_pct"))
            )
            benchmark_return = cls._percent_value(
                cls._first(raw, ("standard_index_nav", "基准区间收益", "benchmark_return_pct"))
            )
            if not period or (fund_return is None and benchmark_return is None):
                continue
            output.append(
                {
                    "period": period,
                    "period_group": raw.get("period_group") or raw.get("业绩类型"),
                    "fund_return_pct": fund_return,
                    "benchmark_return_pct": benchmark_return,
                    "excess_return_pct": (
                        round(fund_return - benchmark_return, 6)
                        if fund_return is not None and benchmark_return is not None
                        else None
                    ),
                    "fund_max_drawdown_pct": cls._percent_value(
                        cls._first(raw, ("self_max_draw_down", "本产品最大回撤"))
                    ),
                    "benchmark_max_drawdown_pct": cls._percent_value(
                        cls._first(raw, ("standard_index_max_draw_down", "基准最大回撤"))
                    ),
                    "peer_rank": cls._first(raw, ("self_nav_rank", "周期收益同类排名")),
                }
            )
        return output

    @classmethod
    def _normalize_manager_tenures(cls, payload: Any) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for raw in cls._rows(payload):
            manager = str(cls._first(raw, ("基金经理", "manager")) or "").strip()
            start_date = cls._date_value(cls._first(raw, ("起始期", "起始时间", "start_date")))
            end_value = cls._first(raw, ("截止期", "截止时间", "end_date"))
            end_text = str(end_value or "").strip()
            if not manager or not start_date:
                continue
            output.append(
                {
                    "manager": manager,
                    "managers": [item for item in manager.split() if item],
                    "start_date": start_date,
                    "end_date": None if end_text in {"", "至今"} else cls._date_value(end_value),
                    "current": end_text in {"", "至今"},
                    "tenure_text": cls._first(raw, ("任职期间", "任职天数", "tenure")),
                    "tenure_return_pct": cls._percent_value(
                        cls._first(raw, ("任职回报", "tenure_return_pct"))
                    ),
                }
            )
        output.sort(key=lambda row: str(row.get("start_date") or ""), reverse=True)
        return output

    @classmethod
    def _normalize_scale_history(cls, payload: Any) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for raw in cls._rows(payload):
            report_date = cls._date_value(
                cls._first(raw, ("FSRQ", "日期", "截止日期", "report_date"))
            )
            if not report_date:
                continue
            subscriptions = cls._amount_100m(
                raw, normalized_key="subscriptions_100m", raw_key="QJSG"
            )
            redemptions = cls._amount_100m(
                raw, normalized_key="redemptions_100m", raw_key="QJSH"
            )
            output.append(
                {
                    "report_date": report_date,
                    "subscriptions_100m_shares": subscriptions,
                    "redemptions_100m_shares": redemptions,
                    "net_flow_100m_shares": (
                        round(subscriptions - redemptions, 6)
                        if subscriptions is not None and redemptions is not None
                        else None
                    ),
                    "total_shares_100m": cls._amount_100m(
                        raw, normalized_key="total_shares_100m", raw_key="QMZFE"
                    ),
                    "net_assets_100m_cny": cls._amount_100m(
                        raw, normalized_key="net_assets_100m_cny", raw_key="QMJZC"
                    ),
                    "reported_share_change_pct": cls._percent_value(
                        cls._first(raw, ("ZFEBDL", "份额变动率", "reported_share_change_pct"))
                    ),
                }
            )
        output.sort(key=lambda row: str(row["report_date"]), reverse=True)
        return output[:20]

    @staticmethod
    def _scale_change(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {}
        latest = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        share_change = None
        asset_change = None
        if previous:
            latest_shares = coerce_numeric(latest.get("total_shares_100m"))
            previous_shares = coerce_numeric(previous.get("total_shares_100m"))
            latest_assets = coerce_numeric(latest.get("net_assets_100m_cny"))
            previous_assets = coerce_numeric(previous.get("net_assets_100m_cny"))
            if latest_shares is not None and previous_shares not in {None, 0}:
                share_change = (latest_shares / previous_shares - 1) * 100
            if latest_assets is not None and previous_assets not in {None, 0}:
                asset_change = (latest_assets / previous_assets - 1) * 100
        return {
            "latest_report_date": latest.get("report_date"),
            "previous_report_date": previous.get("report_date") if previous else None,
            "latest_total_shares_100m": latest.get("total_shares_100m"),
            "latest_net_assets_100m_cny": latest.get("net_assets_100m_cny"),
            "latest_net_flow_100m_shares": latest.get("net_flow_100m_shares"),
            "total_share_change_pct": round(share_change, 6) if share_change is not None else None,
            "net_asset_change_pct": round(asset_change, 6) if asset_change is not None else None,
            "status": "available" if previous else "data_gap",
        }

    @classmethod
    def _amount_100m(
        cls, row: dict[str, Any], *, normalized_key: str, raw_key: str
    ) -> float | None:
        normalized = cls._numeric(row.get(normalized_key))
        if normalized is not None:
            return normalized
        raw = cls._numeric(row.get(raw_key))
        return round(raw / 100_000_000, 8) if raw is not None else None

    @classmethod
    def _normalize_holdings(cls, payload: Any) -> tuple[list[dict[str, Any]], str | None]:
        period: str | None = None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "rows" in payload[0]:
            period = str(payload[0].get("period") or "").strip() or None
            raw_rows = list(payload[0].get("rows") or [])
        else:
            raw_rows = cls._rows(payload)
            periods = [str(row.get("季度") or "").strip() for row in raw_rows if row.get("季度")]
            if periods:
                period = periods[0]
                raw_rows = [row for row in raw_rows if str(row.get("季度") or "").strip() == period]
        normalized: list[dict[str, Any]] = []
        for index, row in enumerate(raw_rows, start=1):
            code = str(cls._first(row, ("股票代码", "证券代码", "code")) or "").strip()
            name = str(cls._first(row, ("股票名称", "证券简称", "name")) or "").strip()
            if not code or not name:
                continue
            normalized.append(
                {
                    "rank": int(cls._numeric(cls._first(row, ("序号", "rank"))) or index),
                    "code": code,
                    "name": name,
                    "weight_pct": cls._percent_value(
                        cls._first(row, ("占净值比例", "比例", "weight", "weight_pct"))
                    ),
                    "shares_10k": cls._numeric(
                        cls._first(row, ("持股数", "持股数 （万股）", "持股数（万股）"))
                    ),
                    "market_value_10k": cls._numeric(
                        cls._first(row, ("持仓市值", "持仓市值 （万元人民币）", "持仓市值（万元）"))
                    ),
                }
            )
        normalized.sort(key=lambda row: int(row["rank"]))
        return normalized, period

    @classmethod
    def _normalize_nav(cls, payload: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for raw in cls._rows(payload):
            nav = cls._numeric(cls._first(raw, ("单位净值", "净值", "close", "nav")))
            nav_date = cls._date_value(cls._first(raw, ("净值日期", "日期", "date")))
            if nav is None or nav <= 0 or not nav_date:
                continue
            rows.append({"date": nav_date, "nav": nav})
        rows.sort(key=lambda row: str(row["date"]))
        return rows[-756:]

    @staticmethod
    def _risk_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        values = [float(row["nav"]) for row in rows if row.get("nav") is not None]
        if len(values) < 2:
            return {}
        peak = values[0]
        max_drawdown = 0.0
        returns: list[float] = []
        previous = values[0]
        for value in values[1:]:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = min(max_drawdown, value / peak - 1.0)
            if previous > 0:
                returns.append(value / previous - 1.0)
            previous = value
        volatility = statistics.stdev(returns) * math.sqrt(252) * 100 if len(returns) >= 2 else None
        return {
            "observation_count": len(values),
            "max_drawdown_pct": round(max_drawdown * 100, 4),
            "annualized_volatility_pct": round(volatility, 4) if volatility is not None else None,
            "period_return_pct": round((values[-1] / values[0] - 1.0) * 100, 4),
            "period_start": rows[0]["date"],
            "period_end": rows[-1]["date"],
        }

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if hasattr(payload, "to_dict"):
            return [dict(row) for row in payload.to_dict("records")]
        return [dict(row) for row in payload if isinstance(row, dict)]

    @staticmethod
    def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            if key in row and row[key] not in (None, ""):
                return row[key]
        return None

    @staticmethod
    def _numeric(value: Any) -> float | None:
        number = coerce_numeric(value)
        return round(float(number), 8) if number is not None and math.isfinite(float(number)) else None

    @classmethod
    def _percent_value(cls, value: Any) -> float | None:
        if value is None:
            return None
        text = str(normalize_value(value)).strip()
        number = cls._numeric(text.replace("%", ""))
        return number

    @staticmethod
    def _date_value(value: Any) -> str | None:
        normalized = normalize_value(value)
        if normalized is None:
            return None
        if hasattr(normalized, "isoformat"):
            return normalized.isoformat()
        text = str(normalized).strip()
        return text or None

    @staticmethod
    def _error(stage: str, exc: Exception) -> dict[str, str]:
        return {"stage": stage, "error_type": exc.__class__.__name__}

    @classmethod
    def _unique_codes(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            code = FundSecurityMaster.normalize_code(value)
            if code and code not in result:
                result.append(code)
        return result


__all__ = ["FundSnapshotSkill"]
