from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from domain_packs.finance_ashare.holding_reports import freeze_fund_holdings
from research_system.market_data.report_inputs import bundle_hashes_for_symbols
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.strategies.contracts import (
    JobStatus,
    ReportSetStatus,
    StrategyDocumentV1,
    next_weekday,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.factors import extract_uzi_score, point_in_time_available
from research_system.strategies.models import (
    CanonicalUziAnalysis,
    DailyReportNode,
    DailyReportSet,
    EvidenceObservation,
    JobAttempt,
    OrchestrationJob,
    StrategyAssignment,
    StrategyVersion,
)
from research_system.strategies.repository import StrategyRepository
from research_system.strategies.service import OwnerScope
from research_system.strategies.trading_calendar import is_trading_day, trading_dates


class _JobControlRequested(RuntimeError):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class ReportOrchestrationService:
    """Persistent daily-report coordinator; threads execute work but never own state."""

    _DAILY_REPORT_BATCH_TIMEOUT_SECONDS = 30 * 60

    def __init__(
        self,
        database: StrategyDatabase,
        *,
        run_service,
        holding_report_service,
        workspace_root: Path,
        max_workers: int = 2,
        start_scheduler: bool = True,
        signal_engine=None,
        historical_backfill_batch_timeout_seconds: int = 6 * 60 * 60,
    ) -> None:
        self.database = database
        self.repository = StrategyRepository()
        self.run_service = run_service
        self.holding_report_service = holding_report_service
        self.workspace_root = Path(workspace_root)
        self.signal_engine = signal_engine
        if historical_backfill_batch_timeout_seconds <= 0:
            raise ValueError("historical_backfill_batch_timeout_seconds must be positive")
        self.historical_backfill_batch_timeout_seconds = int(
            historical_backfill_batch_timeout_seconds
        )
        self._worker_id = f"report-worker-{generate_ulid()}"
        self._lock = RLock()
        self._canonical_lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(int(max_workers), 4)),
            thread_name_prefix="cognitrade-report-job",
        )
        self._futures: dict[str, Future] = {}
        self._closed = False
        self._scheduler_stop = Event()
        self._scheduler_thread: Thread | None = None
        self._recover_jobs()
        if start_scheduler:
            self._scheduler_thread = Thread(
                target=self._schedule_loop,
                name="cognitrade-strategy-scheduler",
                daemon=True,
            )
            self._scheduler_thread.start()

    def close(self) -> None:
        self._closed = True
        self._scheduler_stop.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=2.0)
        self._executor.shutdown(wait=False, cancel_futures=False)

    def estimate(
        self,
        scope: OwnerScope,
        *,
        assignment_id: str,
        strategy_version_id: str | None = None,
        start_date: date,
        end_date: date,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        dates = self._trading_dates(start_date, end_date)
        if len(dates) > 60:
            raise StrategyDomainError("backfill_range_too_large", "单个补跑任务最多 60 个交易日")
        with self.database.session() as session:
            assignment, version, document = self._assignment_contract(
                session,
                scope,
                assignment_id,
                strategy_version_id=strategy_version_id,
            )
        symbols: list[str] = []
        source_available = False
        if source_run_id:
            try:
                artifacts = self._run_artifacts(source_run_id, scope, artifact_type="fund_snapshot")
                frozen = freeze_fund_holdings(
                    artifacts,
                    fund_code=assignment.fund_code,
                    top_n=document.report_inputs.top_n,
                )
                symbols = [
                    str(row["symbol"])
                    for row in frozen["holdings"]
                    if bool(row.get("supported"))
                ]
                source_available = True
            except Exception:
                source_available = False
        if not symbols:
            symbols = [f"unknown-{index + 1}" for index in range(document.report_inputs.top_n)]
        pin_hash = self._uzi_pin_hash(document)
        hits = 0
        if source_available:
            with self.database.session() as session:
                for as_of in dates:
                    for symbol in symbols:
                        canonical = self._canonical_uzi_key(symbol, as_of, pin_hash)
                        if self._canonical_completed(session, scope, canonical):
                            hits += 1
        total = len(symbols) * len(dates)
        return {
            "assignment_id": assignment_id,
            "fund_code": assignment.fund_code,
            "strategy_version_id": version.id,
            "start_date": start_date,
            "end_date": end_date,
            "trading_day_count": len(dates),
            "fund_report_count": len(dates),
            "unique_stock_analysis_upper_bound": total,
            "cache_hit_count": hits,
            "model_call_upper_bound": max(0, total - hits),
            "source_run_available": source_available,
            "point_in_time_policy": "unverified evidence is blocked, never replaced by current data",
        }

    def create_job(
        self,
        scope: OwnerScope,
        *,
        assignment_id: str,
        strategy_version_id: str | None = None,
        start_date: date,
        end_date: date,
        source_run_id: str | None = None,
        manual: bool = True,
        force_new: bool = False,
    ) -> dict[str, Any]:
        dates = self._trading_dates(start_date, end_date)
        if not dates:
            raise StrategyDomainError("no_trading_days", "日期区间内没有交易日")
        if len(dates) > 60:
            raise StrategyDomainError("backfill_range_too_large", "单个补跑任务最多 60 个交易日")
        with self.database.session() as session:
            assignment, version, document = self._assignment_contract(
                session,
                scope,
                assignment_id,
                strategy_version_id=strategy_version_id,
            )
            # A disabled Assignment must not schedule fresh production reports.  A
            # manual, explicitly versioned job is also the backtest signal
            # materialization path: it may reuse finalized reports without
            # reactivating scheduling or paper execution.
            if (
                (not assignment.enabled or assignment.paused)
                and (not manual or strategy_version_id is None)
            ):
                raise StrategyConflict("assignment_not_active", "Assignment 已禁用或暂停")
            key_payload = {
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "assignment_id": assignment.id,
                "version_id": version.id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "source_run_id": source_run_id,
                "manual": manual,
            }
            idempotency_key = f"daily-report:{_hash(key_payload)}"
            if force_new:
                idempotency_key += f":{generate_ulid()}"
            existing = session.scalar(
                select(OrchestrationJob).where(
                    OrchestrationJob.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return self._job_payload(session, existing)
            request = {
                "assignment_id": assignment.id,
                "strategy_version_id": version.id,
                "strategy_content_hash": version.content_hash,
                "fund_code": assignment.fund_code,
                "fund_name": assignment.fund_name,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "dates": [value.isoformat() for value in dates],
                "source_run_id": source_run_id,
                "manual": manual,
                "top_n": document.report_inputs.top_n,
                "pin_set_hash": self._pin_hash(document),
            }
            job = OrchestrationJob(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                job_type="daily_report" if len(dates) == 1 else "historical_backfill",
                status=JobStatus.QUEUED.value,
                idempotency_key=idempotency_key,
                request_json=request,
                progress_json={
                    "total": len(dates),
                    "completed": 0,
                    "failed": 0,
                    "current_date": None,
                    "report_set_ids": [],
                    "batches": {},
                    "report_outcomes": {},
                    "errors": [],
                },
            )
            session.add(job)
            session.flush()
            attempt = JobAttempt(
                job_id=job.id,
                attempt_number=1,
                selected_nodes_json=[],
                status=JobStatus.QUEUED.value,
            )
            session.add(attempt)
            session.flush()
            payload = self._job_payload(session, job)
        self._submit(job.id, attempt.id)
        return payload

    def get_job(self, scope: OwnerScope, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            job = self._require_job(session, scope, job_id)
            return self._job_payload(session, job)

    def list_jobs(
        self,
        scope: OwnerScope,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(OrchestrationJob)
                    .where(
                        OrchestrationJob.tenant_id == scope.tenant_id,
                        OrchestrationJob.user_id == scope.user_id,
                    )
                    .order_by(OrchestrationJob.created_at.desc())
                    .limit(min(max(limit, 1), 200))
                )
            )
            return {"jobs": [self._job_payload(session, row) for row in rows]}

    def pause(self, scope: OwnerScope, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            job = self._require_job(session, scope, job_id)
            if job.status not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
                raise StrategyConflict("job_not_pausable", "当前任务状态不能暂停")
            job.pause_requested = True
            return self._job_payload(session, job)

    def resume(self, scope: OwnerScope, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            job = self._require_job(session, scope, job_id)
            if job.status != JobStatus.PAUSED.value:
                raise StrategyConflict("job_not_paused", "任务当前不是暂停状态")
            job.pause_requested = False
            job.status = JobStatus.QUEUED.value
            attempt = self._new_attempt(session, job, selected_nodes=[])
            payload = self._job_payload(session, job)
        self._submit(job.id, attempt.id)
        return payload

    def retry(
        self,
        scope: OwnerScope,
        job_id: str,
        *,
        symbols: Iterable[str] | None = None,
        full_rerun: bool = False,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        selected = list(dict.fromkeys(str(value).strip() for value in (symbols or []) if str(value).strip()))
        if full_rerun and not confirmed:
            raise StrategyDomainError(
                "full_rerun_confirmation_required",
                "完整重跑需要明确确认",
                status_code=409,
            )
        with self.database.session() as session:
            job = self._require_job(session, scope, job_id)
            if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
                raise StrategyConflict("job_still_running", "任务仍在执行，不能同时重试")
            progress = dict(job.progress_json or {})
            if not full_rerun and not selected:
                selected = list(progress.get("failed_symbols") or [])
            if not full_rerun and not selected:
                raise StrategyConflict("no_failed_nodes", "没有可重试的失败节点")
            request = dict(job.request_json or {})
            request["retry"] = True
            request["retry_symbols"] = selected
            request["full_rerun"] = full_rerun
            request["retry_requested_at"] = datetime.now(UTC).isoformat()
            job.request_json = request
            job.pause_requested = False
            job.cancel_requested = False
            job.status = JobStatus.QUEUED.value
            attempt = self._new_attempt(session, job, selected_nodes=selected)
            payload = self._job_payload(session, job)
        self._submit(job.id, attempt.id)
        return payload

    def list_report_sets(
        self,
        scope: OwnerScope,
        *,
        fund_code: str | None = None,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            rows = self.repository.list_report_sets(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                fund_code=fund_code,
                as_of_date=as_of_date,
            )
            return {"report_sets": [self._report_payload(session, row) for row in rows]}

    def get_report_set(self, scope: OwnerScope, report_set_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = self.repository.get_report_set(
                session,
                report_set_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if row is None:
                raise StrategyDomainError("daily_report_set_not_found", "未找到日报版本", status_code=404)
            return self._report_payload(session, row, detail=True)

    def run_due_schedules(self, *, now: datetime | None = None) -> list[str]:
        timezone = ZoneInfo("Asia/Shanghai")
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone)
        else:
            current = current.astimezone(timezone)
        today = current.date()
        if not self._is_trading_day(today):
            return []
        created: list[str] = []
        with self.database.session() as session:
            assignments = list(
                session.scalars(
                    select(StrategyAssignment).where(
                        StrategyAssignment.enabled.is_(True),
                        StrategyAssignment.paused.is_(False),
                    )
                )
            )
            due: list[tuple[OwnerScope, str]] = []
            for assignment in assignments:
                schedule = dict(assignment.schedule_json or {})
                if not schedule.get("enabled"):
                    continue
                local_time = str(schedule.get("local_time") or "16:30")
                if current.strftime("%H:%M") < local_time:
                    continue
                due.append(
                    (
                        OwnerScope(assignment.tenant_id, assignment.user_id, "strategy_scheduler"),
                        assignment.id,
                    )
                )
        for scope, assignment_id in due:
            try:
                payload = self.create_job(
                    scope,
                    assignment_id=assignment_id,
                    start_date=today,
                    end_date=today,
                    manual=False,
                )
                created.append(str(payload["id"]))
            except StrategyDomainError:
                continue
        return created

    def _submit(self, job_id: str, attempt_id: str) -> None:
        if self._closed:
            return
        with self._lock:
            active = self._futures.get(job_id)
            if active is not None and not active.done():
                active.add_done_callback(
                    lambda _future, current_job=job_id, current_attempt=attempt_id: self._submit(
                        current_job,
                        current_attempt,
                    )
                )
                return
            future = self._executor.submit(self._execute_job, job_id, attempt_id)
            self._futures[job_id] = future
            future.add_done_callback(
                lambda current, key=job_id: self._forget(key, current)
            )

    def _forget(self, job_id: str, future: Future) -> None:
        with self._lock:
            if self._futures.get(job_id) is future:
                self._futures.pop(job_id, None)

    def _execute_job(self, job_id: str, attempt_id: str) -> None:
        try:
            claimed = self._claim_job(job_id, attempt_id)
            if claimed is None:
                return
            request, progress = claimed
            dates = [date.fromisoformat(value) for value in request.get("dates") or []]
            failed = 0
            completed = 0
            report_ids = list(progress.get("report_set_ids") or [])
            batches = dict(progress.get("batches") or {})
            report_outcomes = dict(progress.get("report_outcomes") or {})
            errors = list(progress.get("errors") or [])
            failed_symbols: set[str] = set(progress.get("failed_symbols") or [])
            retry_symbols = set(str(value) for value in request.get("retry_symbols") or [])
            if request.get("full_rerun"):
                failed_symbols.clear()
            for as_of in dates:
                control = self._heartbeat(job_id)
                if control != JobStatus.RUNNING.value:
                    raise _JobControlRequested(control)
                with self.database.session() as session:
                    job = session.get(OrchestrationJob, job_id)
                    if job is None:
                        return
                    current_progress = dict(job.progress_json or {})
                    current_progress["current_date"] = as_of.isoformat()
                    job.progress_json = current_progress
                try:
                    result = self._process_date(
                        job_id=job_id,
                        request=request,
                        as_of=as_of,
                        previous_batch_id=batches.get(as_of.isoformat()),
                    )
                    report_id = str(result["report_set_id"])
                    if report_id not in report_ids:
                        report_ids.append(report_id)
                    if result.get("batch_id"):
                        batches[as_of.isoformat()] = str(result["batch_id"])
                    report_outcomes[as_of.isoformat()] = {
                        "report_set_id": report_id,
                        "status": str(result.get("report_status") or "unknown"),
                        "coverage": float(result.get("report_coverage") or 0.0),
                    }
                    if request.get("retry"):
                        failed_symbols.difference_update(retry_symbols)
                    failed_symbols.update(result.get("failed_symbols") or [])
                    completed += 1
                except _JobControlRequested:
                    raise
                except Exception as exc:
                    failed += 1
                    errors.append(
                        {
                            "as_of_date": as_of.isoformat(),
                            "type": exc.__class__.__name__,
                            "message": str(exc),
                        }
                    )
                with self.database.session() as session:
                    job = session.get(OrchestrationJob, job_id)
                    if job is not None:
                        job.progress_json = {
                            "total": len(dates),
                            "completed": completed,
                            "failed": failed,
                            "current_date": as_of.isoformat(),
                            "report_set_ids": report_ids,
                            "batches": batches,
                            "report_outcomes": report_outcomes,
                            "complete_report_days": sum(
                                1 for row in report_outcomes.values()
                                if str((row or {}).get("status") or "") == "final"
                            ),
                            "partial_report_days": sum(
                                1 for row in report_outcomes.values()
                                if str((row or {}).get("status") or "") == "final_partial"
                            ),
                            "blocked_report_days": sum(
                                1 for row in report_outcomes.values()
                                if str((row or {}).get("status") or "") == "blocked"
                            ),
                            "errors": errors,
                            "failed_symbols": sorted(failed_symbols),
                        }
            with self.database.session() as session:
                job = session.get(OrchestrationJob, job_id)
                attempt = session.get(JobAttempt, attempt_id)
                if job is None or attempt is None:
                    return
                if failed == 0 and not failed_symbols:
                    job.status = JobStatus.COMPLETED.value
                elif completed or failed_symbols:
                    job.status = JobStatus.COMPLETED_PARTIAL.value
                else:
                    job.status = JobStatus.FAILED.value
                job.completed_at = datetime.now(UTC)
                job.error_summary = errors[-1]["message"] if errors else None
                attempt.status = job.status
                attempt.error_summary = job.error_summary
                attempt.artifact_refs_json = [
                    {"type": "daily_report_set", "id": report_id}
                    for report_id in report_ids
                ]
                attempt.completed_at = datetime.now(UTC)
                job.lease_owner = None
                job.lease_until = None
        except _JobControlRequested as control:
            self._settle_control_request(job_id, attempt_id, control.status)
        except Exception as exc:
            with self.database.session() as session:
                job = session.get(OrchestrationJob, job_id)
                attempt = session.get(JobAttempt, attempt_id)
                if job is not None:
                    job.status = JobStatus.FAILED.value
                    job.error_summary = str(exc)
                    job.completed_at = datetime.now(UTC)
                    job.lease_owner = None
                    job.lease_until = None
                if attempt is not None:
                    attempt.status = JobStatus.FAILED.value
                    attempt.error_summary = str(exc)
                    attempt.completed_at = datetime.now(UTC)

    def _claim_job(
        self,
        job_id: str,
        attempt_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            query = select(OrchestrationJob).where(OrchestrationJob.id == job_id)
            if not self.database.is_sqlite:
                query = query.with_for_update(skip_locked=True)
            job = session.scalar(query)
            attempt = session.get(JobAttempt, attempt_id)
            if job is None or attempt is None:
                return None
            lease_until = _aware_utc(job.lease_until)
            if (
                job.lease_owner
                and job.lease_owner != self._worker_id
                and lease_until is not None
                and lease_until > now
            ):
                return None
            if job.status not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
                return None
            job.status = JobStatus.RUNNING.value
            job.lease_owner = self._worker_id
            job.lease_until = now + timedelta(seconds=60)
            attempt.status = JobStatus.RUNNING.value
            return dict(job.request_json or {}), dict(job.progress_json or {})

    def _heartbeat(self, job_id: str) -> str:
        with self.database.session() as session:
            job = session.get(OrchestrationJob, job_id)
            if job is None:
                return JobStatus.CANCELLED.value
            if job.cancel_requested:
                return JobStatus.CANCELLED.value
            if job.pause_requested:
                return JobStatus.PAUSED.value
            if job.lease_owner not in {None, self._worker_id}:
                lease_until = _aware_utc(job.lease_until)
                if lease_until is not None and lease_until > datetime.now(UTC):
                    return JobStatus.CANCELLED.value
            job.lease_owner = self._worker_id
            job.lease_until = datetime.now(UTC) + timedelta(seconds=60)
            return JobStatus.RUNNING.value

    def _settle_control_request(self, job_id: str, attempt_id: str, status: str) -> None:
        with self.database.session() as session:
            job = session.get(OrchestrationJob, job_id)
            attempt = session.get(JobAttempt, attempt_id)
            if job is not None:
                job.status = status
                job.lease_owner = None
                job.lease_until = None
                if status == JobStatus.CANCELLED.value:
                    job.completed_at = datetime.now(UTC)
            if attempt is not None:
                attempt.status = status
                if status in {JobStatus.PAUSED.value, JobStatus.CANCELLED.value}:
                    attempt.completed_at = datetime.now(UTC)

    def _process_date(
        self,
        *,
        job_id: str,
        request: dict[str, Any],
        as_of: date,
        previous_batch_id: str | None,
    ) -> dict[str, Any]:
        scope = self._job_scope(job_id)
        assignment_id = str(request["assignment_id"])
        strategy_version_id = str(request.get("strategy_version_id") or "").strip() or None
        with self.database.session() as session:
            assignment, version, document = self._assignment_contract(
                session,
                scope,
                assignment_id,
                strategy_version_id=strategy_version_id,
            )
            pin_hash = self._pin_hash(document)
            uzi_pin_hash = self._uzi_pin_hash(document)
            existing = session.scalar(
                select(DailyReportSet)
                .where(
                    DailyReportSet.tenant_id == scope.tenant_id,
                    DailyReportSet.user_id == scope.user_id,
                    DailyReportSet.fund_code == assignment.fund_code,
                    DailyReportSet.as_of_date == as_of,
                    DailyReportSet.pin_set_hash == pin_hash,
                    DailyReportSet.status.in_(["final", "final_partial", "blocked"]),
                )
                .order_by(DailyReportSet.revision.desc())
                .limit(1)
            )
            building = session.scalar(
                select(DailyReportSet)
                .where(
                    DailyReportSet.tenant_id == scope.tenant_id,
                    DailyReportSet.user_id == scope.user_id,
                    DailyReportSet.fund_code == assignment.fund_code,
                    DailyReportSet.as_of_date == as_of,
                    DailyReportSet.pin_set_hash == pin_hash,
                    DailyReportSet.status == ReportSetStatus.BUILDING.value,
                )
                .order_by(DailyReportSet.revision.desc())
                .limit(1)
            )
            if existing is not None and not request.get("retry"):
                self._recompute_signal(
                    scope,
                    assignment.id,
                    existing.id,
                    strategy_version_id=version.id,
                )
                return {
                    "report_set_id": existing.id,
                    "batch_id": None,
                    "failed_symbols": self._report_failed_symbols(session, existing.id),
                    "report_status": existing.status,
                    "report_coverage": existing.coverage,
                }
            building_report_set_id = building.id if building is not None else None
            building_source_run_id = building.source_run_id if building is not None else None

        source_run_id = str(request.get("source_run_id") or "").strip() or None
        if building_source_run_id:
            source_run_id = building_source_run_id
        if source_run_id is None:
            source_run_id = self._find_latest_fund_run(scope, assignment.fund_code, as_of)
        if source_run_id is None:
            report_set_id = self._create_blocked_report(
                scope,
                assignment=assignment,
                version=version,
                as_of=as_of,
                reason="point_in_time_fund_report_unavailable",
                pin_hash=pin_hash,
            )
            self._recompute_or_carry(
                scope,
                assignment_id=assignment.id,
                report_set_id=report_set_id,
                as_of=as_of,
                strategy_version_id=version.id,
            )
            return {
                "report_set_id": report_set_id,
                "batch_id": None,
                "failed_symbols": [],
                **self._report_progress_snapshot(report_set_id),
            }

        artifacts = self._run_artifacts(source_run_id, scope)
        snapshot = next(
            (row for row in reversed(artifacts) if row.get("artifact_type") == "fund_snapshot"),
            None,
        )
        if snapshot is None:
            raise StrategyDomainError("fund_snapshot_unavailable", "来源运行没有 fund_snapshot", status_code=409)
        frozen = freeze_fund_holdings(
            [snapshot],
            fund_code=assignment.fund_code,
            top_n=document.report_inputs.top_n,
            now=as_of,
        )
        pit_verified, availability = self._availability(snapshot, as_of)
        candidate = self._fund_candidate(snapshot, assignment.fund_code)
        analysis = self._fund_analysis(artifacts, assignment.fund_code)
        fund_payload = {
            **candidate,
            **analysis,
            "holding_source": frozen.get("holding_source"),
            "is_fund_of_funds": frozen.get("is_fund_of_funds"),
            "disclosure_date": frozen.get("disclosure_date"),
            "disclosure_age_days": frozen.get("disclosure_age_days"),
            "stale_level": frozen.get("stale_level"),
            "report_allowed": pit_verified,
            "critical_count": 0 if pit_verified else 1,
            "point_in_time_verified": pit_verified,
            "availability": availability,
        }
        if building_report_set_id is not None:
            report_set_id = building_report_set_id
        else:
            try:
                report_set_id = self._begin_report_set(
                    scope,
                    assignment=assignment,
                    version=version,
                    as_of=as_of,
                    pin_hash=pin_hash,
                    source_run_id=source_run_id,
                    source_artifact_id=str(snapshot.get("artifact_id") or "") or None,
                    fund_payload=fund_payload,
                    top_n=document.report_inputs.top_n,
                    initial_gaps=[] if pit_verified else ["point_in_time_unverified"],
                )
            except IntegrityError:
                report_set_id = self._latest_report_set_id(
                    scope,
                    fund_code=assignment.fund_code,
                    as_of=as_of,
                    pin_hash=pin_hash,
                )
        if not pit_verified:
            self._finalize_blocked_existing(report_set_id, reason="point_in_time_unverified")
            self._recompute_or_carry(
                scope,
                assignment_id=assignment.id,
                report_set_id=report_set_id,
                as_of=as_of,
                strategy_version_id=version.id,
            )
            return {
                "report_set_id": report_set_id,
                "batch_id": None,
                "failed_symbols": [],
                **self._report_progress_snapshot(report_set_id),
            }

        retry_symbols = list(request.get("retry_symbols") or [])
        claim_symbols = (
            set(str(value) for value in retry_symbols)
            if request.get("retry") and not request.get("full_rerun")
            else None
        )
        report_manifest = None
        load_frozen_manifest = getattr(
            self.holding_report_service,
            "frozen_report_manifest",
            None,
        )
        if previous_batch_id and callable(load_frozen_manifest):
            report_manifest = load_frozen_manifest(
                previous_batch_id,
                **self._runtime_scope(scope),
            )
        freeze_report_inputs = getattr(self.holding_report_service, "freeze_report_inputs", None)
        if report_manifest is None and callable(freeze_report_inputs):
            report_manifest = freeze_report_inputs(
                [
                    str(row.get("symbol") or "")
                    for row in frozen.get("holdings") or []
                    if bool(row.get("supported")) and str(row.get("symbol") or "")
                ],
                as_of_date=as_of,
                profile=document.report_inputs.research_profile,
                pin_set_hash=uzi_pin_hash,
                **self._runtime_scope(scope),
                context={
                    "orchestration_job_id": job_id,
                    "assignment_id": assignment.id,
                    "fund_code": assignment.fund_code,
                    "source_run_id": source_run_id,
                    "holdings": list(frozen.get("holdings") or []),
                },
            )
        supported_holding_symbols = [
            str(row.get("symbol") or "")
            for row in frozen.get("holdings") or []
            if bool(row.get("supported")) and str(row.get("symbol") or "")
        ]
        bundle_manifest_hashes = bundle_hashes_for_symbols(
            report_manifest,
            supported_holding_symbols,
        )
        reuse, owned_canonical = self._canonical_plan(
            scope,
            job_id=job_id,
            holdings=list(frozen.get("holdings") or []),
            as_of=as_of,
            pin_hash=uzi_pin_hash,
            manifest_hashes=bundle_manifest_hashes,
            claim_symbols=claim_symbols,
        )
        batch = self.holding_report_service.create_batch(
            source_run_id,
            fund_code=assignment.fund_code,
            profile=document.report_inputs.research_profile,
            source_artifact_id=str(snapshot.get("artifact_id") or "") or None,
            confirm_stale=True,
            force=bool(request.get("retry")),
            top_n=document.report_inputs.top_n,
            as_of_date=as_of,
            pin_set_hash=uzi_pin_hash,
            canonical_reuse=reuse,
            report_manifest=report_manifest,
            selected_symbols=(
                retry_symbols
                if request.get("retry") and not request.get("full_rerun")
                else None
            ),
            **self._runtime_scope(scope),
        )
        batch = self._wait_batch(
            str(batch["batch_id"]),
            scope,
            job_id=job_id,
            canonical_keys=set(owned_canonical.values()),
            timeout_seconds=self._batch_timeout_seconds(request),
        )
        self._publish_canonical_results(
            scope,
            job_id=job_id,
            owned=owned_canonical,
            batch=batch,
            as_of=as_of,
            pin_hash=uzi_pin_hash,
        )
        failed_symbols = self._finalize_report_set(
            scope,
            report_set_id=report_set_id,
            batch=batch,
            frozen=frozen,
            pin_hash=uzi_pin_hash,
            as_of=as_of,
            manifest_hashes=bundle_manifest_hashes,
        )
        self._recompute_signal(
            scope,
            assignment.id,
            report_set_id,
            strategy_version_id=version.id,
        )
        return {
            "report_set_id": report_set_id,
            "batch_id": batch["batch_id"],
            "failed_symbols": failed_symbols,
            **self._report_progress_snapshot(report_set_id),
        }

    def _report_progress_snapshot(self, report_set_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            report_set = session.get(DailyReportSet, report_set_id)
            if report_set is None:
                return {"report_status": "unknown", "report_coverage": 0.0}
            return {
                "report_status": report_set.status,
                "report_coverage": float(report_set.coverage or 0.0),
            }

    def _begin_report_set(
        self,
        scope: OwnerScope,
        *,
        assignment: StrategyAssignment,
        version: StrategyVersion,
        as_of: date,
        pin_hash: str,
        source_run_id: str,
        source_artifact_id: str | None,
        fund_payload: dict[str, Any],
        top_n: int,
        initial_gaps: list[str],
    ) -> str:
        with self.database.session() as session:
            latest_revision = int(
                session.scalar(
                    select(func.max(DailyReportSet.revision)).where(
                        DailyReportSet.tenant_id == scope.tenant_id,
                        DailyReportSet.user_id == scope.user_id,
                        DailyReportSet.fund_code == assignment.fund_code,
                        DailyReportSet.as_of_date == as_of,
                        DailyReportSet.pin_set_hash == pin_hash,
                    )
                )
                or 0
            )
            revision = latest_revision + 1
            report_set = DailyReportSet(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                assignment_id=assignment.id,
                fund_code=assignment.fund_code,
                fund_name=assignment.fund_name or str(fund_payload.get("fund_name") or "") or None,
                as_of_date=as_of,
                effective_date=self._report_effective_date(
                    assignment=assignment,
                    version=version,
                    as_of=as_of,
                    revision=revision,
                ),
                pin_set_hash=pin_hash,
                revision=revision,
                status=ReportSetStatus.BUILDING.value,
                max_top_n=top_n,
                source_run_id=source_run_id,
                source_artifact_id=source_artifact_id,
                data_gaps_json=list(initial_gaps),
            )
            session.add(report_set)
            session.flush()
            session.add(
                DailyReportNode(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    report_set_id=report_set.id,
                    node_type="fund_report",
                    subject_code=assignment.fund_code,
                    subject_name=report_set.fund_name,
                    canonical_key=f"fund:{assignment.fund_code}:{as_of.isoformat()}:{pin_hash}",
                    status="completed" if not initial_gaps else "blocked",
                    run_id=source_run_id,
                    artifact_id=source_artifact_id,
                    input_hash=_hash({"source_artifact_id": source_artifact_id, "as_of": as_of.isoformat()}),
                    content_hash=_hash(fund_payload),
                    confidence=1.0 if not initial_gaps else 0.0,
                    data_gaps_json=list(initial_gaps),
                    source_json=fund_payload,
                )
            )
            self._record_evidence(session, assignment.fund_code, source_artifact_id, fund_payload)
            return report_set.id

    def _create_blocked_report(
        self,
        scope: OwnerScope,
        *,
        assignment: StrategyAssignment,
        version: StrategyVersion,
        as_of: date,
        reason: str,
        pin_hash: str,
    ) -> str:
        report_set_id = self._begin_report_set(
            scope,
            assignment=assignment,
            version=version,
            as_of=as_of,
            pin_hash=pin_hash,
            source_run_id="unavailable",
            source_artifact_id=None,
            fund_payload={
                "fund_code": assignment.fund_code,
                "fund_name": assignment.fund_name,
                "report_allowed": False,
                "critical_count": 1,
                "point_in_time_verified": False,
            },
            top_n=int(StrategyDocumentV1.model_validate(version.document_json).report_inputs.top_n),
            initial_gaps=[reason, "point_in_time_unverified"],
        )
        self._finalize_blocked_existing(report_set_id, reason=reason)
        return report_set_id

    def _finalize_blocked_existing(self, report_set_id: str, *, reason: str) -> None:
        with self.database.session() as session:
            report_set = session.get(DailyReportSet, report_set_id)
            if report_set is None:
                return
            report_set.status = ReportSetStatus.BLOCKED.value
            report_set.coverage = 0.0
            report_set.count_coverage = 0.0
            report_set.weight_coverage = 0.0
            report_set.data_gaps_json = list(
                dict.fromkeys([*(report_set.data_gaps_json or []), reason])
            )
            report_set.source_hash = _hash(
                {"report_set_id": report_set.id, "reason": reason, "revision": report_set.revision}
            )
            report_set.finalized_at = datetime.now(UTC)

    def _finalize_report_set(
        self,
        scope: OwnerScope,
        *,
        report_set_id: str,
        batch: dict[str, Any],
        frozen: dict[str, Any],
        pin_hash: str,
        as_of: date,
        manifest_hashes: dict[str, str] | None = None,
    ) -> list[str]:
        failed_symbols: list[str] = []
        with self.database.session() as session:
            report_set = session.scalar(
                select(DailyReportSet)
                .where(DailyReportSet.id == report_set_id)
                .with_for_update()
            )
            if report_set is None:
                raise StrategyDomainError("daily_report_set_not_found", "日报构建记录丢失", status_code=500)
            if report_set.status != ReportSetStatus.BUILDING.value:
                return self._report_failed_symbols(session, report_set.id)
            previous_by_key: dict[str, DailyReportNode] = {}
            for item in batch.get("holdings") or []:
                symbol = str(item.get("symbol") or "")
                canonical = self._canonical_uzi_key(
                    symbol,
                    as_of,
                    pin_hash,
                    (manifest_hashes or {}).get(symbol, "legacy-unverified"),
                )
                previous = self._canonical_node(session, scope, canonical)
                if previous is not None:
                    previous_by_key[canonical] = previous
                source, artifact_id, content_hash, confidence, gaps = self._uzi_source(
                    scope,
                    item,
                    previous=previous,
                )
                context_link = self._create_holding_context_link(
                    scope,
                    report_set=report_set,
                    item=item,
                    canonical_key=canonical,
                    canonical_artifact_id=artifact_id,
                    canonical_content_hash=content_hash,
                    source=source,
                    confidence=confidence,
                    gaps=gaps,
                )
                status = "completed" if item.get("report_available") and extract_uzi_score(source) is not None else str(item.get("status") or "failed")
                if status != "completed" and bool(item.get("supported")):
                    failed_symbols.append(symbol)
                session.add(
                    DailyReportNode(
                        tenant_id=scope.tenant_id,
                        user_id=scope.user_id,
                        report_set_id=report_set.id,
                        node_type="holding_uzi",
                        subject_code=symbol,
                        subject_name=str(item.get("name") or symbol),
                        rank=_integer(item.get("rank")),
                        disclosed_weight_pct=_number(item.get("fund_weight_pct")),
                        effective_weight_pct=_number(
                            item.get("fund_weight_pct"),
                            item.get("target_security_weight_pct"),
                        ),
                        canonical_key=canonical,
                        status=status,
                        run_id=str(item.get("child_run_id") or "") or None,
                        artifact_id=context_link.artifact_id,
                        input_hash=_hash(
                            {
                                "symbol": symbol,
                                "as_of": as_of.isoformat(),
                                "uzi_pin_set_hash": pin_hash,
                            }
                        ),
                        content_hash=content_hash,
                        confidence=confidence,
                        data_gaps_json=gaps,
                        source_json={
                            **source,
                            "supported": bool(item.get("supported")),
                            "canonical_reused": bool(item.get("canonical_reused")),
                            "canonical_artifact_id": artifact_id,
                            "canonical_content_hash": content_hash,
                            "context_link_artifact_id": context_link.artifact_id,
                        },
                    )
                )
            session.flush()
            nodes = self.repository.list_report_nodes(session, report_set.id)
            holdings = [node for node in nodes if node.node_type == "holding_uzi"]
            completed = [node for node in holdings if node.status == "completed"]
            count_coverage = len(completed) / max(1, report_set.max_top_n)
            known_weights = [node.effective_weight_pct for node in holdings]
            if known_weights and all(value is not None and value >= 0 for value in known_weights):
                total_weight = sum(float(value or 0.0) for value in known_weights)
                completed_weight = sum(
                    float(node.effective_weight_pct or 0.0) for node in completed
                )
                weight_coverage = completed_weight / total_weight if total_weight > 0 else None
            else:
                weight_coverage = None
            coverage = min(count_coverage, weight_coverage) if weight_coverage is not None else count_coverage
            gaps = list(dict.fromkeys([*(report_set.data_gaps_json or []), *(batch.get("data_gap") or [])]))
            if failed_symbols:
                gaps.append("holding_uzi_incomplete")
            report_set.count_coverage = min(1.0, count_coverage)
            report_set.weight_coverage = min(1.0, weight_coverage) if weight_coverage is not None else None
            report_set.coverage = min(1.0, coverage)
            report_set.data_gaps_json = list(dict.fromkeys(gaps))
            report_set.status = (
                ReportSetStatus.FINAL.value
                if not failed_symbols and not gaps
                else ReportSetStatus.FINAL_PARTIAL.value
            )
            report_set.source_hash = _hash(
                {
                    "fund_artifact": report_set.source_artifact_id,
                    "batch_id": batch.get("batch_id"),
                    "nodes": [
                        {"id": node.id, "content_hash": node.content_hash, "status": node.status}
                        for node in nodes
                    ],
                    "revision": report_set.revision,
                }
            )
            report_set.finalized_at = datetime.now(UTC)
        return sorted(set(failed_symbols))

    def _create_holding_context_link(
        self,
        scope: OwnerScope,
        *,
        report_set: DailyReportSet,
        item: dict[str, Any],
        canonical_key: str,
        canonical_artifact_id: str | None,
        canonical_content_hash: str | None,
        source: dict[str, Any],
        confidence: float,
        gaps: list[str],
    ) -> ArtifactRecord:
        symbol = str(item.get("symbol") or "")
        board = ArtifactBoard(
            self.workspace_root
            / "strategy_reports"
            / report_set.id
            / "artifact_board.jsonl"
        )
        source_refs: list[dict[str, Any]] = []
        if canonical_artifact_id:
            source_refs.append(
                {
                    "type": "artifact",
                    "id": canonical_artifact_id,
                    "content_hash": canonical_content_hash,
                }
            )
        canonical_run_id = str(item.get("child_run_id") or source.get("canonical_run_id") or "")
        if canonical_run_id:
            source_refs.append({"type": "run", "locator": canonical_run_id})
        if report_set.source_artifact_id:
            source_refs.append(
                {
                    "type": "artifact",
                    "id": report_set.source_artifact_id,
                    "locator": str(report_set.source_run_id or ""),
                }
            )
        return board.add(
            ArtifactRecord(
                artifact_type="fund_holding_stock_report_link",
                schema_version="1",
                run_id=report_set.id,
                thread_id=f"daily-report:{report_set.id}",
                request_id=report_set.id,
                user_id=scope.user_id,
                tenant_id=scope.tenant_id,
                producer=ArtifactProducer(
                    executor="strategy_report_orchestrator",
                    capability="finance.strategy.daily_report.link_holding_uzi",
                    node_id=f"holding-link-{symbol}",
                    domain_id="finance_ashare",
                ),
                content={
                    "report_set_id": report_set.id,
                    "report_revision": report_set.revision,
                    "fund_code": report_set.fund_code,
                    "fund_name": report_set.fund_name,
                    "as_of_date": report_set.as_of_date.isoformat(),
                    "symbol": symbol,
                    "name": item.get("name"),
                    "rank": item.get("rank"),
                    "disclosed_weight_pct": item.get("fund_weight_pct"),
                    "effective_weight_pct": item.get(
                        "fund_weight_pct",
                        item.get("target_security_weight_pct"),
                    ),
                    "canonical_key": canonical_key,
                    "canonical_run_id": canonical_run_id or None,
                    "canonical_artifact_id": canonical_artifact_id,
                    "canonical_content_hash": canonical_content_hash,
                },
                source_refs=source_refs,
                confidence=max(0.0, min(1.0, float(confidence))),
                risk_flags=list(gaps),
                metadata={"immutable": True, "canonical_reused": bool(item.get("canonical_reused"))},
            )
        )

    def _latest_report_set_id(
        self,
        scope: OwnerScope,
        *,
        fund_code: str,
        as_of: date,
        pin_hash: str,
    ) -> str:
        with self.database.session() as session:
            report_set = session.scalar(
                select(DailyReportSet)
                .where(
                    DailyReportSet.tenant_id == scope.tenant_id,
                    DailyReportSet.user_id == scope.user_id,
                    DailyReportSet.fund_code == fund_code,
                    DailyReportSet.as_of_date == as_of,
                    DailyReportSet.pin_set_hash == pin_hash,
                )
                .order_by(DailyReportSet.revision.desc())
                .limit(1)
            )
            if report_set is None:
                raise StrategyDomainError(
                    "daily_report_set_not_found",
                    "并发日报创建后未找到可复用版本",
                    status_code=500,
                )
            return report_set.id

    @staticmethod
    def _report_failed_symbols(session, report_set_id: str) -> list[str]:
        nodes = list(
            session.scalars(
                select(DailyReportNode).where(
                    DailyReportNode.report_set_id == report_set_id,
                    DailyReportNode.node_type == "holding_uzi",
                    DailyReportNode.status != "completed",
                )
            )
        )
        return sorted(
            {
                node.subject_code
                for node in nodes
                if bool((node.source_json or {}).get("supported", True))
            }
        )

    def _uzi_source(
        self,
        scope: OwnerScope,
        item: dict[str, Any],
        *,
        previous: DailyReportNode | None,
    ) -> tuple[dict[str, Any], str | None, str | None, float, list[str]]:
        if item.get("canonical_reused") and previous is not None:
            return (
                dict(previous.source_json or {}),
                previous.artifact_id,
                previous.content_hash,
                float(previous.confidence or 0.0),
                list(previous.data_gaps_json or []),
            )
        run_id = str(item.get("child_run_id") or "")
        if not run_id:
            return {}, None, None, 0.0, list(item.get("data_gap") or ["holding_uzi_missing"])
        try:
            artifacts = self._run_artifacts(run_id, scope)
        except Exception:
            return {}, None, None, 0.0, list(item.get("data_gap") or ["holding_uzi_unavailable"])
        native = next(
            (row for row in reversed(artifacts) if row.get("artifact_type") == "uzi_native_analysis"),
            None,
        )
        report = next(
            (row for row in reversed(artifacts) if row.get("artifact_type") == "research_report"),
            None,
        )
        chosen = native or report or {}
        content = dict(chosen.get("content") or {})
        report_content = dict((report or {}).get("content") or {})
        score = extract_uzi_score({"source": content})
        source = {
            **content,
            "uzi_overall_score": score,
            "report_allowed": report_content.get("report_allowed", content.get("report_allowed", False)),
            "critical_count": report_content.get("critical_count", content.get("critical_count", 0)),
            "canonical_run_id": run_id,
        }
        gaps = list(
            dict.fromkeys(
                [
                    *(item.get("data_gap") or []),
                    *((chosen or {}).get("risk_flags") or []),
                    *((report or {}).get("risk_flags") or []),
                ]
            )
        )
        return (
            source,
            str(chosen.get("artifact_id") or "") or None,
            (str(chosen.get("content_hash") or "") or (_hash(content) if content else None)),
            float(chosen.get("confidence") or 0.0),
            gaps,
        )

    def _canonical_plan(
        self,
        scope: OwnerScope,
        *,
        job_id: str,
        holdings: list[dict[str, Any]],
        as_of: date,
        pin_hash: str,
        manifest_hashes: dict[str, str] | None = None,
        claim_symbols: set[str] | None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        supported = {
            str(row.get("symbol") or ""): dict(row)
            for row in holdings
            if bool(row.get("supported")) and str(row.get("symbol") or "")
        }
        requested = set(supported) if claim_symbols is None else set(supported).intersection(claim_symbols)
        reuse, owned, pending = self._reserve_canonical_bulk(
            scope,
            job_id=job_id,
            holdings=supported,
            requested=requested,
            as_of=as_of,
            pin_hash=pin_hash,
            manifest_hashes=manifest_hashes,
        )
        deadline = time.monotonic() + 60 * 30
        next_heartbeat = time.monotonic()
        while pending and time.monotonic() < deadline:
            if time.monotonic() >= next_heartbeat:
                control = self._heartbeat(job_id)
                if control != JobStatus.RUNNING.value:
                    raise _JobControlRequested(control)
                self._heartbeat_canonical(job_id, set(owned.values()))
                next_heartbeat = time.monotonic() + 10.0
            time.sleep(0.25)
            waiting_holdings = {
                symbol: supported[symbol]
                for symbol in pending.values()
                if symbol in supported
            }
            current_reuse, current_owned, pending = self._reserve_canonical_bulk(
                scope,
                job_id=job_id,
                holdings=waiting_holdings,
                requested=set(waiting_holdings),
                as_of=as_of,
                pin_hash=pin_hash,
                manifest_hashes=manifest_hashes,
            )
            reuse.update(current_reuse)
            owned.update(current_owned)
        if pending:
            raise TimeoutError(
                "canonical UZI reservation timed out: " + ",".join(sorted(pending.values()))
            )
        return reuse, owned

    def _reserve_canonical_bulk(
        self,
        scope: OwnerScope,
        *,
        job_id: str,
        holdings: dict[str, dict[str, Any]],
        requested: set[str],
        as_of: date,
        pin_hash: str,
        manifest_hashes: dict[str, str] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str]]:
        records = [
            (
                self._canonical_uzi_key(
                    symbol,
                    as_of,
                    pin_hash,
                    (manifest_hashes or {}).get(symbol, "legacy-unverified"),
                ),
                symbol,
            )
            for symbol in holdings
        ]
        records.sort()
        for attempt in range(8):
            reuse: dict[str, dict[str, Any]] = {}
            owned: dict[str, str] = {}
            pending: dict[str, str] = {}
            guard = self._canonical_lock if self.database.is_sqlite else nullcontext()
            try:
                with guard:
                    with self.database.session() as session:
                        now = datetime.now(UTC)
                        for canonical_key, symbol in records:
                            analysis = session.scalar(
                                select(CanonicalUziAnalysis)
                                .where(
                                    CanonicalUziAnalysis.tenant_id == scope.tenant_id,
                                    CanonicalUziAnalysis.user_id == scope.user_id,
                                    CanonicalUziAnalysis.canonical_key == canonical_key,
                                )
                                .with_for_update()
                            )
                            if analysis is None:
                                node = self._canonical_node(session, scope, canonical_key)
                                if node is not None and node.run_id:
                                    analysis = CanonicalUziAnalysis(
                                        tenant_id=scope.tenant_id,
                                        user_id=scope.user_id,
                                        canonical_key=canonical_key,
                                        symbol=symbol,
                                        as_of_date=as_of,
                                        pin_set_hash=pin_hash,
                                        status="completed",
                                        run_id=node.run_id,
                                        artifact_id=node.artifact_id,
                                        content_hash=node.content_hash,
                                        confidence=node.confidence,
                                        source_json=dict(node.source_json or {}),
                                        data_gaps_json=list(node.data_gaps_json or []),
                                        completed_at=node.created_at,
                                    )
                                    session.add(analysis)
                                    reuse[symbol] = self._canonical_payload(node)
                                elif symbol in requested:
                                    analysis = CanonicalUziAnalysis(
                                        tenant_id=scope.tenant_id,
                                        user_id=scope.user_id,
                                        canonical_key=canonical_key,
                                        symbol=symbol,
                                        as_of_date=as_of,
                                        pin_set_hash=pin_hash,
                                        status="reserved",
                                        producer_job_id=job_id,
                                        lease_owner=self._worker_id,
                                        lease_until=now + timedelta(seconds=90),
                                    )
                                    session.add(analysis)
                                    owned[symbol] = canonical_key
                                continue
                            if analysis.status == "completed" and analysis.run_id:
                                reuse[symbol] = self._canonical_payload(analysis)
                                continue
                            if symbol not in requested:
                                continue
                            lease_until = _aware_utc(analysis.lease_until)
                            can_claim = (
                                analysis.producer_job_id == job_id
                                or analysis.status == "failed"
                                or lease_until is None
                                or lease_until <= now
                            )
                            if can_claim:
                                analysis.status = "reserved"
                                analysis.producer_job_id = job_id
                                analysis.lease_owner = self._worker_id
                                analysis.lease_until = now + timedelta(seconds=90)
                                analysis.run_id = None
                                analysis.artifact_id = None
                                analysis.content_hash = None
                                analysis.confidence = None
                                analysis.source_json = {}
                                analysis.data_gaps_json = []
                                analysis.error_summary = None
                                analysis.completed_at = None
                                owned[symbol] = canonical_key
                            else:
                                pending[canonical_key] = symbol
                        session.flush()
                return reuse, owned, pending
            except (IntegrityError, OperationalError):
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise RuntimeError("canonical UZI reservation retry exhausted")

    def _heartbeat_canonical(self, job_id: str, canonical_keys: set[str]) -> None:
        if not canonical_keys:
            return
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(CanonicalUziAnalysis).where(
                        CanonicalUziAnalysis.producer_job_id == job_id,
                        CanonicalUziAnalysis.status == "reserved",
                        CanonicalUziAnalysis.canonical_key.in_(canonical_keys),
                    )
                )
            )
            lease_until = datetime.now(UTC) + timedelta(seconds=90)
            for row in rows:
                row.lease_owner = self._worker_id
                row.lease_until = lease_until

    def _publish_canonical_results(
        self,
        scope: OwnerScope,
        *,
        job_id: str,
        owned: dict[str, str],
        batch: dict[str, Any],
        as_of: date,
        pin_hash: str,
    ) -> None:
        if not owned:
            return
        self._heartbeat_canonical(job_id, set(owned.values()))
        items = {
            str(item.get("symbol") or ""): dict(item)
            for item in batch.get("holdings") or []
        }
        results: dict[str, dict[str, Any]] = {}
        for symbol, canonical_key in owned.items():
            item = items.get(symbol, {"symbol": symbol, "status": "failed"})
            source, artifact_id, content_hash, confidence, gaps = self._uzi_source(
                scope,
                item,
                previous=None,
            )
            completed = bool(item.get("report_available")) and extract_uzi_score(source) is not None
            results[canonical_key] = {
                "symbol": symbol,
                "status": "completed" if completed else "failed",
                "run_id": str(item.get("child_run_id") or "") or None,
                "artifact_id": artifact_id,
                "content_hash": content_hash,
                "confidence": confidence,
                "source": source,
                "gaps": gaps,
                "error": None if completed else str(item.get("error") or item.get("status") or "failed"),
            }
        with self.database.session() as session:
            now = datetime.now(UTC)
            for canonical_key, result in results.items():
                analysis = session.scalar(
                    select(CanonicalUziAnalysis)
                    .where(
                        CanonicalUziAnalysis.tenant_id == scope.tenant_id,
                        CanonicalUziAnalysis.user_id == scope.user_id,
                        CanonicalUziAnalysis.canonical_key == canonical_key,
                    )
                    .with_for_update()
                )
                if (
                    analysis is None
                    or analysis.status != "reserved"
                    or analysis.producer_job_id != job_id
                ):
                    continue
                analysis.symbol = result["symbol"]
                analysis.as_of_date = as_of
                analysis.pin_set_hash = pin_hash
                analysis.status = result["status"]
                analysis.run_id = result["run_id"]
                analysis.artifact_id = result["artifact_id"]
                analysis.content_hash = result["content_hash"]
                analysis.confidence = result["confidence"]
                analysis.source_json = result["source"]
                analysis.data_gaps_json = result["gaps"]
                analysis.error_summary = result["error"]
                analysis.lease_owner = None
                analysis.lease_until = None
                analysis.completed_at = now

    @staticmethod
    def _canonical_payload(row: CanonicalUziAnalysis | DailyReportNode) -> dict[str, Any]:
        return {
            "run_id": row.run_id,
            "artifact_id": row.artifact_id,
            "content_hash": row.content_hash,
            "confidence": row.confidence,
        }

    @staticmethod
    def _canonical_analysis(
        session,
        scope: OwnerScope,
        canonical_key: str,
    ) -> CanonicalUziAnalysis | None:
        return session.scalar(
            select(CanonicalUziAnalysis).where(
                CanonicalUziAnalysis.tenant_id == scope.tenant_id,
                CanonicalUziAnalysis.user_id == scope.user_id,
                CanonicalUziAnalysis.canonical_key == canonical_key,
                CanonicalUziAnalysis.status == "completed",
            )
        )

    @classmethod
    def _canonical_completed(cls, session, scope: OwnerScope, canonical_key: str) -> bool:
        analysis = cls._canonical_analysis(session, scope, canonical_key)
        if analysis is not None and analysis.run_id:
            return True
        return cls._canonical_node(session, scope, canonical_key) is not None

    @staticmethod
    def _canonical_node(session, scope: OwnerScope, canonical_key: str) -> DailyReportNode | None:
        return session.scalar(
            select(DailyReportNode)
            .where(
                DailyReportNode.tenant_id == scope.tenant_id,
                DailyReportNode.user_id == scope.user_id,
                DailyReportNode.node_type == "holding_uzi",
                DailyReportNode.canonical_key == canonical_key,
                DailyReportNode.status == "completed",
            )
            .order_by(DailyReportNode.created_at.desc())
            .limit(1)
        )

    def _wait_batch(
        self,
        batch_id: str,
        scope: OwnerScope,
        *,
        job_id: str,
        canonical_keys: set[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout_seconds = int(
            timeout_seconds
            if timeout_seconds is not None
            else self._DAILY_REPORT_BATCH_TIMEOUT_SECONDS
        )
        deadline = time.monotonic() + timeout_seconds
        next_heartbeat = time.monotonic()
        while time.monotonic() < deadline:
            if time.monotonic() >= next_heartbeat:
                control = self._heartbeat(job_id)
                if control != JobStatus.RUNNING.value:
                    raise _JobControlRequested(control)
                self._heartbeat_canonical(job_id, set(canonical_keys or set()))
                next_heartbeat = time.monotonic() + 10.0
            state = self.holding_report_service.detail(batch_id, **self._runtime_scope(scope))
            if state.get("status") != "running":
                return state
            time.sleep(0.25)
        try:
            self.holding_report_service.cancel(
                batch_id,
                **self._runtime_scope(scope),
            )
        except Exception:
            # Timeout remains the primary failure. A failed cancellation is
            # retried by normal stale-batch recovery on the next service boot.
            pass
        raise TimeoutError(
            f"holding report batch timed out and cancellation was requested: {batch_id}"
        )

    def _batch_timeout_seconds(self, request: dict[str, Any]) -> int:
        dates = list(request.get("dates") or [])
        if len(dates) > 1:
            return self.historical_backfill_batch_timeout_seconds
        return self._DAILY_REPORT_BATCH_TIMEOUT_SECONDS

    def _run_artifacts(
        self,
        run_id: str,
        scope: OwnerScope,
        *,
        artifact_type: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = self.run_service.artifacts(
            run_id,
            artifact_type=artifact_type,
            status="active",
            **self._runtime_scope(scope),
        )
        return [dict(row) for row in payload.get("artifacts") or [] if isinstance(row, dict)]

    def _find_latest_fund_run(
        self,
        scope: OwnerScope,
        fund_code: str,
        as_of: date,
    ) -> str | None:
        rows = self.run_service.history(**self._runtime_scope(scope))
        for row in rows:
            if str(row.get("intent") or "") != "fund_research":
                continue
            run_id = str(row.get("run_id") or "")
            if not run_id:
                continue
            try:
                artifacts = self._run_artifacts(run_id, scope, artifact_type="fund_snapshot")
            except Exception:
                continue
            snapshot = artifacts[-1] if artifacts else None
            if snapshot and self._fund_candidate(snapshot, fund_code):
                available, _ = self._availability(snapshot, as_of)
                if available:
                    return run_id
        return None

    @staticmethod
    def _fund_candidate(snapshot: dict[str, Any], fund_code: str) -> dict[str, Any]:
        content = dict(snapshot.get("content") or {})
        for row in content.get("funds") or []:
            if isinstance(row, dict) and str(row.get("fund_code") or "") == str(fund_code):
                return dict(row)
        return {}

    @staticmethod
    def _fund_analysis(artifacts: list[dict[str, Any]], fund_code: str) -> dict[str, Any]:
        artifact = next(
            (row for row in reversed(artifacts) if row.get("artifact_type") == "fund_analysis"),
            None,
        )
        content = dict((artifact or {}).get("content") or {})
        for key in ("funds", "results", "analyses"):
            for row in content.get(key) or []:
                if isinstance(row, dict) and str(row.get("fund_code") or "") == str(fund_code):
                    return dict(row)
        return content if str(content.get("fund_code") or "") == str(fund_code) else {}

    @staticmethod
    def _availability(snapshot: dict[str, Any], as_of: date) -> tuple[bool, dict[str, Any]]:
        created_at = str(snapshot.get("created_at") or "") or None
        source_refs = [row for row in snapshot.get("source_refs") or [] if isinstance(row, dict)]
        published_at = next((str(row.get("published_at")) for row in source_refs if row.get("published_at")), None)
        first_seen_at = next(
            (
                str(row.get("first_seen_at") or row.get("retrieved_at"))
                for row in source_refs
                if row.get("first_seen_at") or row.get("retrieved_at")
            ),
            created_at,
        )
        available = point_in_time_available(
            as_of_date=as_of,
            published_at=published_at,
            first_seen_at=first_seen_at,
        )
        return available, {
            "published_at": published_at,
            "first_seen_at": first_seen_at,
            "retrieved_at": created_at or first_seen_at,
        }

    @staticmethod
    def _record_evidence(
        session,
        fund_code: str,
        artifact_id: str | None,
        fund_payload: dict[str, Any],
    ) -> None:
        availability = dict(fund_payload.get("availability") or {})
        first_seen = _datetime(availability.get("first_seen_at")) or datetime.now(UTC)
        retrieved = _datetime(availability.get("retrieved_at")) or first_seen
        published = _datetime(availability.get("published_at"))
        content_hash = _hash(fund_payload)
        source = f"fund_snapshot:{artifact_id or content_hash}"
        existing = session.scalar(
            select(EvidenceObservation).where(
                EvidenceObservation.source == source,
                EvidenceObservation.content_hash == content_hash,
            )
        )
        if existing is None:
            session.add(
                EvidenceObservation(
                    subject_type="fund",
                    subject_code=fund_code,
                    source=source,
                    event_date=_date(fund_payload.get("as_of")),
                    published_at=published,
                    first_seen_at=first_seen,
                    retrieved_at=retrieved,
                    content_hash=content_hash,
                    artifact_id=artifact_id,
                    metadata_json={"point_in_time_verified": fund_payload.get("point_in_time_verified")},
                )
            )

    def _recompute_signal(
        self,
        scope: OwnerScope,
        assignment_id: str,
        report_set_id: str,
        *,
        strategy_version_id: str | None = None,
    ) -> None:
        if self.signal_engine is None:
            return
        self.signal_engine.recompute(
            scope,
            assignment_id=assignment_id,
            report_set_id=report_set_id,
            strategy_version_id=strategy_version_id,
        )

    def _recompute_or_carry(
        self,
        scope: OwnerScope,
        *,
        assignment_id: str,
        report_set_id: str,
        as_of: date,
        strategy_version_id: str | None = None,
    ) -> None:
        if self.signal_engine is None:
            return
        try:
            self.signal_engine.carry_forward(
                scope,
                assignment_id=assignment_id,
                as_of_date=as_of,
                strategy_version_id=strategy_version_id,
            )
        except StrategyConflict:
            self._recompute_signal(
                scope,
                assignment_id,
                report_set_id,
                strategy_version_id=strategy_version_id,
            )

    def _assignment_contract(
        self,
        session,
        scope: OwnerScope,
        assignment_id: str,
        *,
        strategy_version_id: str | None = None,
    ) -> tuple[StrategyAssignment, StrategyVersion, StrategyDocumentV1]:
        assignment = self.repository.get_assignment(
            session,
            assignment_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        if assignment is None:
            raise StrategyDomainError("assignment_not_found", "未找到 Assignment", status_code=404)
        version = session.get(
            StrategyVersion,
            strategy_version_id or assignment.active_version_id,
        )
        if version is None:
            raise StrategyDomainError("strategy_version_not_found", "Assignment 策略版本不存在", status_code=409)
        if version.strategy_id != assignment.strategy_id:
            raise StrategyConflict(
                "assignment_strategy_version_mismatch",
                "所选策略版本不属于当前 Assignment 的策略",
            )
        return assignment, version, StrategyDocumentV1.model_validate(version.document_json)

    def _job_scope(self, job_id: str) -> OwnerScope:
        with self.database.session() as session:
            job = session.get(OrchestrationJob, job_id)
            if job is None:
                raise StrategyDomainError("report_job_not_found", "日报任务不存在", status_code=404)
            return OwnerScope(job.tenant_id, job.user_id, "report_job_worker")

    def _require_job(self, session, scope: OwnerScope, job_id: str) -> OrchestrationJob:
        job = self.repository.get_job(
            session,
            job_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        if job is None:
            raise StrategyDomainError("report_job_not_found", "日报任务不存在", status_code=404)
        return job

    def _new_attempt(
        self,
        session,
        job: OrchestrationJob,
        *,
        selected_nodes: list[str],
    ) -> JobAttempt:
        attempts = self.repository.list_attempts(session, job.id)
        attempt = JobAttempt(
            job_id=job.id,
            parent_attempt_id=attempts[-1].id if attempts else None,
            attempt_number=len(attempts) + 1,
            selected_nodes_json=selected_nodes,
            status=JobStatus.QUEUED.value,
        )
        session.add(attempt)
        session.flush()
        return attempt

    def _job_payload(self, session, row: OrchestrationJob) -> dict[str, Any]:
        return {
            "id": row.id,
            "job_type": row.job_type,
            "status": row.status,
            "request": row.request_json,
            "progress": row.progress_json,
            "pause_requested": row.pause_requested,
            "cancel_requested": row.cancel_requested,
            "error_summary": row.error_summary,
            "attempts": [self._attempt_payload(value) for value in self.repository.list_attempts(session, row.id)],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "completed_at": row.completed_at,
        }

    def _report_payload(self, session, row: DailyReportSet, *, detail: bool = False) -> dict[str, Any]:
        payload = {
            "id": row.id,
            "assignment_id": row.assignment_id,
            "fund_code": row.fund_code,
            "fund_name": row.fund_name,
            "as_of_date": row.as_of_date,
            "effective_date": row.effective_date,
            "pin_set_hash": row.pin_set_hash,
            "revision": row.revision,
            "status": row.status,
            "max_top_n": row.max_top_n,
            "count_coverage": row.count_coverage,
            "weight_coverage": row.weight_coverage,
            "coverage": row.coverage,
            "source_hash": row.source_hash,
            "source_run_id": row.source_run_id,
            "source_artifact_id": row.source_artifact_id,
            "data_gaps": row.data_gaps_json,
            "finalized_at": row.finalized_at,
            "created_at": row.created_at,
        }
        if detail:
            payload["nodes"] = [
                self._node_payload(node)
                for node in self.repository.list_report_nodes(session, row.id)
            ]
        return payload

    @staticmethod
    def _attempt_payload(row: JobAttempt) -> dict[str, Any]:
        return {
            "id": row.id,
            "attempt_number": row.attempt_number,
            "parent_attempt_id": row.parent_attempt_id,
            "selected_nodes": row.selected_nodes_json,
            "status": row.status,
            "artifact_refs": row.artifact_refs_json,
            "error_summary": row.error_summary,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    @staticmethod
    def _node_payload(row: DailyReportNode) -> dict[str, Any]:
        return {
            "id": row.id,
            "node_type": row.node_type,
            "subject_code": row.subject_code,
            "subject_name": row.subject_name,
            "rank": row.rank,
            "disclosed_weight_pct": row.disclosed_weight_pct,
            "effective_weight_pct": row.effective_weight_pct,
            "canonical_key": row.canonical_key,
            "status": row.status,
            "run_id": row.run_id,
            "artifact_id": row.artifact_id,
            "content_hash": row.content_hash,
            "confidence": row.confidence,
            "data_gaps": row.data_gaps_json,
            "source": row.source_json,
        }

    def _recover_jobs(self) -> None:
        try:
            now = datetime.now(UTC)
            with self.database.session() as session:
                rows = list(
                    session.scalars(
                        select(OrchestrationJob).where(
                            OrchestrationJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value])
                        )
                    )
                )
                recover: list[tuple[str, str]] = []
                for job in rows:
                    lease_until = _aware_utc(job.lease_until)
                    if (
                        job.status == JobStatus.RUNNING.value
                        and lease_until is not None
                        and lease_until > now
                    ):
                        continue
                    attempts = self.repository.list_attempts(session, job.id)
                    if job.status == JobStatus.RUNNING.value:
                        if attempts:
                            attempts[-1].status = JobStatus.FAILED.value
                            attempts[-1].error_summary = "worker_lease_expired"
                            attempts[-1].completed_at = now
                        attempt = self._new_attempt(session, job, selected_nodes=[])
                    else:
                        attempt = (
                            attempts[-1]
                            if attempts and attempts[-1].status == JobStatus.QUEUED.value
                            else self._new_attempt(session, job, selected_nodes=[])
                        )
                    job.status = JobStatus.QUEUED.value
                    job.lease_owner = None
                    job.lease_until = None
                    attempt.status = JobStatus.QUEUED.value
                    recover.append((job.id, attempt.id))
            for job_id, attempt_id in recover:
                self._submit(job_id, attempt_id)
        except Exception:
            return

    def _schedule_loop(self) -> None:
        while not self._scheduler_stop.wait(30.0):
            try:
                self.run_due_schedules()
            except Exception:
                continue

    @staticmethod
    def _trading_dates(start: date, end: date) -> list[date]:
        if end < start:
            raise StrategyDomainError("invalid_date_range", "结束日期不能早于开始日期")
        return trading_dates(start, end)

    @staticmethod
    def _is_trading_day(value: date) -> bool:
        return is_trading_day(value)

    @staticmethod
    def _pin_hash(document: StrategyDocumentV1) -> str:
        return _hash(
            {
                "pins": document.pins.model_dump(mode="json"),
                "research_profile": document.report_inputs.research_profile,
                "top_n": document.report_inputs.top_n,
            }
        )

    @staticmethod
    def _report_effective_date(
        *,
        assignment: StrategyAssignment,
        version: StrategyVersion,
        as_of: date,
        revision: int,
        now: datetime | None = None,
    ) -> date:
        first_effective = next_weekday(as_of)
        if revision <= 1:
            return first_effective
        document = StrategyDocumentV1.model_validate(version.document_json)
        schedule = {
            **document.schedule_defaults.model_dump(mode="json"),
            **dict(assignment.schedule_json or {}),
        }
        timezone = ZoneInfo(str(schedule.get("timezone") or "Asia/Shanghai"))
        hour_text, minute_text = str(schedule.get("settlement_cutoff") or "08:30").split(":", 1)
        cutoff = datetime(
            first_effective.year,
            first_effective.month,
            first_effective.day,
            int(hour_text),
            int(minute_text),
            tzinfo=timezone,
        )
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone)
        else:
            current = current.astimezone(timezone)
        return next_weekday(first_effective) if current >= cutoff else first_effective

    @staticmethod
    def _uzi_pin_hash(document: StrategyDocumentV1) -> str:
        return _hash(
            {
                "uzi_methodology_version": document.pins.uzi_methodology_version,
                "uzi_source_commit": document.pins.uzi_source_commit,
                "model_id": document.pins.model_id,
                "model_parameters": document.pins.model_parameters,
                "prompt_hash": document.pins.prompt_hash,
                "output_schema_versions": document.pins.output_schema_versions,
                "research_profile": document.report_inputs.research_profile,
            }
        )

    @staticmethod
    def _canonical_uzi_key(
        symbol: str,
        as_of: date,
        pin_hash: str,
        manifest_hash: str = "legacy-unverified",
    ) -> str:
        input_hash = _hash(
            {
                "symbol": symbol,
                "as_of": as_of.isoformat(),
                "uzi_pin_set_hash": pin_hash,
                "market_data_manifest_hash": manifest_hash,
            }
        )
        return (
            f"uzi:{symbol}:{as_of.isoformat()}:{pin_hash}:"
            f"{manifest_hash}:{input_hash}"
        )

    @staticmethod
    def _runtime_scope(scope: OwnerScope) -> dict[str, str | None]:
        if ReportOrchestrationService._is_local(scope):
            return {"user_id": None, "tenant_id": None}
        return {"user_id": scope.user_id, "tenant_id": scope.tenant_id}

    @staticmethod
    def _is_local(scope: OwnerScope) -> bool:
        return scope.tenant_id == "local" and scope.user_id == "local_development"


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "" or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


__all__ = ["ReportOrchestrationService"]
