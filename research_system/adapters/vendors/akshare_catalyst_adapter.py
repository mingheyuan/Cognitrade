from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import re
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value


class AkShareCatalystAdapter:
    vendor_name = "akshare"

    def get_symbol_news(self, *, symbol: str, limit: int = 3) -> list[dict[str, Any]]:
        news_df = call_with_retry(lambda: ak.stock_news_em(symbol=symbol), retries=2, delay_seconds=0.5)
        if news_df.empty:
            return []
        return [self._normalize_record(item) for item in news_df.head(limit).to_dict("records")]

    def get_symbol_research_reports(self, *, symbol: str, limit: int = 3) -> list[dict[str, Any]]:
        report_df = call_with_retry(lambda: ak.stock_research_report_em(symbol=symbol), retries=2, delay_seconds=0.5)
        if report_df.empty:
            return []
        return [self._normalize_record(item) for item in report_df.head(limit).to_dict("records")]

    def get_theme_news(self, *, theme: str, constituents: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
        theme_token = str(theme).strip()
        if not theme_token:
            return []

        try:
            main_df = call_with_retry(ak.stock_news_main_cx, retries=2, delay_seconds=0.5)
        except Exception:
            main_df = None

        news_items: list[dict[str, Any]] = []
        if main_df is not None and not main_df.empty:
            for row in main_df.to_dict("records"):
                normalized = self._normalize_record(row)
                summary = str(normalized.get("summary") or "")
                tag = str(normalized.get("tag") or "")
                if theme_token in summary or theme_token in tag:
                    news_items.append(
                        {
                            "source_type": "theme_main_news",
                            "matched_on": "theme",
                            **normalized,
                        }
                    )
                    if len(news_items) >= limit:
                        break

        if len(news_items) >= limit:
            return news_items[:limit]

        seen_links = {str(item.get("url") or item.get("新闻链接") or "") for item in news_items}
        for item in constituents[:5]:
            symbol = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if not symbol:
                continue
            try:
                symbol_news = self.get_symbol_news(symbol=symbol, limit=3)
            except Exception:
                continue
            for news in symbol_news:
                title = str(news.get("新闻标题") or "")
                content = str(news.get("新闻内容") or "")
                link = str(news.get("新闻链接") or "")
                if theme_token not in title and theme_token not in content and name not in title and name not in content:
                    continue
                if link and link in seen_links:
                    continue
                seen_links.add(link)
                news_items.append(
                    {
                        "source_type": "constituent_news",
                        "matched_on": name if name and name in title + content else theme_token,
                        "symbol": symbol,
                        "name": name,
                        **news,
                    }
                )
                if len(news_items) >= limit:
                    return news_items[:limit]

        return news_items[:limit]

    def get_policy_signals(
        self,
        *,
        theme: str,
        constituents: list[dict[str, Any]] | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        constituents = constituents or []
        theme_terms = self._theme_terms(theme)

        try:
            main_df = call_with_retry(ak.stock_news_main_cx, retries=2, delay_seconds=0.5)
        except Exception:
            main_df = None

        if main_df is None or main_df.empty:
            return []

        raw_signals: list[dict[str, Any]] = []
        seen_links: set[str] = set()
        for row in main_df.to_dict("records"):
            normalized = self._normalize_record(row)
            summary = str(normalized.get("summary") or "").strip()
            tag = str(normalized.get("tag") or "").strip()
            link = str(normalized.get("url") or "").strip()
            if not summary or (link and link in seen_links):
                continue

            match_info = self._match_policy_signal(
                theme=theme,
                theme_terms=theme_terms,
                constituents=constituents,
                summary=summary,
                tag=tag,
            )
            if match_info is None:
                continue

            policy_type = self._classify_policy_signal(summary, tag)
            priority = self._policy_priority(
                theme=theme,
                summary=summary,
                tag=tag,
                matched_terms=match_info["matched_terms"],
                policy_type=policy_type,
            )
            if priority < 3:
                continue

            seen_links.add(link)
            raw_signals.append(
                {
                    "source_type": "policy_signal",
                    "policy_type": policy_type,
                    "policy_priority": priority,
                    "matched_keyword": match_info["matched_keyword"],
                    "matched_terms": match_info["matched_terms"],
                    "match_scope": match_info["match_scope"],
                    **normalized,
                }
            )

        raw_signals.sort(
            key=lambda item: (
                int(item.get("policy_priority") or 0),
                self._policy_time_score(str(item.get("tag") or ""), str(item.get("summary") or "")),
                len(str(item.get("summary") or "")),
            ),
            reverse=True,
        )
        return raw_signals[:limit]

    def get_regulatory_signals(self, *, theme: str, constituents: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
        keyword_candidates = self._disclosure_keywords(theme)
        raw_signals: list[dict[str, Any]] = []
        seen_links: set[str] = set()
        end_date = date.today()
        start_date = end_date - timedelta(days=30)

        for keyword in keyword_candidates:
            try:
                df = call_with_retry(
                    lambda keyword=keyword: ak.stock_zh_a_disclosure_report_cninfo(
                        symbol="",
                        market="沪深京",
                        keyword=keyword,
                        category="日常经营",
                        start_date=start_date.strftime("%Y%m%d"),
                        end_date=end_date.strftime("%Y%m%d"),
                    ),
                    retries=2,
                    delay_seconds=0.5,
                )
            except Exception:
                continue
            if df.empty:
                continue
            for row in df.to_dict("records"):
                normalized = self._normalize_record(row)
                title = self._strip_markup(str(normalized.get("公告标题") or ""))
                name = self._strip_markup(str(normalized.get("简称") or ""))
                symbol = str(normalized.get("代码") or "").strip()
                link = str(normalized.get("公告链接") or "")
                if not title or (link and link in seen_links):
                    continue
                if not self._matches_theme_or_constituent(
                    theme=theme,
                    title=title,
                    name=name,
                    symbol=symbol,
                    constituents=constituents,
                ):
                    continue
                signal_type = self._classify_regulatory_signal(title)
                priority = self._regulatory_priority(signal_type, title)
                if priority < 3:
                    continue
                seen_links.add(link)
                raw_signals.append(
                    {
                        "source_type": "regulatory_signal",
                        "matched_keyword": keyword,
                        "signal_type": signal_type,
                        "signal_priority": priority,
                        "代码": symbol,
                        "简称": name,
                        "公告标题": title,
                        "公告时间": normalized.get("公告时间"),
                        "公告链接": link,
                    }
                )

        raw_signals.sort(
            key=lambda item: (
                int(item.get("signal_priority") or 0),
                str(item.get("公告时间") or ""),
            ),
            reverse=True,
        )
        return raw_signals[:limit]

    def build_catalyst_digest(
        self,
        *,
        theme: str,
        constituents: list[dict[str, Any]],
        catalyst_samples: list[dict[str, Any]],
        theme_news: list[dict[str, Any]],
        policy_signals: list[dict[str, Any]] | None = None,
        regulatory_signals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        policy_signals = policy_signals or []
        regulatory_signals = regulatory_signals or []
        digest: dict[str, Any] = {
            "theme": theme,
            "headline_count": len(theme_news),
            "report_count": 0,
            "policy_signal_count": len(policy_signals),
            "regulatory_signal_count": len(regulatory_signals),
            "top_news_titles": [],
            "top_report_titles": [],
            "top_policy_titles": [],
            "top_regulatory_titles": [],
            "policy_signal_types": [],
            "representative_symbols": [],
            "catalyst_keywords": [],
        }

        top_news_titles: list[str] = []
        report_titles: list[str] = []
        policy_titles: list[str] = []
        regulatory_titles: list[str] = []
        representative_symbols: list[str] = []
        keyword_counter: Counter[str] = Counter()
        policy_type_counter: Counter[str] = Counter()

        for item in theme_news[:4]:
            title = str(item.get("summary") or item.get("新闻标题") or "").strip()
            if title:
                top_news_titles.append(title)
                for keyword in self._extract_keywords(title):
                    keyword_counter[keyword] += 1
            name = str(item.get("name") or "").strip()
            symbol = str(item.get("symbol") or "").strip()
            if name or symbol:
                representative_symbols.append(f"{name}({symbol})" if name and symbol else name or symbol)

        for item in policy_signals[:3]:
            title = str(item.get("summary") or "").strip()
            if title:
                policy_titles.append(title)
                for keyword in self._extract_keywords(title):
                    keyword_counter[keyword] += 1
            policy_type = str(item.get("policy_type") or "").strip()
            if policy_type:
                policy_type_counter[policy_type] += 1

        for item in regulatory_signals[:3]:
            title = str(item.get("公告标题") or "").strip()
            if title:
                regulatory_titles.append(title)
                for keyword in self._extract_keywords(title):
                    keyword_counter[keyword] += 1

        report_count = 0
        for sample in catalyst_samples:
            name = str(sample.get("name") or "").strip()
            symbol = str(sample.get("symbol") or "").strip()
            if name or symbol:
                representative_symbols.append(f"{name}({symbol})" if name and symbol else name or symbol)
            reports = sample.get("recent_reports") or []
            for report in reports[:2]:
                title = str(report.get("报告名称") or "").strip()
                if title:
                    report_titles.append(title)
                    for keyword in self._extract_keywords(title):
                        keyword_counter[keyword] += 1
                report_count += 1

        if not representative_symbols:
            for item in constituents[:3]:
                name = str(item.get("name") or "").strip()
                symbol = str(item.get("code") or "").strip()
                if name or symbol:
                    representative_symbols.append(f"{name}({symbol})" if name and symbol else name or symbol)

        digest["report_count"] = report_count
        digest["top_news_titles"] = top_news_titles[:4]
        digest["top_report_titles"] = report_titles[:4]
        digest["top_policy_titles"] = policy_titles[:3]
        digest["top_regulatory_titles"] = regulatory_titles[:3]
        digest["policy_signal_types"] = [name for name, _ in policy_type_counter.most_common(3)]
        digest["representative_symbols"] = list(dict.fromkeys(representative_symbols))[:5]
        digest["catalyst_keywords"] = [item for item, _ in keyword_counter.most_common(8)]
        return digest

    def _normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {str(key): normalize_value(value) for key, value in record.items()}

    def _extract_keywords(self, text: str) -> list[str]:
        normalized = str(text).strip()
        if not normalized:
            return []
        candidates = [
            "创新药",
            "减肥药",
            "AI",
            "算力",
            "机器人",
            "订单",
            "业绩",
            "盈利",
            "回暖",
            "恢复",
            "增长",
            "催化",
            "集采",
            "注册证书",
            "临床试验",
            "政府补助",
            "复牌",
            "终止",
            "控制权",
            "出海",
            "CDMO",
            "CRO",
            "合作",
            "授权",
        ]
        hits = [keyword for keyword in candidates if keyword in normalized]
        if hits:
            return hits
        return [normalized[:12]] if normalized else []

    def _theme_terms(self, theme: str) -> list[str]:
        theme = str(theme).strip()
        mapping = {
            "创新药": ["创新药", "创新药企", "新药", "药品", "医药", "临床", "药监", "医保", "集采", "出海", "授权", "BD"],
            "医疗器械": ["医疗器械", "器械", "耗材", "注册证", "医疗设备", "集采"],
            "医疗服务": ["医疗服务", "医院", "诊疗", "医保", "医疗"],
        }
        terms = mapping.get(theme, [theme])
        return [term for term in terms if term]

    def _policy_keywords(self, theme: str) -> list[str]:
        mapping = {
            "创新药": ["创新药", "医药", "药品", "医保", "药监", "集采", "出海", "授权", "合作", "临床"],
            "医疗器械": ["医疗器械", "器械", "医疗", "注册证", "集采"],
            "医疗服务": ["医疗服务", "医疗", "医保", "医院", "诊疗"],
        }
        return mapping.get(theme, [theme, "政策", "产业"])

    def _disclosure_keywords(self, theme: str) -> list[str]:
        mapping = {
            "创新药": ["药品", "医药", "临床试验", "注册证书", "创新"],
            "医疗器械": ["医疗", "器械", "注册证", "备案"],
            "医疗服务": ["医疗", "服务", "医院", "诊疗"],
        }
        return mapping.get(theme, [theme, "经营"])

    def _match_policy_signal(
        self,
        *,
        theme: str,
        theme_terms: list[str],
        constituents: list[dict[str, Any]],
        summary: str,
        tag: str,
    ) -> dict[str, Any] | None:
        text = f"{tag} {summary}"
        matched_terms: list[str] = []
        matched_scope = "broad"

        for term in self._policy_keywords(theme):
            if term and term in text:
                matched_terms.append(term)

        for term in theme_terms:
            if term and term in text:
                matched_terms.append(term)
                matched_scope = "theme"

        for item in constituents[:10]:
            name = str(item.get("name") or "").strip()
            code = str(item.get("code") or "").strip()
            if name and name in text:
                matched_terms.append(name)
                matched_scope = "constituent"
            if code and code in text:
                matched_terms.append(code)
                matched_scope = "constituent"

        matched_terms = list(dict.fromkeys(matched_terms))
        if not matched_terms:
            return None

        if any(noise in text for noise in self._policy_noise_keywords()):
            return None

        if not self._is_theme_relevant_policy_text(theme=theme, text=text, matched_terms=matched_terms):
            return None

        return {
            "matched_keyword": matched_terms[0],
            "matched_terms": matched_terms[:5],
            "match_scope": matched_scope,
        }

    def _is_theme_relevant_policy_text(self, *, theme: str, text: str, matched_terms: list[str]) -> bool:
        text = str(text)
        if theme and theme in text:
            return True
        if any(term in text for term in ("创新药", "新药", "药品", "医药", "临床", "药监", "医保", "集采", "出海", "授权", "BD", "CRO", "CDMO")):
            return True
        if any(term in matched_terms for term in ("创新药", "新药", "药品", "医药", "医保", "药监", "集采", "出海", "授权", "合作", "临床")):
            return True
        return False

    def _policy_noise_keywords(self) -> tuple[str, ...]:
        return (
            "住房",
            "楼市",
            "房地产",
            "住宅",
            "公积金",
            "货币政策",
            "利率路径",
            "主权债",
            "汇率",
            "铁矿石",
            "白银",
            "航空业",
            "文旅",
            "OpenAI",
            "芯片",
            "食品和药品",
        )

    def _classify_policy_signal(self, summary: str, tag: str) -> str:
        text = f"{tag} {summary}"
        if any(keyword in text for keyword in ("医保", "药监", "集采", "审批", "审评", "注册", "指南")):
            return "regulation_policy"
        if any(keyword in text for keyword in ("出海", "订单", "合作", "授权", "BD", "海外")):
            return "industry_development"
        if any(keyword in text for keyword in ("投融资", "融资", "并购", "产业链")):
            return "industry_capital"
        return "theme_policy"

    def _policy_priority(
        self,
        *,
        theme: str,
        summary: str,
        tag: str,
        matched_terms: list[str],
        policy_type: str,
    ) -> int:
        text = f"{tag} {summary}"
        score = 0

        if theme and theme in text:
            score += 3
        if any(term in text for term in matched_terms):
            score += 2
        if any(term in text for term in ("创新药", "新药", "医药", "药品", "临床", "药监", "医保", "集采")):
            score += 2
        if any(term in text for term in ("出海", "海外", "授权", "合作", "BD", "订单")):
            score += 2
        if any(term in text for term in ("政策", "监管", "指南", "审批", "审评", "准入", "支付")):
            score += 1

        type_bonus = {
            "regulation_policy": 2,
            "industry_development": 2,
            "industry_capital": 1,
            "theme_policy": 0,
        }
        score += type_bonus.get(policy_type, 0)

        if "周刊提前读" in tag:
            score += 1
        if "市场动态" in tag:
            score += 0

        return score

    def _policy_time_score(self, tag: str, summary: str) -> int:
        score = 0
        if "周刊提前读" in tag:
            score += 2
        if "研报精华" in tag:
            score += 1
        if "市场动态" in tag:
            score += 1
        if any(term in summary for term in ("近期", "最新", "下一程", "应对", "趋势")):
            score += 1
        return score

    def _classify_regulatory_signal(self, title: str) -> str:
        rules = [
            ("注册证书", "registration"),
            ("注册批件", "registration"),
            ("临床试验", "clinical"),
            ("IND", "clinical"),
            ("政府补助", "subsidy"),
            ("合作", "partnership"),
            ("授权", "partnership"),
            ("许可", "partnership"),
            ("股权", "equity"),
            ("控制权", "control"),
            ("自愿披露", "voluntary_disclosure"),
        ]
        for keyword, label in rules:
            if keyword in title:
                return label
        return "business_disclosure"

    def _regulatory_priority(self, signal_type: str, title: str) -> int:
        if any(noise in title for noise in ("H股公告-翌日披露报表", "董事会", "股东会", "章程", "工商变更")):
            return 0
        priority_map = {
            "registration": 5,
            "clinical": 5,
            "partnership": 4,
            "subsidy": 3,
            "control": 3,
            "equity": 2,
            "voluntary_disclosure": 2,
            "business_disclosure": 1,
        }
        return priority_map.get(signal_type, 1)

    def _matches_theme_or_constituent(
        self,
        *,
        theme: str,
        title: str,
        name: str,
        symbol: str,
        constituents: list[dict[str, Any]],
    ) -> bool:
        if theme and theme in title:
            return True
        for item in constituents[:10]:
            constituent_name = str(item.get("name") or "").strip()
            constituent_symbol = str(item.get("code") or "").strip()
            if constituent_name and constituent_name in title:
                return True
            if constituent_symbol and constituent_symbol == symbol:
                return True
            if constituent_name and constituent_name == name:
                return True
        return False

    def _strip_markup(self, value: str) -> str:
        return re.sub(r"</?em>", "", str(value or ""))
