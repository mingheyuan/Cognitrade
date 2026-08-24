from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from research_system.market_data.contracts import (
    CONTRACT_VERSION,
    EvidenceGrade,
    EvidenceInput,
    SnapshotIngestInput,
    SnapshotStatus,
    canonical_hash,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_UTC_OFFSET_RE = re.compile(
    r"^(?:(?:UTC|GMT)\s*)?(?P<sign>[+-])(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?$",
    re.IGNORECASE,
)
_SOURCE_TIMEZONE_FIELDS = ("source_timezone", "timezone", "time_zone", "tz")
_NATIVE_UNAVAILABLE_STATUSES = frozenset(
    {
        "assumption_only",
        "data_gap",
        "failed",
        "missing_result",
        "no_data",
        "parse_failed",
        "provider_unavailable",
        "rate_limited",
        "timeout",
    }
)
_QUALITATIVE_DIMENSIONS = frozenset(
    {
        "3_macro",
        "7_industry",
        "13_policy",
        "14_moat",
        "17_sentiment",
        "18_trap",
        "19_contests",
    }
)


class UziSnapshotAdapter:
    """Convert one live UZI collector result into the strict PIT ingest contract.

    The adapter never upgrades historical evidence merely because a provider can
    return a current page today.  Native UZI fetchers have no ``as_of`` argument,
    so a result observed after the requested market date is persisted as a
    ``data_gap`` for audit instead of leaking into a backtest.
    """

    def __init__(
        self, *, registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY
    ) -> None:
        self.registry = registry
        self._dimension_by_snapshot_type = {
            contract.snapshot_type: contract.dimension for contract in registry
        }

    def dimension_for_snapshot_type(self, snapshot_type: str) -> str:
        try:
            return self._dimension_by_snapshot_type[str(snapshot_type or "").strip()]
        except KeyError as exc:
            raise ValueError(f"unsupported UZI snapshot type: {snapshot_type}") from exc

    def adapt(
        self,
        snapshot: Mapping[str, Any],
        *,
        symbol: str,
        market: str,
        exchange_timezone: str,
        as_of_date: date,
        collection_task_id: str | None = None,
        provider_attempt_id: str | None = None,
    ) -> SnapshotIngestInput:
        snapshot_type = str(snapshot.get("snapshot_type") or "").strip()
        dimension = self.dimension_for_snapshot_type(snapshot_type)
        facts = (
            dict(snapshot.get("facts") or {})
            if isinstance(snapshot.get("facts"), Mapping)
            else {}
        )
        raw = self._raw_data(facts)
        observed_at = self._timestamp(snapshot.get("retrieved_at"))
        local_observed_date = observed_at.astimezone(ZoneInfo(exchange_timezone)).date()
        payload, semantic_errors = self._normalize_payload(
            dimension,
            raw,
            facts=facts,
            snapshot=dict(snapshot),
            symbol=symbol,
            market=market,
            observed_at=observed_at,
        )
        declared_status = (
            str(
                snapshot.get("quality_status")
                or snapshot.get("status")
                or facts.get("availability")
                or "data_gap"
            )
            .strip()
            .lower()
        )
        # UZI's ``degraded``/``partial`` describe acquisition quality, not the
        # strict PIT contract.  Preserve raw results as availability candidates
        # and let normalized fields, evidence, and ingestion validation decide;
        # only explicit provider failures are rejected up front.
        native_available = (
            bool(raw) and declared_status not in _NATIVE_UNAVAILABLE_STATUSES
        )
        pit_safe = as_of_date == local_observed_date
        if not pit_safe:
            semantic_errors.append(
                "native UZI fetcher has no historical as_of contract; current observation cannot reconstruct this date"
            )
        evidence, evidence_errors = self._evidence(
            snapshot=dict(snapshot),
            dimension=dimension,
            raw=raw,
            observed_at=observed_at,
            pit_safe=pit_safe,
            exchange_timezone=exchange_timezone,
        )
        semantic_errors.extend(evidence_errors)
        semantic_errors = list(dict.fromkeys(semantic_errors))
        status = (
            SnapshotStatus.AVAILABLE
            if native_available and not semantic_errors and pit_safe
            else SnapshotStatus.DATA_GAP
        )
        source = (
            snapshot.get("source")
            if isinstance(snapshot.get("source"), Mapping)
            else {}
        )
        provider_id = str(
            source.get("provider_id") or source.get("source_id") or f"uzi:{dimension}"
        )
        return SnapshotIngestInput(
            symbol=symbol,
            market=market,
            exchange_timezone=exchange_timezone,
            dimension=dimension,
            contract_version=CONTRACT_VERSION,
            as_of_date=as_of_date,
            status=status,
            payload=payload,
            effective_at=observed_at,
            published_at=observed_at,
            observed_at=observed_at,
            ingested_at=datetime.now(timezone.utc),
            source_version="uzi-3.9.4-b004d7a",
            source_priority=int(source.get("chain_position") or 0) + 10,
            origin="provider",
            query_manifest={
                "requested_as_of_date": as_of_date.isoformat(),
                "observed_local_date": local_observed_date.isoformat(),
                "pit_safe": pit_safe,
                "snapshot_type": snapshot_type,
                "provider": provider_id,
                "native_quality_status": declared_status,
                "degrade_reasons": list(snapshot.get("degrade_reasons") or []),
                "missing_fields": list(snapshot.get("missing_fields") or []),
            },
            validation={
                "adapter": "cognitrade_uzi_pit_v1",
                "semantic_errors": semantic_errors,
                "native_available": native_available,
                "pit_safe": pit_safe,
            },
            field_sources={"$": provider_id},
            evidence=evidence,
            observation_metadata={
                "native_snapshot_date": snapshot.get("snapshot_date"),
                "native_fallback_applied": bool(snapshot.get("fallback_applied")),
            },
            collection_task_id=collection_task_id,
            provider_attempt_id=provider_attempt_id,
        )

    @staticmethod
    def _raw_data(facts: dict[str, Any]) -> dict[str, Any]:
        envelope = facts.get("_uzi_raw_result")
        if isinstance(envelope, Mapping) and isinstance(envelope.get("data"), Mapping):
            raw = dict(envelope["data"])
        else:
            raw = {
                key: value
                for key, value in facts.items()
                if not str(key).startswith("_") and key not in {"availability"}
            }
        for field in ("fund_managers", "similar_stocks"):
            if field not in raw and field in facts:
                raw[field] = facts[field]
        return raw

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if isinstance(value, datetime):
            resolved = value
        else:
            try:
                resolved = datetime.fromisoformat(
                    str(value or "").replace("Z", "+00:00")
                )
            except ValueError:
                resolved = datetime.now(timezone.utc)
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc)

    def _normalize_payload(
        self,
        dimension: str,
        raw: dict[str, Any],
        *,
        facts: dict[str, Any],
        snapshot: dict[str, Any],
        symbol: str,
        market: str,
        observed_at: datetime,
    ) -> tuple[dict[str, Any], list[str]]:
        payload: dict[str, Any] = {"raw": raw}
        if dimension == "0_basic":
            price = self._number(raw.get("price"))
            market_cap = self._number(raw.get("market_cap_raw"), raw.get("market_cap"))
            shares = self._number(
                raw.get("shares_outstanding"), raw.get("total_shares")
            )
            if shares is None and price not in (None, 0) and market_cap is not None:
                shares = market_cap / price
            payload.update(
                symbol=str(raw.get("code") or symbol),
                market=market,
                industry=raw.get("industry"),
                currency=raw.get("currency")
                or ("CNY" if market.upper().startswith("CN") else None),
                price=price,
                market_cap=market_cap,
                shares_outstanding=shares,
                observed_at=observed_at.isoformat(),
            )
        elif dimension == "1_financials":
            years = list(raw.get("financial_years") or [])
            histories = {
                "revenue": list(raw.get("revenue_history") or []),
                "net_profit": list(raw.get("net_profit_history") or []),
                "roe": list(raw.get("roe_history") or []),
            }
            payload["periods"] = [
                {
                    "period": str(year),
                    **{
                        key: values[index]
                        for key, values in histories.items()
                        if index < len(values)
                    },
                }
                for index, year in enumerate(years)
            ]
            payload["latest"] = raw
        elif dimension == "2_kline":
            history = (
                facts.get("history")
                if isinstance(facts.get("history"), Mapping)
                else {}
            )
            payload.update(
                ohlcv=list(
                    raw.get("ohlcv")
                    or history.get("bars")
                    or raw.get("bars")
                    or raw.get("candles_60d")
                    or []
                ),
                corporate_actions=list(raw.get("corporate_actions") or []),
                adjustment_factors=list(raw.get("adjustment_factors") or []),
                indicators=raw.get("indicators") or {},
            )
        elif dimension == "3_macro":
            payload["observations"] = self._items(
                raw.get("observations"),
                raw.get("web_search_snippets"),
                raw.get("macro_headlines_playwright"),
            )
        elif dimension == "4_peers":
            peers = list(raw.get("peer_table") or raw.get("peers") or [])
            payload.update(
                target=raw.get("self")
                or (raw.get("global_peer_comparison") or {}).get("target")
                or {},
                peers=[
                    row
                    for row in peers
                    if not isinstance(row, Mapping) or not row.get("is_self")
                ],
                classification_version=raw.get("classification_version")
                or raw.get("industry"),
            )
        elif dimension == "5_chain":
            payload.update(
                segments=raw.get("main_business_raw")
                or raw.get("main_business_breakdown")
                or [],
                upstream=raw.get("upstream"),
                downstream=raw.get("downstream"),
                concentration={
                    "client": raw.get("client_concentration"),
                    "supplier": raw.get("supplier_concentration"),
                },
            )
        elif dimension == "6_research":
            payload.update(
                reports=list(raw.get("recent_reports") or raw.get("reports") or []),
                query_status={
                    "status": "success",
                    "result_count": raw.get("report_count"),
                },
                summary=raw,
            )
        elif dimension == "7_industry":
            payload.update(
                classification=raw.get("classification") or raw.get("industry"),
                valuation=raw.get("valuation") or raw.get("industry_pe"),
                growth=raw.get("growth") or raw.get("industry_growth"),
                tam=raw.get("tam") or raw.get("market_size"),
                penetration=raw.get("penetration"),
                cycle=raw.get("cycle") or raw.get("lifecycle"),
            )
        elif dimension == "8_materials":
            payload.update(
                exposure={
                    "core_material": raw.get("core_material"),
                    "cost_share": raw.get("cost_share"),
                    "import_dependency": raw.get("import_dep"),
                    "detail": raw.get("materials_detail"),
                },
                prices=list(raw.get("prices") or raw.get("price_history_12m") or []),
            )
        elif dimension == "9_futures":
            payload.update(
                contract_mapping=raw.get("contract_mapping")
                or raw.get("linked_contract"),
                prices=list(raw.get("prices") or raw.get("contract_prices") or []),
                trend=raw.get("contract_trend"),
            )
        elif dimension == "10_valuation":
            payload.update(
                pe=raw.get("pe"),
                pb=raw.get("pb"),
                history=raw.get("history") or raw.get("valuation_history") or [],
                percentiles={
                    "pe": raw.get("pe_quantile"),
                    "pb": raw.get("pb_quantile"),
                },
                industry_pe=raw.get("industry_pe"),
                denominators=raw.get("denominators") or {},
                shares_outstanding=raw.get("shares_outstanding")
                or raw.get("total_shares"),
            )
        elif dimension == "11_governance":
            payload.update(
                controller=raw.get("controller") or raw.get("actual_controller"),
                pledges=list(raw.get("pledge") or raw.get("pledges") or []),
                ownership_changes=list(
                    raw.get("insider_trades_1y") or raw.get("ownership_changes") or []
                ),
                penalties=list(raw.get("penalties") or raw.get("punishments") or []),
            )
        elif dimension == "12_capital_flow":
            payload.update(
                money_flow=raw.get("money_flow")
                or raw.get("main_flow")
                or raw.get("recent_flows"),
                northbound=raw.get("northbound") or raw.get("hsgt"),
                margin=raw.get("margin") or raw.get("margin_detail"),
                shareholder_counts=raw.get("shareholder_counts")
                or raw.get("holder_counts"),
                institutions=raw.get("institutions") or raw.get("institution_holders"),
                events=raw.get("events")
                or {
                    "block_trades": raw.get("block_trades"),
                    "unlock": raw.get("unlock"),
                },
            )
        elif dimension == "13_policy":
            payload["policies"] = self._items(
                raw.get("policies"),
                raw.get("snippets"),
                {
                    key: raw.get(key)
                    for key in ("policy_dir", "subsidy", "monitoring", "anti_trust")
                },
            )
        elif dimension == "14_moat":
            payload.update(
                factors=raw.get("factors")
                or raw.get("scores")
                or {
                    key: raw.get(key)
                    for key in ("intangible", "switching", "network", "scale")
                },
                research_and_development=raw.get("research_and_development")
                or raw.get("rd_summary"),
            )
        elif dimension == "15_events":
            events = self._items(
                raw.get("events"),
                raw.get("event_timeline"),
                raw.get("recent_notices"),
                raw.get("recent_news"),
            )
            payload.update(
                events=events,
                query_status={
                    "status": "success" if raw else "unknown",
                    "notice_count": raw.get("disclosures_count"),
                    "news_count": raw.get("news_count"),
                },
                truncation=raw.get("truncation") or {"latest_policy_cap": 30},
            )
        elif dimension == "16_lhb":
            payload.update(
                records=list(raw.get("lhb_records") or raw.get("records") or []),
                query_status={
                    "status": "success" if "lhb_count_30d" in raw else "unknown",
                    "result_count": raw.get("lhb_count_30d"),
                },
            )
        elif dimension == "17_sentiment":
            payload.update(
                items=self._items(
                    raw.get("items"),
                    raw.get("platform_snippets"),
                    raw.get("news_multi_source"),
                ),
                query_status={
                    "platforms": raw.get("platform_hits"),
                    "news_sources_ok": raw.get("news_sources_ok"),
                    "data_available": raw.get("sentiment_data_available"),
                },
            )
        elif dimension == "18_trap":
            signals = list(raw.get("signals") or [])
            if not signals:
                hits = {
                    str(row.get("signal") or row.get("id") or index): row
                    for index, row in enumerate(raw.get("signals_hit_detail") or [])
                    if isinstance(row, Mapping)
                }
                signals = [
                    {
                        "id": index,
                        "status": "hit" if str(index) in hits else "insufficient",
                        "evidence": hits.get(str(index)),
                    }
                    for index in range(1, 9)
                ]
            payload["signals"] = signals
        elif dimension == "19_contests":
            payload.update(
                results=self._items(
                    raw.get("results"),
                    raw.get("xueqiu_cubes"),
                    raw.get("tgb_mentions"),
                    raw.get("ths_simu"),
                ),
                authorization_status=raw.get("authorization_status")
                or (raw.get("xueqiu_meta") or {}).get("status")
                or "unknown",
                query_status=raw.get("query_status") or raw.get("summary") or {},
            )
        elif dimension == "6_fund_holders":
            holder_data = raw.get("fund_managers") or raw
            if not isinstance(holder_data, Mapping):
                holder_data = {
                    "funds": holder_data if isinstance(holder_data, list) else []
                }
            payload.update(
                reporting_period=holder_data.get("reporting_period")
                or holder_data.get("quarter")
                or holder_data.get("period"),
                funds=holder_data.get("funds")
                or holder_data.get("holders")
                or holder_data.get("fund_managers")
                or [],
                top_holders=holder_data.get("top_holders")
                or holder_data.get("top100")
                or holder_data.get("funds")
                or [],
                nav_history=holder_data.get("nav_history")
                or holder_data.get("fund_stats")
                or [],
            )
        elif dimension == "similar_stocks":
            rows = raw.get("similar_stocks") or raw.get("stocks") or []
            payload.update(
                classification_version=raw.get("classification_version")
                or raw.get("industry")
                or "uzi-3.9.4-static-map",
                stocks=list(rows) if isinstance(rows, list) else [],
            )

        contract = self.registry.get(dimension)
        missing = [
            field
            for field in contract.required_fields
            if not self._path_present(payload, field)
        ]
        semantic_errors = [f"missing normalized field: {field}" for field in missing]
        if dimension == "2_kline" and len(payload.get("ohlcv") or []) < 260:
            semantic_errors.append("unadjusted OHLCV has fewer than 260 trading days")
        if dimension == "4_peers" and len(payload.get("peers") or []) < 3:
            semantic_errors.append("fewer than three valid comparable companies")
        if dimension == "18_trap" and any(
            row.get("status") == "insufficient"
            for row in payload.get("signals") or []
            if isinstance(row, Mapping)
        ):
            semantic_errors.append("one or more trap signals lack evidence")
        return payload, list(dict.fromkeys(semantic_errors))

    def _evidence(
        self,
        *,
        snapshot: dict[str, Any],
        dimension: str,
        raw: dict[str, Any],
        observed_at: datetime,
        pit_safe: bool,
        exchange_timezone: str,
    ) -> tuple[list[EvidenceInput], list[str]]:
        source = (
            snapshot.get("source")
            if isinstance(snapshot.get("source"), Mapping)
            else {}
        )
        provider = str(
            source.get("provider_id") or source.get("source_id") or f"uzi:{dimension}"
        )
        source_timezone = self._declared_timezone(source) or exchange_timezone
        url_rows = list(self._url_records(raw))
        evidence: list[EvidenceInput] = []
        semantic_errors: list[str] = []
        if pit_safe and dimension not in _QUALITATIVE_DIMENSIONS:
            evidence.append(
                EvidenceInput(
                    provider=provider,
                    source_kind="structured",
                    content_hash=canonical_hash({"provider": provider, "raw": raw}),
                    published_at=observed_at,
                    observed_at=observed_at,
                    grade=EvidenceGrade.A,
                    immutable_proof=True,
                    metadata={"dimension": dimension, "native_source": dict(source)},
                )
            )
        for index, row in enumerate(url_rows[:20]):
            published, timestamp_present, time_basis, time_error = self._published_time(
                row,
                fallback=observed_at,
                source_timezone=self._declared_timezone(row) or source_timezone,
            )
            if time_error:
                semantic_errors.append(f"evidence row {index} {time_error}")
                continue
            if published > observed_at:
                semantic_errors.append(
                    "evidence row "
                    f"{index} published_at {published.isoformat()} is after "
                    f"observed_at {observed_at.isoformat()}"
                )
                continue
            # A live URL is not immutable historical proof by itself.  Current
            # official disclosures observed before cutoff qualify as A; generic
            # web/search rows remain C and therefore cannot unlock a Bundle.
            official = self._looks_official(row)
            grade = (
                EvidenceGrade.A
                if pit_safe and official and timestamp_present
                else EvidenceGrade.C
            )
            evidence.append(
                EvidenceInput(
                    provider=str(row.get("provider") or row.get("source") or provider),
                    source_url=str(row.get("url") or row.get("pdf") or row.get("link")),
                    source_kind="official_disclosure" if official else "web",
                    title=str(row.get("title") or row.get("name") or "")[:512] or None,
                    content_hash=canonical_hash({"row": row, "index": index}),
                    published_at=published,
                    observed_at=observed_at,
                    grade=grade,
                    immutable_proof=official,
                    metadata={"dimension": dimension, "published_at_basis": time_basis},
                )
            )
        if not evidence:
            evidence.append(
                EvidenceInput(
                    provider=provider,
                    source_kind="search"
                    if dimension in _QUALITATIVE_DIMENSIONS
                    else "structured",
                    content_hash=canonical_hash({"provider": provider, "raw": raw}),
                    published_at=observed_at,
                    observed_at=observed_at,
                    grade=EvidenceGrade.C,
                    immutable_proof=False,
                    metadata={
                        "dimension": dimension,
                        "reason": "PIT evidence is not independently verifiable",
                    },
                )
            )
        return evidence, semantic_errors

    @classmethod
    def _url_records(cls, value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, Mapping):
            if any(value.get(key) for key in ("url", "pdf", "link")):
                yield dict(value)
            for child in value.values():
                yield from cls._url_records(child)
        elif isinstance(value, list):
            for child in value:
                yield from cls._url_records(child)

    @classmethod
    def _published_time(
        cls,
        row: Mapping[str, Any],
        *,
        fallback: datetime,
        source_timezone: str,
    ) -> tuple[datetime, bool, str, str | None]:
        invalid_fields: list[str] = []
        for key in ("published_at", "publish_time", "date", "公告日期", "报告日期"):
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not _DATE_RE.match(text):
                invalid_fields.append(key)
                continue
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                invalid_fields.append(key)
                continue
            if parsed.tzinfo is None:
                local_timezone, timezone_error = cls._parse_timezone(source_timezone)
                if timezone_error:
                    return fallback, False, "observed_at_fallback", timezone_error
                parsed = parsed.replace(tzinfo=local_timezone)
                basis = f"source_local:{source_timezone}"
            else:
                basis = "timestamp_offset"
            return parsed.astimezone(timezone.utc), True, basis, None
        if invalid_fields:
            fields = ", ".join(dict.fromkeys(invalid_fields))
            return (
                fallback,
                False,
                "observed_at_fallback",
                f"has unparseable publication time field(s): {fields}",
            )
        return fallback, False, "observed_at_fallback", None

    @staticmethod
    def _declared_timezone(value: Mapping[str, Any]) -> str | None:
        for key in _SOURCE_TIMEZONE_FIELDS:
            timezone_value = value.get(key)
            if timezone_value is not None and str(timezone_value).strip():
                return str(timezone_value).strip()
        return None

    @staticmethod
    def _parse_timezone(value: str) -> tuple[tzinfo, str | None]:
        name = str(value or "").strip()
        try:
            return ZoneInfo(name), None
        except (ValueError, ZoneInfoNotFoundError):
            match = _UTC_OFFSET_RE.match(name)
            if match is None:
                return timezone.utc, f"has invalid source timezone: {name or '<empty>'}"
            hours = int(match.group("hours"))
            minutes = int(match.group("minutes") or 0)
            if hours > 23 or minutes > 59:
                return timezone.utc, f"has invalid source timezone: {name}"
            direction = 1 if match.group("sign") == "+" else -1
            return timezone(direction * timedelta(hours=hours, minutes=minutes)), None

    @staticmethod
    def _looks_official(row: Mapping[str, Any]) -> bool:
        url = str(row.get("url") or row.get("pdf") or row.get("link") or "").lower()
        return any(
            token in url
            for token in ("cninfo.com.cn", "sse.com.cn", "szse.cn", "gov.cn")
        )

    @classmethod
    def _items(cls, *values: Any) -> list[Any]:
        output: list[Any] = []
        for value in values:
            if value is None or value == "" or value == "—":
                continue
            if isinstance(value, list):
                output.extend(item for item in value if item not in (None, "", "—"))
            elif isinstance(value, Mapping):
                for key, item in value.items():
                    if isinstance(item, list):
                        output.extend(item)
                    elif item not in (None, "", "—", []):
                        output.append({"category": key, "value": item})
            else:
                output.append(value)
        return output

    @staticmethod
    def _number(*values: Any) -> float | None:
        for value in values:
            if value in (None, "") or isinstance(value, bool):
                continue
            try:
                if isinstance(value, str):
                    value = value.replace(",", "").replace("亿", "")
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _path_present(payload: Mapping[str, Any], path: str) -> bool:
        value: Any = payload
        for token in path.split("."):
            if not isinstance(value, Mapping) or token not in value:
                return False
            value = value[token]
        if value is None or value == "":
            return False
        return not (isinstance(value, (list, dict, tuple)) and not value)


__all__ = ["UziSnapshotAdapter"]
