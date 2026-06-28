from __future__ import annotations

from datetime import date, timedelta
from difflib import SequenceMatcher
import re
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value


class AkShareSectorAdapter:
    vendor_name = "akshare"

    def resolve_sector(self, *, query: str) -> dict[str, Any]:
        normalized_query = self._normalize_query(query)
        candidates = self._load_sector_candidates()
        scored_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            score = self._match_score(normalized_query, candidate["sector_name"])
            exact = normalized_query == candidate["sector_name"] or candidate["sector_name"] in normalized_query
            if score < 0.45 and not exact:
                continue
            scored_candidates.append(
                {
                    **candidate,
                    "score": score,
                    "exact": exact,
                    "matched_query": normalized_query,
                }
            )

        if not scored_candidates:
            raise ValueError(f"no sector match found for query: {query}")

        scored_candidates.sort(
            key=lambda item: (
                item["score"],
                item["exact"],
                item["catalog_source"] == "em",
                -abs(len(item["sector_name"]) - len(normalized_query)),
            ),
            reverse=True,
        )
        best = scored_candidates[0]
        if best["score"] < 0.6 and not best["exact"]:
            raise ValueError(f"sector match confidence too low for query: {query}")
        return best

    def get_sector_snapshot(self, *, query: str, sector_name: str, sector_type: str) -> dict[str, Any]:
        em_error: str | None = None
        try:
            return self._get_sector_snapshot_em(query=query, sector_name=sector_name, sector_type=sector_type)
        except Exception as exc:
            em_error = str(exc)

        try:
            snapshot = self._get_sector_snapshot_ths(query=query, sector_name=sector_name, sector_type=sector_type)
            snapshot["fallback_reason"] = em_error
            return snapshot
        except Exception as exc:
            if em_error:
                raise ValueError(f"em_error={em_error}; ths_error={exc}") from exc
            raise

    def _get_sector_snapshot_em(self, *, query: str, sector_name: str, sector_type: str) -> dict[str, Any]:
        today = date.today()
        start_date = (today - timedelta(days=30)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")

        if sector_type == "concept":
            spot_df = call_with_retry(lambda: ak.stock_board_concept_spot_em(symbol=sector_name))
            hist_df = call_with_retry(
                lambda: ak.stock_board_concept_hist_em(symbol=sector_name, start_date=start_date, end_date=end_date)
            )
            cons_df = call_with_retry(lambda: ak.stock_board_concept_cons_em(symbol=sector_name))
        else:
            spot_df = call_with_retry(lambda: ak.stock_board_industry_spot_em(symbol=sector_name))
            hist_df = call_with_retry(
                lambda: ak.stock_board_industry_hist_em(symbol=sector_name, start_date=start_date, end_date=end_date)
            )
            cons_df = call_with_retry(lambda: ak.stock_board_industry_cons_em(symbol=sector_name))

        if hist_df.empty:
            raise ValueError(f"empty sector history for {sector_name}")

        latest_hist = self._normalize_hist_record(hist_df.iloc[-1].to_dict())
        recent_hist = [self._normalize_hist_record(item) for item in hist_df.tail(5).to_dict("records")]
        all_constituents = [self._normalize_constituent_record(item) for item in cons_df.to_dict("records")]
        top_constituents = all_constituents[:10]

        return {
            "query": query,
            "sector_name": sector_name,
            "sector_type": sector_type,
            "start_date": start_date,
            "end_date": end_date,
            "data_source": "em",
            "spot": self._normalize_spot_metrics(spot_df.to_dict("records")),
            "latest_hist": latest_hist,
            "recent_hist": recent_hist,
            "top_constituents": top_constituents,
            "constituent_breadth_source": all_constituents,
            "constituent_count": int(len(cons_df)),
        }

    def _get_sector_snapshot_ths(self, *, query: str, sector_name: str, sector_type: str) -> dict[str, Any]:
        today = date.today()
        start_date = (today - timedelta(days=30)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")

        if sector_type == "concept":
            hist_df = call_with_retry(
                lambda: ak.stock_board_concept_index_ths(symbol=sector_name, start_date=start_date, end_date=end_date)
            )
            info_df = call_with_retry(lambda: ak.stock_board_concept_info_ths(symbol=sector_name))
            try:
                cons_df = call_with_retry(lambda: ak.stock_board_concept_cons_em(symbol=sector_name))
            except Exception:
                cons_df = None
        else:
            hist_df = call_with_retry(
                lambda: ak.stock_board_industry_index_ths(symbol=sector_name, start_date=start_date, end_date=end_date)
            )
            info_df = call_with_retry(lambda: ak.stock_board_industry_info_ths(symbol=sector_name))
            try:
                cons_df = call_with_retry(lambda: ak.stock_board_industry_cons_em(symbol=sector_name))
            except Exception:
                cons_df = None

        if hist_df.empty:
            raise ValueError(f"empty ths sector history for {sector_name}")

        latest_hist = self._normalize_hist_record(hist_df.iloc[-1].to_dict())
        recent_hist = [self._normalize_hist_record(item) for item in hist_df.tail(5).to_dict("records")]
        all_constituents: list[dict[str, Any]] = []
        top_constituents = []
        if cons_df is not None and not cons_df.empty:
            all_constituents = [self._normalize_constituent_record(item) for item in cons_df.to_dict("records")]
            top_constituents = all_constituents[:10]

        return {
            "query": query,
            "sector_name": sector_name,
            "sector_type": sector_type,
            "start_date": start_date,
            "end_date": end_date,
            "data_source": "ths",
            "spot": self._normalize_info_metrics(info_df.to_dict("records")),
            "latest_hist": latest_hist,
            "recent_hist": recent_hist,
            "top_constituents": top_constituents,
            "constituent_breadth_source": all_constituents,
            "constituent_count": int(len(cons_df)) if cons_df is not None else 0,
        }

    def get_benchmark_snapshot(
        self,
        *,
        benchmark_code: str = "000300",
        benchmark_name: str = "HS300",
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        hist_df = call_with_retry(
            lambda: ak.index_zh_a_hist(
                symbol=benchmark_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
            )
        )
        latest_hist = self._normalize_hist_record(hist_df.iloc[-1].to_dict()) if not hist_df.empty else {}
        recent_hist = [self._normalize_hist_record(item) for item in hist_df.tail(5).to_dict("records")] if not hist_df.empty else []
        return {
            "benchmark_code": benchmark_code,
            "benchmark_name": benchmark_name,
            "start_date": start_date,
            "end_date": end_date,
            "latest_hist": latest_hist,
            "recent_hist": recent_hist,
        }

    def build_relative_strength(
        self,
        *,
        sector_recent_hist: list[dict[str, Any]],
        benchmark_recent_hist: list[dict[str, Any]],
    ) -> dict[str, Any]:
        comparisons: list[dict[str, Any]] = []
        benchmark_by_date = {str(item.get("date")): item for item in benchmark_recent_hist if item.get("date")}
        for sector_row in sector_recent_hist:
            date_key = str(sector_row.get("date"))
            benchmark_row = benchmark_by_date.get(date_key)
            if not benchmark_row:
                continue
            sector_pct = self._to_float(sector_row.get("pct_change"))
            benchmark_pct = self._to_float(benchmark_row.get("pct_change"))
            if sector_pct is None or benchmark_pct is None:
                continue
            comparisons.append(
                {
                    "date": date_key,
                    "sector_pct_change": sector_pct,
                    "benchmark_pct_change": benchmark_pct,
                    "excess_pct_change": round(sector_pct - benchmark_pct, 2),
                }
            )

        latest = comparisons[-1] if comparisons else None
        return {
            "latest_excess_pct_change": latest.get("excess_pct_change") if latest else None,
            "recent_relative_strength": comparisons,
        }

    def _load_sector_candidates(self) -> list[dict[str, Any]]:
        candidates: dict[tuple[str, str], dict[str, Any]] = {}

        concept_em_rows = call_with_retry(ak.stock_board_concept_name_em).to_dict("records")
        for row in concept_em_rows:
            self._add_candidate(
                candidates,
                sector_name=str(row.get("板块名称") or "").strip(),
                sector_code=str(row.get("板块代码") or "").strip(),
                sector_type="concept",
                catalog_source="em",
            )

        industry_em_rows = call_with_retry(ak.stock_board_industry_name_em).to_dict("records")
        for row in industry_em_rows:
            self._add_candidate(
                candidates,
                sector_name=str(row.get("板块名称") or "").strip(),
                sector_code=str(row.get("板块代码") or "").strip(),
                sector_type="industry",
                catalog_source="em",
            )

        concept_ths_rows = call_with_retry(ak.stock_board_concept_name_ths).to_dict("records")
        for row in concept_ths_rows:
            self._add_candidate(
                candidates,
                sector_name=str(row.get("name") or "").strip(),
                sector_code=str(row.get("code") or "").strip(),
                sector_type="concept",
                catalog_source="ths",
            )

        industry_ths_rows = call_with_retry(ak.stock_board_industry_name_ths).to_dict("records")
        for row in industry_ths_rows:
            self._add_candidate(
                candidates,
                sector_name=str(row.get("name") or "").strip(),
                sector_code=str(row.get("code") or "").strip(),
                sector_type="industry",
                catalog_source="ths",
            )

        return list(candidates.values())

    def _add_candidate(
        self,
        candidates: dict[tuple[str, str], dict[str, Any]],
        *,
        sector_name: str,
        sector_code: str,
        sector_type: str,
        catalog_source: str,
    ) -> None:
        if not sector_name:
            return
        key = (sector_type, sector_name)
        candidate = {
            "sector_name": sector_name,
            "sector_code": sector_code,
            "sector_type": sector_type,
            "catalog_source": catalog_source,
        }
        existing = candidates.get(key)
        if existing is None or (existing["catalog_source"] != "em" and catalog_source == "em"):
            candidates[key] = candidate

    def _normalize_query(self, query: str) -> str:
        cleaned = query.strip()
        stop_phrases = [
            "帮我",
            "看下",
            "看看",
            "分析",
            "研究",
            "最近",
            "近期",
            "当前",
            "现在",
            "一下",
            "板块",
            "行业",
            "主题",
            "概念",
            "赛道",
            "行情",
            "走势",
            "表现",
            "情况",
            "怎么样",
            "如何",
            "怎么看",
            "如何看",
            "这个",
        ]
        for phrase in stop_phrases:
            cleaned = cleaned.replace(phrase, " ")
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9()+\-]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned.replace(" ", "") or query.strip()

    def _match_score(self, query: str, candidate: str) -> float:
        if not query or not candidate:
            return 0.0
        normalized_candidate = candidate.strip().lower()
        normalized_query = query.strip().lower()
        if normalized_candidate == normalized_query:
            return 1.0
        if normalized_candidate in normalized_query:
            length_gap = abs(len(normalized_query) - len(normalized_candidate))
            return 0.95 - min(length_gap * 0.01, 0.15)
        if normalized_query in normalized_candidate:
            length_gap = abs(len(normalized_query) - len(normalized_candidate))
            return 0.93 - min(length_gap * 0.01, 0.15)
        token_overlap = self._token_overlap_score(normalized_query, normalized_candidate)
        return SequenceMatcher(None, normalized_query, normalized_candidate).ratio() + token_overlap

    def _token_overlap_score(self, query: str, candidate: str) -> float:
        tokens = re.findall(r"[A-Za-z0-9()+\-]+|[\u4e00-\u9fff]{2,}", query)
        if not tokens:
            return 0.0
        hits = sum(1 for token in tokens if token and token in candidate)
        return min(hits * 0.08, 0.24)

    def _normalize_spot_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for row in rows:
            item = str(row.get("item") or row.get("项目") or "").strip()
            if not item:
                continue
            value = normalize_value(row.get("value") if "value" in row else row.get("值"))
            metrics[self._metric_key(item)] = value
        return metrics

    def _normalize_info_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for row in rows:
            item = str(row.get("项目") or row.get("item") or "").strip()
            if not item:
                continue
            metrics[self._metric_key(item)] = normalize_value(row.get("值") if "值" in row else row.get("value"))
        return metrics

    def _metric_key(self, item: str) -> str:
        mapping = {
            "最新": "latest",
            "最新价": "latest",
            "今开": "open",
            "开盘": "open",
            "开盘价": "open",
            "昨收": "prev_close",
            "最高": "high",
            "最高价": "high",
            "最低": "low",
            "最低价": "low",
            "涨跌幅": "pct_change",
            "涨跌额": "pct_change_amount",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "换手率": "turnover_rate",
            "总市值": "market_cap",
            "流通市值": "float_market_cap",
            "上涨家数": "up_count",
            "下跌家数": "down_count",
            "平盘家数": "flat_count",
            "领涨股": "leader_stock",
            "领涨股-涨跌幅": "leader_stock_pct_change",
            "领涨股票": "leader_stock",
            "领涨股票-涨跌幅": "leader_stock_pct_change",
        }
        return mapping.get(item, item.lower())

    def _normalize_hist_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "date": normalize_value(record.get("日期") or record.get("date")),
            "open": normalize_value(record.get("开盘") or record.get("开盘价") or record.get("open")),
            "close": normalize_value(record.get("收盘") or record.get("收盘价") or record.get("close")),
            "high": normalize_value(record.get("最高") or record.get("最高价") or record.get("high")),
            "low": normalize_value(record.get("最低") or record.get("最低价") or record.get("low")),
            "pct_change": normalize_value(record.get("涨跌幅") or record.get("pct_change")),
            "pct_change_amount": normalize_value(record.get("涨跌额") or record.get("pct_change_amount")),
            "volume": normalize_value(record.get("成交量") or record.get("volume")),
            "amount": normalize_value(record.get("成交额") or record.get("amount")),
            "amplitude": normalize_value(record.get("振幅") or record.get("amplitude")),
            "turnover_rate": normalize_value(record.get("换手率") or record.get("turnover_rate")),
        }

    def _normalize_constituent_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(record.get("代码") or record.get("code") or "").strip(),
            "name": str(record.get("名称") or record.get("name") or "").strip(),
            "latest_price": normalize_value(record.get("最新价") or record.get("latest_price")),
            "pct_change": normalize_value(record.get("涨跌幅") or record.get("pct_change")),
            "pct_change_amount": normalize_value(record.get("涨跌额") or record.get("pct_change_amount")),
            "volume": normalize_value(record.get("成交量") or record.get("volume")),
            "amount": normalize_value(record.get("成交额") or record.get("amount")),
            "amplitude": normalize_value(record.get("振幅") or record.get("amplitude")),
            "high": normalize_value(record.get("最高") or record.get("high")),
            "low": normalize_value(record.get("最低") or record.get("low")),
            "open": normalize_value(record.get("今开") or record.get("open")),
            "prev_close": normalize_value(record.get("昨收") or record.get("prev_close")),
            "turnover_rate": normalize_value(record.get("换手率") or record.get("turnover_rate")),
            "pe_dynamic": normalize_value(record.get("市盈率-动态") or record.get("pe_dynamic")),
            "pb": normalize_value(record.get("市净率") or record.get("pb")),
        }

    def _to_float(self, value: Any) -> float | None:
        if value in (None, "", False):
            return None
        try:
            return float(value)
        except Exception:
            return None
