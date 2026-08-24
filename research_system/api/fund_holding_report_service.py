from __future__ import annotations

import re
import zipfile
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from domain_packs.finance_ashare.holding_reports import (
    StaleHoldingsConfirmationRequired,
    freeze_fund_holdings,
    require_stale_confirmation,
)
from domain_packs.finance_ashare.reporting import (
    render_uzi_native_html,
    render_uzi_native_pdf,
)
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.id_utils import generate_ulid
from research_system.market_data.report_inputs import (
    ReportInputError,
    bundle_hashes_for_symbols,
    normalize_manifest,
    slots_for_manifest,
)
from research_system.runtime.presentation.display import (
    localize_public_error_fields,
    localize_runtime_error,
)
from research_system.runtime.storage.jsonl import append_jsonl, read_json, write_json
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.protocol import RequestEnvelope

_BATCH_FILE = "fund_holding_batch.json"
_DELIVERABLE_ITEM_STATUSES = {"completed"}
_RECOVERABLE_RUN_STATUSES = {"running", "paused", "waiting_approval", "gated", "timed_out", "failed", "cancelled"}


class FundHoldingBatchNotFound(FileNotFoundError):
    pass


class FundHoldingBatchConflict(ValueError):
    pass


class FundHoldingReportsUnavailable(ValueError):
    pass


class FundHoldingReportService:
    """Persist and supervise one fund-to-stock-report fan-out outside Pack logic."""

    def __init__(self, *, settings, agent_os, run_service) -> None:
        self.settings = settings
        self.agent_os = agent_os
        self.run_service = run_service
        self.max_concurrency = max(
            1,
            min(int(getattr(settings, "fund_holding_report_concurrency", 2)), 5),
        )
        self.llm_max_concurrency = max(
            1,
            int(getattr(settings, "uzi_llm_max_concurrency", 2)),
        )
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_concurrency,
            thread_name_prefix="cognitrade-fund-holding",
        )
        self._futures: dict[str, Future] = {}
        self._closed = False
        binder = getattr(run_service, "bind_fund_holding_reports", None)
        if callable(binder):
            binder(self)
        self._recover_batches()

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def recover_pending_batches(self) -> None:
        """Resume persisted batches after the DB report boundary is bound."""

        self._recover_batches()

    @property
    def report_input_service(self):
        """Resolve the optional DB-backed Bundle boundary lazily.

        The API lifespan binds it after the shared database is constructed, so
        the holding coordinator must not snapshot the value in ``__init__``.
        """

        return getattr(self.run_service, "report_input_service", None)

    def _has_fund_report_lineage(self) -> bool:
        service = self.report_input_service
        return bool(
            service is not None
            and getattr(service, "has_fund_report_lineage", False)
        )

    def _trusted_fund_manifest(
        self,
        manifest: dict[str, Any],
        *,
        fund_code: str,
        symbols: Iterable[str],
        user_id: str | None,
        tenant_id: str | None,
    ) -> dict[str, Any]:
        service = self.report_input_service
        loader = getattr(service, "load_fund_report_manifest", None)
        if callable(loader):
            return loader(
                manifest,
                fund_code=fund_code,
                expected_symbols=symbols,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        return normalize_manifest(manifest, expected_symbols=list(symbols))

    @staticmethod
    def _supported_symbols(state: dict[str, Any]) -> list[str]:
        return [
            str(item.get("symbol") or "").strip()
            for item in state.get("holdings") or []
            if item.get("supported") and str(item.get("symbol") or "").strip()
        ]

    @staticmethod
    def _assert_source_matches_fund_manifest(
        frozen: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        """Keep the legacy display artifact aligned with the DB authority."""

        holdings = [
            dict(item)
            for item in frozen.get("holdings") or []
            if isinstance(item, dict)
        ]
        manifest_items = [
            dict(item)
            for item in manifest.get("items") or []
            if isinstance(item, dict)
        ]

        def code(value: object) -> str:
            match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or "").upper())
            return match.group(1) if match else str(value or "").strip().upper()

        if (
            len(holdings) != 10
            or any(not item.get("supported") for item in holdings)
            or [code(item.get("symbol")) for item in holdings]
            != [code(item.get("symbol")) for item in manifest_items]
        ):
            raise ReportInputError(
                "fund_holding_portfolio_mismatch",
                "原基金快照必须与父基金 PIT Bundle 的前十大 A 股顺序完全一致",
            )
        for holding, item in zip(holdings, manifest_items, strict=True):
            try:
                matches = abs(
                    float(holding.get("fund_weight_pct"))
                    - float(item.get("weight"))
                ) <= 1e-8
            except (TypeError, ValueError):
                matches = False
            if not matches:
                raise ReportInputError(
                    "fund_holding_weight_mismatch",
                    f"{holding.get('symbol')} 的原基金快照权重与父基金 Bundle 不一致",
                )

    def freeze_report_inputs(
        self,
        symbols: Iterable[str],
        *,
        as_of_date: date | str,
        profile: str,
        pin_set_hash: str,
        user_id: str | None,
        tenant_id: str | None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        service = self.report_input_service
        if service is None:
            return None
        resolved_context = dict(context or {})
        fund_code = str(resolved_context.get("fund_code") or "").strip()
        if fund_code and callable(getattr(service, "freeze_fund_holdings", None)):
            return service.freeze_fund_holdings(
                fund_code=fund_code,
                expected_symbols=symbols,
                as_of_date=as_of_date,
                tenant_id=tenant_id,
                user_id=user_id,
                workflow_pins={
                    "strategy_pin_set_hash": pin_set_hash,
                    "research_profile": profile,
                },
                context=resolved_context,
                fund_bundle_id=str(
                    resolved_context.get("fund_bundle_id") or ""
                ).strip()
                or None,
            )
        return service.freeze_symbols(
            symbols,
            as_of_date=as_of_date,
            tenant_id=tenant_id,
            user_id=user_id,
            workflow_pins={
                "strategy_pin_set_hash": pin_set_hash,
                "research_profile": profile,
            },
            context=resolved_context,
        )

    def frozen_report_manifest(
        self,
        batch_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the original immutable manifest for node-level recovery."""

        state = self._load_batch(batch_id, user_id=user_id, tenant_id=tenant_id)
        manifest = state.get("_report_data_manifest")
        if not isinstance(manifest, dict):
            return None
        return self._trusted_fund_manifest(
            manifest,
            fund_code=str(state.get("fund_code") or ""),
            symbols=self._supported_symbols(state),
            user_id=user_id,
            tenant_id=tenant_id,
        )

    def create_batch(
        self,
        source_run_id: str,
        *,
        fund_code: str,
        profile: str,
        source_artifact_id: str | None = None,
        confirm_stale: bool = False,
        force: bool = False,
        top_n: int = 10,
        as_of_date: date | str | None = None,
        pin_set_hash: str | None = None,
        canonical_reuse: dict[str, dict[str, Any]] | None = None,
        selected_symbols: Iterable[str] | None = None,
        report_manifest: dict[str, Any] | None = None,
        fund_bundle_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_profile = self._profile(profile)
        if self._has_fund_report_lineage() and int(top_n) != 10:
            raise ReportInputError(
                "fund_top_ten_required",
                "数据库基金报告路径必须且只能分析前十大持仓",
            )
        payload = self.run_service.artifacts(
            source_run_id,
            artifact_type="fund_snapshot",
            status="active",
            user_id=user_id,
            tenant_id=tenant_id,
        )
        frozen = freeze_fund_holdings(
            list(payload.get("artifacts") or []),
            fund_code=fund_code,
            source_artifact_id=source_artifact_id,
            top_n=top_n,
        )
        require_stale_confirmation(frozen, confirmed=confirm_stale)
        normalized_as_of_date = str(
            as_of_date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        )[:10]
        normalized_pin_set_hash = str(pin_set_hash or "legacy-unpinned")
        supported_symbols = [
            str(row.get("symbol") or "").strip()
            for row in frozen.get("holdings") or []
            if bool(row.get("supported")) and str(row.get("symbol") or "").strip()
        ]
        if report_manifest is None and self.report_input_service is not None:
            report_manifest = self.freeze_report_inputs(
                supported_symbols,
                as_of_date=normalized_as_of_date,
                profile=normalized_profile,
                pin_set_hash=normalized_pin_set_hash,
                user_id=user_id,
                tenant_id=tenant_id,
                context={
                    "source_run_id": source_run_id,
                    "source_fund_artifact_id": frozen["source_artifact_id"],
                    "fund_code": frozen["fund_code"],
                    "fund_bundle_id": fund_bundle_id,
                    "holdings": list(frozen.get("holdings") or []),
                },
            )
        if report_manifest is not None:
            report_manifest = self._trusted_fund_manifest(
                report_manifest,
                fund_code=str(frozen["fund_code"]),
                symbols=supported_symbols,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            if self._has_fund_report_lineage():
                self._assert_source_matches_fund_manifest(frozen, report_manifest)
        report_manifest_hash = str((report_manifest or {}).get("manifest_hash") or "legacy-unverified")
        bundle_manifest_hashes = bundle_hashes_for_symbols(
            report_manifest,
            supported_symbols,
        )
        reusable = {
            str(symbol): dict(payload)
            for symbol, payload in (canonical_reuse or {}).items()
            if isinstance(payload, dict)
        }
        selected = (
            {str(symbol).strip() for symbol in selected_symbols if str(symbol).strip()}
            if selected_symbols is not None
            else None
        )
        with self._lock:
            existing = self._find_existing(
                source_run_id=source_run_id,
                source_artifact_id=str(frozen["source_artifact_id"]),
                fund_code=str(frozen["fund_code"]),
                profile=normalized_profile,
                top_n=int(frozen["top_n"]),
                as_of_date=normalized_as_of_date,
                pin_set_hash=normalized_pin_set_hash,
                report_manifest_hash=report_manifest_hash,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            # Explicit regeneration creates history only after the matching
            # batch is terminal; it must never duplicate active stock work.
            if existing is not None and (
                existing.get("status") == "running" or not force
            ):
                return existing

            batch_id = generate_ulid()
            request = RequestEnvelope(
                intent="fund_holding_research",
                user_query=(
                    f"为基金 {frozen['fund_name']}（{frozen['fund_code']}）的最新披露前 {frozen['top_n']} 大持仓"
                    f"生成 {normalized_profile} 档股票报告"
                ),
                user_id=user_id,
                tenant_id=tenant_id,
                idempotency_key=f"fund-holding-batch:{batch_id}",
                slots={
                    "source_fund_run_id": source_run_id,
                    "source_fund_artifact_id": frozen["source_artifact_id"],
                    "fund_code": frozen["fund_code"],
                    "research_profile": normalized_profile,
                    "top_n": int(frozen["top_n"]),
                    "as_of_date": normalized_as_of_date,
                    "pin_set_hash": normalized_pin_set_hash,
                    "report_manifest_id": (report_manifest or {}).get("manifest_id"),
                    "report_manifest_hash": report_manifest_hash,
                },
                constraints={"managed_fund_holding_batch": True},
            )
            self.agent_os.run_store.create_run(
                request,
                "fund_holding_research",
                run_id=batch_id,
            )
            now = self._now()
            state: dict[str, Any] = {
                "batch_id": batch_id,
                "request_id": request.request_id,
                "source_run_id": source_run_id,
                "source_artifact_id": frozen["source_artifact_id"],
                "fund_code": frozen["fund_code"],
                "fund_name": frozen["fund_name"],
                "fund_type": frozen.get("fund_type"),
                "profile": normalized_profile,
                "top_n": int(frozen["top_n"]),
                "as_of_date": normalized_as_of_date,
                "pin_set_hash": normalized_pin_set_hash,
                "report_manifest_id": (report_manifest or {}).get("manifest_id"),
                "report_manifest_hash": report_manifest_hash,
                "bundle_manifest_hashes": bundle_manifest_hashes,
                "_report_data_manifest": report_manifest,
                "status": "running",
                "concurrency": self.max_concurrency,
                "llm_concurrency": self.llm_max_concurrency,
                "holding_source": frozen["holding_source"],
                "is_fund_of_funds": bool(frozen.get("is_fund_of_funds")),
                "disclosure_date": frozen.get("disclosure_date"),
                "disclosure_age_days": frozen.get("disclosure_age_days"),
                "stale_level": frozen.get("stale_level") or "unknown",
                "cancel_requested": False,
                "source_data_gap": list(frozen.get("data_gap") or []),
                "data_gap": list(frozen.get("data_gap") or []),
                "holdings": [],
                "counts": {},
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }
            for holding in list(frozen.get("holdings") or []):
                item = {
                    **dict(holding),
                    "child_run_id": None,
                    "attempt_run_ids": [],
                    "report_available": False,
                    "retry_count": 0,
                    "data_gap": [],
                    "error": None,
                    "started_at": None,
                    "completed_at": None,
                }
                if item.get("supported"):
                    cached = reusable.get(str(item["symbol"]))
                    if cached and cached.get("run_id"):
                        child_run_id = str(cached["run_id"])
                        item["child_run_id"] = child_run_id
                        item["attempt_run_ids"] = [child_run_id]
                        item["status"] = "completed"
                        item["report_available"] = True
                        item["canonical_reused"] = True
                        item["canonical_artifact_id"] = cached.get("artifact_id")
                        item["canonical_content_hash"] = cached.get("content_hash")
                        item["confidence"] = cached.get("confidence")
                        item["completed_at"] = now
                    elif selected is not None and str(item["symbol"]) not in selected:
                        item["status"] = "failed"
                        item["canonical_reused"] = False
                        item["data_gap"] = ["not_selected_for_retry"]
                        item["error"] = "该节点未包含在本次手动重试范围内"
                        item["completed_at"] = now
                    else:
                        child_run_id = self._prepare_child(state, item)
                        item["child_run_id"] = child_run_id
                        item["attempt_run_ids"] = [child_run_id]
                        item["status"] = "queued"
                        item["canonical_reused"] = False
                else:
                    item["status"] = "unsupported"
                    item["data_gap"] = ["holding_market_not_supported"]
                state["holdings"].append(item)
            self._refresh_parent_status(state)
            self._write_batch(state, event_type="fund_holding_batch_created")
            for item in state["holdings"]:
                if item["status"] == "queued":
                    self._submit_item(batch_id, str(item["symbol"]), str(item["child_run_id"]))
            return self._public(state)

    def list_batches(
        self,
        *,
        source_run_id: str | None = None,
        fund_code: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for root in self._batch_roots():
            state = read_json(root / _BATCH_FILE)
            if not isinstance(state, dict):
                continue
            if source_run_id and str(state.get("source_run_id") or "") != source_run_id:
                continue
            if fund_code and str(state.get("fund_code") or "") != str(fund_code):
                continue
            if not self._matches_scope(root, user_id=user_id, tenant_id=tenant_id):
                continue
            rows.append(
                self._public_delivery_state(
                    state,
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
            )
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows

    def detail(
        self,
        batch_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        root = self._batch_root(batch_id)
        self._assert_scope(root, user_id=user_id, tenant_id=tenant_id)
        state = read_json(root / _BATCH_FILE)
        if not isinstance(state, dict):
            raise FundHoldingBatchNotFound(batch_id)
        return self._public_delivery_state(
            state,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    def run_detail(
        self,
        batch_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.detail(batch_id, user_id=user_id, tenant_id=tenant_id)
        return {
            "mode": "fund_holding_batch",
            "request": read_json(self._batch_root(batch_id) / "request.json"),
            "snapshot": state,
            "turn": None,
            "task": {"task_type": "fund_holding_research", "execution_mode": "batch"},
            "artifacts": self.artifacts(
                batch_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )["artifacts"],
        }

    def artifacts(
        self,
        batch_id: str,
        *,
        artifact_type: str | None = None,
        status: str | None = "active",
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        root = self._batch_root(batch_id)
        self._assert_scope(root, user_id=user_id, tenant_id=tenant_id)
        state = read_json(root / _BATCH_FILE) or {}
        public_state = self._public_delivery_state(
            state,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        rows = ArtifactBoard(root / "artifact_board.jsonl").list_artifacts(
            thread_id=None,
            artifact_type=artifact_type,
            status=status,
        )
        artifact_rows = [row.model_dump(mode="json") for row in rows]
        if public_state.get("report_manifest_status") == "invalidated":
            # Preserve the immutable ArtifactBoard history on disk while
            # projecting the revoked lineage into every public batch view.
            for row in artifact_rows:
                if row.get("artifact_type") != "fund_holding_report_batch":
                    continue
                content = dict(row.get("content") or {})
                content.update(
                    {
                        "status": public_state.get("status"),
                        "counts": public_state.get("counts"),
                        "holdings": public_state.get("holdings"),
                        "data_gap": public_state.get("data_gap"),
                        "report_manifest_status": "invalidated",
                    }
                )
                row["content"] = content
                row["risk_flags"] = list(
                    dict.fromkeys(
                        [
                            *list(row.get("risk_flags") or []),
                            "fund_report_parent_dependency_invalidated",
                        ]
                    )
                )
        return {
            "run_id": batch_id,
            "mode": "fund_holding_batch",
            "status": public_state.get("status"),
            "turn": None,
            "artifacts": artifact_rows,
        }

    def history_row(self, batch_id: str) -> dict[str, Any]:
        state = read_json(self._batch_root(batch_id) / _BATCH_FILE)
        if not isinstance(state, dict):
            raise FundHoldingBatchNotFound(batch_id)
        public_state = self._public_delivery_state(state)
        counts = dict(public_state.get("counts") or {})
        message = (
            f"{public_state.get('fund_name')} 前十大持仓报告："
            f"{counts.get('deliverable', 0)}/{counts.get('supported', 0)} 可交付"
        )
        return {
            "run_id": batch_id,
            "request_id": state.get("request_id"),
            "intent": "fund_holding_research",
            "domain_id": "finance_ashare",
            "mode": "fund_holding_batch",
            "status": public_state.get("status"),
            "graph_id": None,
            "graph_revision": None,
            "updated_at": public_state.get("updated_at"),
            "message": message,
            "display_summary": f"{public_state.get('status')}: {message}",
            "child_run_ids": [
                item.get("child_run_id")
                for item in public_state.get("holdings") or []
                if item.get("child_run_id")
            ],
            "child_run_count": counts.get("supported", 0),
            "task_type": "fund_holding_research",
            "execution_mode": "batch",
        }

    def metrics(self, batch_id: str, **scope) -> dict[str, Any]:
        state = self.detail(batch_id, **scope)
        duration_seconds = self._duration_seconds(state)
        return {
            "run_id": batch_id,
            "status": state["status"],
            "counts": state["counts"],
            "tool_call_count": 0,
            "usage": {
                "tokens": 0,
                "cost": 0.0,
                "duration_seconds": duration_seconds,
            },
            "concurrency": {
                "holding_reports": int(
                    state.get("concurrency") or self.max_concurrency
                ),
                "uzi_llm": int(
                    state.get("llm_concurrency") or self.llm_max_concurrency
                ),
            },
            "node_status_counts": dict(Counter(item["status"] for item in state["holdings"])),
            "event_type_counts": {},
            "graph_event_counts": {},
        }

    @staticmethod
    def _duration_seconds(state: dict[str, Any]) -> float:
        started = str(state.get("created_at") or "").strip()
        finished = str(
            state.get("completed_at")
            or state.get("updated_at")
            or ""
        ).strip()
        if not started or not finished:
            return 0.0
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            finish_dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return round(max((finish_dt - start_dt).total_seconds(), 0.0), 3)

    def steps(self, batch_id: str, **scope) -> dict[str, Any]:
        state = self.detail(batch_id, **scope)
        return {
            "run_id": batch_id,
            "steps": [
                {
                    "node_id": f"holding-{item['rank']}-{item['symbol']}",
                    "capability": "finance.uzi.native.report.compose",
                    "status": item["status"],
                    "objective": f"生成 {item['name']}（{item['symbol']}）UZI 报告",
                    "child_run_id": item.get("child_run_id"),
                }
                for item in state["holdings"]
            ],
            "step_counts": state["counts"],
        }

    def trace(self, batch_id: str, **scope) -> dict[str, Any]:
        state = self.detail(batch_id, **scope)
        return {
            "run_id": batch_id,
            "mode": "fund_holding_batch",
            "source_run_id": state["source_run_id"],
            "source_artifact_id": state["source_artifact_id"],
            "child_runs": [
                {
                    "symbol": item["symbol"],
                    "child_run_id": item.get("child_run_id"),
                    "attempt_run_ids": item.get("attempt_run_ids") or [],
                    "status": item["status"],
                }
                for item in state["holdings"]
            ],
        }

    def cancel(
        self,
        batch_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        running_ids: list[str] = []
        with self._lock:
            state = self._load_batch(batch_id, user_id=user_id, tenant_id=tenant_id)
            state["cancel_requested"] = True
            for item in state["holdings"]:
                if item["status"] == "queued":
                    item["status"] = "cancelled"
                    item["completed_at"] = self._now()
                    future = self._futures.get(self._future_key(batch_id, item["symbol"], item.get("child_run_id")))
                    if future is not None:
                        future.cancel()
                elif item["status"] == "running" and item.get("child_run_id"):
                    running_ids.append(str(item["child_run_id"]))
            self._refresh_parent_status(state)
            self._write_batch(state, event_type="fund_holding_batch_cancel_requested")
        for child_run_id in running_ids:
            try:
                self.run_service.cancel(
                    child_run_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
            except Exception:
                continue
        return self.detail(batch_id, user_id=user_id, tenant_id=tenant_id)

    def retry(
        self,
        batch_id: str,
        *,
        symbols: Iterable[str] | None = None,
        include_cancelled: bool = False,
        force: bool = False,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        selected = {str(symbol).strip() for symbol in (symbols or []) if str(symbol).strip()}
        scheduled: list[tuple[str, str]] = []
        with self._lock:
            state = self._load_batch(batch_id, user_id=user_id, tenant_id=tenant_id)
            frozen_manifest = state.get("_report_data_manifest")
            if isinstance(frozen_manifest, dict):
                state["_report_data_manifest"] = self._trusted_fund_manifest(
                    frozen_manifest,
                    fund_code=str(state.get("fund_code") or ""),
                    symbols=self._supported_symbols(state),
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
            allowed = {"failed"} | ({"cancelled"} if include_cancelled else set())
            if force:
                allowed.update({"completed", "completed_partial", "cancelled"})
            for item in state["holdings"]:
                if item["status"] not in allowed or (selected and item["symbol"] not in selected):
                    continue
                child_run_id = self._prepare_child(state, item)
                item["child_run_id"] = child_run_id
                item.setdefault("attempt_run_ids", []).append(child_run_id)
                item["status"] = "queued"
                item["report_available"] = False
                item["retry_count"] = 0
                item["data_gap"] = []
                item["error"] = None
                item["started_at"] = None
                item["completed_at"] = None
                scheduled.append((str(item["symbol"]), child_run_id))
            if not scheduled:
                raise FundHoldingBatchConflict(
                    "没有符合重试条件的终态持仓报告"
                )
            # A persisted batch may have been created under an older worker
            # limit. Retries use the current service pool, so keep the public
            # batch contract aligned with the concurrency actually enforced.
            state["concurrency"] = self.max_concurrency
            state["cancel_requested"] = False
            state["status"] = "running"
            state["completed_at"] = None
            self._refresh_parent_status(state)
            self._write_batch(state, event_type="fund_holding_batch_retry_started")
            for symbol, child_run_id in scheduled:
                self._submit_item(batch_id, symbol, child_run_id)
            return self._public(state)

    def build_pdf_zip(
        self,
        batch_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> tuple[bytes, str, int]:
        state = self._public_delivery_state(
            self._load_batch(batch_id, user_id=user_id, tenant_id=tenant_id),
            user_id=user_id,
            tenant_id=tenant_id,
            force_verify=True,
        )
        deliverable = [
            item
            for item in state["holdings"]
            if item.get("report_available") and item.get("child_run_id")
        ]
        if not deliverable:
            raise FundHoldingReportsUnavailable("该批次没有可交付的股票报告")
        buffer = BytesIO()
        included = 0
        zip_errors: list[str] = []
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in deliverable:
                try:
                    payload = self.run_service.artifacts(
                        str(item["child_run_id"]),
                        artifact_type=None,
                        status="active",
                        user_id=user_id,
                        tenant_id=tenant_id,
                    )
                    payload = localize_public_error_fields(payload)
                    rendered = render_uzi_native_html(
                        list(payload.get("artifacts") or []),
                        run_id=str(item["child_run_id"]),
                    )
                    pdf = render_uzi_native_pdf(rendered.html)
                    filename = self._report_filename(item)
                    archive.writestr(filename, pdf)
                    included += 1
                except Exception:
                    zip_errors.append(f"{item['symbol']} {item['name']}：PDF 打包失败")
            archive.writestr("README.txt", self._zip_readme(state, included=included, zip_errors=zip_errors))
        if included == 0:
            raise FundHoldingReportsUnavailable("所有可交付报告的 PDF 渲染均失败")
        filename = f"fund-{state['fund_code']}-holding-reports-{state['profile']}.zip"
        return buffer.getvalue(), filename, included

    def is_batch(self, run_id: str) -> bool:
        try:
            return (self.agent_os.run_store.load_run_paths(run_id).root / _BATCH_FILE).is_file()
        except FileNotFoundError:
            return False

    def is_hidden_child(self, run_id: str) -> bool:
        try:
            request = read_json(self.agent_os.run_store.load_run_paths(run_id).root / "request.json")
        except FileNotFoundError:
            return False
        constraints = request.get("constraints") if isinstance(request, dict) else None
        return isinstance(constraints, dict) and constraints.get("fund_holding_report_child") is True

    def require_child_report_lineage(
        self,
        child_run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Block direct child delivery after its parent fund input is revoked."""

        request = read_json(
            self.agent_os.run_store.load_run_paths(child_run_id).root / "request.json"
        ) or {}
        constraints = request.get("constraints") if isinstance(request, dict) else {}
        parent_batch_id = str(
            (constraints or {}).get("parent_batch_id") or ""
        ).strip()
        if not parent_batch_id:
            raise FundHoldingReportsUnavailable("持仓股票报告缺少父批次引用")
        state = self._load_batch(
            parent_batch_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if self._has_fund_report_lineage() and not isinstance(
            state.get("_report_data_manifest"),
            dict,
        ):
            raise FundHoldingReportsUnavailable(
                "持仓股票报告缺少可验证的父基金冻结 manifest"
            )
        public_state = self._public_delivery_state(
            state,
            user_id=user_id,
            tenant_id=tenant_id,
            force_verify=True,
        )
        if public_state.get("report_manifest_status") == "invalidated":
            raise FundHoldingReportsUnavailable(
                "父基金 PIT Bundle 或冻结报告 manifest 已失效"
            )

    def _prepare_child(self, state: dict[str, Any], item: dict[str, Any]) -> str:
        child_run_id = generate_ulid()
        attempt = len(item.get("attempt_run_ids") or []) + 1
        parent_request = read_json(
            self.agent_os.run_store.load_run_paths(state["batch_id"]).root / "request.json"
        ) or {}
        slots: dict[str, Any] = {"symbols": [item["symbol"]]}
        constraints: dict[str, Any] = {
            "research_profile": state["profile"],
            "fund_holding_report_child": True,
            "canonical_uzi_analysis": True,
            "analysis_as_of_date": state.get("as_of_date"),
            "strategy_pin_set_hash": state.get("pin_set_hash"),
            "parent_batch_id": state["batch_id"],
            "hidden_from_history": True,
        }
        frozen_manifest = state.get("_report_data_manifest")
        if isinstance(frozen_manifest, dict):
            slots.update(slots_for_manifest(frozen_manifest, symbols=[str(item["symbol"])]))
            constraints.update(
                {
                    "data_source_mode": "bundle_only",
                    "report_manifest_id": frozen_manifest.get("manifest_id"),
                    "report_manifest_hash": frozen_manifest.get("manifest_hash"),
                    "market_data_bundle_manifest_hash": (
                        state.get("bundle_manifest_hashes") or {}
                    ).get(str(item["symbol"])),
                }
            )
        request = RequestEnvelope(
            intent="stock_check",
            user_query=f"分析A股 {item['symbol']} {item['name']}，生成可审计的UZI股票研究报告。",
            thread_id=f"holding:{state['batch_id']}:{item['symbol']}:{attempt}",
            user_id=parent_request.get("user_id"),
            tenant_id=parent_request.get("tenant_id"),
            slots=slots,
            constraints=constraints,
        )
        self.agent_os.prepare_run(request, run_id=child_run_id, intent="stock_check")
        return child_run_id

    def _submit_item(self, batch_id: str, symbol: str, child_run_id: str) -> None:
        if self._closed:
            return
        key = self._future_key(batch_id, symbol, child_run_id)
        if key in self._futures and not self._futures[key].done():
            return
        future = self._executor.submit(self._execute_item, batch_id, symbol, child_run_id)
        self._futures[key] = future
        future.add_done_callback(lambda _future, current=key: self._forget_future(current))

    def _execute_item(self, batch_id: str, symbol: str, child_run_id: str) -> None:
        with self._lock:
            try:
                state = self._load_batch(batch_id)
            except FundHoldingBatchNotFound:
                return
            item = self._item(state, symbol=symbol, child_run_id=child_run_id)
            if item is None or item["status"] not in {"queued", "running"}:
                return
            if (
                isinstance(state.get("_report_data_manifest"), dict)
                and self.report_input_service is None
            ):
                # API construction binds the strategy database after this
                # coordinator. Leave the persisted child pending until
                # AgentOSRunService.bind_report_inputs explicitly resumes it.
                return
            if state.get("cancel_requested") and item["status"] in {"queued", "running"}:
                item["status"] = "cancelled"
                item["completed_at"] = self._now()
                self._refresh_parent_status(state)
                self._write_batch(state, event_type="fund_holding_child_cancelled")
                return
            item["status"] = "running"
            item["started_at"] = item.get("started_at") or self._now()
            self._refresh_parent_status(state)
            self._write_batch(state, event_type="fund_holding_child_started")

        final_status = "failed"
        report_available = False
        data_gap: list[str] = []
        error: str | None = None
        retry_count = 0
        terminal_detail: dict[str, Any] = {}
        try:
            request_payload = read_json(self.agent_os.run_store.load_run_paths(child_run_id).root / "request.json")
            request = RequestEnvelope.model_validate(request_payload or {})
            report_inputs = self.report_input_service
            if report_inputs is not None:
                # Re-cross the DB trust boundary immediately before every
                # first execution or resumed attempt.  Parent-fund revocation
                # therefore blocks the child even if its request file was
                # prepared while the manifest was still valid.
                request = report_inputs.freeze_request(request)
            seen_recovery_states: set[tuple[Any, ...]] = set()
            while True:
                detail = self.agent_os.inspect_run(child_run_id)
                mode = str(detail.get("mode") or "")
                current = self._detail_status(detail)
                if mode == "pending":
                    self.agent_os.handle_request(request, run_id=child_run_id)
                elif current in _RECOVERABLE_RUN_STATUSES:
                    self.agent_os.resume(child_run_id)
                elif current != "completed":
                    break
                detail = self.agent_os.inspect_run(child_run_id)
                terminal_detail = detail
                final_status = self._detail_status(detail) or "failed"
                report_available, data_gap = self._report_state(detail)
                if report_available and final_status in {"completed", "completed_partial"}:
                    break
                if final_status not in _RECOVERABLE_RUN_STATUSES:
                    break
                recovery_state = self._recovery_fingerprint(detail)
                if recovery_state in seen_recovery_states:
                    break
                seen_recovery_states.add(recovery_state)
                retry_count += 1
                with self._lock:
                    state = self._load_batch(batch_id)
                    item = self._item(state, symbol=symbol, child_run_id=child_run_id)
                    if item is None or state.get("cancel_requested"):
                        break
                    item["retry_count"] = retry_count
                    self._write_batch(state, event_type="fund_holding_child_auto_retry")
        except Exception as exc:
            # The graph may have committed both delivery artifacts before a
            # response/synthesis post-processing step failed.  Reconcile from
            # the durable ArtifactBoard so a usable stock report is not
            # discarded merely because the outer response could not finalize.
            try:
                detail = self.agent_os.inspect_run(child_run_id)
                terminal_detail = detail
                reconciled_report, reconciled_gaps = self._report_state(detail)
                graph_status = str((detail.get("snapshot") or {}).get("status") or "")
                reconciled_status = (
                    graph_status
                    if reconciled_report and graph_status == "completed"
                    else self._detail_status(detail)
                )
            except Exception:
                reconciled_status = None
                reconciled_report = False
                reconciled_gaps = []
            if reconciled_report and reconciled_status == "completed":
                final_status = reconciled_status
                report_available = True
                data_gap = list(dict.fromkeys(reconciled_gaps))
                error = None
            else:
                final_status = "failed"
                error = localize_runtime_error(exc, fallback="持仓股票报告执行失败，请稍后重试。")
                data_gap = ["holding_stock_report_execution_failed"]

        if not report_available and error is None:
            error = self._terminal_report_error(terminal_detail, final_status)

        with self._lock:
            try:
                state = self._load_batch(batch_id)
            except FundHoldingBatchNotFound:
                return
            item = self._item(state, symbol=symbol, child_run_id=child_run_id)
            if item is None:
                return
            item["retry_count"] = retry_count
            item["report_available"] = report_available
            item["data_gap"] = list(dict.fromkeys(data_gap))
            item["error"] = error
            if report_available and final_status in {"completed", "completed_partial"}:
                item["status"] = "completed"
                item["error"] = None
            elif final_status == "cancelled" or (state.get("cancel_requested") and not report_available):
                item["status"] = "cancelled"
            else:
                item["status"] = "failed"
                item["error"] = item["error"] or f"子运行以 {final_status} 状态结束，未生成完整报告"
            item["completed_at"] = self._now()
            self._refresh_parent_status(state)
            self._write_batch(state, event_type="fund_holding_child_finished")

    def _recover_batches(self) -> None:
        for root in self._batch_roots():
            state = read_json(root / _BATCH_FILE)
            if not isinstance(state, dict) or state.get("status") not in {"running"}:
                continue
            if (
                isinstance(state.get("_report_data_manifest"), dict)
                and self.report_input_service is None
            ):
                # DB-backed manifests require a live loader before any child
                # can be resumed. Legacy batches without a manifest keep their
                # historical standalone recovery behaviour.
                continue
            for item in state.get("holdings") or []:
                if item.get("status") in {"queued", "running"} and item.get("child_run_id"):
                    self._submit_item(root.name, str(item["symbol"]), str(item["child_run_id"]))

    def _refresh_parent_status(self, state: dict[str, Any]) -> None:
        items = list(state.get("holdings") or [])
        status_counts = Counter(str(item.get("status") or "") for item in items)
        deliverable = sum(
            1
            for item in items
            if item.get("report_available") and item.get("status") in _DELIVERABLE_ITEM_STATUSES
        )
        supported = sum(1 for item in items if item.get("supported"))
        counts = {
            "total": len(items),
            "supported": supported,
            "queued": status_counts.get("queued", 0),
            "running": status_counts.get("running", 0),
            "deliverable": deliverable,
            "failed": status_counts.get("failed", 0),
            "cancelled": status_counts.get("cancelled", 0),
            "unsupported": status_counts.get("unsupported", 0),
        }
        state["counts"] = counts
        gaps = list(state.get("source_data_gap") or [])
        if counts["unsupported"]:
            gaps.append("unsupported_holding_market")
        if any(item.get("status") == "completed_partial" for item in items):
            gaps.append("holding_child_report_data_gap")
        active = counts["queued"] + counts["running"]
        if active:
            state["status"] = "running"
            state["completed_at"] = None
        elif state.get("cancel_requested") and counts["cancelled"]:
            state["status"] = "completed_partial"
            gaps.append("holding_batch_cancelled")
        elif supported == 0:
            state["status"] = "completed_partial"
        elif deliverable == supported and not gaps and all(item.get("status") == "completed" for item in items if item.get("supported")):
            state["status"] = "completed"
        elif deliverable > 0:
            state["status"] = "completed_partial"
            gaps.append("holding_child_reports_incomplete")
        elif counts["failed"] == supported:
            state["status"] = "failed"
            gaps.append("all_holding_stock_reports_failed")
        else:
            state["status"] = "completed_partial"
            gaps.append("holding_child_reports_incomplete")
        if state["status"] != "running":
            state["completed_at"] = state.get("completed_at") or self._now()
        state["data_gap"] = list(dict.fromkeys(gaps))

    def _write_batch(self, state: dict[str, Any], *, event_type: str) -> None:
        state["updated_at"] = self._now()
        root = self.agent_os.run_store.load_run_paths(str(state["batch_id"])).root
        write_json(
            root / "run_state.json",
            {
                "run_id": state["batch_id"],
                "request_id": state["request_id"],
                "intent": "fund_holding_research",
                "status": state["status"],
                "current_phase": "holding_reports",
                "active_agent": "fund_holding_report_coordinator",
                "updated_at": state["updated_at"],
            },
        )
        write_json(
            root / "summary.json",
            {
                "run_id": state["batch_id"],
                "request_id": state["request_id"],
                "intent": "fund_holding_research",
                "domain_id": "finance_ashare",
                "mode": "fund_holding_batch",
                "status": state["status"],
                "message": (
                    f"{state['fund_name']} 持仓股票报告 "
                    f"{state['counts'].get('deliverable', 0)}/{state['counts'].get('supported', 0)} 可交付"
                ),
                "updated_at": state["updated_at"],
            },
        )
        board = ArtifactBoard(root / "artifact_board.jsonl")
        previous = board.list_artifacts(
            thread_id=None,
            artifact_type="fund_holding_report_batch",
            status="active",
        )
        artifact_content = {
            key: value
            for key, value in state.items()
            if key not in {"request_id", "source_data_gap", "_report_data_manifest"}
        }
        board.add(
            ArtifactRecord(
                artifact_type="fund_holding_report_batch",
                run_id=state["batch_id"],
                thread_id=f"batch:{state['batch_id']}",
                request_id=state["request_id"],
                user_id=(read_json(root / "request.json") or {}).get("user_id"),
                tenant_id=(read_json(root / "request.json") or {}).get("tenant_id"),
                producer=ArtifactProducer(
                    executor="fund_holding_report_coordinator",
                    capability="finance.fund.holding_reports",
                    node_id="fund-holding-report-batch",
                    domain_id="finance_ashare",
                ),
                content=artifact_content,
                source_refs=[
                    {
                        "type": "fund_snapshot_artifact",
                        "id": state["source_artifact_id"],
                        "locator": state["source_run_id"],
                    }
                ],
                confidence=(
                    1.0
                    if state["status"] == "completed"
                    else 0.6
                    if state["counts"].get("deliverable")
                    else 0.0
                ),
                risk_flags=list(state.get("data_gap") or []),
                supersedes_artifact_id=previous[-1].artifact_id if previous else None,
                metadata={"profile": state["profile"], "managed_batch": True},
            )
        )
        event = {
            "event_id": generate_ulid(),
            "request_id": state["request_id"],
            "run_id": state["batch_id"],
            "thread_id": f"batch:{state['batch_id']}",
            "intent": "fund_holding_research",
            "event_type": event_type,
            "phase": "holding_reports",
            "actor": "fund_holding_report_coordinator",
            "agent": "fund_holding_report_coordinator",
            "message": f"基金持仓报告批次状态：{state['status']}",
            "payload": {"status": state["status"], "counts": state["counts"]},
            "artifacts": [],
            "created_at": state["updated_at"],
        }
        append_jsonl(root / "events.jsonl", event)
        append_jsonl(root / "execution_events.jsonl", event)
        if state["status"] in {"completed", "completed_partial", "failed", "cancelled"}:
            append_jsonl(
                root / "execution_events.jsonl",
                {
                    **event,
                    "event_id": generate_ulid(),
                    "event_type": "assistant_message_ready",
                    "delivery_status": state["status"],
                    "message": (
                        f"{state['fund_name']} 持仓股票报告已结束："
                        f"{state['counts'].get('deliverable', 0)}/"
                        f"{state['counts'].get('supported', 0)} 可交付。"
                    ),
                },
            )
        # Publish the batch snapshot last so readers never observe a terminal
        # state before its Artifact and terminal SSE event are durable.
        write_json(root / _BATCH_FILE, state)

    def _report_state(self, detail: dict[str, Any]) -> tuple[bool, list[str]]:
        active = [
            row
            for row in list(detail.get("artifacts") or [])
            if isinstance(row, dict) and str(row.get("status") or "active") == "active"
        ]
        native = next(
            (row for row in reversed(active) if row.get("artifact_type") == "uzi_native_analysis"),
            None,
        )
        report = next(
            (row for row in reversed(active) if row.get("artifact_type") == "research_report"),
            None,
        )
        gaps: list[str] = []
        for row in active:
            if row.get("artifact_type") not in {"uzi_native_analysis", "research_report"}:
                continue
            gaps.extend(str(item) for item in list(row.get("risk_flags") or []) if str(item).strip())
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            gaps.extend(str(item) for item in list(content.get("data_gap") or []) if str(item).strip())
            if row.get("artifact_type") == "uzi_native_analysis":
                for result in list(content.get("results") or []):
                    if not isinstance(result, dict):
                        continue
                    gaps.extend(
                        str(issue.get("issue") or issue.get("dim") or "uzi_self_review_issue")
                        for issue in list((result.get("self_review") or {}).get("issues") or [])
                        if isinstance(issue, dict) and issue.get("severity") in {"critical", "warning"}
                    )
        native_content = native.get("content") if isinstance(native, dict) and isinstance(native.get("content"), dict) else {}
        report_content = report.get("content") if isinstance(report, dict) and isinstance(report.get("content"), dict) else {}
        native_id = str(native.get("artifact_id") or "") if isinstance(native, dict) else ""
        available = bool(
            native
            and report
            and native_content.get("report_allowed") is True
            and int(native_content.get("critical_count") or 0) == 0
            and report_content.get("report_allowed") is True
            and int(report_content.get("critical_count") or 0) == 0
            and str(report_content.get("native_analysis_artifact_id") or "") == native_id
        )
        if native is None:
            gaps.append("uzi_native_analysis_missing")
        elif native_content.get("report_allowed") is not True or int(native_content.get("critical_count") or 0):
            gaps.append("uzi_critical_quality_gate_blocked")
        if report is None:
            gaps.append("uzi_native_research_report_missing")
        return available, list(dict.fromkeys(gaps))

    @staticmethod
    def _terminal_report_error(detail: dict[str, Any], final_status: str) -> str:
        """Expose every UZI critical and suggested fix instead of a generic gate label."""

        artifacts = [
            row
            for row in list(detail.get("artifacts") or [])
            if isinstance(row, dict) and str(row.get("status") or "active") == "active"
        ]
        native = next(
            (row for row in reversed(artifacts) if row.get("artifact_type") == "uzi_native_analysis"),
            None,
        )
        content = (
            native.get("content")
            if isinstance(native, dict) and isinstance(native.get("content"), dict)
            else {}
        )
        issue_rows = [
            issue
            for result in list(content.get("results") or [])
            if isinstance(result, dict)
            for issue in list((result.get("self_review") or {}).get("issues") or [])
            if isinstance(issue, dict) and str(issue.get("severity") or "").lower() == "critical"
        ]
        terminal_agent_failure = any(
            str(issue.get("dim") or "") == "agent_analysis"
            and str(issue.get("evidence") or "")
            in {"llm_transport_timeout", "llm_provider_unavailable"}
            for issue in issue_rows
        )
        critical_rows: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        has_non_agent_critical = False
        has_transport_timeout = False
        has_provider_unavailable = False
        for issue in issue_rows:
            dimension = str(issue.get("dim") or "")
            failure_kind = str(issue.get("evidence") or "")
            raw_issue = str(issue.get("issue") or "")
            if terminal_agent_failure and (
                (
                    dimension == "agent_analysis"
                    and (
                        failure_kind in {"file not found", "agent_reviewed!=true"}
                        or "agent_analysis.json 不存在" in raw_issue
                        or "Agent 分析缺失或未确认" in raw_issue
                    )
                )
                or (
                    dimension == "qualitative_deep_dive"
                    and "not_requested" in failure_kind
                )
            ):
                continue
            issue_text = FundHoldingReportService._public_gate_text(
                issue.get("issue"),
                "未说明的质量缺口",
            )
            suggested_fix = FundHoldingReportService._public_gate_text(
                issue.get("suggested_fix"),
                "",
            )
            has_non_agent_critical = has_non_agent_critical or dimension != "agent_analysis"
            if failure_kind == "llm_transport_timeout":
                has_transport_timeout = True
                suggested_fix = "模型服务响应超时；请稍后仅重试此项，系统会按当前研究档位重新生成结构化分析"
            elif failure_kind == "llm_provider_unavailable":
                has_provider_unavailable = True
                suggested_fix = "模型上游服务暂时不可用；系统已完成短退避重试，可稍后仅重试此项"
            elif dimension == "agent_analysis" and "可以忽略" in suggested_fix:
                suggested_fix = "在 Agent OS 中完成结构化 Agent 分析后重算与自审，不允许忽略该 critical"
            elif "run.py" in suggested_fix or "fetch_X" in suggested_fix:
                suggested_fix = "通过 Agent OS 仅重试该持仓，系统会按当前研究档位重新执行启用的数据源"
            elif "stage2" in suggested_fix:
                suggested_fix = "通过 Agent OS 重新合成并执行 UZI 质量自审"
            key = (issue_text, suggested_fix)
            if key not in seen:
                seen.add(key)
                critical_rows.append(key)

        if critical_rows:
            details = "；".join(
                f"{issue}（建议：{suggested_fix}）" if suggested_fix else issue
                for issue, suggested_fix in critical_rows
            )
            if has_transport_timeout and not has_non_agent_critical:
                return f"UZI 质量门未通过：{details}。模型请求已超时，可稍后仅重试此项。"
            if has_provider_unavailable and not has_non_agent_critical:
                return f"UZI 质量门未通过：{details}。模型上游暂时不可用，可稍后仅重试此项。"
            return f"UZI 质量门未通过：{details}。补充真实数据或恢复数据源后，可仅重试此项。"
        if final_status == "gated":
            return "UZI 质量门未通过，但子运行未返回具体 critical；请打开“证据”查看运行事件后仅重试此项。"
        return f"子运行以 {final_status} 状态结束，未生成完整报告"

    @staticmethod
    def _public_gate_text(value: Any, fallback: str) -> str:
        text = " ".join(str(value or "").split()).strip() or fallback
        # The UI must retain the business reason without treating an exception
        # class name as a raw stack-trace diagnostic and hiding the whole gate.
        return re.sub(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b", "运行异常", text)

    @staticmethod
    def _recovery_fingerprint(detail: dict[str, Any]) -> tuple[Any, ...]:
        """Describe durable recovery progress without timestamps or counters.

        A repeated fingerprint means the same suggested fixes, artifacts and
        self-review issues were observed after a resume.  This terminates only
        because no new recovery action is available, never because an
        arbitrary round limit was reached.
        """

        artifacts = [
            row
            for row in list(detail.get("artifacts") or [])
            if isinstance(row, dict) and str(row.get("status") or "active") == "active"
        ]
        native = next(
            (row for row in reversed(artifacts) if row.get("artifact_type") == "uzi_native_analysis"),
            {},
        )
        content = native.get("content") if isinstance(native.get("content"), dict) else {}
        issues = sorted(
            (
                str(issue.get("severity") or ""),
                str(issue.get("dim") or ""),
                str(issue.get("issue") or ""),
                str(issue.get("suggested_fix") or ""),
            )
            for result in list(content.get("results") or [])
            if isinstance(result, dict)
            for issue in list((result.get("self_review") or {}).get("issues") or [])
            if isinstance(issue, dict)
        )
        recovery = sorted(
            (
                str(row.get("stage") or ""),
                str(row.get("symbol") or ""),
                str(row.get("snapshot_type") or ""),
                str(row.get("source_id") or row.get("source_ids") or ""),
                str(row.get("status") or ""),
            )
            for row in list(content.get("recovery_history") or [])
            if isinstance(row, dict)
        )
        artifact_signature = sorted(
            (
                str(row.get("artifact_id") or ""),
                str(row.get("artifact_type") or ""),
                str(row.get("status") or "active"),
            )
            for row in artifacts
        )
        return (
            FundHoldingReportService._detail_status(detail),
            tuple(artifact_signature),
            tuple(issues),
            tuple(recovery),
        )

    def _find_existing(self, **criteria) -> dict[str, Any] | None:
        user_id = criteria.pop("user_id", None)
        tenant_id = criteria.pop("tenant_id", None)
        for root in self._batch_roots():
            if not self._matches_scope(root, user_id=user_id, tenant_id=tenant_id):
                continue
            state = read_json(root / _BATCH_FILE)
            if not isinstance(state, dict):
                continue
            if all(str(state.get(key) or "") == str(value or "") for key, value in criteria.items()):
                return self._public(state)
        return None

    def _load_batch(self, batch_id: str, **scope) -> dict[str, Any]:
        root = self._batch_root(batch_id)
        self._assert_scope(root, **scope)
        state = read_json(root / _BATCH_FILE)
        if not isinstance(state, dict):
            raise FundHoldingBatchNotFound(batch_id)
        return state

    def _batch_root(self, batch_id: str) -> Path:
        try:
            root = self.agent_os.run_store.load_run_paths(batch_id).root
        except FileNotFoundError as exc:
            raise FundHoldingBatchNotFound(batch_id) from exc
        if not (root / _BATCH_FILE).is_file():
            raise FundHoldingBatchNotFound(batch_id)
        return root

    def _batch_roots(self) -> list[Path]:
        root = self.settings.runs_root
        if not root.exists():
            return []
        return [path for path in sorted(root.iterdir(), reverse=True) if path.is_dir() and (path / _BATCH_FILE).is_file()]

    def _matches_scope(self, root: Path, *, user_id: str | None, tenant_id: str | None) -> bool:
        request = read_json(root / "request.json")
        if not isinstance(request, dict):
            return False
        if user_id is not None and request.get("user_id") != user_id:
            return False
        return not (tenant_id is not None and request.get("tenant_id") != tenant_id)

    def _assert_scope(self, root: Path, *, user_id: str | None = None, tenant_id: str | None = None) -> None:
        if not self._matches_scope(root, user_id=user_id, tenant_id=tenant_id):
            raise FundHoldingBatchNotFound(root.name)

    @staticmethod
    def _item(state: dict[str, Any], *, symbol: str, child_run_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in state.get("holdings") or []
                if str(item.get("symbol") or "") == symbol
                and str(item.get("child_run_id") or "") == child_run_id
            ),
            None,
        )

    @staticmethod
    def _detail_status(detail: dict[str, Any]) -> str:
        return str((detail.get("turn") or {}).get("status") or (detail.get("snapshot") or {}).get("status") or "")

    @staticmethod
    def _profile(value: str) -> str:
        profile = str(value or "").strip().lower()
        if profile not in {"lite", "medium", "deep"}:
            raise ValueError("research_profile 必须是 lite、medium 或 deep")
        return profile

    def _public_delivery_state(
        self,
        state: dict[str, Any],
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        force_verify: bool = False,
    ) -> dict[str, Any]:
        """Expose no deliverable after its parent lineage stops validating."""

        should_verify = bool(
            self._has_fund_report_lineage()
            and isinstance(state.get("_report_data_manifest"), dict)
            and (
                force_verify or str(state.get("status") or "") != "running"
            )
        )
        if not should_verify:
            return self._public(state)
        parent_request = read_json(
            self.agent_os.run_store.load_run_paths(str(state["batch_id"])).root
            / "request.json"
        ) or {}
        resolved_user = user_id if user_id is not None else parent_request.get("user_id")
        resolved_tenant = (
            tenant_id if tenant_id is not None else parent_request.get("tenant_id")
        )
        try:
            self._trusted_fund_manifest(
                dict(state["_report_data_manifest"]),
                fund_code=str(state.get("fund_code") or ""),
                symbols=self._supported_symbols(state),
                user_id=resolved_user,
                tenant_id=resolved_tenant,
            )
        except ReportInputError as exc:
            invalidated = dict(state)
            invalidated["status"] = "failed"
            invalidated["report_manifest_status"] = "invalidated"
            invalidated["report_manifest_error_code"] = exc.code
            invalidated["data_gap"] = list(
                dict.fromkeys(
                    [
                        *list(state.get("data_gap") or []),
                        "fund_report_parent_dependency_invalidated",
                    ]
                )
            )
            invalidated_holdings: list[dict[str, Any]] = []
            for source in state.get("holdings") or []:
                item = dict(source)
                if item.get("supported"):
                    item["status"] = "failed"
                    item["report_available"] = False
                    item["error"] = "父基金 PIT Bundle 或冻结报告 manifest 已失效"
                    item["data_gap"] = list(
                        dict.fromkeys(
                            [
                                *list(item.get("data_gap") or []),
                                "fund_report_parent_dependency_invalidated",
                            ]
                        )
                    )
                invalidated_holdings.append(item)
            invalidated["holdings"] = invalidated_holdings
            counts = dict(state.get("counts") or {})
            supported = sum(1 for item in invalidated_holdings if item.get("supported"))
            counts.update(
                {
                    "supported": supported,
                    "queued": 0,
                    "running": 0,
                    "deliverable": 0,
                    "failed": supported,
                    "cancelled": 0,
                }
            )
            invalidated["counts"] = counts
            return self._public(invalidated)
        return self._public(state)

    def _public(self, state: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in state.items()
            if key not in {"request_id", "source_data_gap", "_report_data_manifest"}
        }
        # Batches written by older coordinators only persisted the word
        # ``gated``.  Enrich those immutable historical rows on read from the
        # child ArtifactBoard so users immediately see every critical and fix
        # without regenerating the whole batch.
        payload["holdings"] = [dict(item) for item in list(payload.get("holdings") or [])]
        for item in payload["holdings"]:
            error = str(item.get("error") or "")
            child_run_id = str(item.get("child_run_id") or "")
            if not child_run_id or "子运行以 gated 状态结束" not in error:
                continue
            try:
                detail = self.agent_os.inspect_run(child_run_id)
            except Exception:
                continue
            item["error"] = self._terminal_report_error(detail, "gated")
        return localize_public_error_fields(payload)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _future_key(batch_id: str, symbol: object, child_run_id: object) -> str:
        return f"{batch_id}:{symbol}:{child_run_id}"

    def _forget_future(self, key: str) -> None:
        with self._lock:
            self._futures.pop(key, None)

    @staticmethod
    def _report_filename(item: dict[str, Any]) -> str:
        safe_name = "".join(char for char in str(item.get("name") or "") if char not in '\\/:*?"<>|').strip()
        return f"{int(item.get('rank') or 0):02d}_{item['symbol']}_{safe_name or item['symbol']}.pdf"

    @staticmethod
    def _zip_readme(state: dict[str, Any], *, included: int, zip_errors: list[str]) -> str:
        lines = [
            "Cognitrade 基金持仓股票研究报告包",
            "",
            f"基金：{state['fund_name']}（{state['fund_code']}）",
            f"持仓披露日期：{state.get('disclosure_date') or '未取得'}",
            f"披露距生成日：{state.get('disclosure_age_days') if state.get('disclosure_age_days') is not None else '未知'} 天",
            f"研究档位：{state['profile']}",
            f"批次状态：{state['status']}",
            f"成功写入PDF：{included}",
            "",
            "基金定期披露持仓不代表当前仍持有相关证券；每份股票结论均独立于基金持仓关系。",
            "",
            "报告状态：",
        ]
        lines.extend(
            (
                f"{int(item['rank']):02d}. {item['symbol']} {item['name']} - {item['status']}"
                + (f"；缺口：{', '.join(item.get('data_gap') or [])}" if item.get("data_gap") else "")
            )
            for item in state["holdings"]
        )
        if zip_errors:
            lines.extend(["", "ZIP生成异常：", *[f"- {error}" for error in zip_errors]])
        return "\n".join(lines) + "\n"


__all__ = [
    "FundHoldingBatchConflict",
    "FundHoldingBatchNotFound",
    "FundHoldingReportService",
    "FundHoldingReportsUnavailable",
    "StaleHoldingsConfirmationRequired",
]
