"""Database-only outcome prices for report-condition replay.

Outcome prices have their own cutoff and never enter a research Manifest.
This reader intentionally does not depend on any acquisition service.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from sqlalchemy import select

from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.ingestion import (
    _query_proof_errors,
    snapshot_content_hash,
)
from research_system.market_data.models import MarketDataSnapshot
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import (
    _database_utc,
    _snapshot_pit_visibility_errors,
)
from research_system.strategies.backtesting.instruments import (
    AssetType,
    TradableInstrument,
)
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.trading_calendar import trading_dates


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _action_evidence_errors(
    payload: dict[str, Any], query: dict[str, Any], *, start: date, end: date
) -> list[str]:
    """Empty adapter defaults are not evidence that no split/dividend occurred."""
    errors = []
    queries = query.get("field_queries")
    for field in ("corporate_actions", "adjustment_factors"):
        values = payload.get(field)
        if not isinstance(values, list):
            errors.append(f"{field}: missing factual result")
            continue
        proof = queries.get(field) if isinstance(queries, dict) else None
        if (
            not isinstance(proof, dict)
            or proof.get("query_scope") != "field"
            or proof.get("field") != field
        ):
            errors.append(
                f"{field}: no field-specific query proof; legacy dimension-wide success is insufficient"
            )
        errors.extend(
            _query_proof_errors(
                proof, label=field, expected_count=len(values), require_zero=not values
            )
        )
        window = proof.get("query_window") if isinstance(proof, dict) else None
        window = window if isinstance(window, dict) else {}
        first, last = _day(window.get("start")), _day(window.get("end"))
        if first is None or last is None or first > start or last < end:
            errors.append(
                f"{field}: query proof does not cover requested price-basis interval"
            )
        if any(
            not isinstance(row, dict) or _day(row.get("date")) is None for row in values
        ):
            errors.append(
                f"{field}: undated result cannot establish corporate-action coverage"
            )
    return errors


def read_frozen_market_rows(
    database,
    instrument: TradableInstrument,
    *,
    start_date: date,
    end_date: date,
    as_of_date: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if instrument.asset_type != AssetType.STOCK or instrument.currency != "CNY":
        raise StrategyDomainError(
            "report_replay_instrument_unsupported",
            "报告条件回放仅支持人民币 A 股",
            status_code=409,
        )
    if end_date > as_of_date:
        raise StrategyDomainError(
            "market_data_as_of_violation", "行情结束日期不能晚于行情截止日"
        )
    if end_date < start_date:
        raise StrategyDomainError(
            "market_data_range_invalid", "行情结束日期不能早于开始日期"
        )
    symbol = f"{instrument.code}.{instrument.exchange}"
    details: dict[str, Any] = {
        "suggested_collection": {
            "symbols": [symbol],
            "market": "CN_EQUITY",
            "dimensions": ["2_kline"],
            "start_date": end_date.isoformat(),
            "end_date": end_date.isoformat(),
            "mode": "missing_or_stale",
        },
        "required_price_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    }
    sessions = trading_dates(start_date, end_date)
    if not sessions:
        raise StrategyDomainError(
            "market_data_range_has_no_sessions",
            "请求区间没有交易日",
            status_code=409,
            details=details,
        )
    if database is None:
        raise StrategyDomainError(
            "market_data_center_unavailable",
            "报告回放未绑定数据中心，不能隐式联网取行情",
            status_code=409,
            details=details,
        )
    cutoff = cutoff_at_for_date(as_of_date, "Asia/Shanghai")
    rejected: list[dict[str, Any]] = []
    with database.session() as session:
        repository = MarketDataRepository(session)
        candidates = session.scalars(
            select(MarketDataSnapshot)
            .where(
                MarketDataSnapshot.symbol.in_([symbol, instrument.code]),
                MarketDataSnapshot.market == "CN_EQUITY",
                MarketDataSnapshot.dimension == "2_kline",
                MarketDataSnapshot.as_of_date <= as_of_date,
                MarketDataSnapshot.published_at <= cutoff,
                MarketDataSnapshot.revoked_at.is_(None),
                MarketDataSnapshot.status.in_(["available", "partial"]),
            )
            .order_by(
                MarketDataSnapshot.as_of_date.desc(),
                MarketDataSnapshot.created_at.desc(),
            )
            .limit(100)
        )
        for snapshot in candidates:
            payload = dict(snapshot.payload_json or {})
            raw = dict(payload.get("raw") or {})
            query = dict(snapshot.query_manifest_json or {})
            coverage = dict(query.get("coverage") or {})
            adjustment = str(
                coverage.get("ohlcv_adjustment") or raw.get("ohlcv_adjustment") or ""
            ).lower()
            if adjustment not in {"none", "unadjusted", "raw"}:
                rejected.append(
                    {
                        "snapshot_id": snapshot.id,
                        "reason": "price_adjustment_unverified",
                    }
                )
                continue
            series = repository.list_snapshot_series_points(snapshot.id)
            visibility_errors = _snapshot_pit_visibility_errors(
                repository, snapshot, cutoff, series_points=series
            )
            if snapshot.evidence_grade not in {"A", "B"}:
                visibility_errors.append("snapshot evidence is not grade A/B")
            if not repository.list_snapshot_evidence(snapshot.id):
                visibility_errors.append("snapshot has no retained evidence")
            if visibility_errors:
                rejected.append(
                    {
                        "snapshot_id": snapshot.id,
                        "reason": "outcome_cutoff_unverified",
                        "errors": visibility_errors,
                    }
                )
                continue
            digest = snapshot_content_hash(
                symbol=snapshot.symbol,
                market=snapshot.market,
                exchange_timezone=snapshot.exchange_timezone,
                dimension=snapshot.dimension,
                contract_version=snapshot.contract_version,
                as_of_date=snapshot.as_of_date,
                status=snapshot.status,
                evidence_grade=snapshot.evidence_grade,
                payload=payload,
                effective_at=_database_utc(snapshot.effective_at),
                published_at=_database_utc(snapshot.published_at),
                source_version=snapshot.source_version,
                source_priority=snapshot.source_priority,
                query_manifest=query,
                field_sources=dict(snapshot.field_sources_json or {}),
                series_hashes=[point.content_hash for point in series],
            )
            if digest != snapshot.content_hash:
                raise StrategyDomainError(
                    "market_snapshot_hash_mismatch",
                    "数据中心行情快照内容校验失败",
                    status_code=409,
                    details={"snapshot_id": snapshot.id},
                )
            rows = [
                dict(row) for row in payload.get("ohlcv") or [] if isinstance(row, dict)
            ]
            selected = [row for row in rows if _day(row.get("date")) in sessions]
            if any(_day(row["date"]) > snapshot.as_of_date for row in selected):
                rejected.append(
                    {
                        "snapshot_id": snapshot.id,
                        "reason": "price_postdates_snapshot_cutoff",
                    }
                )
                continue
            if not selected:
                rejected.append(
                    {"snapshot_id": snapshot.id, "reason": "requested_range_missing"}
                )
                continue
            missing_dates = sorted(
                set(sessions) - {_day(row.get("date")) for row in selected}
            )
            if missing_dates:
                rejected.append(
                    {
                        "snapshot_id": snapshot.id,
                        "reason": "requested_sessions_missing",
                        "dates": [day.isoformat() for day in missing_dates],
                    }
                )
                continue
            action_errors = _action_evidence_errors(
                payload, query, start=start_date, end=sessions[-1]
            )
            if action_errors:
                rejected.append(
                    {
                        "snapshot_id": snapshot.id,
                        "reason": "corporate_action_coverage_unverified",
                        "errors": action_errors,
                    }
                )
                continue
            actions = [
                dict(row)
                for row in payload.get("corporate_actions") or []
                if isinstance(row, dict)
            ]
            in_range_actions = [
                row for row in actions if start_date <= _day(row["date"]) <= end_date
            ]
            factors = [
                row
                for row in payload["adjustment_factors"]
                if start_date <= _day(row["date"]) <= end_date
            ]
            try:
                factor_values = {float(row["factor"]) for row in factors}
                factors_invalid = any(
                    not math.isfinite(value) or value <= 0 for value in factor_values
                )
            except (KeyError, TypeError, ValueError):
                factors_invalid = True
                factor_values = set()
            if factors_invalid:
                rejected.append(
                    {"snapshot_id": snapshot.id, "reason": "adjustment_factor_invalid"}
                )
                continue
            if len(factor_values) > 1:
                in_range_actions.append(
                    {
                        "type": "adjustment_factor_changed",
                        "factors": sorted(factor_values),
                    }
                )
            if in_range_actions:
                raise StrategyDomainError(
                    "report_replay_corporate_actions_unsupported",
                    "回测区间含公司行动，本版不以错误复权价格冒充准确回放",
                    status_code=409,
                    details={
                        "snapshot_id": snapshot.id,
                        "corporate_actions": in_range_actions,
                    },
                )
            return selected, {
                "provider": "market_data_center",
                "source_version": snapshot.source_version,
                "snapshot_id": snapshot.id,
                "snapshot_content_hash": snapshot.content_hash,
                "snapshot_as_of_date": snapshot.as_of_date.isoformat(),
                "published_at": _database_utc(snapshot.published_at).isoformat(),
                "observed_at": _database_utc(snapshot.observed_at).isoformat(),
                "outcome_cutoff_at": cutoff.isoformat(),
                "adjustment": "raw",
                "currency": "CNY",
                "price_unit": "per_share",
                "corporate_actions": [],
                "corporate_action_coverage": {
                    "verified": True,
                    "start": start_date.isoformat(),
                    "end": sessions[-1].isoformat(),
                },
                "corporate_action_queries": {
                    key: query["field_queries"][key]
                    for key in ("corporate_actions", "adjustment_factors")
                },
                "source_query": query.get("source_queries")
                or query.get("coverage")
                or {},
            }
    details["rejected_snapshots"] = rejected
    raise StrategyDomainError(
        "market_history_missing_in_data_center",
        "数据中心缺少可回放的未复权行情，请先显式采集",
        status_code=409,
        details=details,
    )
