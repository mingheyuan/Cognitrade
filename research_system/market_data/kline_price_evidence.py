"""Explicit collection of price-basis facts, independent of UZI indicators.

No report or replay reader calls this collector. Source failures are facts about
an attempted query, never a successful empty corporate-action history.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from research_system.adapters.vendors.baostock_socket_safety import baostock_session
from research_system.market_data.ingestion import _query_proof_errors

FIELDS = ("corporate_actions", "adjustment_factors")
PROVIDER_VERSION = "baostock-price-evidence-v1"


def has_complete_price_evidence(snapshot: Any, as_of_date: date) -> bool:
    if snapshot is None:
        return False
    payload = dict(snapshot.payload_json or {})
    manifest = dict(snapshot.query_manifest_json or {})
    adjustment = str(
        dict(manifest.get("coverage") or {}).get("ohlcv_adjustment")
        or dict(payload.get("raw") or {}).get("ohlcv_adjustment")
        or ""
    ).lower()
    if adjustment not in {"none", "unadjusted", "raw"} or not payload.get("ohlcv"):
        return False
    queries = manifest.get("field_queries") or {}
    for field in FIELDS:
        values = payload.get(field)
        proof = queries.get(field)
        if not isinstance(values, list) or not isinstance(proof, dict):
            return False
        if proof.get("query_scope") != "field" or proof.get("field") != field:
            return False
        if _query_proof_errors(
            proof, label=field, expected_count=len(values), require_zero=not values
        ):
            return False
        window = proof.get("query_window") or {}
        first, last = _day(window.get("start")), _day(window.get("end"))
        if first is None or last is None or not first <= as_of_date <= last:
            return False
    return True


class BaoStockPriceEvidenceCollector:
    def __init__(self, *, client: Any = None, deadline_seconds: float = 60.0):
        self.client = client
        self.deadline_seconds = deadline_seconds

    def collect(
        self, *, symbol: str, start_date: date, end_date: date
    ) -> dict[str, Any]:
        deadline = monotonic() + self.deadline_seconds
        dividends: list[dict[str, Any]] = []
        factors: list[dict[str, Any]] = []
        dividend_sources: list[dict[str, Any]] = []
        factor_source: dict[str, Any] | None = None
        try:
            code = _code(symbol)
            if (
                end_date < start_date
                or end_date
                > datetime.now(timezone.utc)
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
            ):
                raise ValueError("invalid historical price-evidence range")
            with baostock_session(
                client=self.client,
                lock_timeout_seconds=min(30.0, max(0.01, deadline - monotonic())),
            ) as client:
                for year in range(start_date.year, end_date.year + 1):
                    source_id = f"baostock.query_dividend_data:{year}"
                    parameters = {
                        "code": code,
                        "year": str(year),
                        "yearType": "operate",
                    }
                    rows, source = self._query(
                        lambda p=parameters: client.query_dividend_data(**p),
                        source_id,
                        parameters,
                        deadline=deadline,
                        normalizer=lambda row: _dividend(row, start_date, end_date),
                    )
                    dividends.extend(rows)
                    dividend_sources.append(source)
                parameters = {
                    "code": code,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                }
                factors, factor_source = self._query(
                    lambda: client.query_adjust_factor(**parameters),
                    "baostock.query_adjust_factor",
                    parameters,
                    deadline=deadline,
                    normalizer=lambda row: _factor(row, start_date, end_date),
                )
        except Exception as exc:
            failure = _failure("baostock.session", {}, exc)
            dividend_sources.append(failure)
            factor_source = factor_source or failure
        factor_source = factor_source or _failure(
            "baostock.query_adjust_factor", {}, RuntimeError("query did not run")
        )
        # BaoStock factors are dated adjustment events, not daily constant rows.
        # One event is already an unsupported action, even without dividends.
        actions = dividends + [
            {
                "date": row["date"],
                "type": "adjustment_event",
                "provider": "baostock.query_adjust_factor",
            }
            for row in factors
        ]
        queries = {
            "corporate_actions": _proof(
                "corporate_actions",
                actions,
                dividend_sources + [factor_source],
                start_date,
                end_date,
            ),
            "adjustment_factors": _proof(
                "adjustment_factors", factors, [factor_source], start_date, end_date
            ),
        }
        sources = dividend_sources + [factor_source]
        failures = [row for row in sources if row["status"] != "success"]
        return {
            "provider_version": PROVIDER_VERSION,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "corporate_actions": actions,
            "adjustment_factors": factors,
            "field_queries": queries,
            "field_sources": {
                "corporate_actions": "baostock.query_dividend_data+query_adjust_factor",
                "adjustment_factors": "baostock.query_adjust_factor",
            },
            "status": "partial" if failures else "available",
            "retryable": bool(failures),
            "error_reason": "; ".join(
                f"{row['source']}: {row.get('error_reason') or row['status']}"
                for row in failures
            )
            or None,
        }

    @staticmethod
    def _query(loader, source_id, parameters, *, deadline, normalizer):
        try:
            if monotonic() >= deadline:
                raise TimeoutError("price-evidence collection deadline reached")
            result = loader()
            if monotonic() >= deadline:
                raise TimeoutError("price-evidence collection deadline reached")
            if str(getattr(result, "error_code", "")) != "0":
                raise _QueryFailure(str(getattr(result, "error_code", "unknown")))
            fields = list(getattr(result, "fields", []))
            rows = []
            visited = 0
            while result.next():
                visited += 1
                if monotonic() >= deadline:
                    raise TimeoutError("price-evidence collection deadline reached")
                if visited > 10000:
                    raise ValueError("price-evidence row safety limit exceeded")
                raw = dict(zip(fields, result.get_row_data()))
                row = normalizer(raw)
                if row is not None:
                    rows.append(row)
            if str(getattr(result, "error_code", "")) != "0":
                raise _QueryFailure(str(getattr(result, "error_code", "unknown")))
            return rows, {
                "source": source_id,
                "status": "success",
                "result_count": len(rows),
                "parameters": dict(parameters),
                "date_filter": "operation_date_within_query_window",
            }
        except Exception as exc:
            return [], _failure(source_id, parameters, exc)


def enrich_kline_snapshot(
    snapshot: dict[str, Any],
    *,
    as_of_date: date,
    collector: BaoStockPriceEvidenceCollector,
) -> dict[str, Any]:
    result = deepcopy(snapshot)
    evidence = collector.collect(
        symbol=str(snapshot.get("symbol") or ""),
        start_date=as_of_date - timedelta(days=550),
        end_date=as_of_date,
    )
    facts = dict(result.get("facts") or {})
    facts["price_evidence"] = evidence
    result["facts"] = facts
    result["field_sources"] = {
        **dict(result.get("field_sources") or {}),
        **evidence["field_sources"],
    }
    return result


def _proof(field, rows, sources, start, end):
    sources = list({row["source"]: row for row in sources}.values())
    return {
        "query_scope": "field",
        "field": field,
        "query_window": {"start": start.isoformat(), "end": end.isoformat()},
        "required_sources": [row["source"] for row in sources],
        "sources": sources,
        "result_count": len(rows),
    }


def _failure(source, parameters, exc):
    return {
        "source": source,
        "status": "timeout"
        if isinstance(exc, TimeoutError)
        else "parse_failed"
        if isinstance(exc, ValueError)
        else "provider_unavailable",
        "result_count": None,
        "parameters": dict(parameters),
        "error_reason": f"baostock_error:{exc.code}"
        if isinstance(exc, _QueryFailure)
        else type(exc).__name__,
    }


class _QueryFailure(RuntimeError):
    def __init__(self, code):
        self.code = code


def _code(symbol):
    raw = str(symbol).upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ))?", raw)
    if not match or not match[1].startswith(("0", "3", "6")):
        raise ValueError(
            "BaoStock price evidence supports Shanghai/Shenzhen A shares only"
        )
    return f"{'sh' if match[1].startswith('6') else 'sz'}.{match[1]}"


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _dividend(row, start, end):
    day = _day(row.get("dividOperateDate"))
    if day is None:
        raise ValueError("corporate action has no operation date")
    if not start <= day <= end:
        return None
    announcement = _day(row.get("dividPlanAnnounceDate"))
    if announcement is not None and announcement > end:
        raise ValueError("corporate action was published after historical cutoff")
    return {
        "date": day.isoformat(),
        "type": "dividend_or_distribution",
        "provider": "baostock.query_dividend_data",
        "source_fields": dict(row),
    }


def _factor(row, start, end):
    day = _day(row.get("dividOperateDate"))
    if day is None:
        raise ValueError("adjustment factor has no operation date")
    if not start <= day <= end:
        return None
    factor = float(row.get("adjustFactor"))
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("adjustment factor is not finite and positive")
    return {
        "date": day.isoformat(),
        "factor": factor,
        "provider": "baostock.query_adjust_factor",
        "source_fields": dict(row),
    }
