from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from domain_packs.finance_ashare.holding_reports import (
    FundHoldingReportError,
    freeze_fund_holdings,
)
from research_system.market_data.contracts import (
    EvidenceGrade,
    EvidenceInput,
    SnapshotIngestInput,
    SnapshotStatus,
    canonical_hash,
    cutoff_at_for_date,
    require_aware_utc,
)
from research_system.market_data.dimension_contracts import DimensionRegistry
from research_system.market_data.fund_holdings_contracts import (
    FUND_HOLDINGS_CONTRACT_VERSION,
    FUND_TOP_HOLDINGS_COUNT,
    FUND_TOP_HOLDINGS_DIMENSION,
    FUND_TOP_HOLDINGS_REGISTRY,
    FundHoldingsLineage,
    FundTopHolding,
    FundTopHoldingsPayload,
    payload_as_json,
    validate_fund_top_holdings_ingest,
)
from research_system.market_data.instruments import (
    AssetType,
    SecurityIdentityError,
    SecurityIdentityResolver,
)


class FundHoldingsAdapterError(ValueError):
    """A malformed adapter call, distinct from an auditable provider data gap."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


class FundTopHoldingsSnapshotAdapter:
    """Convert FundSnapshotSkill output into the strict parent-fund PIT slot.

    The adapter is intentionally network-free.  It accepts either the raw dict
    returned by ``FundSnapshotSkill.run`` or an ArtifactRecord-like
    ``fund_snapshot`` envelope and uses ``freeze_fund_holdings`` for the existing
    direct/ETF-feeder selection semantics.
    """

    def __init__(
        self,
        *,
        identity_resolver: SecurityIdentityResolver | None = None,
        registry: DimensionRegistry = FUND_TOP_HOLDINGS_REGISTRY,
    ) -> None:
        if registry.contract_version != FUND_HOLDINGS_CONTRACT_VERSION:
            raise ValueError("fund holdings adapter requires its independent PIT contract")
        if registry.dimension_ids != (FUND_TOP_HOLDINGS_DIMENSION,):
            raise ValueError("fund holdings adapter requires the one-slot registry")
        self.identity_resolver = identity_resolver or SecurityIdentityResolver()
        self.registry = registry

    def adapt(
        self,
        artifact_or_skill_output: Mapping[str, Any],
        *,
        fund_code: str,
        as_of_date: date,
        exchange_timezone: str = "Asia/Shanghai",
        top_n: int = FUND_TOP_HOLDINGS_COUNT,
        observed_at: datetime | None = None,
        collection_task_id: str | None = None,
        provider_attempt_id: str | None = None,
    ) -> SnapshotIngestInput:
        if int(top_n) != FUND_TOP_HOLDINGS_COUNT:
            raise FundHoldingsAdapterError(
                "fund_top_n_contract_mismatch",
                f"基金持仓 PIT 合同固定要求前 {FUND_TOP_HOLDINGS_COUNT} 只 A 股",
            )
        if exchange_timezone != "Asia/Shanghai":
            raise FundHoldingsAdapterError(
                "fund_timezone_invalid",
                "中国公募基金持仓合同必须使用 Asia/Shanghai",
            )
        resolved_as_of = self._date(as_of_date, field_name="as_of_date")
        envelope, content = self._artifact(artifact_or_skill_output)
        observed = self._observed_at(envelope, content, override=observed_at)
        ingested = max(datetime.now(timezone.utc), observed)
        observed_local_date = observed.astimezone(
            ZoneInfo(exchange_timezone)
        ).date()
        cutoff = (
            observed
            if resolved_as_of == observed_local_date
            else cutoff_at_for_date(resolved_as_of, exchange_timezone)
        )
        normalized_fund_code = self._fund_code(fund_code)
        candidate = self._candidate(content, normalized_fund_code)
        semantic_errors: list[str] = []

        fund_name = str((candidate or {}).get("fund_name") or normalized_fund_code).strip()
        fund_type = str((candidate or {}).get("fund_type") or "").strip() or None
        try:
            fund_identity = self.identity_resolver.resolve_fund(
                normalized_fund_code,
                name=fund_name,
                fund_type=fund_type,
                resolution_source="fund_snapshot_artifact",
            )
        except SecurityIdentityError as exc:
            raise FundHoldingsAdapterError(exc.code, exc.message) from exc

        try:
            # Ask the existing freezer for up to twenty disclosed rows, then
            # select ten supported A-shares.  This allows an unsupported row to
            # be skipped without silently reducing a nominal top-ten report.
            frozen = freeze_fund_holdings(
                [envelope],
                fund_code=normalized_fund_code,
                source_artifact_id=str(envelope.get("artifact_id") or "") or None,
                now=resolved_as_of,
                top_n=20,
            )
        except FundHoldingReportError as exc:
            semantic_errors.append(f"fund_holdings_unavailable:{exc.__class__.__name__}")
            frozen = {
                "source_artifact_id": str(envelope.get("artifact_id") or ""),
                "fund_code": normalized_fund_code,
                "fund_name": fund_name,
                "fund_type": fund_type,
                "holding_source": "direct_equity_disclosure",
                "is_fund_of_funds": False,
                "disclosure_date": None,
                "holdings": [],
                "data_gap": [str(exc)],
            }

        disclosure_date = self._optional_date(frozen.get("disclosure_date"))
        if disclosure_date is None:
            semantic_errors.append("holding_disclosure_date_missing")
        elif disclosure_date > resolved_as_of:
            semantic_errors.append("holding_disclosure_period_after_as_of")

        holding_source = str(
            frozen.get("holding_source") or "direct_equity_disclosure"
        )
        is_fund_of_funds = bool(frozen.get("is_fund_of_funds"))
        parent_allocation = self._parent_allocation(candidate, is_fund_of_funds)
        if is_fund_of_funds and parent_allocation is None:
            semantic_errors.append("target_fund_allocation_weight_missing")

        holdings = self._holdings(
            frozen,
            disclosure_date=disclosure_date,
            holding_source=holding_source,
            parent_allocation=parent_allocation,
            errors=semantic_errors,
        )
        if len(holdings) != FUND_TOP_HOLDINGS_COUNT:
            semantic_errors.append(
                f"supported_a_share_count:{len(holdings)};expected:{FUND_TOP_HOLDINGS_COUNT}"
            )

        source_refs = self._source_refs(envelope, content)
        lineage, visibility_mode = self._lineage(
            source_refs,
            candidate=candidate,
            fund_code=normalized_fund_code,
            holding_source=holding_source,
            disclosure_date=disclosure_date,
            observed_at=observed,
            as_of_date=resolved_as_of,
            cutoff_at=cutoff,
            errors=semantic_errors,
        )
        if resolved_as_of > observed_local_date:
            semantic_errors.append("future_as_of_not_supported")
        availability_at = max(
            (item.published_at for item in lineage),
            default=observed,
        )
        if availability_at > cutoff:
            semantic_errors.append("fund_holdings_not_visible_by_cutoff")

        evidence = self._evidence(
            lineage=lineage,
            source_refs=source_refs,
            envelope=envelope,
            content=content,
            candidate=candidate,
            holdings=holdings,
            observed_at=observed,
            pit_safe=not semantic_errors,
            visibility_mode=visibility_mode,
        )
        query_manifest = self._query_manifest(
            as_of_date=resolved_as_of,
            cutoff_at=cutoff,
            holding_source=holding_source,
            holdings=holdings,
            lineage=lineage,
            visibility_mode=visibility_mode,
            selection_complete=not semantic_errors,
            source_refs=source_refs,
        )

        payload: dict[str, Any] = {
            "fund_code": normalized_fund_code,
            "fund_name": fund_name,
            "fund_type": fund_type,
            "asset_type": fund_identity.asset_type.value,
            "market": fund_identity.market,
            "holding_source": holding_source,
            "is_fund_of_funds": is_fund_of_funds,
            "requested_top_n": FUND_TOP_HOLDINGS_COUNT,
            "analysis_as_of_date": resolved_as_of.isoformat(),
            "cutoff_at": cutoff.isoformat(),
            "disclosure_date": disclosure_date.isoformat() if disclosure_date else None,
            "availability_at": availability_at.isoformat(),
            "parent_allocation_weight_pct": parent_allocation,
            "holdings": [item.model_dump(mode="json") for item in holdings],
            "lineage": [item.model_dump(mode="json") for item in lineage],
            "source_artifact_id": str(frozen.get("source_artifact_id") or "") or None,
        }

        status = SnapshotStatus.DATA_GAP
        if not semantic_errors:
            try:
                strict_payload = FundTopHoldingsPayload.model_validate(payload)
            except ValidationError as exc:
                semantic_errors.append(f"strict_fund_payload_invalid:{exc}")
            else:
                payload = payload_as_json(strict_payload)
                status = SnapshotStatus.AVAILABLE

        effective_at = (
            datetime.combine(disclosure_date, time.min, tzinfo=timezone.utc)
            if disclosure_date is not None
            else observed
        )
        snapshot = SnapshotIngestInput(
            symbol=normalized_fund_code,
            market="CN_FUND",
            exchange_timezone=exchange_timezone,
            dimension=FUND_TOP_HOLDINGS_DIMENSION,
            contract_version=FUND_HOLDINGS_CONTRACT_VERSION,
            as_of_date=resolved_as_of,
            status=status,
            payload=payload,
            effective_at=effective_at,
            published_at=availability_at if status == SnapshotStatus.AVAILABLE else observed,
            observed_at=observed,
            ingested_at=ingested,
            source_version="fund-snapshot-skill-v1",
            source_priority=20,
            origin="provider",
            query_manifest=query_manifest,
            validation={
                "adapter": "cognitrade_fund_top_holdings_pit_v1",
                "semantic_errors": list(dict.fromkeys(semantic_errors)),
                "pit_safe": status == SnapshotStatus.AVAILABLE,
                "visibility_mode": visibility_mode,
                "fund_identity": fund_identity.model_dump(mode="json"),
                "historical_reconstruction": dict(
                    content.get("historical_reconstruction") or {}
                ),
                "provider_errors": list(content.get("provider_errors") or []),
            },
            field_sources={"$": "fund_snapshot_skill"},
            evidence=evidence,
            observation_metadata={
                "source_artifact_id": frozen.get("source_artifact_id"),
                "native_quality_status": content.get("quality_status"),
                "native_data_gap": list(content.get("data_gap") or []),
            },
            collection_task_id=collection_task_id,
            provider_attempt_id=provider_attempt_id,
        )
        if snapshot.status == SnapshotStatus.AVAILABLE:
            # This validator is intentionally stronger than DimensionContract
            # and is also the stable hook that Bundle publish/materialize must
            # call after reconstructing the persisted input.
            validate_fund_top_holdings_ingest(snapshot)
        return snapshot

    def _holdings(
        self,
        frozen: Mapping[str, Any],
        *,
        disclosure_date: date | None,
        holding_source: str,
        parent_allocation: float | None,
        errors: list[str],
    ) -> list[FundTopHolding]:
        rows: list[FundTopHolding] = []
        seen: set[str] = set()
        for raw in list(frozen.get("holdings") or []):
            if not isinstance(raw, Mapping) or raw.get("supported") is not True:
                continue
            try:
                identity = self.identity_resolver.resolve_stock(
                    str(raw.get("symbol") or ""),
                    name=str(raw.get("name") or "") or None,
                )
            except SecurityIdentityError:
                continue
            if identity.asset_type != AssetType.STOCK or identity.market != "CN_EQUITY":
                continue
            if identity.symbol in seen:
                continue
            seen.add(identity.symbol)
            effective_weight = self._number(raw.get("fund_weight_pct"))
            disclosed_weight = self._number(
                raw.get("target_security_weight_pct")
                if holding_source == "target_etf_lookthrough"
                else raw.get("fund_weight_pct")
            )
            if effective_weight is None or disclosed_weight is None:
                errors.append(f"holding_weight_missing:{identity.symbol}")
                continue
            if disclosure_date is None:
                continue
            try:
                rows.append(
                    FundTopHolding(
                        rank=len(rows) + 1,
                        source_rank=max(1, int(raw.get("rank") or len(rows) + 1)),
                        symbol=identity.symbol,
                        code=identity.code,
                        name=str(raw.get("name") or identity.name or identity.code).strip(),
                        exchange=identity.exchange,
                        fund_weight_pct=effective_weight,
                        disclosed_weight_pct=disclosed_weight,
                        holding_source=holding_source,
                        disclosure_date=disclosure_date,
                    )
                )
            except (TypeError, ValueError) as exc:
                errors.append(f"holding_contract_invalid:{identity.symbol}:{exc}")
            if len(rows) == FUND_TOP_HOLDINGS_COUNT:
                break
        if parent_allocation is not None:
            for row in rows:
                expected = round(row.disclosed_weight_pct * parent_allocation / 100.0, 6)
                if abs(row.fund_weight_pct - expected) > 0.000001:
                    errors.append(f"lookthrough_weight_mismatch:{row.symbol}")
        return rows

    def _lineage(
        self,
        source_refs: list[dict[str, Any]],
        *,
        candidate: Mapping[str, Any] | None,
        fund_code: str,
        holding_source: str,
        disclosure_date: date | None,
        observed_at: datetime,
        as_of_date: date,
        cutoff_at: datetime,
        errors: list[str],
    ) -> tuple[list[FundHoldingsLineage], str]:
        current_observation = (
            observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date() == as_of_date
            and observed_at <= cutoff_at
        )
        roles = (
            ("parent_target_allocation", "fund_target_disclosure"),
            ("target_holdings", "fund_target_etf_holdings"),
        ) if holding_source == "target_etf_lookthrough" else (
            ("parent_holdings", "fund_direct_equity_holdings"),
        )
        lineage: list[FundHoldingsLineage] = []
        historical_proof = True
        for role, source_type in roles:
            ref = next(
                (row for row in source_refs if str(row.get("type") or "") == source_type),
                None,
            )
            if ref is None:
                errors.append(f"required_lineage_source_missing:{source_type}")
                historical_proof = False
                continue
            source_id = str(
                ref.get("id") or ref.get("uri") or ref.get("locator") or ""
            ).strip()
            provider = str(ref.get("provider") or "fund_snapshot_skill").strip()
            if not source_id or not provider:
                errors.append(f"lineage_source_identity_missing:{source_type}")
                historical_proof = False
                continue
            instrument_code = fund_code
            period_end = disclosure_date
            if role == "parent_target_allocation":
                period_end = self._optional_date((candidate or {}).get("target_fund_period"))
            elif role == "target_holdings":
                target_rows = list((candidate or {}).get("target_fund_holdings") or [])
                target = target_rows[0] if target_rows and isinstance(target_rows[0], Mapping) else {}
                instrument_code = self._fund_code(target.get("code")) if target.get("code") else ""
            if period_end is None or not instrument_code:
                errors.append(f"lineage_period_or_instrument_missing:{role}")
                historical_proof = False
                continue

            explicit_publication = self._source_publication(ref, candidate, role=role)
            immutable = self._immutable_source(ref)
            first_seen = self._timestamp(
                ref.get("first_seen_at") or ref.get("retrieved_at") or ref.get("observed_at")
            )
            if current_observation:
                published = observed_at
                basis = "observed_at_current_day"
            elif explicit_publication is not None and immutable:
                published = explicit_publication
                basis = "provider_published_at"
            elif first_seen is not None and immutable:
                published = first_seen
                basis = "immutable_first_seen_at"
            else:
                errors.append(f"historical_lineage_proof_missing:{role}")
                historical_proof = False
                continue
            if published > cutoff_at:
                errors.append(f"lineage_published_after_cutoff:{role}")
                historical_proof = False
            lineage.append(
                FundHoldingsLineage(
                    role=role,
                    instrument_code=instrument_code,
                    period_end=period_end,
                    published_at=published,
                    source_id=source_id,
                    provider=provider,
                    publication_time_basis=basis,
                )
            )
        visibility_mode = (
            "observed_by_cutoff"
            if current_observation
            else "immutable_published_by_cutoff"
        )
        if not current_observation and not historical_proof:
            errors.append("historical_pit_reconstruction_not_proven")
        return lineage, visibility_mode

    @staticmethod
    def _evidence(
        *,
        lineage: list[FundHoldingsLineage],
        source_refs: list[dict[str, Any]],
        envelope: Mapping[str, Any],
        content: Mapping[str, Any],
        candidate: Mapping[str, Any] | None,
        holdings: list[FundTopHolding],
        observed_at: datetime,
        pit_safe: bool,
        visibility_mode: str,
    ) -> list[EvidenceInput]:
        rows: list[EvidenceInput] = []
        grade = (
            EvidenceGrade.A
            if pit_safe and visibility_mode == "observed_by_cutoff"
            else EvidenceGrade.B
            if pit_safe
            else EvidenceGrade.C
        )
        for item in lineage:
            source = next(
                (
                    ref
                    for ref in source_refs
                    if str(
                        ref.get("id") or ref.get("uri") or ref.get("locator") or ""
                    ).strip()
                    == item.source_id
                ),
                {},
            )
            normalized_holdings_binding_hash = canonical_hash(
                {
                    "lineage": item.model_dump(mode="json"),
                    "holdings": [row.model_dump(mode="json") for row in holdings],
                }
            )
            native_pdf = FundTopHoldingsSnapshotAdapter._immutable_source(source)
            content_hash = (
                str(source.get("content_hash") or "").strip().lower()
                if native_pdf
                else canonical_hash(
                    {
                        "source_id": item.source_id,
                        "role": item.role,
                        "candidate": dict(candidate or {}),
                        "artifact_content_hash": envelope.get("content_hash"),
                        "content": dict(content),
                    }
                )
            )
            content_ref = (
                str(source.get("content_ref") or "").strip() if native_pdf else None
            )
            rows.append(
                EvidenceInput(
                    provider=item.provider,
                    source_url=content_ref,
                    source_kind="official_disclosure",
                    title=str(source.get("title") or source.get("report_id") or "").strip()
                    or None,
                    mime_type="application/pdf" if native_pdf else None,
                    content_hash=content_hash,
                    content_ref=content_ref,
                    published_at=item.published_at,
                    observed_at=max(observed_at, item.published_at),
                    grade=grade,
                    immutable_proof=grade in {EvidenceGrade.A, EvidenceGrade.B},
                    metadata={
                        "source_id": item.source_id,
                        "role": item.role,
                        "instrument_code": item.instrument_code,
                        "period_end": item.period_end.isoformat(),
                        "publication_time_basis": item.publication_time_basis,
                        "field_paths": ["holdings", "lineage"],
                        "immutable_content": True,
                        "normalized_holdings_binding_hash": normalized_holdings_binding_hash,
                    },
                )
            )
        if rows:
            return rows
        return [
            EvidenceInput(
                provider="fund_snapshot_skill",
                source_kind="structured",
                content_hash=canonical_hash(dict(content)),
                published_at=observed_at,
                observed_at=observed_at,
                grade=EvidenceGrade.C,
                immutable_proof=False,
                metadata={
                    "reason": "required holdings disclosure lineage is unavailable",
                    "field_paths": ["holdings", "lineage"],
                    "immutable_content": True,
                },
            )
        ]

    @staticmethod
    def _query_manifest(
        *,
        as_of_date: date,
        cutoff_at: datetime,
        holding_source: str,
        holdings: list[FundTopHolding],
        lineage: list[FundHoldingsLineage],
        visibility_mode: str,
        selection_complete: bool,
        source_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        source_ids = [item.source_id for item in lineage]
        window = {"start": as_of_date.isoformat(), "end": as_of_date.isoformat()}
        source_results = [
            {"source": source_id, "status": "success", "result_count": len(holdings)}
            for source_id in source_ids
        ]
        lineage_results = [
            {"source": source_id, "status": "success", "result_count": 1}
            for source_id in source_ids
        ]
        return {
            "requested_as_of_date": as_of_date.isoformat(),
            "field_queries": {
                "holdings": {
                    "query_window": window,
                    "required_sources": source_ids,
                    "sources": source_results,
                    "result_count": len(holdings),
                },
                "lineage": {
                    "query_window": window,
                    "required_sources": source_ids,
                    "sources": lineage_results,
                    "result_count": len(lineage),
                },
            },
            "coverage": {
                "pit_visibility": visibility_mode,
                "asset_contract": "fund_top_holdings",
                "selection_rule": "latest_disclosure_published_by_cutoff",
                "holding_source": holding_source,
            },
            "selection": {
                "cutoff_at": cutoff_at.isoformat(),
                "complete": selection_complete,
                "selected_source_ids": source_ids,
                "candidates": [
                    {
                        "source_id": item.source_id,
                        "role": item.role,
                        "period_end": item.period_end.isoformat(),
                        "published_at": item.published_at.isoformat(),
                        "selected": True,
                        "reason": "latest_eligible_disclosure",
                    }
                    for item in lineage
                ],
            },
            "native_source_refs": source_refs,
        }

    @staticmethod
    def _artifact(value: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = dict(value)
        if str(raw.get("artifact_type") or "") == "fund_snapshot":
            content = raw.get("content")
            if not isinstance(content, Mapping):
                raise FundHoldingsAdapterError(
                    "fund_snapshot_content_invalid",
                    "fund_snapshot artifact 缺少对象 content",
                )
            return raw, dict(content)
        if not isinstance(raw.get("funds"), list):
            raise FundHoldingsAdapterError(
                "fund_snapshot_output_invalid",
                "输入不是 FundSnapshotSkill 输出或 fund_snapshot artifact",
            )
        content_hash = canonical_hash(raw)
        envelope = {
            "artifact_id": f"fund-skill-{content_hash[:26]}",
            "artifact_type": "fund_snapshot",
            "content": raw,
            "content_hash": content_hash,
            "source_refs": list(raw.get("source_refs") or []),
            "created_at": raw.get("retrieved_at"),
        }
        return envelope, raw

    @staticmethod
    def _candidate(content: Mapping[str, Any], fund_code: str) -> dict[str, Any] | None:
        for row in list(content.get("funds") or []):
            if not isinstance(row, Mapping):
                continue
            code = str(row.get("fund_code") or "").strip().split(".", 1)[0].zfill(6)
            if code == fund_code:
                return dict(row)
        return None

    @staticmethod
    def _source_refs(
        envelope: Mapping[str, Any], content: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for value in [
            *list(envelope.get("source_refs") or []),
            *list(content.get("source_refs") or []),
        ]:
            if not isinstance(value, Mapping):
                continue
            row = dict(value)
            key = (
                str(row.get("type") or ""),
                str(row.get("id") or row.get("uri") or row.get("locator") or ""),
            )
            if not all(key) or key in seen:
                continue
            seen.add(key)
            rows.append(row)
        return rows

    @classmethod
    def _source_publication(
        cls,
        source: Mapping[str, Any],
        candidate: Mapping[str, Any] | None,
        *,
        role: str,
    ) -> datetime | None:
        values = [
            source.get("published_at"),
            source.get("publication_time"),
            source.get("公告日期"),
        ]
        if role == "parent_target_allocation":
            values.append((candidate or {}).get("target_fund_published_at"))
        elif role == "target_holdings":
            values.append((candidate or {}).get("lookthrough_published_at"))
        else:
            values.append((candidate or {}).get("holdings_published_at"))
        return next((parsed for value in values if (parsed := cls._timestamp(value))), None)

    @staticmethod
    def _immutable_source(source: Mapping[str, Any]) -> bool:
        content_hash = str(source.get("content_hash") or "").strip()
        content_ref = str(source.get("content_ref") or "").strip()
        source_id = str(
            source.get("id") or source.get("uri") or source.get("locator") or ""
        ).strip()
        return bool(
            source.get("immutable_proof") is True
            and re.fullmatch(r"[0-9a-fA-F]{64}", content_hash)
            and source_id == content_ref
            and re.fullmatch(
                r"https://pdf\.dfcfw\.com/pdf/H2_[A-Za-z0-9_-]+_1\.pdf",
                content_ref,
            )
        )

    @staticmethod
    def _parent_allocation(
        candidate: Mapping[str, Any] | None,
        is_fund_of_funds: bool,
    ) -> float | None:
        if not is_fund_of_funds:
            return None
        rows = list((candidate or {}).get("target_fund_holdings") or [])
        first = rows[0] if rows and isinstance(rows[0], Mapping) else {}
        return FundTopHoldingsSnapshotAdapter._number(first.get("weight_pct"))

    @classmethod
    def _observed_at(
        cls,
        envelope: Mapping[str, Any],
        content: Mapping[str, Any],
        *,
        override: datetime | None,
    ) -> datetime:
        if override is not None:
            return require_aware_utc(override, field_name="observed_at")
        value = envelope.get("created_at") or content.get("retrieved_at")
        parsed = cls._timestamp(value)
        return parsed or datetime.now(timezone.utc)

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            resolved = value
        elif isinstance(value, date):
            resolved = datetime.combine(value, time.max, tzinfo=ZoneInfo("Asia/Shanghai"))
        else:
            text = str(value).strip()
            try:
                if len(text) == 10:
                    resolved = datetime.combine(
                        date.fromisoformat(text),
                        time.max,
                        tzinfo=ZoneInfo("Asia/Shanghai"),
                    )
                else:
                    resolved = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        if resolved.tzinfo is None or resolved.utcoffset() is None:
            return None
        return resolved.astimezone(timezone.utc)

    @staticmethod
    def _fund_code(value: Any) -> str:
        raw = str(value or "").strip().upper().split(".", 1)[0]
        if not (len(raw) == 6 and raw.isdigit()):
            raise FundHoldingsAdapterError(
                "fund_code_invalid",
                f"基金代码必须为六位数字：{value}",
            )
        return raw

    @staticmethod
    def _date(value: Any, *, field_name: str) -> date:
        resolved = FundTopHoldingsSnapshotAdapter._optional_date(value)
        if resolved is None:
            raise FundHoldingsAdapterError(
                f"{field_name}_invalid",
                f"{field_name} 必须是有效日期",
            )
        return resolved

    @staticmethod
    def _optional_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value is None or value == "":
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or value == "" or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


# Short alias for callers that model the slot as a disclosure rather than a
# report child fan-out.  The canonical class name remains explicit above.
FundHoldingsSnapshotAdapter = FundTopHoldingsSnapshotAdapter


__all__ = [
    "FundHoldingsAdapterError",
    "FundHoldingsSnapshotAdapter",
    "FundTopHoldingsSnapshotAdapter",
]
