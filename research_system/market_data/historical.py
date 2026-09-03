from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

HistoricalStrategy = Literal[
    "current_only",
    "dated_series",
    "published_archive",
    "derived_from_pit",
]


@dataclass(frozen=True)
class HistoricalCapability:
    """Point-in-time reconstruction policy for one data-centre dimension.

    ``recoverable_fields`` and ``current_only_fields`` deliberately keep the
    decision at field level.  The previous implementation reduced the whole
    dimension to one boolean and consequently skipped providers before they
    could return dated K-line, valuation, disclosure or holder data.
    """

    dimension: str
    snapshot_type: str
    strategy: HistoricalStrategy
    window_days: int = 0
    recoverable_fields: tuple[str, ...] = ()
    current_only_fields: tuple[str, ...] = ()

    @property
    def can_reconstruct(self) -> bool:
        return self.strategy != "current_only" or bool(self.recoverable_fields)


@dataclass(frozen=True)
class PointInTimeContext:
    """Immutable collection cutoff propagated to skills and providers."""

    as_of_date: date
    cutoff_at: datetime
    exchange_timezone: str

    def __post_init__(self) -> None:
        if self.cutoff_at.tzinfo is None or self.cutoff_at.utcoffset() is None:
            raise ValueError("cutoff_at must be timezone-aware")

    @property
    def historical(self) -> bool:
        return (
            self.as_of_date
            != datetime.now(timezone.utc).astimezone(self.cutoff_at.tzinfo).date()
        )


_CAPABILITIES: tuple[HistoricalCapability, ...] = (
    HistoricalCapability(
        "0_basic",
        "market",
        "derived_from_pit",
        550,
        ("name", "price", "industry", "market_cap", "pe_ttm", "pb"),
    ),
    HistoricalCapability(
        "1_financials",
        "fundamental",
        "published_archive",
        3650,
        ("roe_history", "revenue_history", "net_profit_history", "financial_health"),
    ),
    HistoricalCapability(
        "2_kline",
        "kline",
        "dated_series",
        550,
        ("ohlcv", "stage", "ma_align", "macd"),
    ),
    HistoricalCapability(
        "3_macro", "macro", "published_archive", 365, ("observations",)
    ),
    HistoricalCapability("4_peers", "peers", "derived_from_pit", 550, ("peers",)),
    HistoricalCapability(
        "5_chain",
        "chain",
        "published_archive",
        3650,
        ("segments", "upstream", "downstream", "concentration"),
    ),
    HistoricalCapability(
        "6_research", "research", "published_archive", 365, ("reports",)
    ),
    HistoricalCapability(
        "7_industry",
        "industry",
        "derived_from_pit",
        550,
        ("classification", "valuation", "growth", "cycle"),
    ),
    HistoricalCapability(
        "8_materials", "materials", "dated_series", 550, ("exposure", "prices")
    ),
    HistoricalCapability(
        "9_futures", "futures", "dated_series", 180, ("contract_mapping", "prices")
    ),
    HistoricalCapability(
        "10_valuation",
        "valuation",
        "dated_series",
        5 * 366,
        ("pe", "pb", "history", "percentiles"),
    ),
    HistoricalCapability(
        "11_governance",
        "governance",
        "published_archive",
        365,
        ("controller", "pledges", "ownership_changes", "penalties"),
    ),
    HistoricalCapability(
        "12_capital_flow",
        "capital_flow",
        "dated_series",
        365,
        (
            "money_flow",
            "northbound",
            "margin",
            "shareholder_counts",
            "institutions",
            "events",
        ),
    ),
    HistoricalCapability(
        "13_policy", "policy", "published_archive", 365, ("policies",)
    ),
    HistoricalCapability(
        "14_moat",
        "moat",
        "derived_from_pit",
        3650,
        ("factors", "research_and_development"),
    ),
    HistoricalCapability("15_events", "events", "published_archive", 180, ("events",)),
    HistoricalCapability("16_lhb", "lhb", "dated_series", 30, ("records",)),
    HistoricalCapability(
        "17_sentiment",
        "sentiment",
        "published_archive",
        30,
        ("items",),
        ("heat", "rank", "live_mentions"),
    ),
    HistoricalCapability("18_trap", "trap", "derived_from_pit", 365, ("signals",)),
    HistoricalCapability(
        "19_contests",
        "contests",
        "current_only",
        0,
        (),
        ("rankings", "portfolio_state", "authorization_status"),
    ),
    HistoricalCapability(
        "6_fund_holders",
        "fund_holders",
        "published_archive",
        5 * 366,
        ("reporting_period", "funds", "top_holders", "nav_history"),
    ),
    HistoricalCapability(
        "similar_stocks",
        "similar_stocks",
        "derived_from_pit",
        550,
        ("classification_version", "stocks"),
    ),
)

HISTORICAL_CAPABILITIES = {item.dimension: item for item in _CAPABILITIES}

_DATE_PREFIX = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})")
_DATE_FIELDS = (
    "published_at",
    "publish_time",
    "date",
    "trade_date",
    "point_at",
    "report_date",
    "period",
    "报告日期",
    "公告日期",
    "日期",
    "交易日期",
    "报告期",
    "统计截止日",
    "股东户数统计截止日",
    "解禁日期",
    "解禁时间",
)


def capability_for_dimension(dimension: str) -> HistoricalCapability:
    return HISTORICAL_CAPABILITIES.get(
        dimension,
        HistoricalCapability(dimension, dimension, "current_only"),
    )


def is_safe_degradation_manifest(manifest: Mapping[str, Any] | None) -> bool:
    degradation = manifest.get("degradation") if isinstance(manifest, Mapping) else None
    return bool(
        isinstance(degradation, Mapping)
        and degradation.get("safe_for_report") is True
        and degradation.get("excluded_from_scoring") is True
    )


def reconstruct_historical_snapshot(
    snapshot: Mapping[str, Any],
    *,
    dimension: str,
    as_of_date: date,
) -> dict[str, Any] | None:
    """Return a date-bounded reconstruction candidate or ``None``.

    This function intentionally supports only records with explicit dates.  It
    strips current summaries before the strict adapter sees the result, which
    prevents a historical report from inheriting today's ratios or rankings.
    """

    capability = capability_for_dimension(dimension)
    if not capability.can_reconstruct:
        return None
    prepared = deepcopy(dict(snapshot))
    facts = dict(prepared.get("facts") or {})
    envelope = facts.get("_uzi_raw_result")
    if isinstance(envelope, Mapping) and isinstance(envelope.get("data"), Mapping):
        raw = dict(envelope["data"])
    else:
        raw = {
            key: value
            for key, value in facts.items()
            if not str(key).startswith("_") and key != "availability"
        }

    projected = _project_dimension(
        dimension,
        raw,
        as_of_date=as_of_date,
        window_days=capability.window_days,
    )
    if projected is None:
        return None
    projected_raw, dated_count, earliest, latest = projected
    if isinstance(envelope, Mapping):
        facts["_uzi_raw_result"] = {**dict(envelope), "data": projected_raw}
    else:
        facts = {
            "availability": facts.get("availability") or "available",
            "_uzi_raw_result": {"data": projected_raw},
        }
    prepared["facts"] = facts
    prepared["historical_reconstruction"] = {
        "safe": True,
        "strategy": capability.strategy,
        "requested_as_of_date": as_of_date.isoformat(),
        "window_start": (
            as_of_date - timedelta(days=capability.window_days)
        ).isoformat(),
        "window_end": as_of_date.isoformat(),
        "dated_record_count": dated_count,
        "earliest_record_date": earliest.isoformat() if earliest else None,
        "latest_record_date": latest.isoformat() if latest else None,
        "proof_basis": "date_bounded_structured_provider_response",
    }
    return prepared


def _project_dimension(
    dimension: str,
    raw: dict[str, Any],
    *,
    as_of_date: date,
    window_days: int,
) -> tuple[dict[str, Any], int, date | None, date | None] | None:
    start = as_of_date - timedelta(days=window_days)
    if dimension == "3_macro":
        rows = _bounded_items(
            _items(raw.get("observations"), raw.get("web_search_snippets")),
            start=start,
            end=as_of_date,
        )
        projected = {"observations": rows}
    elif dimension == "6_research":
        rows = _bounded_items(
            _items(raw.get("recent_reports"), raw.get("reports")),
            start=start,
            end=as_of_date,
        )
        projected = {"recent_reports": rows, "report_count": len(rows)}
    elif dimension == "12_capital_flow":
        aliases = {
            "money_flow": (
                "money_flow",
                "main_flow",
                "recent_flows",
                "main_fund_flow_20d",
            ),
            "northbound": ("northbound", "hsgt"),
            "margin": ("margin", "margin_detail", "margin_recent"),
            "shareholder_counts": (
                "shareholder_counts",
                "holder_counts",
                "holder_count_history",
            ),
        }
        projected = {}
        for target, candidates in aliases.items():
            value = next(
                (raw.get(key) for key in candidates if raw.get(key) is not None),
                [],
            )
            projected[target] = _bounded_items(
                _items(value), start=start, end=as_of_date
            )

        institutions = next(
            (
                raw.get(key)
                for key in (
                    "institutions",
                    "institution_holders",
                    "institutional_history",
                )
                if raw.get(key) is not None
            ),
            [],
        )
        if isinstance(institutions, Mapping) and institutions.get("quarters"):
            institutions = _institutional_history_rows(institutions)
        projected["institutions"] = _bounded_items(
            _items(institutions), start=start, end=as_of_date
        )
        projected["events"] = _bounded_items(
            _items(
                raw.get("events"),
                raw.get("block_trades_recent"),
                raw.get("block_trades"),
                raw.get("unlock_recent"),
                raw.get("unlock"),
                raw.get("unlock_schedule"),
            ),
            start=start,
            end=as_of_date,
        )
    elif dimension == "13_policy":
        rows = _bounded_items(
            _items(raw.get("policies"), raw.get("snippets")),
            start=start,
            end=as_of_date,
        )
        projected = {"policies": rows}
    elif dimension == "15_events":
        rows = _bounded_items(
            _items(
                raw.get("events"),
                raw.get("event_timeline"),
                raw.get("recent_notices"),
                raw.get("recent_news"),
            ),
            start=start,
            end=as_of_date,
        )
        projected = {
            "events": rows,
            "disclosures_count": sum(_looks_like_notice(row) for row in rows),
            "news_count": sum(not _looks_like_notice(row) for row in rows),
            "truncation": dict(
                raw.get("truncation") or {"historical_reconstruction": True}
            ),
        }
    elif dimension == "16_lhb":
        rows = _bounded_items(
            _items(raw.get("lhb_records"), raw.get("records")),
            start=start,
            end=as_of_date,
        )
        projected = {"lhb_records": rows, "lhb_count_30d": len(rows)}
    elif dimension == "17_sentiment":
        rows = _bounded_items(
            _items(
                raw.get("items"),
                raw.get("platform_snippets"),
                raw.get("news_multi_source"),
            ),
            start=start,
            end=as_of_date,
        )
        projected = {
            "items": rows,
            "sentiment_data_available": bool(rows),
            "platform_hits": {"historical_archive": len(rows)},
            "news_sources_ok": 1 if rows else 0,
        }
    else:
        return None

    dated = [resolved for item in _walk(projected) if (resolved := _record_date(item))]
    if not dated:
        return None
    return projected, len(dated), min(dated), max(dated)


def _items(*values: Any) -> list[Any]:
    rows: list[Any] = []
    for value in values:
        if isinstance(value, list):
            rows.extend(value)
        elif isinstance(value, Mapping):
            for child in value.values():
                if isinstance(child, list):
                    rows.extend(child)
        elif value is not None:
            rows.append(value)
    return rows


def _bounded_items(values: Iterable[Any], *, start: date, end: date) -> list[Any]:
    rows: list[Any] = []
    seen: set[str] = set()
    for value in values:
        resolved = _record_date(value)
        if resolved is None or not start <= resolved <= end:
            continue
        identity = repr(value)
        if identity in seen:
            continue
        seen.add(identity)
        rows.append(value)
    return rows


def _walk(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        yield from value
    elif isinstance(value, Mapping):
        for child in value.values():
            if isinstance(child, list):
                yield from child


def _record_date(value: Any) -> date | None:
    if isinstance(value, Mapping):
        candidates = (value.get(field) for field in _DATE_FIELDS)
    else:
        candidates = (value,)
    for candidate in candidates:
        if isinstance(candidate, datetime):
            return candidate.date()
        if isinstance(candidate, date):
            return candidate
        match = _DATE_PREFIX.match(str(candidate or "").strip())
        if match:
            try:
                return date.fromisoformat(match.group("date"))
            except ValueError:
                continue
        text = str(candidate or "").strip().upper()
        compact = re.match(r"^(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})$", text)
        if compact:
            try:
                return date(
                    int(compact.group("year")),
                    int(compact.group("month")),
                    int(compact.group("day")),
                )
            except ValueError:
                continue
        quarter = re.match(r"^(?P<year>20\d{2}|\d{2})Q(?P<quarter>[1-4])$", text)
        if quarter:
            year = int(quarter.group("year"))
            year = 2000 + year if year < 100 else year
            quarter_number = int(quarter.group("quarter"))
            month_day = ((3, 31), (6, 30), (9, 30), (12, 31))[quarter_number - 1]
            return date(year, *month_day)
    return None


def _institutional_history_rows(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    quarters = list(value.get("quarters") or [])
    series = {
        "fund": list(value.get("fund") or []),
        "qfii": list(value.get("qfii") or []),
        "social_security": list(
            value.get("social_security") or value.get("shehui") or []
        ),
    }
    return [
        {
            "period": period,
            **{
                key: rows[index] if index < len(rows) else None
                for key, rows in series.items()
            },
        }
        for index, period in enumerate(quarters)
    ]


def _looks_like_notice(value: Any) -> bool:
    text = (
        str(value.get("type") or value.get("category") or "")
        if isinstance(value, Mapping)
        else str(value)
    )
    return "公告" in text or "notice" in text.lower()


__all__ = [
    "HISTORICAL_CAPABILITIES",
    "HistoricalCapability",
    "capability_for_dimension",
    "is_safe_degradation_manifest",
    "reconstruct_historical_snapshot",
]
