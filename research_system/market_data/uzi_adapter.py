from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
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
    cutoff_at_for_date,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.historical import capability_for_dimension, historical_provider_revision
from research_system.market_data.native_projection import EMPTY_INPUT_VERSION

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
    """Persist one live UZI result while preserving UZI's native quality gate.

    Point-in-time, evidence and normalized-shape checks are retained as audit
    metadata.  They must not turn a UZI ``full``/``partial`` result into a hard
    report failure because upstream itself does not use those extra contracts.
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
        native_data_present = bool({key: value for key, value in raw.items() if key != "price_evidence"})
        price_evidence = facts.get("price_evidence")
        if dimension == "2_kline" and isinstance(price_evidence, Mapping):
            # Add independent replay facts to the normalized copy, never alter
            # the pinned native envelope or its qfq technical indicators.
            for field in ("corporate_actions", "adjustment_factors"):
                if field in price_evidence:
                    raw[field] = deepcopy(price_evidence[field])
            raw["field_queries"] = {
                **dict(raw.get("field_queries") or {}),
                **deepcopy(dict(price_evidence.get("field_queries") or {})),
            }
        observed_at = self._timestamp(snapshot.get("retrieved_at"))
        local_observed_date = observed_at.astimezone(ZoneInfo(exchange_timezone)).date()
        reconstruction = (
            dict(snapshot.get("historical_reconstruction") or {})
            if isinstance(snapshot.get("historical_reconstruction"), Mapping)
            else {}
        )
        reconstruction_safe = bool(
            reconstruction.get("safe") is True
            and reconstruction.get("requested_as_of_date") == as_of_date.isoformat()
        )
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
            native_data_present and declared_status not in _NATIVE_UNAVAILABLE_STATUSES
        )
        pit_safe = as_of_date == local_observed_date or reconstruction_safe
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
            reconstruction=reconstruction if reconstruction_safe else None,
        )
        semantic_errors.extend(evidence_errors)
        if not raw:
            semantic_errors.extend(self._provider_failure_errors(snapshot))
        semantic_errors = list(dict.fromkeys(semantic_errors))
        status = (
            SnapshotStatus.AVAILABLE if native_available else SnapshotStatus.DATA_GAP
        )
        source = (
            snapshot.get("source")
            if isinstance(snapshot.get("source"), Mapping)
            else {}
        )
        provider_id = str(
            source.get("provider_id") or source.get("source_id") or f"uzi:{dimension}"
        )
        cutoff_at = cutoff_at_for_date(as_of_date, exchange_timezone)
        published_at = cutoff_at if reconstruction_safe else observed_at
        pit_mode = (
            "historical_reconstruction" if reconstruction_safe else "live_observation"
        )
        query_manifest = {
            "requested_as_of_date": as_of_date.isoformat(),
            "observed_local_date": local_observed_date.isoformat(),
            "pit_safe": pit_safe,
            "pit_mode": pit_mode,
            "snapshot_type": snapshot_type,
            "provider": provider_id,
            "native_quality_status": declared_status,
            "degrade_reasons": list(snapshot.get("degrade_reasons") or []),
            "missing_fields": list(snapshot.get("missing_fields") or []),
            "field_coverage": dict(snapshot.get("field_coverage") or {}),
            "source_trace": [
                dict(item)
                for item in list(snapshot.get("source_trace") or [])
                if isinstance(item, Mapping)
            ],
        }
        if dimension == "2_kline":
            adjustment = str(raw.get("ohlcv_adjustment") or "").strip().lower()
            if adjustment:
                query_manifest["coverage"] = {"ohlcv_adjustment": adjustment}
        if reconstruction_safe:
            query_manifest["historical_reconstruction"] = reconstruction
            if revision := historical_provider_revision(dimension):
                query_manifest["historical_provider_revision"] = revision
            capability = capability_for_dimension(dimension)
            if capability.implementation_status == "inputs_only":
                query_manifest["historical_capability"] = {
                    "strategy": capability.strategy,
                    "implementation_status": capability.implementation_status,
                }
            query_manifest.update(
                self._historical_query_manifest(
                    dimension=dimension,
                    provider=provider_id,
                    as_of_date=as_of_date,
                    reconstruction=reconstruction,
                    payload=payload,
                    snapshot=dict(snapshot),
                )
            )
            field_coverage = self._historical_field_coverage(
                dimension=dimension,
                payload=payload,
                raw=raw,
                reconstruction=reconstruction,
            )
            query_manifest["field_coverage"] = field_coverage
            outcome = self._historical_outcome(
                dimension=dimension,
                snapshot=dict(snapshot),
                raw=raw,
                field_coverage=field_coverage,
            )
            query_manifest["collection_outcome"] = outcome
            if outcome == "partial":
                missing = list(field_coverage.get("missing_fields") or [])
                present = list(field_coverage.get("present_fields") or [])
                partial_retryable = self._has_retryable_provider_failure(
                    query_manifest["source_trace"]
                )
                query_manifest["partial_disclosure"] = {
                    "excluded_from_scoring": False,
                    "reason_code": "historical_fields_partial",
                    "missing_fields": missing,
                    "retryable": partial_retryable,
                    "message": (
                        f"已恢复 {len(present)} 个历史字段；仍缺少：{', '.join(missing)}"
                        if missing
                        else "历史来源仅完成部分验证"
                    ),
                }
            elif outcome == "verified_empty":
                query_manifest["verified_empty_disclosure"] = {
                    "reason_code": "historical_bounded_zero",
                    "message": "规定的历史来源已完成有界查询，结果为零条",
                }
                query_manifest["native_empty_observation"] = {
                    "observation_status": "verified_empty",
                    "query_status": {"status": "success", "result_count": 0},
                }
            elif outcome == "retryable_failure":
                failed_sources = [
                    dict(row)
                    for row in query_manifest["source_trace"]
                    if str(row.get("status") or "").strip().lower()
                    in {
                        "provider_unavailable",
                        "rate_limited",
                        "timeout",
                        "parse_failed",
                    }
                ]
                descriptions = []
                for row in failed_sources:
                    provider_name = str(
                        row.get("provider_id")
                        or row.get("source_id")
                        or "unknown_provider"
                    )
                    provider_status = str(row.get("status") or "provider_unavailable")
                    error_type = str(row.get("error_type") or "").strip()
                    descriptions.append(
                        f"{provider_name}: {provider_status}"
                        + (f" ({error_type})" if error_type else "")
                    )
                query_manifest["provider_failure"] = {
                    "reason_code": "historical_provider_failure",
                    "message": "；".join(descriptions)
                    or "历史数据源本次未返回可用结果",
                    "retryable": True,
                }
            elif outcome == "unavailable":
                query_manifest["degradation"] = {
                    "safe_for_report": True,
                    "excluded_from_scoring": True,
                    "reason_code": "historical_reconstruction_incomplete",
                    "message": "历史来源已自动查询，但结果不足以满足严格维度合同",
                    "attempted_reconstruction": True,
                }
                if dimension == "6_fund_holders" and "fund_holders_publication_dates_missing" in (facts.get("data_gap") or []):
                    query_manifest["degradation"].update(
                        reason_code="fund_holders_publication_dates_missing", retryable=False,
                        message="当前基金持股表只有持仓截止日期，没有公开披露日期或历史版本证明；不能把报告期当作当时可见日期，也不能认定历史无持仓。需补充可信历史披露归档。",
                    )
                if dimension == "13_policy" and (snapshot.get("facts") or {}).get("policy_query_status") == "empty":
                    query_manifest["degradation"].update(
                        reason_code="bounded_archive_gap", retryable=False,
                        message="历史公告查询成功，但在实际查询范围内没有匹配政策记录；这不代表没有历史政策。需补充官方政策归档，而非重复同一公告查询。",
                    )
                if dimension in {"8_materials", "9_futures"}:
                    gap_codes = set(raw.get("data_gap") or []) | set(facts.get("data_gap") or [])
                    if "industry_material_mapping_missing" in gap_codes:
                        query_manifest["degradation"].update(
                            reason_code="industry_material_mapping_missing",
                            retryable=False,
                            attempted_reconstruction=False,
                            message="截止日行业尚未匹配到原料/期货合约，未执行价格查询；这不代表历史价格为空。需补齐历史行业依据或合约映射。",
                        )
                    else:
                        query_manifest["degradation"].update(
                            reason_code="historical_price_query_unproven",
                            retryable=False,
                            message="没有可核验的历史合约价格查询记录，不能将通用 no_data 或依赖查询成功视为已核实为空。",
                        )
                if capability.implementation_status == "inputs_only":
                    statuses = {
                        str(row.get("status") or "").lower()
                        for row in query_manifest["source_trace"]
                    }
                    archive_gap = "no_data" in statuses and not statuses.intersection(
                        {"available", "success", "succeeded"}
                    )
                    missing = ", ".join(field_coverage["missing_fields"])
                    query_manifest["degradation"].update(
                        reason_code=(
                            "bounded_archive_gap"
                            if archive_gap
                            else "normalization_or_derivation_missing"
                        ),
                        message=(
                            "当前限量候选中未获得截止日前可用记录，不代表历史没有记录；"
                            if archive_gap
                            else "历史可比公司集合及比较数据重建尚未接通；"
                            if dimension == "4_peers"
                            else "当前历史路径仅支持素材采集，尚未完成目标字段的规范化或派生；"
                        )
                        + f"缺少 {missing}。需完善历史派生/归档适配，原样重试不能保证补齐。",
                        retryable=False,
                        implementation_status="inputs_only",
                        missing_fields=field_coverage["missing_fields"],
                    )
                if dimension == "3_macro" and self._historical_macro_benchmark_only(
                    snapshot=dict(snapshot), raw=raw, field_coverage=field_coverage,
                ):
                    query_manifest["historical_capability"] = {
                        "strategy": capability.strategy,
                        "implementation_status": "inputs_only",
                        "input_collection": "historical_benchmark",
                        "native_derivation": "not_implemented",
                    }
                    query_manifest["degradation"].update(
                        reason_code="normalization_or_derivation_missing",
                        retryable=False, implementation_status="inputs_only",
                        missing_fields=field_coverage["missing_fields"],
                        message="历史指数接口已返回，但当前路径未接通原生宏观六组证据与派生；指数行情不能替代宏观研究，原样重试不能补齐 observations。",
                    )
            if outcome == "verified_empty":
                status = SnapshotStatus.VERIFIED_EMPTY
            elif outcome in {"retryable_failure", "unavailable"}:
                status = SnapshotStatus.DATA_GAP
            else:
                status = SnapshotStatus.AVAILABLE
        elif pit_safe:
            query_manifest.update(
                self._live_query_manifest(
                    dimension=dimension,
                    provider=provider_id,
                    as_of_date=as_of_date,
                    payload=payload,
                    raw=raw,
                    snapshot=dict(snapshot),
                )
            )
        if dimension == "2_kline" and isinstance(price_evidence, Mapping):
            query_manifest["price_evidence"] = {
                key: deepcopy(price_evidence.get(key))
                for key in ("provider_version", "observed_at", "status", "retryable", "error_reason", "field_sources")
            }
            if price_evidence.get("status") != "available" and native_available:
                query_manifest["collection_outcome"] = "partial"
                query_manifest["partial_disclosure"] = {
                    "excluded_from_scoring": False,
                    "reason_code": "price_evidence_unavailable",
                    "retryable": bool(price_evidence.get("retryable")),
                    "message": "UZI 行情可用于报告，但回放所需公司行动/因子证明未完成：" + str(price_evidence.get("error_reason") or "source query unavailable"),
                }
        # A native symbol has lane-specific roots and optional bonus fields.
        # Preserve their presence states outside DimResult.data. Historical
        # dimension reconstruction alone does not prove a current root spill.
        transport = {
            key: deepcopy(dict(facts[key]))
            for key in ("_uzi_root_fields", "_uzi_top_level_fields")
            if isinstance(facts.get(key), Mapping)
        }
        if transport:
            payload.update(transport)
            digest = canonical_hash(transport)
            captured_before_cutoff = observed_at <= cutoff_at
            query_manifest["native_symbol_transport"] = {
                "version": "native-symbol-transport-v1",
                "content_hash": digest,
                "observed_at": observed_at.isoformat(),
                "bonus_status": "fixed_observation" if captured_before_cutoff else "not_fixed_historical",
                "reason": "captured_before_cutoff" if captured_before_cutoff else "dimension_reconstruction_does_not_prove_symbol_bonus",
            }
            if captured_before_cutoff:
                evidence.append(EvidenceInput(
                    provider=provider_id, source_kind="native_symbol_transport",
                    # The immutable payload already stores the complete
                    # transport. Large native fund/NAV lists exceed the inline
                    # evidence column's 256 KiB bound; commit their exact hash
                    # without duplicating or truncating those source bytes.
                    content_hash=digest,
                    published_at=observed_at, observed_at=observed_at,
                    grade=EvidenceGrade.A, immutable_proof=True,
                    metadata={"transport_version": "native-symbol-transport-v1", "purpose": "native_input_transport", "content_storage": "immutable_snapshot_payload"},
                ))
        if status == SnapshotStatus.VERIFIED_EMPTY:
            # Keep actual zero counts, empty lists and source/error envelope.
            # The collection status is not a native fact or a replacement for
            # these bytes. Store them outside the strict empty payload, bound
            # by the immutable snapshot hash and an explicit transport hash.
            native_input = {key: deepcopy(payload[key]) for key in (
                "raw", "_uzi_raw_result", "_uzi_root_fields", "_uzi_top_level_fields",
            ) if key in payload}
            query_manifest["native_empty_input"] = {
                "version": EMPTY_INPUT_VERSION, "payload": native_input,
                "content_hash": canonical_hash(native_input),
            }
            payload = {}
        return SnapshotIngestInput(
            symbol=symbol,
            market=market,
            exchange_timezone=exchange_timezone,
            dimension=dimension,
            contract_version=CONTRACT_VERSION,
            as_of_date=as_of_date,
            status=status,
            payload=payload,
            effective_at=cutoff_at if reconstruction_safe else observed_at,
            published_at=published_at,
            observed_at=observed_at,
            ingested_at=datetime.now(timezone.utc),
            source_version="uzi-3.9.4-b3665e8",
            source_priority=int(source.get("chain_position") or 0) + 10,
            origin="provider",
            query_manifest=query_manifest,
            validation={
                "adapter": "cognitrade_uzi_native_gate_v1",
                "semantic_errors": semantic_errors,
                "native_available": native_available,
                "native_quality_status": declared_status,
                "native_gate_passed": native_available,
                "pit_safe": pit_safe,
                "audit_passed": not semantic_errors and pit_safe,
                "audit_errors": semantic_errors,
            },
            field_sources={
                "$": provider_id,
                **(
                    dict(price_evidence.get("field_sources") or {})
                    if dimension == "2_kline" and isinstance(price_evidence, Mapping)
                    else {}
                ),
            },
            evidence=evidence,
            observation_metadata={
                "native_snapshot_date": snapshot.get("snapshot_date"),
                "native_fallback_applied": bool(snapshot.get("fallback_applied")),
                "collection_outcome": query_manifest.get("collection_outcome"),
            },
            collection_task_id=collection_task_id,
            provider_attempt_id=provider_attempt_id,
        )

    def disclosed_gap(
        self,
        *,
        symbol: str,
        market: str,
        exchange_timezone: str,
        dimension: str,
        as_of_date: date,
        reason_code: str,
        message: str,
        collection_task_id: str | None = None,
        provider_attempt_id: str | None = None,
        attempted_reconstruction: bool = False,
    ) -> SnapshotIngestInput:
        """Persist a safe, scoring-excluded terminal disclosure.

        The record states that data is unavailable; it never substitutes a
        current value for the requested historical date.
        """

        observed_at = datetime.now(timezone.utc)
        provider = f"cognitrade.historical.{dimension}"
        payload = {
            "availability": "historically_unavailable",
            "reason_code": reason_code,
            "message": message,
            "excluded_from_scoring": True,
        }
        return SnapshotIngestInput(
            symbol=symbol,
            market=market,
            exchange_timezone=exchange_timezone,
            dimension=dimension,
            contract_version=CONTRACT_VERSION,
            as_of_date=as_of_date,
            status=SnapshotStatus.DATA_GAP,
            payload=payload,
            effective_at=cutoff_at_for_date(as_of_date, exchange_timezone),
            published_at=observed_at,
            observed_at=observed_at,
            ingested_at=observed_at,
            source_version="cognitrade-historical-disclosure-v1",
            source_priority=10_000,
            origin="derived",
            query_manifest={
                "requested_as_of_date": as_of_date.isoformat(),
                "native_quality_status": "data_gap",
                "pit_safe": True,
                "pit_mode": "historical_unavailable_disclosure",
                "snapshot_type": self.registry.get(dimension).snapshot_type,
                "provider": provider,
                "degradation": {
                    "safe_for_report": True,
                    "excluded_from_scoring": True,
                    "reason_code": reason_code,
                    "message": message,
                    "attempted_reconstruction": attempted_reconstruction,
                },
            },
            validation={
                "adapter": "cognitrade_historical_disclosure_v1",
                "semantic_errors": [message],
                "native_available": False,
                "pit_safe": True,
            },
            field_sources={"$": provider},
            evidence=[
                EvidenceInput(
                    provider=provider,
                    source_kind="availability_disclosure",
                    content_hash=canonical_hash(
                        {
                            "symbol": symbol,
                            "dimension": dimension,
                            "as_of_date": as_of_date,
                            "reason_code": reason_code,
                        }
                    ),
                    published_at=observed_at,
                    observed_at=observed_at,
                    grade=EvidenceGrade.C,
                    immutable_proof=False,
                    metadata={
                        "reason_code": reason_code,
                        "excluded_from_scoring": True,
                    },
                )
            ],
            observation_metadata={"disclosure_only": True},
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
        # Provider recovery deliberately preserves the immutable UZI envelope
        # and writes recovered observations beside it in ``facts``.  Merge
        # only missing/empty public fields here; otherwise the strict adapter
        # would keep validating the failed native value and silently ignore a
        # successful eFinance/BaoStock/direct-HTTP fallback.
        metadata_fields = {"availability", "data_gap", "message"}
        for field, value in facts.items():
            if str(field).startswith("_") or field in metadata_fields:
                continue
            current = raw.get(field)
            current_missing = (
                current is None
                or current == ""
                or (isinstance(current, (list, tuple, dict)) and not current)
            )
            if field not in raw or current_missing:
                raw[field] = value
        return raw

    @staticmethod
    def _provider_failure_errors(snapshot: Mapping[str, Any]) -> list[str]:
        """Project sanitized source-attempt failures into task diagnostics."""

        errors: list[str] = []
        for row in list(snapshot.get("source_trace") or []):
            if not isinstance(row, Mapping):
                continue
            status = str(row.get("status") or "").strip().lower()
            if status in {"available", "success", "succeeded", "skipped"}:
                continue
            provider = str(
                row.get("provider_id") or row.get("source_id") or "unknown_provider"
            ).strip()
            error_type = str(row.get("error_type") or status or "Unavailable").strip()
            errors.append(
                f"provider {provider}: {status or 'unavailable'} ({error_type})"
            )
        return errors

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
        native_result = facts.get("_uzi_raw_result")
        if isinstance(native_result, Mapping):
            # Preserve the exact DimResult envelope (including the distinction
            # between legacy top-level error and pipeline _pipeline.error).
            # UZI self-review reads those locations differently.
            payload["_uzi_raw_result"] = dict(native_result)
        execution = facts.get("_uzi_execution")
        if isinstance(execution, Mapping) and execution.get("input_contract"):
            # Version the collection boundary in audit metadata, not native data.
            payload["_uzi_execution"] = {
                key: execution[key] for key in ("lane", "input_contract") if key in execution
            }
            if isinstance(execution.get("search_budget"), Mapping):
                payload["_uzi_execution"]["search_budget"] = deepcopy(dict(execution["search_budget"]))
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
            full_history = history.get("bars") if isinstance(history, Mapping) else None
            payload.update(
                ohlcv=list(
                    raw.get("ohlcv")
                    or full_history
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
            comparison = raw.get("comparison")
            nested_peers = (
                comparison.get("peers") if isinstance(comparison, Mapping) else None
            )
            # Host peer snapshots wrap rows in comparison; retain the native
            # top-level shapes' precedence and preserve each source row. This
            # alias does not relax the comparable-count or evidence contract.
            peers = list(
                raw.get("peer_table")
                or raw.get("peers")
                or (nested_peers if isinstance(nested_peers, list) else [])
            )
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
                concentration=raw.get("concentration")
                or {
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
                prices=list(
                    raw.get("prices")
                    or raw.get("contract_prices")
                    or raw.get("price_history_60d")
                    or []
                ),
                trend=raw.get("contract_trend"),
            )
        elif dimension == "10_valuation":
            valuation_history = (
                raw.get("history")
                or raw.get("valuation_history")
                or facts.get("valuation_history")
                or raw.get("pe_history")
                or []
            )
            payload.update(
                pe=raw.get("pe"),
                pb=raw.get("pb"),
                history=list(valuation_history)
                if isinstance(valuation_history, (list, tuple))
                else [],
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
            northbound = raw.get("northbound") or raw.get("hsgt") or []
            if isinstance(northbound, Mapping):
                northbound = (
                    northbound.get("flow_history")
                    or northbound.get("history")
                    or northbound.get("rows")
                    or []
                )
            institutions = raw.get("institutions") or raw.get("institution_holders")
            if not institutions and isinstance(
                raw.get("institutional_history"), Mapping
            ):
                history = raw["institutional_history"]
                quarters = list(history.get("quarters") or [])
                fund_values = list(history.get("fund") or [])
                qfii_values = list(history.get("qfii") or [])
                social_security_values = list(history.get("shehui") or [])
                institutions = [
                    {
                        "period": period,
                        "fund": fund_values[index]
                        if index < len(fund_values)
                        else None,
                        "qfii": qfii_values[index]
                        if index < len(qfii_values)
                        else None,
                        "social_security": social_security_values[index]
                        if index < len(social_security_values)
                        else None,
                    }
                    for index, period in enumerate(quarters)
                ]
            events = raw.get("events")
            if not events:
                events = [
                    {"event_type": event_type, **dict(row)}
                    for event_type, rows in (
                        (
                            "block_trade",
                            raw.get("block_trades_recent")
                            or raw.get("block_trades")
                            or [],
                        ),
                        ("unlock", raw.get("unlock_recent") or raw.get("unlock") or []),
                        ("unlock_schedule", raw.get("unlock_schedule") or []),
                    )
                    for row in rows
                    if isinstance(row, Mapping)
                ]
            payload.update(
                money_flow=raw.get("money_flow")
                or raw.get("main_flow")
                or raw.get("recent_flows")
                or raw.get("main_fund_flow_20d"),
                northbound=list(northbound),
                margin=raw.get("margin")
                or raw.get("margin_detail")
                or raw.get("margin_recent"),
                shareholder_counts=raw.get("shareholder_counts")
                or raw.get("holder_counts")
                or raw.get("holder_count_history"),
                institutions=institutions or [],
                events=events or [],
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
                    raw.get("news_samples"),
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
        query_provable_empty_fields = {
            requirement.path
            for requirement in contract.field_query_requirements
            if requirement.allow_empty
        }
        missing = [
            field
            for field in contract.required_fields
            if not self._path_present(payload, field)
            and field not in query_provable_empty_fields
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
        reconstruction: dict[str, Any] | None = None,
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
        if reconstruction is not None:
            reconstructed_publication = cutoff_at_for_date(
                date.fromisoformat(str(reconstruction["requested_as_of_date"])),
                exchange_timezone,
            )
            evidence.append(
                EvidenceInput(
                    provider=provider,
                    source_kind="historical_structured",
                    content_hash=canonical_hash(
                        {
                            "provider": provider,
                            "raw": raw,
                            "reconstruction": reconstruction,
                        }
                    ),
                    published_at=reconstructed_publication,
                    observed_at=observed_at,
                    grade=EvidenceGrade.B,
                    immutable_proof=True,
                    metadata={
                        "dimension": dimension,
                        "reconstruction": reconstruction,
                    },
                )
            )
        elif pit_safe and dimension not in _QUALITATIVE_DIMENSIONS:
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
            if reconstruction is not None:
                # The date-bounded provider response above proves structured
                # facts, not the historical version of every URL it mentions.
                # Preserve these links in raw for audit/discovery only. Actual
                # document evidence must pass ResearchArchiveCollector and is
                # persisted separately with the original bytes and version proof.
                continue
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
                    immutable_proof=False,
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

    @staticmethod
    def _historical_macro_benchmark_only(*, snapshot, raw, field_coverage) -> bool:
        """Only a proven-success benchmark path is a local derivation gap."""
        trace = [row for row in snapshot.get("source_trace") or [] if isinstance(row, Mapping)]
        requested = [row for row in trace if row.get("request_executed") is not False
                     and str(row.get("status") or "").lower() not in {"not_queried", "disabled"}]
        return bool(
            raw.get("benchmark") and not field_coverage.get("present_fields")
            and any(row.get("interface") == "akshare.index_zh_a_hist"
                    and row.get("request_executed") is True for row in requested)
            and all(str(row.get("status") or "").lower() in {"available", "success", "succeeded"}
                    for row in requested)
        )

    @staticmethod
    def _historical_outcome(
        *,
        dimension: str,
        snapshot: dict[str, Any],
        raw: dict[str, Any],
        field_coverage: dict[str, Any],
    ) -> str:
        facts = (
            dict(snapshot.get("facts") or {})
            if isinstance(snapshot.get("facts"), Mapping)
            else {}
        )
        observation_status = str(
            raw.get("observation_status") or facts.get("observation_status") or ""
        ).lower()
        contract = DEFAULT_DIMENSION_REGISTRY.get(dimension)
        price_dimension = dimension in {"8_materials", "9_futures"}
        if (
            observation_status == "verified_empty"
            and contract.permits_verified_empty
            and not price_dimension
        ):
            return "verified_empty"
        statuses = {
            str(row.get("status") or "").strip().lower()
            for row in list(snapshot.get("source_trace") or [])
            if isinstance(row, Mapping)
        }
        if observation_status == "partially_verified_empty":
            return "partial"
        if statuses.intersection(
            {"provider_unavailable", "rate_limited", "timeout", "parse_failed"}
        ) and not field_coverage.get("present_fields"):
            return "retryable_failure"
        if dimension == "13_policy" and facts.get("policy_query_status") == "empty":
            # A successful zero-match archive query is neither an outage nor
            # verified-empty research. Preserve a gap and native empty gate.
            return "unavailable"
        if dimension == "3_macro" and UziSnapshotAdapter._historical_macro_benchmark_only(
            snapshot=snapshot, raw=raw, field_coverage=field_coverage,
        ):
            return "unavailable"
        present = set(field_coverage.get("present_fields") or [])
        recoverable = set(field_coverage.get("recoverable_fields") or [])
        current_only = set(field_coverage.get("current_only_fields") or [])
        if price_dimension:
            price_queries = [
                row
                for row in snapshot.get("source_trace") or []
                if isinstance(row, Mapping)
                and row.get("query_scope") == "futures_prices"
            ]
            failures = statuses.intersection(
                {"provider_unavailable", "rate_limited", "timeout", "parse_failed"}
            )
            if present and failures:
                return "partial"
            if not present:
                # Only completed queries for actual requested contracts prove
                # zero prices. Industry lookups and unexecuted mapping gaps do
                # not. Keep legacy generic no_data as a disclosed gap.
                if (
                    price_queries
                    and all(
                        row.get("request_executed") is True
                        and row.get("bounded_query_completed") is True
                        and row.get("status") == "no_data"
                        and row.get("in_window_count") == 0
                        for row in price_queries
                    )
                    and contract.permits_verified_empty
                ):
                    return "verified_empty"
                return "unavailable"
        if present:
            return (
                "available"
                if recoverable.issubset(present) and not current_only
                else "partial"
            )
        successful = statuses.intersection(
            {"available", "success", "succeeded", "verified_empty", "no_data"}
        )
        if successful and contract.permits_verified_empty:
            return "verified_empty"
        if successful:
            if (
                capability_for_dimension(dimension).implementation_status
                == "inputs_only"
            ):
                # A successful fetch of inputs (or an empty recent-news page)
                # is not a provider outage.  These paths have no completed PIT
                # derivation, so another identical request cannot supply the
                # absent final fields.  Real source failures are handled above.
                return "unavailable"
            return "retryable_failure"
        return "unavailable"

    @staticmethod
    def _has_retryable_provider_failure(trace: Iterable[Mapping[str, Any]]) -> bool:
        return any(
            str(row.get("status") or "").strip().lower()
            in {"provider_unavailable", "rate_limited", "timeout", "parse_failed"}
            for row in trace
        )

    @classmethod
    def _historical_field_coverage(
        cls,
        *,
        dimension: str,
        payload: dict[str, Any],
        raw: dict[str, Any],
        reconstruction: dict[str, Any],
    ) -> dict[str, Any]:
        """Measure PIT fields after normalization, not provider dict truthiness."""

        capability = capability_for_dimension(dimension)
        recoverable = list(
            reconstruction.get("recoverable_fields") or capability.recoverable_fields
        )
        current_only = list(
            reconstruction.get("current_only_fields") or capability.current_only_fields
        )
        aliases: dict[str, tuple[str, ...]] = {
            "market_cap": ("market_cap", "market_cap_raw"),
            "percentiles": ("percentiles", "pe_quantile", "pb_quantile"),
            "records": ("records", "lhb_records", "lhb_statistics"),
            "items": (
                "items",
                "news_samples",
                "platform_snippets",
                "news_multi_source",
            ),
            "classification": ("classification", "industry"),
            "research_and_development": (
                "research_and_development",
                "rd_summary",
            ),
        }

        def value_for(field: str) -> Any:
            for key in aliases.get(field, (field,)):
                if key in payload and cls._meaningful_historical_value(
                    payload.get(key)
                ):
                    return payload.get(key)
                if key in raw and cls._meaningful_historical_value(raw.get(key)):
                    return raw.get(key)
            return None

        present = [field for field in recoverable if value_for(field) is not None]
        return {
            "recoverable_fields": recoverable,
            "current_only_fields": current_only,
            "present_fields": present,
            "missing_fields": [field for field in recoverable if field not in present],
        }

    @classmethod
    def _meaningful_historical_value(cls, value: Any) -> bool:
        if value is None or value == "" or value == "—":
            return False
        if isinstance(value, Mapping):
            return any(
                cls._meaningful_historical_value(item) for item in value.values()
            )
        if isinstance(value, (list, tuple, set)):
            if not value:
                return False
            if all(
                isinstance(item, Mapping)
                and str(item.get("status") or "").strip().lower() == "insufficient"
                for item in value
            ):
                return False
            return any(cls._meaningful_historical_value(item) for item in value)
        return True

    @staticmethod
    def _historical_query_manifest(
        *,
        dimension: str,
        provider: str,
        as_of_date: date,
        reconstruction: dict[str, Any],
        payload: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        start = str(reconstruction.get("window_start") or as_of_date.isoformat())
        end = as_of_date.isoformat()
        paths: tuple[str, ...] = {
            "0_basic": ("price", "market_cap"),
            "1_financials": ("periods",),
            "2_kline": ("ohlcv", "corporate_actions", "adjustment_factors"),
            "3_macro": ("observations",),
            "4_peers": ("peers",),
            "5_chain": ("segments",),
            "6_research": ("reports",),
            "7_industry": ("industry",),
            "8_materials": ("prices",),
            "9_futures": ("prices",),
            "10_valuation": ("history",),
            "11_governance": ("pledges", "ownership_changes"),
            "12_capital_flow": (
                "money_flow",
                "northbound",
                "margin",
                "events",
            ),
            "13_policy": ("policies",),
            "15_events": ("events",),
            "16_lhb": ("records",),
            "17_sentiment": ("items",),
            "18_trap": ("signals",),
            "6_fund_holders": ("funds",),
            "similar_stocks": ("stocks",),
        }.get(dimension, ())
        trace = [
            dict(row)
            for row in list(snapshot.get("source_trace") or [])
            if isinstance(row, Mapping)
        ]
        if dimension in {"8_materials", "9_futures"}:
            # An industry classification query is not a price query. Retain
            # all traces in source_trace, but scope price proof to contracts.
            trace = [row for row in trace if row.get("query_scope") == "futures_prices"]
        successful_statuses = {
            "available",
            "success",
            "succeeded",
            "verified_empty",
            "no_data",
        }
        source_results: list[dict[str, Any]] = []
        for row in trace:
            source_id = str(row.get("provider_id") or row.get("source_id") or provider)
            row_status = str(row.get("status") or "").strip().lower()
            source_results.append(
                {
                    "source": source_id,
                    "status": (
                        "success"
                        if row_status in successful_statuses
                        else row_status or "unknown"
                    ),
                    "result_count": int(
                        row.get("in_window_count", row.get("count")) or 0
                    ),
                    **{
                        key: row[key]
                        for key in (
                            "symbol",
                            "query_window",
                            "request_executed",
                            "bounded_query_completed",
                            "date_filter",
                        )
                        if key in row
                    },
                    **(
                        {"reason_code": row.get("reason_code")}
                        if row.get("reason_code")
                        else {}
                    ),
                }
            )
        required_source_results = [
            row
            for row in source_results
            if str(row.get("status") or "").lower()
            not in {"not_queried", "not_configured", "not_supported", "skipped"}
        ]
        if not source_results:
            source_results = [
                {"source": provider, "status": "unknown", "result_count": 0}
            ]
        field_queries: dict[str, Any] = {}
        for path in paths:
            if dimension == "2_kline" and path in {
                "corporate_actions",
                "adjustment_factors",
            }:
                proof = UziSnapshotAdapter._kline_price_field_query(
                    dict(payload.get("raw") or {}), path
                )
                if proof is not None:
                    field_queries[path] = proof
                # K-line success is not a query of dividends/splits or factors.
                # In particular, adapter-generated [] must not acquire proof.
                continue
            value = payload.get(path)
            count = len(value) if isinstance(value, (list, tuple, dict)) else 0
            field_queries[path] = {
                "query_window": {"start": start, "end": end},
                "required_sources": list(
                    dict.fromkeys(row["source"] for row in required_source_results)
                ),
                "sources": required_source_results,
                "result_count": count,
            }
        result_count = sum(
            len(payload.get(path) or [])
            if isinstance(payload.get(path), (list, tuple, dict))
            else int(payload.get(path) is not None)
            for path in paths
        )
        query_proof = {
            "query_window": {"start": start, "end": end},
            "required_sources": list(
                dict.fromkeys(row["source"] for row in required_source_results)
            ),
            "sources": required_source_results,
            "result_count": result_count,
        }
        if field_queries:
            query_proof["field_queries"] = field_queries
        return query_proof

    def _live_query_manifest(
        self,
        *,
        dimension: str,
        provider: str,
        as_of_date: date,
        payload: dict[str, Any],
        raw: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Describe bounded queries actually represented by a live payload.

        A non-empty normalized collection proves that the pinned collector ran
        and returned rows.  Empty collections are only proven when the native
        collector explicitly recorded a successful/verified-empty query.  This
        distinction prevents an empty Python default from masquerading as a
        successful zero-result source query.
        """

        contract = self.registry.get(dimension)
        if not contract.field_query_requirements:
            return {}
        field_sources = (
            dict(snapshot.get("field_sources") or {})
            if isinstance(snapshot.get("field_sources"), Mapping)
            else {}
        )
        query_status = raw.get("query_status")
        query_status = query_status if isinstance(query_status, Mapping) else {}
        observation_status = str(raw.get("observation_status") or "").lower()
        source_coverage = raw.get("source_coverage")
        source_coverage = (
            source_coverage if isinstance(source_coverage, Mapping) else {}
        )
        default_windows = {
            "2_kline": 550,
            "6_research": 365,
            "11_governance": 365,
            "12_capital_flow": 365,
            "15_events": 180,
            "16_lhb": 30,
            "17_sentiment": 30,
            "19_contests": 30,
        }
        field_queries: dict[str, Any] = {}
        for requirement in contract.field_query_requirements:
            if dimension == "2_kline" and requirement.path in {"corporate_actions", "adjustment_factors"}:
                proof = self._kline_price_field_query(raw, requirement.path)
                if proof is not None:
                    field_queries[requirement.path] = proof
                continue
            value = payload.get(requirement.path)
            count = len(value) if isinstance(value, (list, tuple, dict)) else 0
            status = str(query_status.get("status") or "").strip().lower()
            coverage_key = {
                "pledges": "pledge",
                "ownership_changes": "insider_trades_1y",
            }.get(requirement.path, requirement.path)
            coverage_status = (
                str(source_coverage.get(coverage_key) or "").strip().lower()
            )
            explicitly_verified_empty = (
                dimension == "16_lhb"
                and requirement.path == "records"
                and observation_status in {"verified_empty", "verified_non_empty"}
            )
            query_succeeded = (
                bool(count)
                or status
                in {
                    "success",
                    "succeeded",
                    "available",
                }
                or coverage_status == "available"
                or explicitly_verified_empty
            )
            if not query_succeeded:
                continue
            source_id = str(
                field_sources.get(requirement.path)
                or field_sources.get("$")
                or provider
            ).strip()
            window_days = max(
                int(requirement.minimum_window_days or 0),
                default_windows.get(dimension, 1),
            )
            field_queries[requirement.path] = {
                "query_window": {
                    "start": (as_of_date - timedelta(days=window_days)).isoformat(),
                    "end": as_of_date.isoformat(),
                },
                "required_sources": [source_id],
                "sources": [
                    {
                        "source": source_id,
                        "status": "success",
                        "result_count": count,
                    }
                ],
                "result_count": count,
            }
        return {"field_queries": field_queries} if field_queries else {}

    @staticmethod
    def _kline_price_field_query(raw: dict[str, Any], field: str) -> dict[str, Any] | None:
        """Forward only an actual field-scoped source query, including failures."""
        queries = raw.get("field_queries")
        proof = queries.get(field) if isinstance(queries, Mapping) else None
        if field not in raw or not isinstance(raw[field], list) or not isinstance(proof, Mapping):
            return None
        return {**deepcopy(dict(proof)), "query_scope": "field", "field": field}

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
