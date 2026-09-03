from __future__ import annotations

"""Freeze immutable market-data inputs before a formal report is queued.

The market-data package owns acquisition and Bundle publication.  This module is
the deliberately small boundary consumed by the Agent OS/report coordinators:
it asks an injected gateway to persist a report manifest, normalises the result,
and projects it into the slots understood by the finance Domain Pack.

No function in this module is allowed to collect data.  A missing, incomplete or
invalid Bundle is a hard preflight failure.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from threading import RLock
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from research_system.market_data.contracts import is_supported_uzi_contract_version
from research_system.schemas.protocol import RequestEnvelope

FORMAL_REPORT_INTENTS = frozenset(
    {
        "stock_check",
        "stock_compare",
        "uzi_analyze_stock",
        "uzi_portfolio_batch",
        "quick_scan",
        "uzi_dcf",
        "uzi_panel_only",
        "uzi_scan_trap",
        "uzi_segmental_model",
        "uzi_comps",
        "uzi_lbo",
        "uzi_initiate",
        "uzi_earnings",
        "uzi_catalysts",
        "uzi_thesis",
        "uzi_screen",
        "uzi_dd",
        "uzi_ic_memo",
        "uzi_ai_readiness",
        "uzi_earnings_preview",
        "uzi_model_update",
        "uzi_three_statement_method",
        "uzi_accretion_dilution_method",
        "uzi_morning_note_method",
        "uzi_sector_review_method",
        "uzi_porter_bcg_method",
        "uzi_unit_economics_method",
        "uzi_value_creation_plan_method",
    }
)
_PROFILE_REQUIRED_INTENTS = frozenset(
    {"stock_check", "stock_compare", "uzi_analyze_stock", "uzi_portfolio_batch"}
)

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_PASSING_STATUSES = frozenset({"available", "verified_empty"})


class ReportInputError(ValueError):
    """A formal report cannot obtain a complete immutable input manifest."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class ReportInputGateway(Protocol):
    """Persistence boundary implemented by the market-data Bundle service."""

    def freeze_report_manifest(
        self,
        *,
        symbols: Sequence[str],
        as_of_date: date,
        tenant_id: str | None,
        user_id: str | None,
        workflow_pins: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Persist and return one fully materialised immutable manifest."""

    def load_frozen_report_manifest(
        self,
        *,
        manifest_id: str,
        manifest_hash: str,
        tenant_id: str | None,
        user_id: str | None,
    ) -> Mapping[str, Any]:
        """Reload one exact persisted manifest without resolving newer Bundles."""


class DatabaseReportInputGateway:
    """Freeze report manifests from already-published database Bundles.

    This adapter intentionally uses ``latest_bundle`` rather than publishing or
    collecting during report preflight.  Bundle publication belongs to the data
    workflow; a missing/invalidated Bundle is therefore a hard report failure.
    """

    def __init__(
        self,
        database,
        *,
        default_market: str = "CN_EQUITY",
        default_exchange_timezone: str = "Asia/Shanghai",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.default_market = str(default_market).strip().upper() or "CN_EQUITY"
        self.default_exchange_timezone = (
            str(default_exchange_timezone).strip() or "Asia/Shanghai"
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._freeze_lock = RLock()

    def freeze_report_manifest(
        self,
        *,
        symbols: Sequence[str],
        as_of_date: date,
        tenant_id: str | None,
        user_id: str | None,
        workflow_pins: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        # The unique owner/hash constraint is the final DB guard.  Serialising
        # the local read-then-insert path also avoids needless IntegrityError
        # retries when two report workers freeze the same input concurrently.
        with self._freeze_lock:
            return self._freeze_report_manifest(
                symbols=symbols,
                as_of_date=as_of_date,
                tenant_id=tenant_id,
                user_id=user_id,
                workflow_pins=workflow_pins,
                context=context,
            )

    def freeze_fund_report_manifest(
        self,
        *,
        fund_code: str,
        as_of_date: date,
        tenant_id: str | None,
        user_id: str | None,
        workflow_pins: Mapping[str, Any],
        context: Mapping[str, Any],
        fund_bundle_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Freeze the exact ten-stock portfolio selected by one fund Bundle.

        The parent ``CN_FUND`` Bundle is the authority for membership, order,
        weights and disclosure lineage.  Caller-provided holdings are never
        used to choose the portfolio, and this path deliberately performs no
        acquisition when the parent or any stock Bundle is missing.
        """

        with self._freeze_lock:
            parent, payload, parent_item = self._resolve_fund_report_parent(
                fund_code=fund_code,
                as_of_date=as_of_date,
                fund_bundle_id=fund_bundle_id,
            )
            holding_rows = [item.model_dump(mode="json") for item in payload.holdings]
            symbols = [str(item["symbol"]) for item in holding_rows]
            fund_context = dict(context or {})
            fund_context.update(
                {
                    "report_kind": "fund_top_holdings",
                    "markets": {symbol: "CN_EQUITY" for symbol in symbols},
                    "weights": {
                        str(item["symbol"]): float(item["fund_weight_pct"])
                        for item in holding_rows
                    },
                    "item_context": {
                        str(item["symbol"]): {
                            "fund_code": payload.fund_code,
                            "fund_name": payload.fund_name,
                            "holding_name": item["name"],
                        }
                        for item in holding_rows
                    },
                    "bundle_dependencies": [
                        {
                            "dependency_kind": "fund_top_holdings",
                            "bundle_id": parent.id,
                            "bundle_manifest_hash": parent.manifest_hash,
                            "snapshot_id": parent_item.snapshot_id,
                            "snapshot_content_hash": parent_item.snapshot_content_hash,
                        }
                    ],
                }
            )
            return self._freeze_report_manifest(
                symbols=symbols,
                as_of_date=as_of_date,
                tenant_id=tenant_id,
                user_id=user_id,
                workflow_pins=workflow_pins,
                context=fund_context,
            )

    def _resolve_fund_report_parent(
        self,
        *,
        fund_code: str,
        as_of_date: date,
        fund_bundle_id: str | None,
    ):
        from research_system.market_data.fund_holdings_contracts import (
            FUND_HOLDINGS_CONTRACT_VERSION,
        )
        from research_system.market_data.repository import MarketDataRepository

        normalized_code = _symbol_key(fund_code)
        if not re.fullmatch(r"\d{6}", normalized_code):
            raise ReportInputError(
                "fund_code_invalid",
                "基金持仓报告必须指定六位基金代码",
            )
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            bundle = None
            requested_bundle_id = str(fund_bundle_id or "").strip()
            if requested_bundle_id:
                bundle = repository.get_bundle(requested_bundle_id)
            else:
                bundle = next(
                    (
                        candidate
                        for candidate in repository.list_bundles(
                            symbol=normalized_code,
                            market="CN_FUND",
                            as_of_date=as_of_date,
                            include_invalidated=False,
                            limit=100,
                        )
                        if candidate.contract_version
                        == FUND_HOLDINGS_CONTRACT_VERSION
                        and candidate.status == "published"
                    ),
                    None,
                )
            if bundle is None:
                raise ReportInputError(
                    "fund_holding_bundle_missing",
                    "缺少已发布的基金持仓 Bundle；请先显式采集基金持仓",
                )
            if bundle.status != "published" or bundle.invalidated_at is not None:
                raise ReportInputError(
                    "fund_holding_bundle_invalidated",
                    "基金持仓 Bundle 已失效",
                )
            if (
                bundle.market != "CN_FUND"
                or bundle.contract_version != FUND_HOLDINGS_CONTRACT_VERSION
                or bundle.as_of_date != as_of_date
                or _symbol_key(bundle.symbol) != normalized_code
            ):
                raise ReportInputError(
                    "fund_holding_bundle_scope_mismatch",
                    "基金持仓 Bundle 与基金代码、市场、日期或契约不一致",
                )
            _, payload = _materialize_fund_dependency(repository, bundle)
            items = repository.list_bundle_items(bundle.id)
            if len(items) != 1:
                raise ReportInputError(
                    "fund_holding_bundle_slot_mismatch",
                    "基金持仓 Bundle 必须且只能包含一个持仓披露槽",
                )
            # ORM rows are detached after the session closes; the configured
            # strategy database uses expire_on_commit=False, so returning the
            # immutable identifiers here is safe and avoids holding a session
            # open while the ten stock Bundles are materialised.
            return bundle, payload, items[0]

    def load_frozen_report_manifest(
        self,
        *,
        manifest_id: str,
        manifest_hash: str,
        tenant_id: str | None,
        user_id: str | None,
    ) -> Mapping[str, Any]:
        return self._load_frozen_report_manifest(
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            tenant_id=tenant_id,
            user_id=user_id,
            symbols=None,
        )

    def load_frozen_report_manifest_items(
        self,
        *,
        manifest_id: str,
        manifest_hash: str,
        tenant_id: str | None,
        user_id: str | None,
        symbols: Sequence[str],
    ) -> Mapping[str, Any]:
        """Verify a parent manifest while materialising only selected Bundles.

        A fund batch stores one ten-stock manifest, but each child Agent consumes
        exactly one stock Bundle. Reloading all 220 snapshots for every child
        made the trust check quadratic. This scoped path still verifies the
        parent hash, every referenced Bundle row and the fund dependency, then
        performs the expensive snapshot/conflict validation only for the
        requested stock items.
        """

        return self._load_frozen_report_manifest(
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            tenant_id=tenant_id,
            user_id=user_id,
            symbols=_unique_symbols(symbols),
        )

    def _load_frozen_report_manifest(
        self,
        *,
        manifest_id: str,
        manifest_hash: str,
        tenant_id: str | None,
        user_id: str | None,
        symbols: Sequence[str] | None,
    ) -> Mapping[str, Any]:
        """Re-materialise a pinned report input from the database only.

        This is the trust boundary for resumed/internal ``bundle_only``
        requests.  Caller-supplied snapshots are never treated as proof that a
        manifest exists or still points at valid immutable Bundles.
        """

        from research_system.market_data.bundles import BundleMaterializer
        from research_system.market_data.contracts import (
            canonical_hash,
            is_supported_uzi_contract_version,
        )
        from research_system.market_data.repository import MarketDataRepository

        owner_tenant = str(tenant_id or "local")
        owner_user = str(user_id or "local_development")
        expected_hash = str(manifest_hash or "").strip().lower()
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            manifest = repository.get_report_manifest(str(manifest_id or "").strip())
            if (
                manifest is None
                or manifest.tenant_id != owner_tenant
                or manifest.user_id != owner_user
            ):
                raise ReportInputError(
                    "report_manifest_not_found",
                    "冻结的报告输入不存在或不属于当前主体",
                )
            if manifest.manifest_hash != expected_hash:
                raise ReportInputError(
                    "report_manifest_hash_mismatch",
                    "请求中的报告 manifest hash 与数据库记录不一致",
                )
            if manifest.status != "frozen" or manifest.invalidated_at is not None:
                raise ReportInputError(
                    "report_manifest_invalidated",
                    "冻结的报告输入已失效",
                )
            if not is_supported_uzi_contract_version(manifest.contract_version):
                raise ReportInputError(
                    "report_manifest_contract_mismatch",
                    "冻结的报告输入契约版本已不受当前运行时支持",
                )
            if manifest.as_of_date is None:
                raise ReportInputError(
                    "report_manifest_as_of_invalid",
                    "数据库中的报告 manifest 缺少日期",
                )

            stored_items = repository.list_report_manifest_items(manifest.id)
            if not stored_items:
                raise ReportInputError(
                    "report_manifest_empty",
                    "数据库中的报告 manifest 没有 Bundle",
                )
            requested = {_symbol_key(symbol) for symbol in symbols or []}
            stored_rows: list[tuple[Any, Any, dict[str, Any]]] = []
            for item in stored_items:
                bundle = repository.get_bundle(item.bundle_id)
                if (
                    bundle is None
                    or bundle.status != "published"
                    or bundle.invalidated_at is not None
                ):
                    raise ReportInputError(
                        "published_bundle_invalidated",
                        f"{item.symbol} 的冻结 Bundle 已失效",
                    )
                if bundle.manifest_hash != item.bundle_manifest_hash:
                    raise ReportInputError(
                        "report_bundle_hash_mismatch",
                        f"{item.symbol} 的冻结 Bundle hash 与 manifest item 不一致",
                    )
                stored_rows.append(
                    (
                        item,
                        bundle,
                        {
                            "ordinal": item.ordinal,
                            "symbol": item.symbol,
                            "market": item.market,
                            "bundle_id": bundle.id,
                            "bundle_manifest_hash": bundle.manifest_hash,
                            "weight": item.weight,
                            "context": dict(item.context_json or {}),
                        },
                    )
                )

            manifest_items = [row for _, _, row in stored_rows]

            frozen_dependencies = _load_manifest_dependencies(
                repository=repository,
                manifest=manifest,
                stock_items=manifest_items,
            )

            hash_payload = {
                "contract_version": manifest.contract_version,
                "report_kind": manifest.report_kind,
                "as_of_date": manifest.as_of_date.isoformat(),
                "evaluation_at": _aware_utc(manifest.evaluation_at).isoformat(),
                "workflow_pins": dict(manifest.workflow_pins_json or {}),
                "items": [
                    {
                        "ordinal": item["ordinal"],
                        "symbol": item["symbol"],
                        "market": item["market"],
                        "bundle_id": item["bundle_id"],
                        "bundle_manifest_hash": item["bundle_manifest_hash"],
                        "weight": item["weight"],
                        "context": item["context"],
                    }
                    for item in manifest_items
                ],
            }
            if frozen_dependencies:
                # Keep the v1 stock-only hash byte-for-byte stable.  The new
                # key exists only when a parent fund disclosure selected the
                # stock portfolio.
                hash_payload["dependencies"] = [
                    _dependency_hash_row(item) for item in frozen_dependencies
                ]
            if canonical_hash(hash_payload) != manifest.manifest_hash:
                raise ReportInputError(
                    "report_manifest_integrity_mismatch",
                    "数据库中的报告 manifest 未通过内容哈希校验",
                )

            selected_rows = [
                (item, bundle, row)
                for item, bundle, row in stored_rows
                if not requested or _symbol_key(item.symbol) in requested
            ]
            if requested and requested != {
                _symbol_key(item.symbol) for item, _, _ in selected_rows
            }:
                raise ReportInputError(
                    "report_manifest_item_missing",
                    "冻结 manifest 中没有所选标的",
                )
            materializer = BundleMaterializer(repository)
            frozen_items: list[dict[str, Any]] = []
            for item, bundle, manifest_item in selected_rows:
                materialized = materializer.materialize(bundle.id)
                bundle_items = repository.list_bundle_items(bundle.id)
                bundle_coverage = dict(bundle.coverage_json or {})
                frozen_items.append(
                    {
                        **manifest_item,
                        "bundle": {
                            "bundle_id": bundle.id,
                            "symbol": bundle.symbol,
                            "market": bundle.market,
                            "as_of_date": bundle.as_of_date.isoformat(),
                            "cutoff_at": _aware_utc(bundle.cutoff_at).isoformat(),
                            "contract_version": bundle.contract_version,
                            "status": bundle.status,
                            "report_ready": bool(bundle_coverage.get("report_ready")),
                            "report_generatable": bool(
                                bundle_coverage.get("report_generatable")
                            ),
                            "quality": bundle_coverage.get("quality") or "strict",
                            "gate_policy": bundle_coverage.get("gate_policy"),
                            "uzi_integrity": dict(
                                bundle_coverage.get("uzi_integrity") or {}
                            ),
                            "uzi_integrity_by_profile": dict(
                                bundle_coverage.get("uzi_integrity_by_profile") or {}
                            ),
                            "audit_ready": bool(bundle_coverage.get("audit_ready")),
                            "degraded_dimension_count": int(
                                bundle_coverage.get("degraded_dimension_count") or 0
                            ),
                            "expected_dimension_count": bundle.expected_dimension_count,
                            "ready_dimension_count": bundle.ready_dimension_count,
                            "available_count": bundle.available_count,
                            "verified_empty_count": bundle.verified_empty_count,
                            "manifest_hash": bundle.manifest_hash,
                            "revision": bundle.revision,
                            "items": [
                                {
                                    "dimension_id": row.dimension,
                                    "ordinal": row.ordinal,
                                    "snapshot_id": row.snapshot_id,
                                    "content_hash": row.snapshot_content_hash,
                                    "availability_status": row.snapshot_status,
                                    "evidence_grade": row.evidence_grade,
                                }
                                for row in bundle_items
                            ],
                        },
                        "snapshots": [dict(row) for row in materialized.snapshots],
                        "source_refs": [dict(row) for row in materialized.source_refs],
                        "contract_version": bundle.contract_version,
                    }
                )
            return {
                "manifest_id": manifest.id,
                "manifest_hash": manifest.manifest_hash,
                "kind": "single" if len(frozen_items) == 1 else "portfolio",
                "report_kind": manifest.report_kind,
                "status": manifest.status,
                "contract_version": manifest.contract_version,
                "evaluation_at": _aware_utc(manifest.evaluation_at).isoformat(),
                "as_of_date": manifest.as_of_date.isoformat(),
                "workflow_pins": dict(manifest.workflow_pins_json or {}),
                "items": frozen_items,
                **(
                    {"dependencies": frozen_dependencies}
                    if frozen_dependencies
                    else {}
                ),
            }

    def _freeze_report_manifest(
        self,
        *,
        symbols: Sequence[str],
        as_of_date: date,
        tenant_id: str | None,
        user_id: str | None,
        workflow_pins: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        # Delayed imports keep the report boundary independently unit-testable
        # and avoid a persistence import cycle during shared metadata bootstrap.
        from research_system.market_data.bundles import BundleMaterializer
        from research_system.market_data.contracts import (
            CONTRACT_VERSION,
            LEGACY_UZI_CONTRACT_VERSIONS,
            canonical_hash,
        )
        from research_system.market_data.models import (
            ReportDataManifest,
            ReportDataManifestDependency,
            ReportDataManifestItem,
        )
        from research_system.market_data.repository import MarketDataRepository

        owner_tenant = str(tenant_id or "local")
        owner_user = str(user_id or "local_development")
        normalized_symbols = _unique_symbols(symbols)
        current_time = _aware_utc(self.clock())
        requested_evaluation_at = _optional_context_datetime(
            context.get("requested_evaluation_at"),
            field_name="requested_evaluation_at",
        )
        if requested_evaluation_at is not None and requested_evaluation_at > current_time:
            raise ReportInputError(
                "report_evaluation_in_future",
                "报告 evaluation_at 不能晚于当前时间",
            )
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            materializer = BundleMaterializer(repository)
            frozen_items: list[dict[str, Any]] = []
            evaluation_times: list[datetime] = []
            for ordinal, symbol in enumerate(normalized_symbols):
                market = _market_for_symbol(
                    symbol,
                    context=context,
                    default_market=self.default_market,
                )
                bundle = None
                supported_versions = (
                    CONTRACT_VERSION,
                    *sorted(LEGACY_UZI_CONTRACT_VERSIONS),
                )
                for contract_version in supported_versions:
                    for candidate in _symbol_candidates(symbol, market):
                        bundle = repository.latest_bundle(
                            symbol=candidate,
                            market=market,
                            as_of_date=as_of_date,
                            contract_version=contract_version,
                        )
                        if bundle is not None:
                            break
                    if bundle is not None:
                        break
                if bundle is None:
                    raise ReportInputError(
                        "published_bundle_missing",
                        f"{symbol} 在 {as_of_date.isoformat()} 没有已发布的完整 Bundle",
                    )
                if str(bundle.status or "").lower() != "published" or bundle.invalidated_at is not None:
                    raise ReportInputError(
                        "published_bundle_invalidated",
                        f"{symbol} 在 {as_of_date.isoformat()} 的最新 Bundle 已失效",
                    )
                if current_time < _aware_utc(bundle.cutoff_at):
                    raise ReportInputError(
                        "bundle_cutoff_not_reached",
                        f"{symbol} Bundle 的冻结 cutoff 尚未到达，不能提前生成 EOD 报告",
                    )
                if (
                    requested_evaluation_at is not None
                    and _aware_utc(bundle.cutoff_at) > requested_evaluation_at
                ):
                    raise ReportInputError(
                        "bundle_after_report_evaluation",
                        f"{symbol} Bundle cutoff 晚于请求的 evaluation_at，存在未来数据风险",
                    )
                materialized = materializer.materialize(bundle.id)
                if materialized.manifest_hash != bundle.manifest_hash:
                    raise ReportInputError(
                        "bundle_materialization_hash_mismatch",
                        f"{symbol} Bundle 物化 hash 不一致",
                    )
                bundle_items = repository.list_bundle_items(bundle.id)
                if len(bundle_items) != int(bundle.expected_dimension_count):
                    raise ReportInputError(
                        "bundle_dimension_count_mismatch",
                        f"{symbol} Bundle item 数量不完整",
                    )
                evaluation_times.append(_aware_utc(bundle.cutoff_at))
                bundle_coverage = dict(bundle.coverage_json or {})
                frozen_items.append(
                    {
                        "ordinal": ordinal,
                        "symbol": bundle.symbol,
                        "market": bundle.market,
                        "bundle_id": bundle.id,
                        "bundle_manifest_hash": bundle.manifest_hash,
                        "weight": _weight_for_symbol(symbol, context),
                        "context": _item_context(symbol, context),
                        "bundle": {
                            "bundle_id": bundle.id,
                            "symbol": bundle.symbol,
                            "market": bundle.market,
                            "as_of_date": bundle.as_of_date.isoformat(),
                            "cutoff_at": _aware_utc(bundle.cutoff_at).isoformat(),
                            "contract_version": bundle.contract_version,
                            "status": bundle.status,
                            "report_ready": bool(bundle_coverage.get("report_ready")),
                            "report_generatable": bool(
                                bundle_coverage.get("report_generatable")
                            ),
                            "quality": bundle_coverage.get("quality") or "strict",
                            "gate_policy": bundle_coverage.get("gate_policy"),
                            "uzi_integrity": dict(
                                bundle_coverage.get("uzi_integrity") or {}
                            ),
                            "uzi_integrity_by_profile": dict(
                                bundle_coverage.get("uzi_integrity_by_profile") or {}
                            ),
                            "audit_ready": bool(bundle_coverage.get("audit_ready")),
                            "degraded_dimension_count": int(
                                bundle_coverage.get("degraded_dimension_count") or 0
                            ),
                            "expected_dimension_count": bundle.expected_dimension_count,
                            "ready_dimension_count": bundle.ready_dimension_count,
                            "available_count": bundle.available_count,
                            "verified_empty_count": bundle.verified_empty_count,
                            "manifest_hash": bundle.manifest_hash,
                            "revision": bundle.revision,
                            "items": [
                                {
                                    "dimension_id": row.dimension,
                                    "ordinal": row.ordinal,
                                    "snapshot_id": row.snapshot_id,
                                    "content_hash": row.snapshot_content_hash,
                                    "availability_status": row.snapshot_status,
                                    "evidence_grade": row.evidence_grade,
                                }
                                for row in bundle_items
                            ],
                        },
                        "snapshots": [dict(row) for row in materialized.snapshots],
                        "source_refs": [dict(row) for row in materialized.source_refs],
                    }
                )
            frozen_dependencies = _freeze_manifest_dependencies(
                repository=repository,
                requested=list(context.get("bundle_dependencies") or []),
                as_of_date=as_of_date,
                requested_evaluation_at=requested_evaluation_at,
                current_time=current_time,
                stock_items=frozen_items,
            )
            evaluation_times.extend(
                _aware_utc(datetime.fromisoformat(item["cutoff_at"]))
                for item in frozen_dependencies
            )
            evaluation_at = requested_evaluation_at or max(evaluation_times)
            report_kind = str(context.get("report_kind") or "").strip() or (
                "single_stock" if len(frozen_items) == 1 else "portfolio"
            )
            hash_payload = {
                "contract_version": CONTRACT_VERSION,
                "report_kind": report_kind,
                "as_of_date": as_of_date.isoformat(),
                "evaluation_at": evaluation_at.isoformat(),
                "workflow_pins": dict(workflow_pins),
                "items": [
                    {
                        "ordinal": item["ordinal"],
                        "symbol": item["symbol"],
                        "market": item["market"],
                        "bundle_id": item["bundle_id"],
                        "bundle_manifest_hash": item["bundle_manifest_hash"],
                        "weight": item["weight"],
                        "context": item["context"],
                    }
                    for item in frozen_items
                ],
            }
            if frozen_dependencies:
                # Deliberately omit this key for legacy stock-only manifests so
                # existing hashes and resumable reports remain compatible.
                hash_payload["dependencies"] = [
                    _dependency_hash_row(item) for item in frozen_dependencies
                ]
            manifest_hash = canonical_hash(hash_payload)
            manifest = repository.find_report_manifest_by_hash(
                tenant_id=owner_tenant,
                user_id=owner_user,
                manifest_hash=manifest_hash,
            )
            if manifest is None:
                manifest = repository.add_report_manifest(
                    ReportDataManifest(
                        tenant_id=owner_tenant,
                        user_id=owner_user,
                        report_kind=report_kind,
                        status="frozen",
                        contract_version=CONTRACT_VERSION,
                        evaluation_at=evaluation_at,
                        as_of_date=as_of_date,
                        workflow_pins_json=dict(workflow_pins),
                        input_context_json=dict(context),
                        manifest_hash=manifest_hash,
                        frozen_by=owner_user,
                    )
                )
                for item in frozen_items:
                    repository.add_report_manifest_item(
                        ReportDataManifestItem(
                            manifest_id=manifest.id,
                            ordinal=item["ordinal"],
                            symbol=item["symbol"],
                            market=item["market"],
                            bundle_id=item["bundle_id"],
                            bundle_manifest_hash=item["bundle_manifest_hash"],
                            weight=item["weight"],
                            context_json=item["context"],
                        )
                    )
                for item in frozen_dependencies:
                    repository.add_report_manifest_dependency(
                        ReportDataManifestDependency(
                            manifest_id=manifest.id,
                            ordinal=item["ordinal"],
                            dependency_kind=item["dependency_kind"],
                            symbol=item["symbol"],
                            market=item["market"],
                            bundle_id=item["bundle_id"],
                            bundle_manifest_hash=item["bundle_manifest_hash"],
                            snapshot_id=item["snapshot_id"],
                            snapshot_content_hash=item["snapshot_content_hash"],
                            context_json=item["context"],
                        )
                    )
            elif str(manifest.status or "").lower() != "frozen" or manifest.invalidated_at is not None:
                raise ReportInputError(
                    "report_manifest_invalidated",
                    "相同输入的历史报告 manifest 已失效，必须先修复数据 Bundle",
                )
            return {
                "manifest_id": manifest.id,
                "manifest_hash": manifest_hash,
                "kind": "single" if len(frozen_items) == 1 else "portfolio",
                "report_kind": report_kind,
                "status": manifest.status,
                "contract_version": CONTRACT_VERSION,
                "evaluation_at": evaluation_at.isoformat(),
                "as_of_date": as_of_date.isoformat(),
                "workflow_pins": dict(workflow_pins),
                "items": frozen_items,
                **(
                    {"dependencies": frozen_dependencies}
                    if frozen_dependencies
                    else {}
                ),
            }


class ReportInputService:
    """Resolve and freeze single-stock or portfolio Bundle inputs."""

    def __init__(self, gateway: ReportInputGateway) -> None:
        self.gateway = gateway

    def freeze_request(self, request: RequestEnvelope) -> RequestEnvelope:
        """Return a request whose formal-report inputs are frozen and DB-backed.

        The operation is idempotent.  Requests already carrying ``bundle_only``
        inputs are validated again instead of resolving a newer Bundle.
        """

        if not self.is_formal_report(request):
            return request
        if str(request.intent or "").strip() in _PROFILE_REQUIRED_INTENTS and not _research_profile(
            request
        ):
            # Keep intake clarification side-effect free.  The resolved request
            # is passed through this preflight again before graph construction.
            return request
        constraints = dict(request.constraints or {})
        pinned_manifest_id = str(constraints.get("report_manifest_id") or "").strip()
        pinned_manifest_hash = str(constraints.get("report_manifest_hash") or "").strip()
        if (
            str(constraints.get("data_source_mode") or "") != "bundle_only"
            and (pinned_manifest_id or pinned_manifest_hash)
        ):
            if not pinned_manifest_id or not pinned_manifest_hash:
                raise ReportInputError(
                    "report_manifest_pin_incomplete",
                    "精确报告输入必须同时提供 manifest ID 和 hash",
                )
            loader = getattr(self.gateway, "load_frozen_report_manifest", None)
            if not callable(loader):
                raise ReportInputError(
                    "report_manifest_loader_unavailable",
                    "当前报告输入网关不能加载已冻结 Manifest",
                )
            requested_symbols = self.request_symbols(request)
            trusted = normalize_manifest(
                loader(
                    manifest_id=pinned_manifest_id,
                    manifest_hash=pinned_manifest_hash,
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                ),
            )
            trusted_slots = slots_for_manifest(trusted, symbols=requested_symbols)
            trusted_projection = normalize_manifest(
                trusted_slots["report_data_manifest"],
                expected_symbols=requested_symbols,
            )
            validate_bundle_only_input(
                snapshots=trusted_slots["snapshots"],
                manifest=trusted_projection,
                bundle=trusted_slots["market_data_bundle"],
                symbols=requested_symbols,
            )
            slots = dict(request.slots or {})
            slots.update(trusted_slots)
            constraints.update(
                {
                    "data_source_mode": "bundle_only",
                    "report_manifest_id": trusted["manifest_id"],
                    "report_manifest_hash": trusted["manifest_hash"],
                    "analysis_as_of_date": trusted["as_of_date"],
                }
            )
            bundle_hashes = bundle_hashes_for_symbols(
                trusted_projection,
                requested_symbols,
            )
            if len(requested_symbols) == 1:
                constraints["market_data_bundle_manifest_hash"] = bundle_hashes[
                    requested_symbols[0]
                ]
            return request.model_copy(
                update={"slots": slots, "constraints": constraints},
                deep=True,
            )
        if str((request.constraints or {}).get("data_source_mode") or "") == "bundle_only":
            supplied_manifest = (request.slots or {}).get("report_data_manifest")
            validate_bundle_only_input(
                snapshots=list((request.slots or {}).get("snapshots") or []),
                manifest=supplied_manifest,
                bundle=(request.slots or {}).get("market_data_bundle"),
                symbols=self.request_symbols(request),
            )
            scoped_loader = getattr(
                self.gateway,
                "load_frozen_report_manifest_items",
                None,
            )
            loader = (
                scoped_loader
                if callable(scoped_loader)
                else getattr(self.gateway, "load_frozen_report_manifest", None)
            )
            if callable(loader):
                requested_symbols = self.request_symbols(request)
                manifest_id = str(
                    (supplied_manifest or {}).get("manifest_id")
                    or (supplied_manifest or {}).get("id")
                    or ""
                )
                manifest_hash = str((supplied_manifest or {}).get("manifest_hash") or "")
                if callable(scoped_loader):
                    trusted_payload = scoped_loader(
                        manifest_id=manifest_id,
                        manifest_hash=manifest_hash,
                        tenant_id=request.tenant_id,
                        user_id=request.user_id,
                        symbols=requested_symbols,
                    )
                else:
                    trusted_payload = loader(
                        manifest_id=manifest_id,
                        manifest_hash=manifest_hash,
                        tenant_id=request.tenant_id,
                        user_id=request.user_id,
                    )
                trusted = normalize_manifest(trusted_payload)
                # A fund batch freezes one immutable portfolio manifest, while
                # each stock child consumes only its own Bundle. Always derive
                # that child view from the DB-loaded parent; never trust the
                # caller's projected rows or require the parent itself to be a
                # single-symbol manifest.
                trusted_slots = slots_for_manifest(
                    trusted,
                    symbols=requested_symbols,
                )
                trusted_projection = normalize_manifest(
                    trusted_slots["report_data_manifest"],
                    expected_symbols=requested_symbols,
                )
                validate_bundle_only_input(
                    snapshots=trusted_slots["snapshots"],
                    manifest=trusted_projection,
                    bundle=trusted_slots["market_data_bundle"],
                    symbols=requested_symbols,
                )
                slots = dict(request.slots or {})
                slots.update(trusted_slots)
                constraints = dict(request.constraints or {})
                constraints.update(
                    {
                        "report_manifest_id": trusted["manifest_id"],
                        "report_manifest_hash": trusted["manifest_hash"],
                        "analysis_as_of_date": trusted["as_of_date"],
                    }
                )
                bundle_hashes = bundle_hashes_for_symbols(
                    trusted_projection,
                    requested_symbols,
                )
                if len(requested_symbols) == 1:
                    constraints["market_data_bundle_manifest_hash"] = bundle_hashes[
                        requested_symbols[0]
                    ]
                return request.model_copy(
                    update={"slots": slots, "constraints": constraints},
                    deep=True,
                )
            return request
        symbols = self.request_symbols(request)
        # Preserve the normal clarification path for a request that has not yet
        # resolved its target.  Once accepted, every formal execution has symbols.
        if not symbols:
            return request
        manifest = self.freeze_symbols(
            symbols,
            as_of_date=_request_as_of_date(
                request,
                exchange_timezone=str(
                    getattr(self.gateway, "default_exchange_timezone", "Asia/Shanghai")
                ),
            ),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            workflow_pins=_workflow_pins(request),
            context={
                "request_id": request.request_id,
                "intent": request.intent,
                "research_profile": _research_profile(request),
                "report_kind": (
                    "single_stock" if len(symbols) == 1 else "portfolio"
                ),
                "market": (request.slots or {}).get("market")
                or (request.constraints or {}).get("market"),
                "markets": (request.slots or {}).get("markets")
                or (request.constraints or {}).get("markets")
                or {},
                "holdings": list((request.slots or {}).get("portfolio_holdings") or []),
            },
        )
        slots = dict(request.slots or {})
        slots.update(slots_for_manifest(manifest))
        constraints = dict(request.constraints or {})
        constraints.update(
            {
                "data_source_mode": "bundle_only",
                "report_manifest_hash": manifest["manifest_hash"],
                "report_manifest_id": manifest.get("manifest_id"),
                "analysis_as_of_date": manifest["as_of_date"],
            }
        )
        return request.model_copy(
            update={"slots": slots, "constraints": constraints},
            deep=True,
        )

    def freeze_symbols(
        self,
        symbols: Iterable[str],
        *,
        as_of_date: date | str,
        tenant_id: str | None,
        user_id: str | None,
        workflow_pins: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_symbols = _unique_symbols(symbols)
        if not normalized_symbols:
            raise ReportInputError("report_symbols_missing", "正式报告缺少标的，无法冻结 Bundle")
        resolved_date = _parse_date(as_of_date)
        payload = self.gateway.freeze_report_manifest(
            symbols=normalized_symbols,
            as_of_date=resolved_date,
            tenant_id=tenant_id,
            user_id=user_id,
            workflow_pins=dict(workflow_pins or {}),
            context=dict(context or {}),
        )
        manifest = normalize_manifest(payload, expected_symbols=normalized_symbols)
        validate_bundle_only_input(
            snapshots=slots_for_manifest(manifest)["snapshots"],
            manifest=manifest,
            bundle=slots_for_manifest(manifest)["market_data_bundle"],
            symbols=normalized_symbols,
        )
        return manifest

    @property
    def has_fund_report_lineage(self) -> bool:
        """Whether this service can bind reports to a persisted fund parent."""

        return callable(getattr(self.gateway, "freeze_fund_report_manifest", None))

    def freeze_fund_holdings(
        self,
        *,
        fund_code: str,
        expected_symbols: Iterable[str],
        as_of_date: date | str,
        tenant_id: str | None,
        user_id: str | None,
        workflow_pins: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
        fund_bundle_id: str | None = None,
    ) -> dict[str, Any]:
        """Freeze the exact top-ten selected by the persisted fund Bundle.

        Lightweight/non-database gateways predate fund lineage.  They retain
        their stock-only behaviour for tests and embedded legacy deployments;
        the database gateway always takes the strict fail-closed branch.
        """

        expected = _unique_symbols(expected_symbols)
        freezer = getattr(self.gateway, "freeze_fund_report_manifest", None)
        if not callable(freezer):
            return self.freeze_symbols(
                expected,
                as_of_date=as_of_date,
                tenant_id=tenant_id,
                user_id=user_id,
                workflow_pins=workflow_pins,
                context=context,
            )
        payload = freezer(
            fund_code=fund_code,
            as_of_date=_parse_date(as_of_date),
            tenant_id=tenant_id,
            user_id=user_id,
            workflow_pins=dict(workflow_pins or {}),
            context=dict(context or {}),
            fund_bundle_id=fund_bundle_id,
        )
        manifest = normalize_manifest(payload, expected_symbols=expected)
        _validate_fund_report_manifest(manifest, fund_code=fund_code)
        slots = slots_for_manifest(manifest)
        validate_bundle_only_input(
            snapshots=slots["snapshots"],
            manifest=manifest,
            bundle=slots["market_data_bundle"],
            symbols=expected,
        )
        return manifest

    def load_fund_report_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        fund_code: str,
        expected_symbols: Iterable[str],
        tenant_id: str | None,
        user_id: str | None,
    ) -> dict[str, Any]:
        """Reload and verify a previously frozen fund portfolio manifest."""

        expected = _unique_symbols(expected_symbols)
        loader = getattr(self.gateway, "load_frozen_report_manifest", None)
        if not self.has_fund_report_lineage or not callable(loader):
            # Compatibility boundary for old injected gateways.  Production's
            # DatabaseReportInputGateway never enters this branch.
            return normalize_manifest(manifest, expected_symbols=expected)
        supplied = normalize_manifest(manifest, expected_symbols=expected)
        trusted = normalize_manifest(
            loader(
                manifest_id=supplied["manifest_id"],
                manifest_hash=supplied["manifest_hash"],
                tenant_id=tenant_id,
                user_id=user_id,
            ),
            expected_symbols=expected,
        )
        _validate_fund_report_manifest(trusted, fund_code=fund_code)
        return trusted

    @staticmethod
    def is_formal_report(request: RequestEnvelope) -> bool:
        intent = str(request.intent or "").strip()
        if intent in FORMAL_REPORT_INTENTS:
            return True
        constraints = dict(request.constraints or {})
        return bool(
            constraints.get("formal_report")
            or constraints.get("report_requires_bundle")
            or constraints.get("canonical_uzi_analysis")
            or constraints.get("fund_holding_report_child")
        )

    @staticmethod
    def request_symbols(request: RequestEnvelope) -> list[str]:
        slots = dict(request.slots or {})
        symbols: list[str] = [str(value) for value in slots.get("symbols") or []]
        for holding in slots.get("portfolio_holdings") or []:
            if isinstance(holding, Mapping):
                symbols.append(str(holding.get("ticker") or holding.get("symbol") or ""))
        if not any(value.strip() for value in symbols):
            symbols.extend(_CODE.findall(str(request.user_query or "")))
        return _unique_symbols(symbols)


def normalize_manifest(
    payload: Mapping[str, Any],
    *,
    expected_symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Normalise the gateway result without weakening its strict contract."""

    manifest = dict(payload or {})
    manifest_id = str(manifest.get("manifest_id") or manifest.get("id") or "").strip()
    manifest_hash = str(manifest.get("manifest_hash") or "").strip().lower()
    contract_version = str(manifest.get("contract_version") or "").strip()
    status = str(manifest.get("status") or "").strip().lower()
    if not manifest_id:
        raise ReportInputError("report_manifest_not_persisted", "报告输入 manifest 未持久化")
    if not _HEX_64.fullmatch(manifest_hash):
        raise ReportInputError("report_manifest_hash_invalid", "报告输入 manifest hash 无效")
    if not contract_version:
        raise ReportInputError("report_manifest_contract_missing", "报告输入 manifest 缺少契约版本")
    if status != "frozen":
        raise ReportInputError(
            "report_manifest_not_frozen",
            f"报告输入 manifest 状态不可用：{status or 'unknown'}",
        )
    try:
        as_of = _parse_date(manifest.get("as_of_date"))
    except (TypeError, ValueError) as exc:
        raise ReportInputError("report_manifest_as_of_invalid", "报告输入 manifest 缺少有效日期") from exc
    items = [dict(row) for row in manifest.get("items") or [] if isinstance(row, Mapping)]
    if not items:
        raise ReportInputError("report_manifest_empty", "报告输入 manifest 没有 Bundle")
    by_symbol: dict[str, dict[str, Any]] = {}
    for item in items:
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            raise ReportInputError("report_manifest_symbol_missing", "报告输入 manifest 存在无标的 Bundle")
        key = _symbol_key(symbol)
        if key in by_symbol:
            raise ReportInputError("report_manifest_symbol_duplicate", f"报告输入 manifest 重复标的：{symbol}")
        bundle = dict(item.get("bundle") or {})
        bundle.setdefault("bundle_id", item.get("bundle_id"))
        bundle.setdefault("symbol", symbol)
        item["bundle"] = bundle
        item["bundle_id"] = str(bundle.get("bundle_id") or "").strip()
        item["symbol"] = symbol
        item.setdefault("contract_version", contract_version)
        item["snapshots"] = [
            dict(row) for row in item.get("snapshots") or [] if isinstance(row, Mapping)
        ]
        item["source_refs"] = [
            dict(row) for row in item.get("source_refs") or [] if isinstance(row, Mapping)
        ]
        by_symbol[key] = item
    if expected_symbols:
        expected = {_symbol_key(symbol) for symbol in expected_symbols}
        actual = set(by_symbol)
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ReportInputError(
                "report_manifest_symbol_mismatch",
                f"报告输入标的不一致，缺少={missing}，额外={extra}",
            )
    manifest.update(
        {
            "manifest_id": manifest_id,
            "manifest_hash": manifest_hash,
            "contract_version": contract_version,
            "status": status,
            "as_of_date": as_of.isoformat(),
            "kind": "single" if len(items) == 1 else "portfolio",
            "items": items,
        }
    )
    return manifest


def slots_for_manifest(
    manifest: Mapping[str, Any],
    *,
    symbols: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Project a frozen manifest to immutable Agent OS request slots."""

    selected = {_symbol_key(value) for value in symbols or []}
    items = [
        dict(row)
        for row in manifest.get("items") or []
        if isinstance(row, Mapping)
        and (not selected or _symbol_key(str(row.get("symbol") or "")) in selected)
    ]
    if not items:
        raise ReportInputError("report_manifest_item_missing", "冻结 manifest 中没有所选标的")
    snapshots = [dict(snapshot) for item in items for snapshot in item.get("snapshots") or []]
    source_refs = _unique_refs(
        [
            *[dict(ref) for item in items for ref in item.get("source_refs") or []],
            *[
                {
                    "type": "market_data_bundle",
                    "id": str(item.get("bundle_id") or ""),
                    "locator": str(manifest.get("manifest_id") or ""),
                }
                for item in items
            ],
        ]
    )
    bundles = [dict(item.get("bundle") or {}) for item in items]
    bundle_slot: dict[str, Any]
    if len(bundles) == 1:
        bundle_slot = bundles[0]
        bundle_slot["report_manifest_id"] = manifest.get("manifest_id")
        bundle_slot["report_manifest_hash"] = manifest.get("manifest_hash")
    else:
        bundle_slot = {
            "kind": "portfolio",
            "manifest_id": manifest.get("manifest_id"),
            "manifest_hash": manifest.get("manifest_hash"),
            "as_of_date": manifest.get("as_of_date"),
            "bundles": bundles,
        }
    projected_manifest = dict(manifest)
    projected_manifest["items"] = items
    projected_manifest["kind"] = "single" if len(items) == 1 else "portfolio"
    if selected and len(items) != len(list(manifest.get("items") or [])):
        projected_manifest["parent_manifest_hash"] = manifest.get("manifest_hash")
    return {
        "snapshots": snapshots,
        "market_data_bundle": bundle_slot,
        "report_data_manifest": projected_manifest,
        "source_refs": source_refs,
    }


def bundle_hashes_for_symbols(
    manifest: Mapping[str, Any] | None,
    symbols: Iterable[str],
) -> dict[str, str]:
    """Return request-symbol -> immutable per-security Bundle hash."""

    if not isinstance(manifest, Mapping):
        return {}
    items = {
        _symbol_key(str(item.get("symbol") or "")): item
        for item in manifest.get("items") or []
        if isinstance(item, Mapping)
    }
    resolved: dict[str, str] = {}
    for symbol in _unique_symbols(symbols):
        item = items.get(_symbol_key(symbol))
        if item is None:
            continue
        bundle = item.get("bundle") if isinstance(item.get("bundle"), Mapping) else {}
        value = str(
            item.get("bundle_manifest_hash")
            or bundle.get("manifest_hash")
            or ""
        ).strip().lower()
        if _HEX_64.fullmatch(value):
            resolved[symbol] = value
    return resolved


def validate_bundle_only_input(
    *,
    snapshots: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None,
    bundle: Mapping[str, Any] | None,
    symbols: Iterable[str] | None = None,
) -> None:
    """Strictly validate supplied Bundle rows without contacting providers."""

    if not isinstance(manifest, Mapping):
        raise ReportInputError("report_manifest_missing", "bundle_only 缺少冻结的报告 manifest")
    normalized = normalize_manifest(manifest, expected_symbols=_unique_symbols(symbols or []))
    manifest_hash = str(normalized["manifest_hash"])
    if isinstance(bundle, Mapping):
        advertised = str(
            bundle.get("report_manifest_hash")
            or (bundle.get("manifest_hash") if bundle.get("kind") == "portfolio" else "")
            or ""
        ).strip().lower()
        if advertised and advertised != manifest_hash:
            raise ReportInputError("report_manifest_hash_mismatch", "Bundle 与报告 manifest hash 不一致")
    all_rows = [dict(row) for row in snapshots if isinstance(row, Mapping)]
    if not all_rows:
        raise ReportInputError("bundle_snapshots_missing", "bundle_only 没有物化快照")
    expected_total = 0
    for item in normalized["items"]:
        _validate_manifest_item(item, all_rows)
        bundle_payload = dict(item.get("bundle") or {})
        expected_total += _positive_int(
            bundle_payload.get("expected_dimension_count")
            or item.get("expected_dimension_count")
            or len(bundle_payload.get("items") or [])
            or len(item.get("snapshots") or [])
        )
    if len(all_rows) != expected_total:
        raise ReportInputError(
            "bundle_snapshot_total_mismatch",
            f"bundle_only 应有 {expected_total} 条快照，实际 {len(all_rows)} 条",
        )


def _validate_manifest_item(item: Mapping[str, Any], all_rows: Sequence[Mapping[str, Any]]) -> None:
    symbol = str(item.get("symbol") or "").strip()
    bundle = dict(item.get("bundle") or {})
    bundle_id = str(bundle.get("bundle_id") or item.get("bundle_id") or "").strip()
    if not bundle_id:
        raise ReportInputError("bundle_id_missing", f"{symbol} 缺少 bundle_id")
    status = str(bundle.get("status") or "").strip().lower()
    if status != "published":
        raise ReportInputError("bundle_not_published", f"{symbol} Bundle 未发布：{status}")
    if (
        bundle.get("report_ready") is not True
        and bundle.get("report_generatable") is not True
    ):
        raise ReportInputError(
            "bundle_not_report_ready",
            f"{symbol} Bundle 已过期或结构不完整，无法作为 UZI Agent 输入",
        )
    degraded_bundle = bool(
        bundle.get("report_ready") is not True
        and bundle.get("report_generatable") is True
        and bundle.get("quality") == "degraded"
    )
    bundle_contract = str(bundle.get("contract_version") or "").strip()
    if not bundle_contract or bundle_contract != str(item.get("contract_version") or "").strip():
        raise ReportInputError("bundle_contract_mismatch", f"{symbol} Bundle 契约与报告 manifest 不一致")
    uses_uzi_gate = is_supported_uzi_contract_version(bundle_contract)
    # UZI's data-integrity output is recovery input, not the final publication
    # gate.  A structurally complete Bundle may contain explicit provider gaps;
    # the native full Agent workflow repairs them and only final self_review
    # criticals can block HTML/PDF assembly.
    expected_count = _positive_int(
        bundle.get("expected_dimension_count")
        or item.get("expected_dimension_count")
        or len(bundle.get("items") or [])
        or len(item.get("snapshots") or [])
    )
    expected_items = [
        dict(row) for row in bundle.get("items") or [] if isinstance(row, Mapping)
    ]
    if not expected_items:
        raise ReportInputError("bundle_manifest_items_missing", f"{symbol} Bundle manifest 缺少维度引用")
    _verify_bundle_manifest(bundle, expected_items, symbol=symbol)
    # The top-level ``slots.snapshots`` is what the analysis node will actually
    # consume, so validation must never be satisfied by the manifest's embedded
    # audit copy when those supplied rows have been altered.
    item_rows = [
        dict(row)
        for row in all_rows
        if _symbol_key(_snapshot_symbol(row)) == _symbol_key(symbol)
    ]
    if len(item_rows) != expected_count:
        raise ReportInputError(
            "bundle_dimension_count_mismatch",
            f"{symbol} Bundle 应有 {expected_count} 槽，实际 {len(item_rows)} 槽",
        )
    actual_by_dimension: dict[str, dict[str, Any]] = {}
    for row in item_rows:
        row_symbol = _snapshot_symbol(row)
        if row_symbol and _symbol_key(row_symbol) != _symbol_key(symbol):
            raise ReportInputError("bundle_snapshot_symbol_mismatch", f"{symbol} Bundle 混入 {row_symbol} 快照")
        dimension = _snapshot_dimension(row)
        if not dimension:
            raise ReportInputError("bundle_snapshot_dimension_missing", f"{symbol} 快照缺少 dimension_id")
        if dimension in actual_by_dimension:
            raise ReportInputError("bundle_snapshot_dimension_duplicate", f"{symbol} 重复维度：{dimension}")
        quality = _snapshot_status(row)
        query_manifest = (
            dict(row.get("query_manifest") or {})
            if isinstance(row.get("query_manifest"), Mapping)
            else {}
        )
        degradation = (
            dict(query_manifest.get("degradation") or {})
            if isinstance(query_manifest.get("degradation"), Mapping)
            else {}
        )
        safe_degradation = bool(
            degraded_bundle
            and quality == "data_gap"
            and degradation.get("safe_for_report") is True
            and degradation.get("excluded_from_scoring") is True
        )
        if (
            not uses_uzi_gate
            and quality not in _PASSING_STATUSES
            and not safe_degradation
        ):
            raise ReportInputError(
                "bundle_snapshot_not_eligible",
                f"{symbol}/{dimension} 状态 {quality or 'unknown'} 不能用于正式报告",
            )
        evidence_grade = str(
            row.get("evidence_grade")
            or (row.get("_market_data") or {}).get("evidence_grade")
            or ""
        ).strip().upper()
        if (
            not uses_uzi_gate
            and evidence_grade not in {"A", "B"}
            and not safe_degradation
        ):
            raise ReportInputError(
                "bundle_snapshot_evidence_ineligible",
                f"{symbol}/{dimension} 证据等级 {evidence_grade} 不能用于正式报告",
            )
        row_contract = str(row.get("contract_version") or "").strip()
        if row_contract != bundle_contract:
            raise ReportInputError(
                "bundle_snapshot_contract_mismatch",
                f"{symbol}/{dimension} 快照契约与 Bundle 不一致",
            )
        if (
            not uses_uzi_gate
            and not [
                ref
                for ref in row.get("source_refs") or []
                if isinstance(ref, Mapping)
            ]
        ):
            raise ReportInputError(
                "bundle_snapshot_evidence_missing",
                f"{symbol}/{dimension} 快照没有可审计证据引用",
            )
        actual_by_dimension[dimension] = row
    if expected_items:
        expected_by_dimension = {
            str(row.get("dimension_id") or row.get("snapshot_type") or "").strip(): row
            for row in expected_items
        }
        if "" in expected_by_dimension or set(expected_by_dimension) != set(actual_by_dimension):
            raise ReportInputError("bundle_dimension_set_mismatch", f"{symbol} Bundle 维度集合与 manifest 不一致")
        for dimension, expected in expected_by_dimension.items():
            actual = actual_by_dimension[dimension]
            expected_hash = str(expected.get("content_hash") or "").strip().lower()
            actual_hash = str(
                actual.get("content_hash")
                or (actual.get("_market_data") or {}).get("content_hash")
                or ""
            ).strip().lower()
            if not _HEX_64.fullmatch(expected_hash) or actual_hash != expected_hash:
                raise ReportInputError(
                    "bundle_snapshot_hash_mismatch",
                    f"{symbol}/{dimension} 快照 hash 与 Bundle manifest 不一致",
                )


def _verify_bundle_manifest(
    bundle: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
) -> None:
    from research_system.market_data.bundles import bundle_manifest_hash

    try:
        cutoff = datetime.fromisoformat(
            str(bundle.get("cutoff_at") or "").replace("Z", "+00:00")
        )
        calculated = bundle_manifest_hash(
            symbol=str(bundle.get("symbol") or symbol),
            market=str(bundle.get("market") or ""),
            as_of_date=_parse_date(bundle.get("as_of_date")),
            cutoff_at=_aware_utc(cutoff),
            contract_version=str(bundle.get("contract_version") or ""),
            items=[
                {
                    "dimension": row.get("dimension_id") or row.get("dimension"),
                    "ordinal": row.get("ordinal"),
                    "snapshot_id": row.get("snapshot_id"),
                    "content_hash": row.get("content_hash"),
                    "status": row.get("availability_status") or row.get("status"),
                    "evidence_grade": row.get("evidence_grade"),
                }
                for row in items
            ],
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ReportInputError(
            "bundle_manifest_invalid",
            f"{symbol} Bundle manifest 元数据无效",
        ) from exc
    advertised = str(bundle.get("manifest_hash") or "").strip().lower()
    if calculated != advertised:
        raise ReportInputError(
            "bundle_manifest_hash_mismatch",
            f"{symbol} Bundle manifest 自校验失败",
        )


def _snapshot_dimension(row: Mapping[str, Any]) -> str:
    metadata = row.get("_market_data") if isinstance(row.get("_market_data"), Mapping) else {}
    return str(
        row.get("dimension_id")
        or metadata.get("dimension_id")
        or row.get("snapshot_type")
        or ""
    ).strip()


def _snapshot_symbol(row: Mapping[str, Any]) -> str:
    metadata = row.get("_market_data") if isinstance(row.get("_market_data"), Mapping) else {}
    return str(row.get("symbol") or metadata.get("symbol") or "").strip()


def _snapshot_status(row: Mapping[str, Any]) -> str:
    metadata = row.get("_market_data") if isinstance(row.get("_market_data"), Mapping) else {}
    facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
    return str(
        row.get("availability_status")
        or metadata.get("availability_status")
        or row.get("quality_status")
        or row.get("status")
        or facts.get("availability")
        or ""
    ).strip().lower()


def _dependency_hash_row(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ordinal": int(item["ordinal"]),
        "dependency_kind": str(item["dependency_kind"]),
        "symbol": str(item["symbol"]),
        "market": str(item["market"]),
        "bundle_id": str(item["bundle_id"]),
        "bundle_manifest_hash": str(item["bundle_manifest_hash"]),
        "snapshot_id": str(item["snapshot_id"]),
        "snapshot_content_hash": str(item["snapshot_content_hash"]),
        "context": dict(item.get("context") or {}),
    }


def _validate_fund_report_manifest(
    manifest: Mapping[str, Any],
    *,
    fund_code: str,
) -> None:
    """Require an exact parent-derived ten-stock manifest, including lineage."""

    items = [dict(item) for item in manifest.get("items") or [] if isinstance(item, Mapping)]
    dependencies = [
        dict(item)
        for item in manifest.get("dependencies") or []
        if isinstance(item, Mapping)
    ]
    if str(manifest.get("report_kind") or "") != "fund_top_holdings":
        raise ReportInputError(
            "fund_report_kind_mismatch",
            "基金持仓报告 manifest 类型必须是 fund_top_holdings",
        )
    if len(items) != 10:
        raise ReportInputError(
            "fund_top_ten_required",
            "基金持仓报告必须且只能包含十只股票",
        )
    if len(dependencies) != 1 or str(
        dependencies[0].get("dependency_kind") or ""
    ) != "fund_top_holdings":
        raise ReportInputError(
            "fund_report_dependency_required",
            "基金持仓报告必须钉住一个父基金 PIT Bundle",
        )
    dependency = dependencies[0]
    context = dict(dependency.get("context") or {})
    expected_code = _symbol_key(fund_code)
    if (
        str(dependency.get("market") or "").upper() != "CN_FUND"
        or _symbol_key(str(dependency.get("symbol") or "")) != expected_code
        or _symbol_key(str(context.get("fund_code") or "")) != expected_code
    ):
        raise ReportInputError(
            "fund_report_dependency_scope_mismatch",
            "基金报告 manifest 的父基金依赖与请求基金不一致",
        )
    selected_top_n = context.get("selected_top_n")
    try:
        selected_count = int(selected_top_n)
    except (TypeError, ValueError) as exc:
        raise ReportInputError(
            "fund_report_dependency_integrity_mismatch",
            "基金报告 manifest 缺少父披露持仓数量承诺",
        ) from exc
    if selected_count != 10:
        raise ReportInputError(
            "fund_top_ten_required",
            "基金持仓报告的父披露必须精确选择十只股票",
        )
    item_symbols = [_symbol_key(str(item.get("symbol") or "")) for item in items]
    holding_symbols = [
        _symbol_key(str(symbol)) for symbol in context.get("holding_symbols") or []
    ]
    if item_symbols != holding_symbols:
        raise ReportInputError(
            "fund_holding_portfolio_mismatch",
            "报告股票组合必须与父基金披露的前十大持仓顺序完全一致",
        )
    holding_weights = context.get("holding_weights")
    if not isinstance(holding_weights, Mapping):
        raise ReportInputError(
            "fund_report_dependency_integrity_mismatch",
            "基金报告 manifest 缺少父披露权重承诺",
        )
    for item in items:
        symbol = str(item.get("symbol") or "")
        expected_weight = holding_weights.get(
            symbol,
            holding_weights.get(_symbol_key(symbol)),
        )
        try:
            matches = abs(float(item.get("weight")) - float(expected_weight)) <= 1e-8
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise ReportInputError(
                "fund_holding_weight_mismatch",
                f"{symbol} 的报告权重与父基金披露不一致",
            )


def _canonical_fund_dependency_context(payload) -> dict[str, Any]:
    from research_system.market_data.contracts import canonical_hash

    holdings = [item.model_dump(mode="json") for item in payload.holdings]
    return {
        "fund_code": payload.fund_code,
        "fund_name": payload.fund_name,
        "asset_type": payload.asset_type,
        "holding_source": payload.holding_source,
        "disclosure_date": payload.disclosure_date.isoformat(),
        "availability_at": payload.availability_at.isoformat(),
        "selected_top_n": len(holdings),
        "holding_symbols": [item["symbol"] for item in holdings],
        "holding_weights": {
            item["symbol"]: item["fund_weight_pct"] for item in holdings
        },
        "lineage_hash": canonical_hash(
            [item.model_dump(mode="json") for item in payload.lineage]
        ),
    }


def _materialize_fund_dependency(repository, bundle):
    from research_system.market_data.bundles import (
        BundleIntegrityError,
        BundleMaterializer,
    )
    from research_system.market_data.fund_holdings_contracts import (
        FUND_HOLDINGS_CONTRACT_VERSION,
        FUND_TOP_HOLDINGS_DIMENSION,
        FUND_TOP_HOLDINGS_REGISTRY,
        validate_fund_top_holdings_payload,
    )

    if bundle.contract_version != FUND_HOLDINGS_CONTRACT_VERSION:
        raise ReportInputError(
            "fund_holding_bundle_contract_mismatch",
            "基金持仓 Bundle 契约版本不受当前运行时支持",
        )
    try:
        materialized = BundleMaterializer(
            repository,
            registry=FUND_TOP_HOLDINGS_REGISTRY,
        ).materialize(bundle.id)
    except BundleIntegrityError as exc:
        raise ReportInputError(
            "fund_holding_bundle_invalid",
            "基金持仓 Bundle 未通过完整性校验",
        ) from exc
    if len(materialized.snapshots) != 1:
        raise ReportInputError(
            "fund_holding_bundle_slot_mismatch",
            "基金持仓 Bundle 必须且只能包含一个持仓披露槽",
        )
    snapshot_payload = materialized.snapshots[0]
    if str(snapshot_payload.get("dimension_id") or "") != FUND_TOP_HOLDINGS_DIMENSION:
        raise ReportInputError(
            "fund_holding_bundle_slot_mismatch",
            "基金持仓 Bundle 槽位不正确",
        )
    try:
        payload = validate_fund_top_holdings_payload(
            dict(snapshot_payload.get("facts") or {})
        )
    except (TypeError, ValueError) as exc:
        raise ReportInputError(
            "fund_holding_payload_invalid",
            "基金持仓披露未通过十只股票与 PIT 语义校验",
        ) from exc
    if (
        payload.fund_code != bundle.symbol
        or payload.market != bundle.market
        or payload.analysis_as_of_date != bundle.as_of_date
        or _aware_utc(payload.cutoff_at) > _aware_utc(bundle.cutoff_at)
    ):
        raise ReportInputError(
            "fund_holding_bundle_scope_mismatch",
            "基金持仓披露与 Bundle 的基金代码、市场、日期或 cutoff 不一致",
        )
    return materialized, payload


def _bind_fund_holdings_to_stock_items(payload, stock_items: list[dict[str, Any]]) -> None:
    expected = list(payload.holdings)
    if len(stock_items) != len(expected) or [
        _symbol_key(str(item["symbol"])) for item in stock_items
    ] != [_symbol_key(item.symbol) for item in expected]:
        raise ReportInputError(
            "fund_holding_portfolio_mismatch",
            "报告股票组合必须与基金披露中的前十大持仓顺序完全一致",
        )
    for item, holding in zip(stock_items, expected, strict=True):
        existing_weight = item.get("weight")
        if existing_weight is not None and abs(
            float(existing_weight) - float(holding.fund_weight_pct)
        ) > 1e-8:
            raise ReportInputError(
                "fund_holding_weight_mismatch",
                f"{holding.symbol} 的报告权重与基金披露不一致",
            )
        item["weight"] = float(holding.fund_weight_pct)
        item_context = dict(item.get("context") or {})
        item_context.update(
            {
                "fund_rank": holding.rank,
                "source_rank": holding.source_rank,
                "holding_source": holding.holding_source,
                "disclosed_weight_pct": holding.disclosed_weight_pct,
                "disclosure_date": holding.disclosure_date.isoformat(),
            }
        )
        item["context"] = item_context


def _freeze_manifest_dependencies(
    *,
    repository,
    requested: list[Any],
    as_of_date: date,
    requested_evaluation_at: datetime | None,
    current_time: datetime,
    stock_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not requested:
        return []
    if len(requested) != 1 or not isinstance(requested[0], Mapping):
        raise ReportInputError(
            "report_dependency_invalid",
            "当前基金持仓报告必须且只能钉住一个父基金 Bundle",
        )
    raw = dict(requested[0])
    if str(raw.get("dependency_kind") or "fund_top_holdings") != "fund_top_holdings":
        raise ReportInputError(
            "report_dependency_kind_unsupported",
            "当前仅支持 fund_top_holdings 报告依赖",
        )
    bundle_id = str(raw.get("bundle_id") or "").strip()
    bundle = repository.get_bundle(bundle_id) if bundle_id else None
    if bundle is None:
        raise ReportInputError(
            "fund_holding_bundle_missing",
            "缺少已发布的基金持仓 Bundle；请先显式采集基金持仓",
        )
    if bundle.status != "published" or bundle.invalidated_at is not None:
        raise ReportInputError(
            "fund_holding_bundle_invalidated",
            "基金持仓 Bundle 已失效",
        )
    supplied_hash = str(raw.get("bundle_manifest_hash") or "").strip().lower()
    if supplied_hash and supplied_hash != bundle.manifest_hash:
        raise ReportInputError(
            "fund_holding_bundle_hash_mismatch",
            "基金持仓 Bundle hash 与数据库记录不一致",
        )
    if bundle.as_of_date != as_of_date:
        raise ReportInputError(
            "fund_holding_bundle_date_mismatch",
            "基金持仓 Bundle 与报告日期不一致",
        )
    cutoff = _aware_utc(bundle.cutoff_at)
    if current_time < cutoff:
        raise ReportInputError(
            "fund_holding_cutoff_not_reached",
            "基金持仓 Bundle 的冻结 cutoff 尚未到达",
        )
    if requested_evaluation_at is not None and cutoff > requested_evaluation_at:
        raise ReportInputError(
            "fund_holding_after_report_evaluation",
            "基金持仓 Bundle cutoff 晚于报告 evaluation_at",
        )
    _, payload = _materialize_fund_dependency(repository, bundle)
    _bind_fund_holdings_to_stock_items(payload, stock_items)
    bundle_items = repository.list_bundle_items(bundle.id)
    if len(bundle_items) != 1:
        raise ReportInputError(
            "fund_holding_bundle_slot_mismatch",
            "基金持仓 Bundle 必须且只能包含一个持仓披露槽",
        )
    bundle_item = bundle_items[0]
    supplied_snapshot_id = str(raw.get("snapshot_id") or "").strip()
    supplied_snapshot_hash = str(raw.get("snapshot_content_hash") or "").strip().lower()
    if supplied_snapshot_id and supplied_snapshot_id != bundle_item.snapshot_id:
        raise ReportInputError(
            "fund_holding_snapshot_mismatch",
            "基金持仓 snapshot id 与 Bundle 不一致",
        )
    if supplied_snapshot_hash and supplied_snapshot_hash != bundle_item.snapshot_content_hash:
        raise ReportInputError(
            "fund_holding_snapshot_hash_mismatch",
            "基金持仓 snapshot hash 与 Bundle 不一致",
        )
    return [
        {
            "ordinal": 0,
            "dependency_kind": "fund_top_holdings",
            "symbol": bundle.symbol,
            "market": bundle.market,
            "bundle_id": bundle.id,
            "bundle_manifest_hash": bundle.manifest_hash,
            "snapshot_id": bundle_item.snapshot_id,
            "snapshot_content_hash": bundle_item.snapshot_content_hash,
            "cutoff_at": cutoff.isoformat(),
            "context": _canonical_fund_dependency_context(payload),
        }
    ]


def _load_manifest_dependencies(*, repository, manifest, stock_items) -> list[dict[str, Any]]:
    stored = repository.list_report_manifest_dependencies(manifest.id)
    if not stored:
        return []
    if len(stored) != 1 or stored[0].dependency_kind != "fund_top_holdings":
        raise ReportInputError(
            "report_dependency_invalid",
            "冻结的报告依赖结构不受当前运行时支持",
        )
    dependency = stored[0]
    bundle = repository.get_bundle(dependency.bundle_id)
    if bundle is None or bundle.status != "published" or bundle.invalidated_at is not None:
        raise ReportInputError(
            "fund_holding_bundle_invalidated",
            "冻结的父基金持仓 Bundle 已失效",
        )
    if (
        bundle.symbol != dependency.symbol
        or bundle.market != dependency.market
        or bundle.manifest_hash != dependency.bundle_manifest_hash
        or bundle.as_of_date != manifest.as_of_date
    ):
        raise ReportInputError(
            "fund_holding_dependency_drift",
            "冻结的父基金持仓依赖与 Bundle 不一致",
        )
    _, payload = _materialize_fund_dependency(repository, bundle)
    _bind_fund_holdings_to_stock_items(payload, stock_items)
    bundle_items = repository.list_bundle_items(bundle.id)
    if len(bundle_items) != 1:
        raise ReportInputError(
            "fund_holding_bundle_slot_mismatch",
            "冻结的父基金 Bundle 槽位数不正确",
        )
    item = bundle_items[0]
    canonical_context = _canonical_fund_dependency_context(payload)
    if (
        item.snapshot_id != dependency.snapshot_id
        or item.snapshot_content_hash != dependency.snapshot_content_hash
        or dict(dependency.context_json or {}) != canonical_context
    ):
        raise ReportInputError(
            "fund_holding_dependency_integrity_mismatch",
            "冻结的父基金 snapshot 或持仓选择承诺已漂移",
        )
    return [
        {
            "ordinal": dependency.ordinal,
            "dependency_kind": dependency.dependency_kind,
            "symbol": dependency.symbol,
            "market": dependency.market,
            "bundle_id": dependency.bundle_id,
            "bundle_manifest_hash": dependency.bundle_manifest_hash,
            "snapshot_id": dependency.snapshot_id,
            "snapshot_content_hash": dependency.snapshot_content_hash,
            "cutoff_at": _aware_utc(bundle.cutoff_at).isoformat(),
            "context": canonical_context,
        }
    ]


def _request_as_of_date(
    request: RequestEnvelope,
    *,
    exchange_timezone: str = "Asia/Shanghai",
) -> date:
    value = (
        (request.constraints or {}).get("analysis_as_of_date")
        or (request.slots or {}).get("as_of_date")
        or datetime.now(ZoneInfo(exchange_timezone)).date()
    )
    return _parse_date(value)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_context_datetime(value: Any, *, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReportInputError(
                "report_evaluation_invalid",
                f"{field_name} 必须是带时区的 ISO 时间",
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReportInputError(
            "report_evaluation_invalid",
            f"{field_name} 必须包含时区",
        )
    return parsed.astimezone(timezone.utc)


def _market_for_symbol(
    symbol: str,
    *,
    context: Mapping[str, Any],
    default_market: str,
) -> str:
    markets = context.get("markets")
    if isinstance(markets, Mapping):
        for key in (symbol, _symbol_key(symbol)):
            value = markets.get(key)
            if value:
                return str(value).strip().upper()
    explicit = str(context.get("market") or "").strip().upper()
    if explicit:
        return explicit
    upper = str(symbol or "").strip().upper()
    if upper.endswith(".HK") or upper.startswith("HK"):
        return "HK_EQUITY"
    if upper.endswith((".US", ".NYSE", ".NASDAQ")):
        return "US_EQUITY"
    return default_market


def _weight_for_symbol(symbol: str, context: Mapping[str, Any]) -> float | None:
    weights = context.get("weights")
    if isinstance(weights, Mapping):
        value = weights.get(symbol, weights.get(_symbol_key(symbol)))
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    for row in context.get("holdings") or []:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or row.get("symbol") or "")
        if _symbol_key(ticker) != _symbol_key(symbol):
            continue
        value = row.get(
            "weight",
            row.get("weight_pct", row.get("fund_weight_pct")),
        )
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _item_context(symbol: str, context: Mapping[str, Any]) -> dict[str, Any]:
    rows = context.get("item_context")
    if isinstance(rows, Mapping):
        value = rows.get(symbol, rows.get(_symbol_key(symbol)))
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value or "")[:10])


def _research_profile(request: RequestEnvelope) -> str | None:
    value = (request.constraints or {}).get("research_profile") or (request.slots or {}).get(
        "research_profile"
    )
    return str(value).strip() or None if value is not None else None


def _workflow_pins(request: RequestEnvelope) -> dict[str, Any]:
    constraints = dict(request.constraints or {})
    return {
        key: constraints[key]
        for key in (
            "strategy_pin_set_hash",
            "workflow_commit",
            "skill_version",
            "model_id",
            "prompt_hash",
            "research_profile",
        )
        if constraints.get(key) is not None
    }


def _positive_int(value: Any) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise ReportInputError("bundle_dimension_count_invalid", "Bundle 缺少有效维度计数") from exc
    if resolved <= 0:
        raise ReportInputError("bundle_dimension_count_invalid", "Bundle 维度计数必须大于 0")
    return resolved


def _symbol_key(value: str) -> str:
    normalized = str(value or "").strip().upper()
    match = _CODE.search(normalized)
    return match.group(1) if match else normalized


def _symbol_candidates(symbol: str, market: str) -> list[str]:
    normalized = str(symbol or "").strip().upper()
    rows = [normalized]
    code = _symbol_key(normalized)
    if market == "CN_EQUITY" and re.fullmatch(r"\d{6}", code) and "." not in normalized:
        if code.startswith(("4", "8")):
            suffix = "BJ"
        elif code.startswith(("5", "6", "9")):
            suffix = "SH"
        else:
            suffix = "SZ"
        rows.append(f"{code}.{suffix}")
    return list(dict.fromkeys(rows))


def _unique_symbols(values: Iterable[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value or "").strip().upper()
        if not symbol:
            continue
        key = _symbol_key(symbol)
        if key in seen:
            continue
        seen.add(key)
        rows.append(symbol)
    return rows


def _unique_refs(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        row = dict(value)
        key = (
            str(row.get("type") or ""),
            str(row.get("id") or ""),
            str(row.get("locator") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


__all__ = [
    "FORMAL_REPORT_INTENTS",
    "DatabaseReportInputGateway",
    "ReportInputError",
    "ReportInputGateway",
    "ReportInputService",
    "bundle_hashes_for_symbols",
    "normalize_manifest",
    "slots_for_manifest",
    "validate_bundle_only_input",
]
