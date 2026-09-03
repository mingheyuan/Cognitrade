from __future__ import annotations

import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from research_system.strategies.contracts import BacktestMode
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.models import CanonicalUziAnalysis, JobAttempt, OrchestrationJob, StrategyVersion
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.orchestration import ReportOrchestrationService
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.strategies.service import OwnerScope, StrategyService
from research_system.strategies.signals import SignalEngineService


def _fund(code: str) -> dict[str, Any]:
    return {
        "fund_code": code,
        "fund_name": f"基金{code}",
        "fund_type": "股票型",
        "holdings_period": "2026-06-30",
        "holdings_coverage": {"is_fund_of_funds": False},
        "top_holdings": [
            {
                "rank": index,
                "code": f"600{index:03d}",
                "name": f"股票{index}",
                "weight_pct": 21 - index,
            }
            for index in range(1, 21)
        ],
    }


class FakeRunService:
    def __init__(self) -> None:
        self.sources = {
            "source-a": self._source_artifacts("110011", verified=True),
            "source-b": self._source_artifacts("000001", verified=True),
            "source-unverified": self._source_artifacts("161725", verified=False),
        }
        self.children: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _source_artifacts(code: str, *, verified: bool) -> list[dict[str, Any]]:
        availability = {
            "created_at": "2026-08-03T07:00:00+08:00" if verified else None,
            "source_refs": (
                [{"published_at": "2026-07-20T09:00:00+08:00"}] if verified else []
            ),
        }
        return [
            {
                "artifact_id": f"snapshot-{code}",
                "artifact_type": "fund_snapshot",
                "status": "active",
                "content_hash": f"snapshot-hash-{code}",
                "content": {"funds": [_fund(code)]},
                **availability,
            },
            {
                "artifact_id": f"analysis-{code}",
                "artifact_type": "fund_analysis",
                "status": "active",
                "content": {
                    "funds": [
                        {
                            "fund_code": code,
                            "factor_values": {
                                "year_1_return": 45,
                                "since_inception_annualized": 30,
                                "benchmark_excess_year_1": 25,
                                "max_drawdown": 0,
                                "volatility": 0,
                                "fee": 0,
                                "return_consistency": 100,
                            },
                            "confidence": 1.0,
                        }
                    ]
                },
            },
        ]

    def add_child(self, run_id: str, symbol: str) -> None:
        self.children[run_id] = [
            {
                "artifact_id": f"native-{run_id}",
                "artifact_type": "uzi_native_analysis",
                "status": "active",
                "content_hash": f"hash-{run_id}",
                "confidence": 1.0,
                "content": {
                    "symbol": symbol,
                    "synthesis": {"overall_score": 85.0},
                    "report_allowed": True,
                    "critical_count": 0,
                },
                "risk_flags": [],
            },
            {
                "artifact_id": f"report-{run_id}",
                "artifact_type": "research_report",
                "status": "active",
                "content": {"report_allowed": True, "critical_count": 0},
                "risk_flags": [],
            },
        ]

    def artifacts(self, run_id: str, *, artifact_type=None, **_scope) -> dict[str, Any]:
        rows = self.sources.get(run_id, self.children.get(run_id, []))
        if artifact_type:
            rows = [row for row in rows if row.get("artifact_type") == artifact_type]
        return {"artifacts": rows}

    def history(self, **_scope) -> list[dict[str, Any]]:
        return [
            {"run_id": run_id, "intent": "fund_research"}
            for run_id in self.sources
        ]

    def run(self, _request):
        return SimpleNamespace(run_id="")


class FakeHoldingReportService:
    def __init__(self, run_service: FakeRunService) -> None:
        self.run_service = run_service
        self.batches: dict[str, dict[str, Any]] = {}
        self.model_calls = 0
        self.counter = 0
        self.delay_seconds = 0.0
        self.lock = Lock()
        self.fail_once_symbols: set[str] = set()
        self.cancelled_batches: list[str] = []

    def create_batch(
        self,
        source_run_id: str,
        *,
        fund_code: str,
        top_n: int,
        canonical_reuse: dict[str, dict[str, Any]],
        **_kwargs,
    ) -> dict[str, Any]:
        selected_raw = _kwargs.get("selected_symbols")
        selected = set(str(value) for value in selected_raw) if selected_raw is not None else None
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        with self.lock:
            self.counter += 1
            batch_number = self.counter
            batch_id = f"batch-{batch_number}"
            holdings: list[dict[str, Any]] = []
            for index, row in enumerate(_fund(fund_code)["top_holdings"][:top_n], start=1):
                symbol = str(row["code"])
                cached = canonical_reuse.get(symbol)
                if cached:
                    child_run_id = str(cached["run_id"])
                    canonical_reused = True
                    item_status = "completed"
                    report_available = True
                    data_gap: list[str] = []
                    error = None
                elif selected is not None and symbol not in selected:
                    child_run_id = ""
                    canonical_reused = False
                    item_status = "failed"
                    report_available = False
                    data_gap = ["not_selected_for_retry"]
                    error = "not selected"
                else:
                    child_run_id = f"child-{batch_number}-{symbol}"
                    canonical_reused = False
                    self.model_calls += 1
                    if symbol in self.fail_once_symbols:
                        self.fail_once_symbols.remove(symbol)
                        item_status = "failed"
                        report_available = False
                        data_gap = ["model_failed"]
                        error = "model failed"
                    else:
                        self.run_service.add_child(child_run_id, symbol)
                        item_status = "completed"
                        report_available = True
                        data_gap = []
                        error = None
                holdings.append(
                    {
                        "rank": index,
                        "symbol": symbol,
                        "name": row["name"],
                        "fund_weight_pct": row["weight_pct"],
                        "supported": True,
                        "status": item_status,
                        "report_available": report_available,
                        "canonical_reused": canonical_reused,
                        "child_run_id": child_run_id,
                        "data_gap": data_gap,
                        "error": error,
                    }
                )
            state = {
                "batch_id": batch_id,
                "status": "completed",
                "source_run_id": source_run_id,
                "fund_code": fund_code,
                "profile": _kwargs.get("profile"),
                "holdings": holdings,
                "data_gap": [],
            }
            self.batches[batch_id] = state
        return state

    def detail(self, batch_id: str, **_scope) -> dict[str, Any]:
        return self.batches[batch_id]

    def retry(self, batch_id: str, **_kwargs) -> dict[str, Any]:
        return self.batches[batch_id]

    def cancel(self, batch_id: str, **_kwargs) -> dict[str, Any]:
        self.cancelled_batches.append(batch_id)
        state = self.batches[batch_id]
        state["status"] = "cancelled"
        return state


class ReportOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.database = StrategyDatabase(
            f"sqlite:///{Path(self.temporary.name) / 'strategies.sqlite3'}"
        )
        self.strategies = StrategyService(
            self.database,
            workspace_root=Path(self.temporary.name),
            auto_bootstrap=True,
        )
        self.owner = OwnerScope("tenant-a", "user-a", "actor-a")
        strategy = self.strategies.create_strategy(self.owner, name="日报策略")
        self.version = self.strategies.publish(self.owner, strategy["id"])
        self.strategy_id = strategy["id"]
        self.run_service = FakeRunService()
        self.holding_service = FakeHoldingReportService(self.run_service)
        self.signals = SignalEngineService(self.database)
        self.orchestration = ReportOrchestrationService(
            self.database,
            run_service=self.run_service,
            holding_report_service=self.holding_service,
            workspace_root=Path(self.temporary.name),
            max_workers=2,
            start_scheduler=False,
            signal_engine=self.signals,
        )

    def tearDown(self) -> None:
        self.orchestration.close()
        self.strategies.close()
        self.temporary.cleanup()

    def test_historical_backfill_uses_dedicated_batch_timeout(self) -> None:
        service = ReportOrchestrationService(
            self.database,
            run_service=self.run_service,
            holding_report_service=self.holding_service,
            workspace_root=Path(self.temporary.name),
            start_scheduler=False,
            historical_backfill_batch_timeout_seconds=28_800,
        )
        try:
            self.assertEqual(
                service._batch_timeout_seconds({"dates": ["2026-08-14"]}),
                30 * 60,
            )
            self.assertEqual(
                service._batch_timeout_seconds(
                    {"dates": ["2026-08-14"]},
                    profile="lite",
                ),
                60 * 60,
            )
            self.assertEqual(
                service._batch_timeout_seconds(
                    {"dates": ["2026-08-14"]},
                    profile="deep",
                ),
                3 * 60 * 60,
            )
            self.assertEqual(
                service._batch_timeout_seconds(
                    {"dates": ["2026-08-13", "2026-08-14"]},
                    profile="deep",
                ),
                28_800,
            )
        finally:
            service.close()

    def test_batch_timeout_requests_cascading_cancellation(self) -> None:
        self.holding_service.batches["batch-timeout"] = {
            "batch_id": "batch-timeout",
            "status": "running",
        }

        with self.assertRaisesRegex(TimeoutError, "cancellation was requested"):
            self.orchestration._wait_batch(
                "batch-timeout",
                self.owner,
                job_id="unused-because-timeout-is-immediate",
                timeout_seconds=0,
            )

        self.assertEqual(self.holding_service.cancelled_batches, ["batch-timeout"])

    def _assignment(self, fund_code: str, mode: BacktestMode = BacktestMode.FUND_ONLY) -> dict:
        return self.strategies.create_assignment(
            self.owner,
            strategy_id=self.strategy_id,
            fund_code=fund_code,
            fund_name=f"基金{fund_code}",
            mode=mode,
        )

    def _assignment_with_document(
        self,
        fund_code: str,
        *,
        top_n: int,
        prompt_hash: str | None = None,
        buy_threshold: float | None = None,
        watch_threshold: float | None = None,
    ) -> dict:
        strategy = self.strategies.create_strategy(
            self.owner,
            name=f"日报策略-{fund_code}-{top_n}-{prompt_hash or 'default'}",
        )
        document = strategy["draft"]["document"]
        document["report_inputs"]["top_n"] = top_n
        if prompt_hash is not None:
            document["pins"]["prompt_hash"] = prompt_hash
        if buy_threshold is not None:
            document["signal_thresholds"]["buy"] = buy_threshold
        if watch_threshold is not None:
            document["signal_thresholds"]["watch"] = watch_threshold
        self.strategies.update_draft(
            self.owner,
            strategy["id"],
            expected_revision=1,
            document=document,
        )
        self.strategies.publish(self.owner, strategy["id"])
        return self.strategies.create_assignment(
            self.owner,
            strategy_id=strategy["id"],
            fund_code=fund_code,
            fund_name=f"基金{fund_code}",
            mode=BacktestMode.FUND_ONLY,
        )

    def _run_job(self, assignment_id: str, source_run_id: str) -> dict[str, Any]:
        job = self.orchestration.create_job(
            self.owner,
            assignment_id=assignment_id,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 3),
            source_run_id=source_run_id,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = self.orchestration.get_job(self.owner, job["id"])
            if current["status"] not in {"queued", "running"}:
                return current
            time.sleep(0.01)
        self.fail("report job did not finish")

    def _wait_job(
        self,
        job_id: str,
        service: ReportOrchestrationService | None = None,
    ) -> dict[str, Any]:
        service = service or self.orchestration
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current = service.get_job(self.owner, job_id)
            if current["status"] not in {"queued", "running"}:
                return current
            time.sleep(0.01)
        self.fail("concurrent report job did not finish")

    def test_report_signal_pipeline_and_cross_fund_canonical_uzi_reuse(self) -> None:
        first = self._assignment("110011")
        first_job = self._run_job(first["id"], "source-a")
        self.assertEqual(first_job["status"], "completed")
        self.assertEqual(self.holding_service.model_calls, 10)
        first_report_id = first_job["progress"]["report_set_ids"][0]
        first_report = self.orchestration.get_report_set(self.owner, first_report_id)
        self.assertEqual(first_report["status"], "final")
        self.assertEqual(len(first_report["nodes"]), 11)
        holding_nodes = [
            node for node in first_report["nodes"] if node["node_type"] == "holding_uzi"
        ]
        self.assertTrue(
            all(
                node["artifact_id"] == node["source"]["context_link_artifact_id"]
                and node["source"]["canonical_artifact_id"]
                for node in holding_nodes
            )
        )
        link_artifacts = ArtifactBoard(
            Path(self.temporary.name)
            / "strategy_reports"
            / first_report_id
            / "artifact_board.jsonl"
        ).list_artifacts(
            thread_id=None,
            artifact_type="fund_holding_stock_report_link",
            status="active",
        )
        self.assertEqual(len(link_artifacts), 10)
        self.assertEqual(
            len(self.signals.list_signals(self.owner, assignment_id=first["id"])["signals"]),
            11,
        )

        second = self._assignment("000001")
        second_job = self._run_job(second["id"], "source-b")
        self.assertEqual(second_job["status"], "completed")
        self.assertEqual(self.holding_service.model_calls, 10)
        second_report = self.orchestration.get_report_set(
            self.owner,
            second_job["progress"]["report_set_ids"][0],
        )
        holding_nodes = [node for node in second_report["nodes"] if node["node_type"] == "holding_uzi"]
        self.assertTrue(all(node["source"]["canonical_reused"] for node in holding_nodes))

    def test_manual_explicit_version_job_can_materialize_signals_for_inactive_assignment(self) -> None:
        assignment = self._assignment("110011")
        self.strategies.update_assignment(
            self.owner,
            assignment["id"],
            enabled=False,
            paused=True,
        )

        with self.assertRaisesRegex(StrategyConflict, "已禁用或暂停"):
            self.orchestration.create_job(
                self.owner,
                assignment_id=assignment["id"],
                start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 3),
                source_run_id="source-a",
            )

        job = self.orchestration.create_job(
            self.owner,
            assignment_id=assignment["id"],
            strategy_version_id=self.version["id"],
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 3),
            source_run_id="source-a",
            manual=True,
        )
        completed = self._wait_job(job["id"])

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["request"]["strategy_version_id"], self.version["id"])
        self.assertTrue(
            self.signals.list_signals(
                self.owner,
                assignment_id=assignment["id"],
            )["signals"]
        )

    def test_backfill_uses_explicit_selected_strategy_version(self) -> None:
        assignment = self._assignment("110011")
        with self.database.session() as session:
            original = session.get(StrategyVersion, self.version["id"])
            document = dict(original.document_json)
            document["report_inputs"] = dict(document["report_inputs"])
            document["report_inputs"]["research_profile"] = "lite"
        self.strategies.update_draft(
            self.owner,
            self.strategy_id,
            expected_revision=0,
            document=document,
        )
        selected = self.strategies.publish(self.owner, self.strategy_id)

        job = self.orchestration.create_job(
            self.owner,
            assignment_id=assignment["id"],
            strategy_version_id=selected["id"],
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 3),
            source_run_id="source-a",
        )
        completed = self._wait_job(job["id"])

        self.assertEqual(completed["request"]["strategy_version_id"], selected["id"])
        self.assertEqual(completed["progress"]["complete_report_days"], 1)
        self.assertEqual(completed["progress"]["partial_report_days"], 0)
        self.assertEqual(self.holding_service.batches["batch-1"]["profile"], "lite")
        signals = self.signals.list_signals(
            self.owner,
            assignment_id=assignment["id"],
        )["signals"]
        self.assertTrue(signals)
        self.assertTrue(all(row["strategy_version_id"] == selected["id"] for row in signals))

    def test_unverified_historical_evidence_is_blocked_without_current_substitution(self) -> None:
        assignment = self._assignment("161725")
        job = self._run_job(assignment["id"], "source-unverified")
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.holding_service.model_calls, 0)
        report = self.orchestration.get_report_set(
            self.owner,
            job["progress"]["report_set_ids"][0],
        )
        self.assertEqual(report["status"], "blocked")
        signal = self.signals.list_signals(self.owner, assignment_id=assignment["id"])["signals"][0]
        self.assertEqual(signal["state"], "BLOCKED")
        self.assertIn("point_in_time_unverified", signal["reasons"])

    def test_concurrent_funds_share_one_database_reserved_uzi_generation(self) -> None:
        first = self._assignment("110011")
        second = self._assignment("000001")
        self.holding_service.delay_seconds = 0.2
        second_database = StrategyDatabase(self.database.database_url)
        second_orchestration = ReportOrchestrationService(
            second_database,
            run_service=self.run_service,
            holding_report_service=self.holding_service,
            workspace_root=Path(self.temporary.name),
            max_workers=1,
            start_scheduler=False,
            signal_engine=SignalEngineService(second_database),
        )
        try:
            first_job = self.orchestration.create_job(
                self.owner,
                assignment_id=first["id"],
                start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 3),
                source_run_id="source-a",
            )
            second_job = second_orchestration.create_job(
                self.owner,
                assignment_id=second["id"],
                start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 3),
                source_run_id="source-b",
            )

            first_result = self._wait_job(first_job["id"])
            second_result = self._wait_job(second_job["id"], second_orchestration)
        finally:
            second_orchestration.close()
            second_database.close()
        self.assertEqual(first_result["status"], "completed")
        self.assertEqual(second_result["status"], "completed")
        self.assertEqual(self.holding_service.model_calls, 10)

        with self.database.session() as session:
            canonical = list(session.query(CanonicalUziAnalysis).all())
        self.assertEqual(len(canonical), 10)
        self.assertTrue(all(row.status == "completed" for row in canonical))

        reports = [
            self.orchestration.get_report_set(
                self.owner,
                result["progress"]["report_set_ids"][0],
            )
            for result in (first_result, second_result)
        ]
        reused = [
            node
            for report in reports
            for node in report["nodes"]
            if node["node_type"] == "holding_uzi" and node["source"].get("canonical_reused")
        ]
        self.assertEqual(len(reused), 10)

    def test_failed_symbol_retry_creates_new_attempt_and_late_report_revision(self) -> None:
        assignment = self._assignment("110011")
        self.holding_service.fail_once_symbols.add("600003")
        first = self._run_job(assignment["id"], "source-a")
        self.assertEqual(first["status"], "completed_partial")
        self.assertEqual(first["progress"]["failed_symbols"], ["600003"])
        first_report = self.orchestration.get_report_set(
            self.owner,
            first["progress"]["report_set_ids"][-1],
        )
        self.assertEqual(first_report["revision"], 1)
        self.assertEqual(first_report["status"], "final_partial")
        self.assertEqual(self.holding_service.model_calls, 10)

        with self.database.session() as session:
            stored_job = session.get(OrchestrationJob, first["id"])
            self.assertIsNotNone(stored_job)
            progress = dict(stored_job.progress_json or {})
            progress["errors"] = [
                {
                    "as_of_date": "2026-08-03",
                    "type": "TimeoutError",
                    "message": "old attempt timed out",
                }
            ]
            stored_job.progress_json = progress
            stored_job.error_summary = "old attempt timed out"

        retried = self.orchestration.retry(
            self.owner,
            first["id"],
            symbols=["600003"],
        )
        self.assertIsNone(retried["error_summary"])
        self.assertIsNone(retried["completed_at"])
        second = self._wait_job(retried["id"])
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["progress"]["failed_symbols"], [])
        self.assertEqual(second["progress"]["errors"], [])
        self.assertIsNone(second["error_summary"])
        self.assertEqual(self.holding_service.model_calls, 11)
        self.assertEqual(len(second["attempts"]), 2)
        self.assertEqual(second["attempts"][1]["selected_nodes"], ["600003"])
        self.assertEqual(second["attempts"][0]["status"], "completed_partial")

        second_report = self.orchestration.get_report_set(
            self.owner,
            second["progress"]["report_set_ids"][-1],
        )
        self.assertEqual(second_report["revision"], 2)
        self.assertEqual(second_report["status"], "final")
        self.assertGreater(second_report["effective_date"], first_report["effective_date"])
        with self.database.session() as session:
            canonical = list(session.query(CanonicalUziAnalysis).all())
        self.assertEqual(len(canonical), 10)
        self.assertTrue(all(row.status == "completed" for row in canonical))

    def test_full_rerun_requires_confirmation_and_reuses_canonical_uzi(self) -> None:
        assignment = self._assignment("110011")
        first = self._run_job(assignment["id"], "source-a")
        manifest_calls: list[str] = []

        def frozen_report_manifest(_batch_id: str, **_scope):
            manifest_calls.append("frozen")
            return None

        def freeze_report_inputs(_symbols, **_kwargs):
            manifest_calls.append("freeze")
            return None

        self.holding_service.frozen_report_manifest = frozen_report_manifest
        self.holding_service.freeze_report_inputs = freeze_report_inputs
        with self.assertRaises(StrategyDomainError) as captured:
            self.orchestration.retry(
                self.owner,
                first["id"],
                full_rerun=True,
                confirmed=False,
            )
        self.assertEqual(captured.exception.code, "full_rerun_confirmation_required")

        rerun = self.orchestration.retry(
            self.owner,
            first["id"],
            full_rerun=True,
            confirmed=True,
        )
        completed = self._wait_job(rerun["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(manifest_calls, ["freeze"])
        self.assertEqual(self.holding_service.model_calls, 10)
        self.assertEqual(len(completed["attempts"]), 2)
        latest = self.orchestration.get_report_set(
            self.owner,
            completed["progress"]["report_set_ids"][-1],
        )
        self.assertEqual(latest["revision"], 2)
        reused = [
            node
            for node in latest["nodes"]
            if node["node_type"] == "holding_uzi" and node["source"].get("canonical_reused")
        ]
        self.assertEqual(len(reused), 10)

    def test_paused_job_resumes_same_building_report_with_new_attempt(self) -> None:
        assignment = self._assignment("110011")
        self.holding_service.delay_seconds = 0.25
        job = self.orchestration.create_job(
            self.owner,
            assignment_id=assignment["id"],
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 3),
            source_run_id="source-a",
        )
        time.sleep(0.05)
        self.orchestration.pause(self.owner, job["id"])
        paused = self._wait_job(job["id"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(len(paused["attempts"]), 1)
        self.assertIsNotNone(paused["attempts"][0]["completed_at"])

        resumed = self.orchestration.resume(self.owner, job["id"])
        completed = self._wait_job(resumed["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(completed["attempts"]), 2)
        report_sets = self.orchestration.list_report_sets(
            self.owner,
            fund_code="110011",
            as_of_date=date(2026, 8, 3),
        )["report_sets"]
        self.assertEqual(len(report_sets), 1)
        self.assertEqual(report_sets[0]["revision"], 1)

    def test_expired_running_job_is_recovered_with_a_new_attempt(self) -> None:
        assignment = self._assignment("110011")
        self.orchestration._closed = True
        queued = self.orchestration.create_job(
            self.owner,
            assignment_id=assignment["id"],
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 3),
            source_run_id="source-a",
        )
        with self.database.session() as session:
            job = session.get(OrchestrationJob, queued["id"])
            self.assertIsNotNone(job)
            job.status = "running"
            job.lease_owner = "dead-worker"
            job.lease_until = datetime.now(UTC) - timedelta(minutes=1)
            attempt = session.query(JobAttempt).filter_by(job_id=job.id).one()
            attempt.status = "running"

        recovered_service = ReportOrchestrationService(
            self.database,
            run_service=self.run_service,
            holding_report_service=self.holding_service,
            workspace_root=Path(self.temporary.name),
            max_workers=1,
            start_scheduler=False,
            signal_engine=self.signals,
        )
        try:
            completed = self._wait_job(queued["id"], recovered_service)
        finally:
            recovered_service.close()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(completed["attempts"]), 2)
        self.assertEqual(completed["attempts"][0]["error_summary"], "worker_lease_expired")
        self.assertEqual(completed["attempts"][1]["status"], "completed")

    def test_backfill_estimate_uses_canonical_hits_and_enforces_sixty_sessions(self) -> None:
        assignment = self._assignment("110011")
        before = self.orchestration.estimate(
            self.owner,
            assignment_id=assignment["id"],
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 3),
            source_run_id="source-a",
        )
        self.assertEqual(before["model_call_upper_bound"], 10)
        self._run_job(assignment["id"], "source-a")
        after = self.orchestration.estimate(
            self.owner,
            assignment_id=assignment["id"],
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 3),
            source_run_id="source-a",
        )
        self.assertEqual(after["cache_hit_count"], 10)
        self.assertEqual(after["model_call_upper_bound"], 0)

        with self.assertRaises(StrategyDomainError) as captured:
            self.orchestration.estimate(
                self.owner,
                assignment_id=assignment["id"],
                start_date=date(2026, 1, 1),
                end_date=date(2026, 5, 31),
                source_run_id="source-a",
            )
        self.assertEqual(captured.exception.code, "backfill_range_too_large")

    def test_scheduler_tick_is_trading_day_time_gated_and_idempotent(self) -> None:
        assignment = self._assignment("110011")
        self.strategies.update_assignment(
            self.owner,
            assignment["id"],
            schedule={"enabled": True, "local_time": "16:30"},
        )
        self.assertEqual(
            self.orchestration.run_due_schedules(
                now=datetime(2026, 8, 3, 16, 29, tzinfo=ZoneInfo("Asia/Shanghai"))
            ),
            [],
        )
        first = self.orchestration.run_due_schedules(
            now=datetime(2026, 8, 3, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        )
        second = self.orchestration.run_due_schedules(
            now=datetime(2026, 8, 3, 16, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(second, first)
        self.assertEqual(
            self.orchestration.run_due_schedules(
                now=datetime(2026, 8, 2, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            ),
            [],
        )
        completed = self._wait_job(first[0])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(self.orchestration.list_jobs(self.owner)["jobs"]), 1)

    def test_top_n_shares_superset_and_uzi_pin_change_invalidates_cache(self) -> None:
        top_five = self._assignment_with_document("110011", top_n=5)
        top_twenty = self._assignment_with_document("000001", top_n=20)
        first = self._run_job(top_five["id"], "source-a")
        second = self._run_job(top_twenty["id"], "source-b")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(self.holding_service.model_calls, 20)
        with self.database.session() as session:
            self.assertEqual(session.query(CanonicalUziAnalysis).count(), 20)

        invalidated = self._assignment_with_document(
            "110011",
            top_n=5,
            prompt_hash="prompt-v2",
        )
        third = self._run_job(invalidated["id"], "source-a")
        self.assertEqual(third["status"], "completed")
        self.assertEqual(self.holding_service.model_calls, 25)
        with self.database.session() as session:
            self.assertEqual(session.query(CanonicalUziAnalysis).count(), 25)

    def test_threshold_only_strategy_reuses_report_and_signal_hash_is_deterministic(self) -> None:
        first_assignment = self._assignment("110011")
        first_job = self._run_job(first_assignment["id"], "source-a")
        report_set_id = first_job["progress"]["report_set_ids"][-1]
        first_fund_signal = next(
            signal
            for signal in self.signals.list_signals(
                self.owner,
                assignment_id=first_assignment["id"],
            )["signals"]
            if signal["kind"] == "fund"
        )
        self.assertEqual(self.holding_service.model_calls, 10)

        stricter_assignment = self._assignment_with_document(
            "110011",
            top_n=10,
            buy_threshold=95,
            watch_threshold=90,
        )
        second_job = self._run_job(stricter_assignment["id"], "source-a")
        self.assertEqual(second_job["progress"]["report_set_ids"][-1], report_set_id)
        self.assertEqual(self.holding_service.model_calls, 10)
        second_fund_signal = next(
            signal
            for signal in self.signals.list_signals(
                self.owner,
                assignment_id=stricter_assignment["id"],
            )["signals"]
            if signal["kind"] == "fund"
        )
        self.assertNotEqual(first_fund_signal["content_hash"], second_fund_signal["content_hash"])
        self.assertEqual(first_fund_signal["source_report_set_id"], report_set_id)
        self.assertEqual(second_fund_signal["source_report_set_id"], report_set_id)

        before = len(
            self.signals.list_signals(
                self.owner,
                assignment_id=stricter_assignment["id"],
            )["signals"]
        )
        repeated = self.signals.recompute(
            self.owner,
            assignment_id=stricter_assignment["id"],
            report_set_id=report_set_id,
        )
        after = len(
            self.signals.list_signals(
                self.owner,
                assignment_id=stricter_assignment["id"],
            )["signals"]
        )
        self.assertEqual(repeated["fund_signal"]["content_hash"], second_fund_signal["content_hash"])
        self.assertEqual(after, before)

        version_detail = self.strategies.get_version(
            self.owner,
            self.strategy_id,
            self.version["id"],
        )
        draft_document = version_detail["document"]
        draft_document["signal_thresholds"]["buy"] = 99
        draft_document["signal_thresholds"]["watch"] = 98
        self.strategies.update_draft(
            self.owner,
            self.strategy_id,
            expected_revision=0,
            document=draft_document,
        )
        persisted_before = len(
            self.signals.list_signals(
                self.owner,
                assignment_id=first_assignment["id"],
            )["signals"]
        )
        preview = self.signals.preview(
            self.owner,
            assignment_id=first_assignment["id"],
            report_set_id=report_set_id,
            draft_strategy_id=self.strategy_id,
        )
        self.assertFalse(preview["persisted"])
        self.assertTrue(str(preview["strategy_version_id"]).startswith("draft:"))
        self.assertEqual(
            len(
                self.signals.list_signals(
                    self.owner,
                    assignment_id=first_assignment["id"],
                )["signals"]
            ),
            persisted_before,
        )
        self.assertEqual(self.holding_service.model_calls, 10)


if __name__ == "__main__":
    unittest.main()
