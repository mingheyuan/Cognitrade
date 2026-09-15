from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    coerce_numeric,
    normalize_value,
)

_REPORT_TITLE = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<half>半)?年度报告")
_HTML_TAG = re.compile(r"<[^>]+>")

# These are industry-level relationships, not claims about named suppliers or
# customers.  Every emitted row carries that scope so a report cannot present
# the deterministic taxonomy as company-specific due diligence.
_CHAIN_TAXONOMY: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "芯片设计",
        ("EDA 工具与半导体 IP", "设计服务与研发基础设施", "芯片验证"),
        ("晶圆代工", "封装测试", "消费电子、汽车电子与 AI 算力"),
    ),
    (
        "集成电路制造",
        ("半导体设备", "硅片、光刻胶与电子特气", "工艺零部件"),
        ("封装测试", "芯片设计企业", "电子与算力终端"),
    ),
    (
        "半导体",
        ("半导体材料", "核心零部件", "精密制造与检测"),
        ("晶圆制造", "封装测试", "消费电子、汽车电子与 AI 算力"),
    ),
    (
        "电子",
        ("电子材料", "芯片与元器件", "精密结构件"),
        ("消费电子", "汽车电子", "通信与工业终端"),
    ),
    (
        "专用设备",
        ("金属材料", "核心零部件", "控制与检测系统"),
        ("制造业客户", "工程服务", "设备维护与升级"),
    ),
    (
        "医药",
        ("原料药", "医药研发", "生产设备与耗材"),
        ("医院", "药店与流通商", "患者与公共卫生体系"),
    ),
    (
        "汽车",
        ("原材料", "汽车零部件", "动力与电子系统"),
        ("整车制造", "经销与售后", "终端消费者"),
    ),
    (
        "电气",
        ("有色金属", "电子元件", "机械与电气部件"),
        ("电网与电站", "工业客户", "新能源终端"),
    ),
    (
        "食品",
        ("农产品", "包装材料", "生产设备"),
        ("经销商", "商超与餐饮", "终端消费者"),
    ),
    (
        "酒",
        ("粮食与水源", "包装材料", "酿造设备"),
        ("经销商", "零售与餐饮", "终端消费者"),
    ),
    (
        "银行",
        ("存款与同业资金", "资本金", "金融基础设施"),
        ("公司客户", "零售客户", "政府与金融机构"),
    ),
)


class AkSharePitContextAdapter:
    """Reconstruct dimensions 5 and 7 from date-bounded structured sources.

    Native UZI's live fetchers use today's company profile and the newest
    business-composition row.  This adapter deliberately uses CNINFO's dated
    industry-change archive, CNINFO's dated industry aggregates, and only a
    business-composition period whose report was visible by ``as_of_date``.
    """

    vendor_name = "akshare_cninfo_pit"

    def get_industry_snapshot(
        self,
        *,
        symbol: str,
        as_of_date: date,
    ) -> dict[str, Any]:
        history, classification_trace = self._classification_history(
            symbol=symbol,
            as_of_date=as_of_date,
        )
        selected = self._latest_by_standard(history, as_of_date=as_of_date)
        primary = self._primary_classification(selected)
        if not primary:
            return {
                "industry": None,
                "classification": {},
                "availability": "data_gap",
                "data_gap": ["historical_industry_classification_unavailable"],
                "degrade_reason": "historical_industry_classification_unavailable",
                "_source": self._source("cninfo.industry_change"),
                "_source_trace": classification_trace,
            }

        csrc = next(
            (
                row
                for row in selected
                if "证监会" in str(row.get("classification_standard") or "")
            ),
            primary,
        )
        valuation_name = str(
            csrc.get("industry_large")
            or csrc.get("industry_middle")
            or csrc.get("industry_name")
            or ""
        ).strip()
        current, current_trace = self._industry_metrics(
            industry_name=valuation_name,
            as_of_date=as_of_date,
            source_id="cninfo.industry_pe.current",
        )
        comparison_date = as_of_date - timedelta(days=365)
        previous, previous_trace = self._industry_metrics(
            industry_name=valuation_name,
            as_of_date=comparison_date,
            source_id="cninfo.industry_pe.comparison",
        )
        growth = self._industry_growth(current=current, previous=previous)
        cycle = self._industry_cycle(growth)
        industry = str(
            primary.get("industry_large")
            or primary.get("industry_middle")
            or primary.get("industry_name")
            or ""
        ).strip()
        classification = {
            "name": industry,
            "standard": primary.get("classification_standard"),
            "code": primary.get("industry_code"),
            "effective_date": primary.get("effective_date"),
            "as_of_date": as_of_date.isoformat(),
            "primary": primary,
            "standards": selected,
            "history_count": len(history),
        }
        evidence = [
            {
                "title": "巨潮资讯上市公司行业归属变动",
                "url": "https://webapi.cninfo.com.cn/#/apiDoc",
                "date": primary.get("effective_date"),
                "provider": "cninfo.industry_change",
            }
        ]
        if current:
            evidence.append(
                {
                    "title": "巨潮资讯行业市盈率",
                    "url": "http://webapi.cninfo.com.cn/#/thematicStatistics",
                    "date": current.get("data_date"),
                    "provider": "cninfo.industry_pe",
                }
            )

        return {
            "industry": industry,
            "classification": classification,
            "classification_version": (
                f"{primary.get('classification_standard')}@"
                f"{primary.get('effective_date')}"
            ),
            "valuation": current,
            "industry_pe": current.get("pe_weighted") if current else None,
            "growth": growth,
            "cycle": cycle,
            "historical_evidence": evidence,
            "availability": "available",
            "data_gap": [
                name
                for name, value in (
                    ("industry_valuation_missing", current),
                    ("industry_growth_missing", growth),
                    ("industry_cycle_missing", cycle),
                )
                if not value
            ],
            "_source": self._source("cninfo.industry_change"),
            "_source_trace": [
                *classification_trace,
                *current_trace,
                *previous_trace,
            ],
        }

    def get_fixed_profile_industry_snapshot(
        self, *, symbol: str, as_of_date: date, fixed_profile: dict[str, Any],
    ) -> dict[str, Any]:
        """Query dated industry metrics for the caller's already-proved identity.

        This does not reclassify the company or promote issuer disclosure to a
        formal classification. Missing matching historical metrics stay gaps.
        """
        raw, proof = fixed_profile["data"], fixed_profile["_pit_context"]
        industry = str(raw.get("industry") or "").strip()
        current, previous, trace = {}, {}, []
        if industry:
            current, first_trace = self._industry_metrics(industry_name=industry,
                as_of_date=as_of_date, source_id="cninfo.industry_pe.current")
            previous, second_trace = self._industry_metrics(industry_name=industry,
                as_of_date=as_of_date - timedelta(days=365), source_id="cninfo.industry_pe.comparison")
            trace.extend([*first_trace, *second_trace])
        growth = self._industry_growth(current=current, previous=previous)
        cycle = self._industry_cycle(growth)
        basis = proof.get("industry_basis") or "bound_profile"
        gaps = [key for key, value in (("historical_industry_identity_missing", industry),
                ("industry_valuation_missing", current), ("industry_growth_missing", growth),
                ("industry_cycle_missing", cycle)) if not value]
        return {"industry": industry or None,
            "classification": {"name": industry or None, "basis": basis,
                "standard": proof.get("classification_system") if basis == "classification" else None,
                "as_of_date": as_of_date.isoformat()},
            "profile_input": deepcopy(proof), "valuation": current,
            "industry_pe": current.get("pe_weighted") if current else None,
            "growth": growth, "cycle": cycle, "historical_evidence": [],
            "availability": "available" if not gaps else "degraded" if industry else "data_gap",
            "data_gap": gaps, "degrade_reason": gaps[0] if gaps else None,
            "_source": self._source("data_center.fixed_profile"),
            "_source_trace": [{"source_id": "data_center.fixed_profile", "status": "available" if industry else "no_data",
                "snapshot_id": proof["snapshot_id"], "snapshot_hash": proof["snapshot_hash"],
                "query_scope": "bound_identity_not_new_observation"}, *trace]}

    def get_chain_snapshot(
        self,
        *,
        symbol: str,
        as_of_date: date,
        industry_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        code = self._code(symbol)
        prefixed = self._eastmoney_symbol(code)
        trace: list[dict[str, Any]] = []
        try:
            frame = call_with_retry(
                lambda: ak.stock_zygc_em(symbol=prefixed),
                retries=1,
                delay_seconds=0.2,
            )
        except Exception as exc:  # noqa: BLE001 - preserve provider failure in trace
            return {
                "availability": "data_gap",
                "data_gap": ["historical_business_composition_provider_failure"],
                "degrade_reason": "historical_business_composition_provider_failure",
                "_source": self._source("em_data.stock_zygc"),
                "_source_trace": [
                    {
                        "source_id": "em_data.stock_zygc",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ],
            }
        rows = self._normalized_rows(frame)
        trace.append(
            {
                "source_id": "em_data.stock_zygc",
                "status": "available" if rows else "no_data",
                "count": len(rows),
            }
        )
        publications, publication_trace = self._report_publications(
            symbol=code,
            as_of_date=as_of_date,
        )
        trace.extend(publication_trace)
        selected_period, published_at, publication_basis = self._visible_period(
            rows,
            publications=publications,
            as_of_date=as_of_date,
        )
        if selected_period is None:
            return {
                "availability": "data_gap",
                "data_gap": ["historical_business_composition_unavailable"],
                "degrade_reason": "historical_business_composition_unavailable",
                "_source": self._source("em_data.stock_zygc"),
                "_source_trace": trace,
            }

        selected_rows = [
            {
                **row,
                "report_date": selected_period.isoformat(),
                "published_at": published_at.isoformat(),
                "publication_basis": publication_basis,
            }
            for row in rows
            if self._calendar_date(row.get("报告日期")) == selected_period
        ]
        breakdown = self._business_breakdown(selected_rows)
        industry = str((industry_context or {}).get("industry") or "").strip()
        upstream, downstream = self._chain_roles(
            industry=industry,
            as_of_date=as_of_date,
        )
        concentration = self._segment_concentration(
            breakdown,
            report_date=selected_period,
        )
        evidence = [
            row
            for row in publications.values()
            if self._calendar_date(row.get("report_date")) == selected_period
        ]
        return {
            "industry": industry or None,
            "main_business_raw": selected_rows,
            "main_business_breakdown": breakdown,
            "upstream": upstream,
            "downstream": downstream,
            "concentration": concentration,
            "report_date": selected_period.isoformat(),
            "published_at": published_at.isoformat(),
            "publication_basis": publication_basis,
            "publication_evidence": evidence,
            "availability": "available",
            "data_gap": [
                name
                for name, value in (
                    ("chain_upstream_taxonomy_missing", upstream),
                    ("chain_downstream_taxonomy_missing", downstream),
                    ("business_segment_concentration_missing", concentration),
                )
                if not value
            ],
            "_source": self._source("em_data.stock_zygc"),
            "_source_trace": [
                *list((industry_context or {}).get("_source_trace") or []),
                *trace,
            ],
        }

    def _classification_history(
        self,
        *,
        symbol: str,
        as_of_date: date,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        code = self._code(symbol)
        try:
            frame = call_with_retry(
                lambda: ak.stock_industry_change_cninfo(
                    symbol=code,
                    start_date="19900101",
                    end_date=as_of_date.strftime("%Y%m%d"),
                ),
                retries=1,
                delay_seconds=0.2,
            )
        except Exception as exc:  # noqa: BLE001 - preserve provider failure in trace
            return [], [
                {
                    "source_id": "cninfo.industry_change",
                    "status": "provider_unavailable",
                    "error_type": exc.__class__.__name__,
                }
            ]
        rows: list[dict[str, Any]] = []
        for raw in self._normalized_rows(frame):
            effective = self._calendar_date(raw.get("变更日期"))
            if effective is None or effective > as_of_date:
                continue
            rows.append(
                {
                    "security_name": raw.get("新证券简称") or raw.get("机构名称"),
                    "symbol": raw.get("证券代码") or code,
                    "classification_standard": raw.get("分类标准"),
                    "classification_standard_code": raw.get("分类标准编码"),
                    "industry_code": raw.get("行业编码"),
                    "industry_section": raw.get("行业门类"),
                    "industry_large": raw.get("行业大类"),
                    "industry_middle": raw.get("行业中类"),
                    "industry_small": raw.get("行业次类"),
                    "industry_name": raw.get("行业大类")
                    or raw.get("行业中类")
                    or raw.get("行业门类"),
                    "effective_date": effective.isoformat(),
                }
            )
        rows.sort(key=lambda row: str(row.get("effective_date") or ""))
        return rows, [
            {
                "source_id": "cninfo.industry_change",
                "status": "available" if rows else "no_data",
                "count": len(rows),
            }
        ]

    def _industry_metrics(
        self,
        *,
        industry_name: str,
        as_of_date: date,
        source_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not industry_name:
            return {}, [{"source_id": source_id, "status": "no_data", "count": 0}]
        last_error: Exception | None = None
        for offset in range(10):
            snapshot_date = as_of_date - timedelta(days=offset)
            try:
                frame = call_with_retry(
                    lambda snapshot_date=snapshot_date: (
                        ak.stock_industry_pe_ratio_cninfo(
                            symbol="证监会行业分类",
                            date=snapshot_date.strftime("%Y%m%d"),
                        )
                    ),
                    retries=1,
                    delay_seconds=0.2,
                )
            except Exception as exc:  # noqa: BLE001 - try prior market date
                last_error = exc
                continue
            if frame is None or getattr(frame, "empty", True):
                continue
            row = self._match_industry_metrics(frame, industry_name)
            if row is None:
                continue
            normalized = {
                str(key): normalize_value(value) for key, value in row.items()
            }
            metrics = {
                "industry_name": normalized.get("行业名称") or industry_name,
                "industry_code": normalized.get("行业编码"),
                "industry_level": normalized.get("行业层级"),
                "data_date": (
                    self._calendar_date(normalized.get("变动日期")) or snapshot_date
                ).isoformat(),
                "company_count": self._number(normalized.get("公司数量")),
                "included_company_count": self._number(
                    normalized.get("纳入计算公司数量")
                ),
                "market_cap_yi": self._number(normalized.get("总市值-静态")),
                "net_profit_yi": self._number(normalized.get("净利润-静态")),
                "pe_weighted": self._number(normalized.get("静态市盈率-加权平均")),
                "pe_median": self._number(normalized.get("静态市盈率-中位数")),
                "pe_average": self._number(normalized.get("静态市盈率-算术平均")),
            }
            return metrics, [
                {
                    "source_id": source_id,
                    "status": "available",
                    "count": 1,
                    "data_date": metrics["data_date"],
                }
            ]
        trace = {"source_id": source_id, "status": "no_data", "count": 0}
        if last_error is not None:
            trace.update(
                status="provider_unavailable",
                error_type=last_error.__class__.__name__,
            )
        return {}, [trace]

    @staticmethod
    def _latest_by_standard(
        rows: list[dict[str, Any]],
        *,
        as_of_date: date,
    ) -> list[dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            effective = AkSharePitContextAdapter._calendar_date(
                row.get("effective_date")
            )
            standard = str(row.get("classification_standard") or "").strip()
            if not standard or effective is None or effective > as_of_date:
                continue
            current = selected.get(standard)
            if (
                current is None
                or str(current.get("effective_date")) < effective.isoformat()
            ):
                selected[standard] = row
        return sorted(
            selected.values(),
            key=lambda row: (
                AkSharePitContextAdapter._standard_priority(
                    str(row.get("classification_standard") or "")
                ),
                str(row.get("effective_date") or ""),
            ),
            reverse=True,
        )

    @staticmethod
    def _primary_classification(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return dict(rows[0]) if rows else {}

    @staticmethod
    def _standard_priority(standard: str) -> int:
        if "申银万国" in standard and "旧" not in standard:
            return 50
        if "巨潮" in standard:
            return 40
        if "证监会" in standard:
            return 30
        if "中证" in standard and "旧" not in standard:
            return 20
        return 10

    @staticmethod
    def _match_industry_metrics(
        frame: Any, industry_name: str
    ) -> dict[str, Any] | None:
        if "行业名称" not in getattr(frame, "columns", []):
            return None
        names = frame["行业名称"].astype(str)
        exact = frame[names == industry_name]
        if not exact.empty:
            return exact.iloc[0].to_dict()
        compact = re.sub(r"[、，,\s]", "", industry_name)
        for index, name in names.items():
            candidate = re.sub(r"[、，,\s]", "", str(name))
            if compact and (compact in candidate or candidate in compact):
                return frame.loc[index].to_dict()
        return None

    def _report_publications(
        self,
        *,
        symbol: str,
        as_of_date: date,
    ) -> tuple[dict[date, dict[str, Any]], list[dict[str, Any]]]:
        start = as_of_date - timedelta(days=800)
        try:
            frame = call_with_retry(
                lambda: ak.stock_zh_a_disclosure_report_cninfo(
                    symbol=symbol,
                    market="沪深京",
                    keyword="年度报告",
                    category="",
                    start_date=start.strftime("%Y%m%d"),
                    end_date=as_of_date.strftime("%Y%m%d"),
                ),
                retries=1,
                delay_seconds=0.2,
            )
        except Exception as exc:  # noqa: BLE001 - conservative fallback stays PIT-safe
            return {}, [
                {
                    "source_id": "cninfo.disclosure_report",
                    "status": "provider_unavailable",
                    "error_type": exc.__class__.__name__,
                }
            ]
        publications: dict[date, dict[str, Any]] = {}
        for row in self._normalized_rows(frame):
            title = _HTML_TAG.sub("", str(row.get("公告标题") or ""))
            if "摘要" in title or "取消" in title:
                continue
            match = _REPORT_TITLE.search(title)
            published = self._calendar_date(row.get("公告时间"))
            if match is None or published is None or published > as_of_date:
                continue
            year = int(match.group("year"))
            report_date = (
                date(year, 6, 30) if match.group("half") else date(year, 12, 31)
            )
            candidate = {
                "title": title,
                "url": row.get("公告链接"),
                "provider": "cninfo.disclosure_report",
                "published_at": published.isoformat(),
                "date": published.isoformat(),
                "report_date": report_date.isoformat(),
            }
            previous = publications.get(report_date)
            if (
                previous is None
                or str(previous.get("published_at")) > published.isoformat()
            ):
                publications[report_date] = candidate
        return publications, [
            {
                "source_id": "cninfo.disclosure_report",
                "status": "available" if publications else "no_data",
                "count": len(publications),
            }
        ]

    @classmethod
    def _visible_period(
        cls,
        rows: list[dict[str, Any]],
        *,
        publications: dict[date, dict[str, Any]],
        as_of_date: date,
    ) -> tuple[date | None, date | None, str | None]:
        periods = sorted(
            {
                resolved
                for row in rows
                if (resolved := cls._calendar_date(row.get("报告日期"))) is not None
                and resolved <= as_of_date
            },
            reverse=True,
        )
        for period in periods:
            publication = publications.get(period)
            explicit = cls._calendar_date((publication or {}).get("published_at"))
            if explicit is not None and explicit <= as_of_date:
                return period, explicit, "cninfo_actual_publication"
            conservative = cls._conservative_publication_date(period)
            if conservative <= as_of_date:
                return period, conservative, "statutory_conservative_deadline"
        return None, None, None

    @staticmethod
    def _conservative_publication_date(report_date: date) -> date:
        if (report_date.month, report_date.day) == (12, 31):
            return date(report_date.year + 1, 4, 30)
        if (report_date.month, report_date.day) == (6, 30):
            return date(report_date.year, 8, 31)
        if (report_date.month, report_date.day) == (3, 31):
            return date(report_date.year, 4, 30)
        if (report_date.month, report_date.day) == (9, 30):
            return date(report_date.year, 10, 31)
        return report_date + timedelta(days=120)

    @staticmethod
    def _business_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for classification in ("按产品分类", "按行业分类", "按地区分类"):
            selected = [row for row in rows if row.get("分类类型") == classification]
            if not selected:
                continue
            return [
                {
                    "name": row.get("主营构成"),
                    "value": AkSharePitContextAdapter._number(row.get("收入比例")),
                    "revenue": AkSharePitContextAdapter._number(row.get("主营收入")),
                    "profit": AkSharePitContextAdapter._number(row.get("主营利润")),
                    "gross_margin": AkSharePitContextAdapter._number(row.get("毛利率")),
                    "classification_type": classification,
                    "report_date": row.get("report_date"),
                    "published_at": row.get("published_at"),
                }
                for row in selected
                if row.get("主营构成")
            ]
        return []

    @staticmethod
    def _chain_roles(
        *,
        industry: str,
        as_of_date: date,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        match = next((row for row in _CHAIN_TAXONOMY if row[0] in industry), None)
        if match is None:
            return [], []

        def rows(values: tuple[str, ...]) -> list[dict[str, Any]]:
            return [
                {
                    "name": value,
                    "scope": "industry_level",
                    "basis": "dated_industry_classification_taxonomy",
                    "classification": industry,
                    "as_of_date": as_of_date.isoformat(),
                    "company_specific": False,
                }
                for value in values
            ]

        return rows(match[1]), rows(match[2])

    @staticmethod
    def _segment_concentration(
        breakdown: list[dict[str, Any]],
        *,
        report_date: date,
    ) -> dict[str, Any]:
        shares = [
            float(value)
            for row in breakdown
            if (value := coerce_numeric(row.get("value"))) is not None
            and 0 <= float(value) <= 1
        ]
        if not shares:
            return {}
        total = sum(shares)
        normalized = [share / total for share in shares] if total > 0 else []
        return {
            "metric": "business_segment_revenue_concentration",
            "segment_count": len(normalized),
            "top_segment_share": round(max(normalized), 6),
            "hhi": round(sum(share * share for share in normalized), 6),
            "report_date": report_date.isoformat(),
            "client_concentration_available": False,
            "supplier_concentration_available": False,
        }

    @staticmethod
    def _industry_growth(
        *,
        current: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        current_profit = AkSharePitContextAdapter._number(current.get("net_profit_yi"))
        previous_profit = AkSharePitContextAdapter._number(
            previous.get("net_profit_yi")
        )
        if current_profit is None or previous_profit is None or previous_profit == 0:
            return {}
        return {
            "metric": "industry_aggregate_net_profit_yoy",
            "value_pct": round((current_profit / previous_profit - 1) * 100, 2),
            "current_net_profit_yi": current_profit,
            "previous_net_profit_yi": previous_profit,
            "current_date": current.get("data_date"),
            "comparison_date": previous.get("data_date"),
        }

    @staticmethod
    def _industry_cycle(growth: dict[str, Any]) -> dict[str, Any]:
        value = AkSharePitContextAdapter._number(growth.get("value_pct"))
        if value is None:
            return {}
        stage = (
            "expansion"
            if value >= 10
            else "recovery"
            if value > 0
            else "contraction"
            if value <= -10
            else "stable"
        )
        return {
            "stage": stage,
            "basis": "industry_aggregate_net_profit_yoy",
            "growth_pct": value,
            "as_of_date": growth.get("current_date"),
        }

    @staticmethod
    def _normalized_rows(frame: Any) -> list[dict[str, Any]]:
        if frame is None or getattr(frame, "empty", True):
            return []
        return [
            {str(key): normalize_value(value) for key, value in row.items()}
            for row in frame.to_dict("records")
        ]

    @staticmethod
    def _number(value: Any) -> float | None:
        parsed = coerce_numeric(value)
        return float(parsed) if parsed is not None else None

    @staticmethod
    def _calendar_date(value: Any) -> date | None:
        if isinstance(value, date):
            return value
        text = str(value or "").strip().replace("/", "-")
        for candidate in (text, text[:10]):
            try:
                return date.fromisoformat(candidate)
            except ValueError:
                continue
        compact = re.match(r"^(20\d{2})(\d{2})(\d{2})", text)
        if compact:
            try:
                return date(*(int(part) for part in compact.groups()))
            except ValueError:
                return None
        return None

    @staticmethod
    def _code(symbol: str) -> str:
        digits = "".join(character for character in str(symbol) if character.isdigit())
        return digits[-6:].zfill(6)

    @staticmethod
    def _eastmoney_symbol(code: str) -> str:
        if code.startswith(("4", "8")):
            return f"BJ{code}"
        if code.startswith(("5", "6", "9")):
            return f"SH{code}"
        return f"SZ{code}"

    @staticmethod
    def _source(source_id: str) -> dict[str, Any]:
        return {
            "vendor": "akshare_cninfo_pit",
            "provider_id": "akshare_cninfo_pit",
            "source_id": source_id,
        }


__all__ = ["AkSharePitContextAdapter"]
