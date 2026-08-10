from __future__ import annotations

import re
import zipfile
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

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
from research_system.runtime.presentation.display import (
    localize_public_error_fields,
    localize_runtime_error,
)
from research_system.runtime.storage.jsonl import append_jsonl, read_json, write_json
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.protocol import RequestEnvelope

_BATCH_FILE = "fund_holding_batch.json"
_TERMINAL_ITEM_STATUSES = {
    "completed", "completed_partial", "failed", "cancelled", "unsupported"
}
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
            min(int(getattr(settings, "fund_holding_report_concurrency", 2)), 10),
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

    def create_batch(
        self,
        source_run_id: str,
        *,
        fund_code: str,
        profile: str,
        source_artifact_id: str | None = None,
        confirm_stale: bool = False,
        force: bool = False,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_profile = self._profile(profile)
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
        )
        require_stale_confirmation(frozen, confirmed=confirm_stale)
        with self._lock:
            existing = self._find_existing(
                source_run_id=source_run_id,
                source_artifact_id=str(frozen["source_artifact_id"]),
                fund_code=str(frozen["fund_code"]),
                profile=normalized_profile,
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
                    f"为基金 {frozen['fund_name']}（{frozen['fund_code']}）的最新披露前十大持仓"
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
                "status": "running",
                "concurrency": self.max_concurrency,
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
                    child_run_id = self._prepare_child(state, item)
                    item["child_run_id"] = child_run_id
                    item["attempt_run_ids"] = [child_run_id]
                    item["status"] = "queued"
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
            rows.append(self._public(state))
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
        return self._public(state)

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
        rows = ArtifactBoard(root / "artifact_board.jsonl").list_artifacts(
            thread_id=None,
            artifact_type=artifact_type,
            status=status,
        )
        return {
            "run_id": batch_id,
            "mode": "fund_holding_batch",
            "status": state.get("status"),
            "turn": None,
            "artifacts": [row.model_dump(mode="json") for row in rows],
        }

    def history_row(self, batch_id: str) -> dict[str, Any]:
        state = read_json(self._batch_root(batch_id) / _BATCH_FILE)
        if not isinstance(state, dict):
            raise FundHoldingBatchNotFound(batch_id)
        counts = dict(state.get("counts") or {})
        message = (
            f"{state.get('fund_name')} 前十大持仓报告："
            f"{counts.get('deliverable', 0)}/{counts.get('supported', 0)} 可交付"
        )
        return {
            "run_id": batch_id,
            "request_id": state.get("request_id"),
            "intent": "fund_holding_research",
            "domain_id": "finance_ashare",
            "mode": "fund_holding_batch",
            "status": state.get("status"),
            "graph_id": None,
            "graph_revision": None,
            "updated_at": state.get("updated_at"),
            "message": message,
            "display_summary": f"{state.get('status')}: {message}",
            "child_run_ids": [
                item.get("child_run_id")
                for item in state.get("holdings") or []
                if item.get("child_run_id")
            ],
            "child_run_count": counts.get("supported", 0),
            "task_type": "fund_holding_research",
            "execution_mode": "batch",
        }

    def metrics(self, batch_id: str, **scope) -> dict[str, Any]:
        state = self.detail(batch_id, **scope)
        return {
            "run_id": batch_id,
            "status": state["status"],
            "counts": state["counts"],
            "tool_call_count": 0,
            "usage": {"tokens": 0, "cost": 0.0, "duration_seconds": 0.0},
            "node_status_counts": dict(Counter(item["status"] for item in state["holdings"])),
            "event_type_counts": {},
            "graph_event_counts": {},
        }

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
        state = self.detail(batch_id, user_id=user_id, tenant_id=tenant_id)
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

    def _prepare_child(self, state: dict[str, Any], item: dict[str, Any]) -> str:
        child_run_id = generate_ulid()
        holding_context = {
            "parent_batch_id": state["batch_id"],
            "source_fund_run_id": state["source_run_id"],
            "source_fund_artifact_id": state["source_artifact_id"],
            "fund_code": state["fund_code"],
            "fund_name": state["fund_name"],
            "fund_type": state.get("fund_type"),
            "holding_rank": item["rank"],
            "symbol": item["symbol"],
            "security_name": item["name"],
            "fund_weight_pct": item.get("fund_weight_pct"),
            "target_security_weight_pct": item.get("target_security_weight_pct"),
            "holding_source": item["holding_source"],
            "disclosure_date": item.get("disclosure_date"),
            "disclosure_age_days": state.get("disclosure_age_days"),
            "stale_level": state.get("stale_level") or "unknown",
            "disclaimer": "基金定期披露持仓不代表当前仍持有该证券；股票结论独立于基金持仓关系。",
        }
        attempt = len(item.get("attempt_run_ids") or []) + 1
        parent_request = read_json(
            self.agent_os.run_store.load_run_paths(state["batch_id"]).root / "request.json"
        ) or {}
        request = RequestEnvelope(
            intent="stock_check",
            user_query=f"分析A股 {item['symbol']} {item['name']}，生成可审计的UZI股票研究报告。",
            thread_id=f"holding:{state['batch_id']}:{item['symbol']}:{attempt}",
            user_id=parent_request.get("user_id"),
            tenant_id=parent_request.get("tenant_id"),
            slots={
                "symbols": [item["symbol"]],
                "holding_context": holding_context,
            },
            constraints={
                "research_profile": state["profile"],
                "fund_holding_report_child": True,
                "parent_batch_id": state["batch_id"],
                "hidden_from_history": True,
            },
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
                if report_available and final_status == "completed":
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
            if report_available and final_status == "completed":
                item["status"] = "completed"
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
            if key not in {"request_id", "source_data_gap"}
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

    def _public(self, state: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in state.items()
            if key not in {"request_id", "source_data_gap"}
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
