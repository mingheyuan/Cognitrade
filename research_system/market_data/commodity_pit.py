"""Read-only safety checks for dated commodity observations, not forecasts.

Older adapters stamped a safe query envelope around an unfiltered futures
response. Inspect its actual dated price facts as well as the envelope. Never
rewrite a frozen payload or treat a future catalyst/contract expiry as a quote.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

_DIMENSIONS = {"8_materials", "9_futures", "materials", "futures"}
_ROW_DATE_KEYS = {"date", "日期", "交易日期", "trade_date", "datetime"}
_DATED_ROWS = {
    "latest_record",
    "prices",
    "price_history_12m",
    "price_history_60d",
    "ohlcv",
}
_DATE_LISTS = {"price_history_dates_12m", "price_history_dates_60d"}
_SKIP_BRANCHES = {
    "source_refs",
    "source_trace",
    "query_manifest",
    "historical_reconstruction",
}


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip().replace("/", "-")
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def commodity_fact_date_errors(
    payload: Mapping[str, Any], *, dimension: str, as_of_date: date
) -> list[str]:
    """Find explicit post-cutoff price observations, without mutating input.

    A missing date is not certified safe here: provenance checks and the dated
    collector remain responsible for proving availability. This guard closes
    the concrete legacy contradiction even when ``pit_safe`` says True.
    """
    if dimension not in _DIMENSIONS:
        return []
    errors: list[str] = []

    def check(value: Any, path: str) -> None:
        observed = _day(value)
        if observed is not None and observed > as_of_date:
            errors.append(
                f"commodity price fact {path} is dated {observed} after cutoff {as_of_date}"
            )

    def visit(value: Any, path: str, parent: str = "") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                location = f"{path}.{key}"
                if key == "latest_record_date" or (
                    parent in _DATED_ROWS and key in _ROW_DATE_KEYS
                ):
                    check(item, location)
                elif key in _DATE_LISTS and isinstance(item, list):
                    for index, observed in enumerate(item):
                        check(observed, f"{location}[{index}]")
                elif key not in _SKIP_BRANCHES:
                    visit(item, location, str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]", parent)

    visit(payload, "payload")
    return errors


def commodity_empty_query_errors(
    manifest: Mapping[str, Any], *, dimension: str, as_of_date: date
) -> list[str]:
    """Reject the legacy automatic 'not queried == verified empty' mistake.

    Native not-applicable mappings and manual disclosures are not automatic
    bounded-zero observations and retain their original meaning.
    """
    if (
        dimension not in _DIMENSIONS
        or manifest.get("pit_mode") != "historical_reconstruction"
        or manifest.get("collection_outcome") != "verified_empty"
        or not (
            manifest.get("provider") in {"uzi_materials_multi", "uzi_futures_multi"}
            or (manifest.get("verified_empty_disclosure") or {}).get("reason_code")
            == "historical_bounded_zero"
        )
    ):
        return []
    queries = [
        row
        for row in manifest.get("source_trace") or []
        if isinstance(row, Mapping) and row.get("query_scope") == "futures_prices"
    ]
    proven = bool(queries) and not set(
        manifest.get("missing_fields") or []
    ).intersection(
        {
            "industry_material_mapping_missing",
            "futures_contract_mapping_missing",
        }
    )
    for row in queries:
        window = row.get("query_window") or {}
        start, end = _day(window.get("start")), _day(window.get("end"))
        count = row.get("in_window_count")
        proven = proven and bool(
            row.get("request_executed") is True
            and row.get("bounded_query_completed") is True
            and row.get("status") == "no_data"
            and isinstance(count, (int, float))
            and not isinstance(count, bool)
            and count == 0
            and start is not None
            and end == as_of_date
            and start <= end
        )
    return (
        []
        if proven
        else [
            "automatic historical commodity empty result lacks a completed bounded contract-price query"
        ]
    )
