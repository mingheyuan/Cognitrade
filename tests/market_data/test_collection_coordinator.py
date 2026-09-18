from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event
from time import monotonic, sleep
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from research_system.market_data.collection import (
    CollectionJobError,
    MarketDataCollectionCoordinator,
)
from research_system.market_data.contracts import CollectionStatus, SnapshotIngestInput
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataBundle,
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
    MarketDataSubscription,
)
from research_system.strategies.database import StrategyDatabase


class _Collector:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        retrieved_at = datetime.now(timezone.utc).isoformat()
        as_of_date = kwargs.get("as_of_date")
        rows = []
        for symbol in kwargs["symbols"]:
            for snapshot_type in kwargs["snapshot_types"]:
                if snapshot_type != "market":
                    continue
                rows.append(
                    {
                        "snapshot_type": "market",
                        "symbol": symbol,
                        "retrieved_at": retrieved_at,
                        "snapshot_date": (
                            as_of_date or datetime.now(timezone.utc).date()
                        ).isoformat(),
                        "quality_status": "available",
                        "facts": {
                            "availability": "available",
                            "_uzi_raw_result": {
                                "data": {
                                    "code": symbol,
                                    "name": "贵州茅台",
                                    "industry": "白酒",
                                    "price": 100.0,
                                    "market_cap_raw": 1_000_000.0,
                                    "pe_ttm": 20.0,
                                    "pb": 3.0,
                                }
                            },
                        },
                        "source": {"provider_id": "test.market", "chain_position": 0},
                        **(
                            {
                                "historical_reconstruction": {
                                    "safe": True,
                                    "strategy": "dated_series",
                                    "requested_as_of_date": as_of_date.isoformat(),
                                    "window_start": (
                                        as_of_date - timedelta(days=550)
                                    ).isoformat(),
                                    "window_end": as_of_date.isoformat(),
                                }
                            }
                            if as_of_date is not None
                            else {}
                        ),
                    }
                )
        return rows


class _HistoricalResearchCollector:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        target = str(kwargs["query"]).rsplit(" ", 1)[-1]
        retrieved_at = datetime.now(timezone.utc).isoformat()
        return [
            {
                "snapshot_type": "research",
                "symbol": symbol,
                "retrieved_at": retrieved_at,
                "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
                "quality_status": "available",
                "facts": {
                    "availability": "available",
                    "_uzi_raw_result": {
                        "data": {
                            "recent_reports": [
                                {
                                    "title": "历史研报",
                                    "报告日期": target,
                                    "url": "https://example.test/research/history",
                                }
                            ],
                            "report_count": 1,
                        }
                    },
                },
                "source": {
                    "provider_id": "test.research_archive",
                    "chain_position": 0,
                },
            }
            for symbol in kwargs["symbols"]
        ]


class _HistoricalOutcomeCollector:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        target = kwargs["as_of_date"]
        if self.outcome == "partial":
            quality = "degraded"
            data = {"pe": 20.0, "pb": 3.0}
            source_trace = [{"source_id": "test.valuation", "status": "available"}]
            missing_fields = ["valuation.pe_history"]
        else:
            quality = "provider_unavailable"
            data = {}
            source_trace = [
                {
                    "source_id": "test.valuation",
                    "status": "provider_unavailable",
                    "error_type": "ConnectionError",
                }
            ]
            missing_fields = ["valuation"]
        return [
            {
                "snapshot_type": "valuation",
                "symbol": symbol,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_date": target.isoformat(),
                "quality_status": quality,
                "missing_fields": missing_fields,
                "facts": {
                    "availability": quality,
                    "_uzi_raw_result": {"data": data},
                },
                "source": {
                    "provider_id": "test.valuation",
                    "chain_position": 0,
                },
                "source_trace": source_trace,
                "historical_reconstruction": {
                    "safe": True,
                    "strategy": "dated_series",
                    "requested_as_of_date": target.isoformat(),
                    "window_start": (target - timedelta(days=550)).isoformat(),
                    "window_end": target.isoformat(),
                },
            }
            for symbol in kwargs["symbols"]
        ]


class CollectionCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = StrategyDatabase("sqlite://")
        self.database.create_schema()
        self.collector = _Collector()
        self.coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
        )

    def tearDown(self) -> None:
        self.coordinator.close()
        self.database.close()

    def test_creation_is_persistent_but_does_not_collect_when_not_submitted(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()

        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )

        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(job["tasks"]), 1)
        self.assertEqual(self.collector.calls, [])

    def test_creation_rejects_concurrency_outside_public_one_to_five_contract(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()
        for concurrency in (0, 6):
            with (
                self.subTest(max_concurrency=concurrency),
                self.assertRaises(CollectionJobError) as captured,
            ):
                self.coordinator.create_job(
                    tenant_id="tenant",
                    user_id="user",
                    actor_id="actor",
                    symbols=["600519.SH"],
                    start_date=today,
                    end_date=today,
                    dimensions=["0_basic"],
                    max_concurrency=concurrency,
                    submit=False,
                )
            self.assertEqual(captured.exception.code, "collection_concurrency_invalid")

    def test_future_exchange_date_is_rejected_before_persistence(self) -> None:
        future_day = datetime.now(timezone.utc).date() + timedelta(days=2)

        with self.assertRaises(CollectionJobError) as captured:
            self.coordinator.create_job(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                symbols=["600519.SH"],
                start_date=future_day,
                end_date=future_day,
                dimensions=["0_basic"],
                submit=False,
            )

        self.assertEqual(captured.exception.code, "collection_future_date")
        with self.database.session() as session:
            self.assertIsNone(session.scalar(select(MarketDataCollectionTask)))

    def test_current_day_collection_persists_a_strict_snapshot(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )

        self.coordinator.run_job_now(job["id"])
        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["tasks"][0]["status"], "succeeded")
        self.assertEqual(len(self.collector.calls), 1)
        self.assertEqual(self.collector.calls[0]["max_workers"], 5)
        self.assertFalse(self.collector.calls[0]["uzi_root_route"])
        self.assertFalse(self.collector.calls[0]["no_resume"])
        with self.database.session() as session:
            result = session.scalar(select(MarketDataCollectionTaskResult))
            self.assertIsNotNone(result.snapshot_id)
            self.assertEqual(result.status, "succeeded")

    def test_complete_dimension_run_keeps_native_uzi_root_workflow(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            publish_when_complete=False,
            submit=False,
        )

        self.coordinator.run_job_now(job["id"])

        self.assertEqual(len(self.collector.calls), 1)
        self.assertTrue(self.collector.calls[0]["uzi_root_route"])
        self.assertEqual(self.collector.calls[0]["uzi_root_route_mode"], "skill")
        self.assertTrue(self.collector.calls[0]["no_resume"])

    def test_full_draft_requires_agent_stage_and_explicit_freeze_confirmation(
        self,
    ) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            publish_when_complete=False,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        collected = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        original_evaluated_at = collected["publication_candidate"]["evaluated_at"]
        with self.database.session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MarketDataBundle)), 0
            )

        recovery_result = {
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "persisted_snapshot_count": 0,
            "coverage": [],
            "stages": {"strict_recovery": {"attempted": 1, "succeeded": 1}},
            "provider_recovery": {
                "attempted_dimensions": 0,
                "persisted_snapshot_count": 0,
                "failures": [],
            },
        }
        with patch(
            "research_system.market_data.collection.recover_collection_job_draft",
            return_value=recovery_result,
        ):
            queued = coordinator.start_agent_recovery(
                job["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
            )
            self.assertEqual(queued["freeze_status"], "awaiting_recovery")
            coordinator._recovery_futures[job["id"]].result(timeout=5)

        recovered = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertEqual(recovered["agent_recovery"]["status"], "succeeded")
        self.assertGreater(
            datetime.fromisoformat(recovered["publication_candidate"]["evaluated_at"]),
            datetime.fromisoformat(original_evaluated_at),
        )
        self.assertTrue(recovered["can_confirm_freeze"])
        with self.database.session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MarketDataBundle)), 0
            )

        frozen = coordinator.confirm_freeze(
            job["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            proposal_hash=recovered["publication_candidate"]["proposal_hash"],
            acknowledge_degraded=True,
        )
        self.assertEqual(frozen["freeze_status"], "frozen")
        with self.database.session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MarketDataBundle)), 1
            )
            row = session.get(MarketDataCollectionJob, job["id"])
            row.status = CollectionStatus.PARTIAL.value
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            task.status = CollectionStatus.FAILED.value
            task.retryable = True
            task.attempt_count = 1
        with self.assertRaises(CollectionJobError) as captured:
            coordinator.retry_failed(
                job["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
            )
        self.assertEqual(captured.exception.code, "collection_retry_bundle_frozen")

    def test_retry_invalidates_old_freeze_candidate_before_new_provider_wave(
        self,
    ) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            publish_when_complete=False,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        before = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertIsNotNone(before["publication_candidate"])
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            row.status = CollectionStatus.PARTIAL.value
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            task.status = CollectionStatus.FAILED.value
            task.retryable = True
            task.attempt_count = 1
        with patch.object(coordinator, "submit") as submit:
            retried = coordinator.retry_failed(
                job["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
            )
        submit.assert_called_once_with(job["id"])
        self.assertIsNone(retried["publication_candidate"])
        self.assertEqual(
            retried["checkpoint"]["publication"]["error_code"],
            "collection_retry_pending",
        )

    def test_restart_resumes_persisted_agent_recovery_stage(self) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            publish_when_complete=False,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            checkpoint.pop("publication_candidate", None)
            checkpoint["publication"] = {"status": "blocked"}
            checkpoint["agent_recovery"] = {
                "status": "running",
                "requested_by": "actor",
            }
            row.checkpoint_json = checkpoint
        with patch.object(coordinator, "_submit_agent_recovery") as submit:
            resumed = coordinator.resume_pending()
        self.assertEqual(resumed, [job["id"]])
        submit.assert_called_once_with(job["id"])
        persisted = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertEqual(persisted["agent_recovery"]["status"], "queued")
        self.assertIn("重启", persisted["agent_recovery"]["message"])

    def test_scoped_event_retry_does_not_collect_basic_or_use_root_workflow(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["15_events"],
            publish_when_complete=False,
            submit=False,
        )

        self.coordinator.run_job_now(job["id"])

        self.assertEqual(self.collector.calls[0]["snapshot_types"], ["events"])
        self.assertFalse(self.collector.calls[0]["uzi_root_route"])
        self.assertFalse(self.collector.calls[0]["no_resume"])
        self.assertTrue(callable(self.collector.calls[0]["progress_callback"]))
        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(
            completed["progress"]["provider"],
            {
                "as_of_date": today.isoformat(),
                "phase": "settled",
                "snapshot_type": None,
                "completed": 1,
                "total": 1,
                "symbol": "600519.SH",
                "scope_symbols": ["600519.SH"],
                "activity_scope": "symbol",
                "started_at": completed["progress"]["provider"]["started_at"],
                "elapsed_seconds": completed["progress"]["provider"]["elapsed_seconds"],
                "updated_at": completed["progress"]["provider"]["updated_at"],
            },
        )
        self.assertGreaterEqual(completed["progress"]["provider"]["elapsed_seconds"], 0)
        self.assertEqual(
            completed["checkpoint"]["provider_progress"],
            completed["progress"]["provider"],
        )
        self.assertTrue(completed["progress"]["provider"]["started_at"])

    def test_adapter_validation_failure_is_isolated_and_batch_continues(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH", "000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            mode="force_all",
            publish_when_complete=False,
            submit=False,
        )
        delegate = self.coordinator.adapter

        class _InvalidFirstAdapter:
            def adapt(self, native, **kwargs):
                if kwargs["symbol"] == "600519.SH":
                    SnapshotIngestInput.model_validate(
                        {"symbol": "sensitive-input-must-not-be-persisted"}
                    )
                return delegate.adapt(native, **kwargs)

        self.coordinator.adapter = _InvalidFirstAdapter()  # type: ignore[assignment]

        self.coordinator.run_job_now(job["id"])

        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        tasks = {task["symbol"]: task for task in completed["tasks"]}
        self.assertEqual(completed["status"], "partial")
        self.assertEqual(completed["progress"]["completed"], 2)
        self.assertEqual(completed["progress"]["total"], 2)
        self.assertEqual(
            completed["progress"]["by_status"], {"failed": 1, "succeeded": 1}
        )
        self.assertEqual(
            set(completed["checkpoint"]["finished_task_ids"]),
            {task["id"] for task in completed["tasks"]},
        )
        self.assertEqual(tasks["600519.SH"]["status"], "failed")
        self.assertEqual(tasks["600519.SH"]["error_code"], "adapter_validation_error")
        self.assertNotIn(
            "sensitive-input-must-not-be-persisted",
            tasks["600519.SH"]["error_message"],
        )
        self.assertFalse(tasks["600519.SH"]["retryable"])
        self.assertEqual(tasks["000858.SZ"]["status"], "succeeded")
        with self.database.session() as session:
            rows = list(session.scalars(select(MarketDataCollectionTaskResult)))
            self.assertEqual(len(rows), 2)
            failed = next(row for row in rows if row.status == "failed")
            self.assertEqual(failed.provider_status, "adapter_error")
            self.assertEqual(failed.error_code, "adapter_validation_error")

    def test_unexpected_ingestion_failure_is_isolated_and_safely_summarized(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH", "000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            mode="force_all",
            publish_when_complete=False,
            submit=False,
        )

        class _FailFirstIngestion:
            def __init__(self, repository, *, registry):
                self.delegate = SnapshotIngestionService(repository, registry=registry)

            def ingest(self, item):
                if item.symbol == "600519.SH":
                    raise RuntimeError("sensitive-provider-detail")
                return self.delegate.ingest(item)

        with patch(
            "research_system.market_data.collection.SnapshotIngestionService",
            _FailFirstIngestion,
        ):
            self.coordinator.run_job_now(job["id"])

        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        tasks = {task["symbol"]: task for task in completed["tasks"]}
        self.assertEqual(completed["status"], "partial")
        self.assertEqual(tasks["600519.SH"]["status"], "failed")
        self.assertEqual(tasks["600519.SH"]["error_code"], "ingestion_unexpected_error")
        self.assertEqual(
            tasks["600519.SH"]["error_message"],
            "ingestion failed with RuntimeError",
        )
        self.assertNotIn(
            "sensitive-provider-detail", tasks["600519.SH"]["error_message"]
        )
        self.assertTrue(tasks["600519.SH"]["retryable"])
        self.assertEqual(tasks["000858.SZ"]["status"], "succeeded")

    def test_worker_failure_terminalizes_active_tasks_and_updates_progress(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH", "000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            publish_when_complete=False,
            submit=False,
        )
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            row.status = "running"
            row.lease_owner = "worker"
            row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job["id"]
                    )
                )
            )
            for task in tasks:
                task.status = "running"
                task.attempt_count = 1
                task.lease_owner = "worker"
                task.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)

        self.coordinator._fail_job(job["id"], RuntimeError("sensitive-worker-detail"))

        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["progress"]["completed"], 2)
        self.assertEqual(completed["progress"]["total"], 2)
        self.assertEqual(completed["progress"]["by_status"], {"failed": 2})
        self.assertNotIn("sensitive-worker-detail", completed["error_summary"])
        self.assertIn("RuntimeError", completed["error_summary"])
        self.assertEqual(
            set(completed["checkpoint"]["finished_task_ids"]),
            {task["id"] for task in completed["tasks"]},
        )
        with self.database.session() as session:
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job["id"]
                    )
                )
            )
            self.assertTrue(all(task.status == "failed" for task in tasks))
            self.assertTrue(all(task.lease_owner is None for task in tasks))
            self.assertTrue(all(task.lease_until is None for task in tasks))
            self.assertTrue(all(task.completed_at is not None for task in tasks))
            self.assertTrue(
                all(task.last_error_code == "worker_unexpected_error" for task in tasks)
            )

    def test_cancelled_finish_terminalizes_all_active_tasks(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH", "000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            publish_when_complete=False,
            submit=False,
        )
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            row.status = "running"
            for task in session.scalars(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            ):
                task.status = "running"
                task.lease_owner = "worker"

        self.coordinator._finish_job(job["id"], cancelled=True)

        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(completed["status"], "cancelled")
        self.assertEqual(completed["progress"]["completed"], 2)
        self.assertEqual(completed["progress"]["by_status"], {"cancelled": 2})
        self.assertTrue(
            all(task["status"] == "cancelled" for task in completed["tasks"])
        )

    def test_cancel_requested_before_worker_start_is_fully_finalized(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH", "000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            publish_when_complete=False,
            submit=False,
        )
        self.coordinator.cancel(
            job["id"], tenant_id="tenant", user_id="user", actor_id="actor"
        )

        self.coordinator.run_job_now(job["id"])

        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(completed["status"], "cancelled")
        self.assertEqual(completed["progress"]["completed"], 2)
        self.assertEqual(completed["progress"]["by_status"], {"cancelled": 2})
        self.assertEqual(self.collector.calls, [])
        self.assertTrue(
            all(task["status"] == "cancelled" for task in completed["tasks"])
        )

    def test_historical_market_source_receives_cutoff_and_is_recovered(
        self,
    ) -> None:
        historical = datetime.now(timezone.utc).date() - timedelta(days=30)
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=historical,
            end_date=historical,
            dimensions=["0_basic"],
            submit=False,
        )

        self.coordinator.run_job_now(job["id"])

        self.assertEqual(len(self.collector.calls), 1)
        self.assertEqual(self.collector.calls[0]["as_of_date"], historical)
        self.assertEqual(self.collector.calls[0]["cutoff_at"].date(), historical)
        with self.database.session() as session:
            task = session.scalar(select(MarketDataCollectionTask))
            result = session.scalar(select(MarketDataCollectionTaskResult))
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.provider_status, "available")
            self.assertFalse(result.retryable)
            self.assertIsNotNone(result.snapshot_id)
        coverage = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(coverage["status"], "succeeded")

    def test_historical_dated_source_is_reconstructed_as_grade_b(self) -> None:
        historical = datetime.now(timezone.utc).date() - timedelta(days=30)
        collector = _HistoricalResearchCollector()
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=collector,
        )
        self.addCleanup(coordinator.close)
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=historical,
            end_date=historical,
            dimensions=["6_research"],
            publish_when_complete=False,
            submit=False,
        )

        coordinator.run_job_now(job["id"])

        completed = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["tasks"][0]["status"], "succeeded")
        self.assertEqual(len(collector.calls), 1)
        with self.database.session() as session:
            snapshot = session.scalar(
                select(MarketDataSnapshot).where(
                    MarketDataSnapshot.dimension == "6_research"
                )
            )
            result = session.scalar(
                select(MarketDataCollectionTaskResult).where(
                    MarketDataCollectionTaskResult.snapshot_id == snapshot.id
                )
            )
            self.assertEqual(snapshot.status, "available")
            self.assertEqual(snapshot.evidence_grade, "B")
            self.assertEqual(
                snapshot.query_manifest_json["pit_mode"],
                "historical_reconstruction",
            )
            self.assertEqual(result.status, "succeeded")

    def test_historical_partial_is_usable_but_not_reported_as_complete(self) -> None:
        historical = datetime.now(timezone.utc).date() - timedelta(days=30)
        collector = _HistoricalOutcomeCollector("partial")
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=collector,
        )
        self.addCleanup(coordinator.close)
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=historical,
            end_date=historical,
            dimensions=["10_valuation"],
            mode="force_all",
            publish_when_complete=False,
            submit=False,
        )

        coordinator.run_job_now(job["id"])

        completed = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        task = completed["tasks"][0]
        self.assertEqual(completed["status"], "partial")
        self.assertEqual(task["status"], "partial")
        self.assertEqual(task["collection_outcome"], "partial")
        self.assertEqual(task["error_code"], "historical_partial")
        self.assertEqual(task["failure_kind"], "partial_available")
        self.assertFalse(task["retryable"])
        with self.database.session() as session:
            result = session.scalar(
                select(MarketDataCollectionTaskResult).where(
                    MarketDataCollectionTaskResult.task_id == task["id"]
                )
            )
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.metrics_json["collection_outcome"], "partial")
            self.assertFalse(result.metrics_json["excluded_from_scoring"])

    def test_historical_provider_failure_stays_retryable(self) -> None:
        historical = datetime.now(timezone.utc).date() - timedelta(days=30)
        collector = _HistoricalOutcomeCollector("retryable_failure")
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=collector,
        )
        self.addCleanup(coordinator.close)
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=historical,
            end_date=historical,
            dimensions=["10_valuation"],
            mode="force_all",
            publish_when_complete=False,
            submit=False,
        )

        coordinator.run_job_now(job["id"])

        completed = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        task = completed["tasks"][0]
        self.assertEqual(completed["status"], "failed")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["collection_outcome"], "retryable_failure")
        self.assertEqual(task["error_code"], "historical_provider_failure")
        self.assertEqual(task["failure_kind"], "source_unavailable")
        self.assertTrue(task["retryable"])
        self.assertEqual(task["reason_code"], "historical_provider_failure")
        self.assertIn("test.valuation: provider_unavailable", task["result_message"])
        self.assertIn("ConnectionError", task["error_message"])

    def test_missing_or_stale_skips_fresh_slot_before_provider_wave(self) -> None:
        today = datetime.now(timezone.utc).date()
        initial = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            mode="missing_or_stale",
            submit=False,
        )
        self.coordinator.run_job_now(initial["id"])
        self.assertEqual(len(self.collector.calls), 1)
        self.collector.calls.clear()

        skipped = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            mode="missing_or_stale",
            submit=False,
        )
        self.coordinator.run_job_now(skipped["id"])

        self.assertEqual(self.collector.calls, [])
        completed = self.coordinator.get_job(
            skipped["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(completed["status"], "succeeded")
        with self.database.session() as session:
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == skipped["id"]
                )
            )
            result = session.scalar(
                select(MarketDataCollectionTaskResult).where(
                    MarketDataCollectionTaskResult.task_id == task.id
                )
            )
            self.assertEqual(result.status, "skipped")
            self.assertEqual(result.provider_status, "existing_report_ready")
            self.assertIsNotNone(result.snapshot_id)

        forced = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            mode="force_all",
            submit=False,
        )
        self.coordinator.run_job_now(forced["id"])
        self.assertEqual(len(self.collector.calls), 1)

    def test_restart_resumes_manual_jobs_but_cancels_subscription_jobs_when_global_off(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()
        with self.database.session() as session:
            subscription = MarketDataSubscription(
                tenant_id="tenant",
                user_id="user",
                symbol="600519.SH",
                market="CN_EQUITY",
                exchange_timezone="Asia/Shanghai",
                enabled=True,
                dimensions_json=["0_basic"],
                schedule_json={"local_time": "23:15", "timezone": "Asia/Shanghai"},
                max_concurrency=5,
            )
            session.add(subscription)
            session.flush()
            subscription_id = subscription.id
        subscription_job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="market_data_subscription",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            origin="subscription",
            subscription_id=subscription_id,
            submit=False,
        )
        manual_job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        submitted: list[str] = []
        self.coordinator.submit = submitted.append  # type: ignore[method-assign]

        resumed = self.coordinator.resume_pending(resume_subscription_jobs=False)

        self.assertEqual(resumed, [manual_job["id"]])
        self.assertEqual(submitted, [manual_job["id"]])
        with self.database.session() as session:
            cancelled = session.get(MarketDataCollectionJob, subscription_job["id"])
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == subscription_job["id"]
                )
            )
            self.assertEqual(cancelled.status, "cancelled")
            self.assertTrue(cancelled.cancel_requested)
            self.assertEqual(task.status, "cancelled")

    def test_restart_does_not_resume_a_disabled_per_symbol_subscription(self) -> None:
        today = datetime.now(timezone.utc).date()
        with self.database.session() as session:
            subscription = MarketDataSubscription(
                tenant_id="tenant",
                user_id="user",
                symbol="600519.SH",
                market="CN_EQUITY",
                exchange_timezone="Asia/Shanghai",
                enabled=False,
                dimensions_json=["0_basic"],
                schedule_json={"local_time": "23:15", "timezone": "Asia/Shanghai"},
                max_concurrency=5,
            )
            session.add(subscription)
            session.flush()
            subscription_id = subscription.id
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="market_data_subscription",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            origin="subscription",
            subscription_id=subscription_id,
            submit=False,
        )
        submitted: list[str] = []
        self.coordinator.submit = submitted.append  # type: ignore[method-assign]

        resumed = self.coordinator.resume_pending(resume_subscription_jobs=True)

        self.assertEqual(resumed, [])
        self.assertEqual(submitted, [])
        with self.database.session() as session:
            cancelled = session.get(MarketDataCollectionJob, job["id"])
            self.assertEqual(cancelled.status, "cancelled")
            self.assertTrue(cancelled.cancel_requested)

    def test_restart_leaves_parented_equity_jobs_to_the_parent_coordinator(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()
        parent = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        child = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            parent_job_id=parent["id"],
            submit=False,
        )
        with self.database.session() as session:
            parent_row = session.get(MarketDataCollectionJob, parent["id"])
            child_row = session.get(MarketDataCollectionJob, child["id"])
            parent_task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == parent["id"]
                )
            )
            child_task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == child["id"]
                )
            )
            parent_row.status = "succeeded"
            parent_task.status = "succeeded"
            child_row.status = "running"
            child_row.lease_owner = "dead-worker"
            child_task.status = "running"
            child_task.lease_owner = "dead-worker"

        submitted: list[str] = []
        self.coordinator.submit = submitted.append  # type: ignore[method-assign]

        self.assertEqual(self.coordinator.resume_pending(), [])
        self.assertEqual(submitted, [])
        with self.database.session() as session:
            child_row = session.get(MarketDataCollectionJob, child["id"])
            child_task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == child["id"]
                )
            )
            self.assertEqual(child_row.status, "running")
            self.assertEqual(child_row.lease_owner, "dead-worker")
            self.assertEqual(child_task.status, "running")
            self.assertEqual(child_task.lease_owner, "dead-worker")

    def test_restart_reconciles_active_tasks_owned_by_a_terminal_parented_job(
        self,
    ) -> None:
        today = datetime.now(timezone.utc).date()
        parent = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        child = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic", "1_financials"],
            parent_job_id=parent["id"],
            submit=False,
        )
        with self.database.session() as session:
            parent_row = session.get(MarketDataCollectionJob, parent["id"])
            parent_task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == parent["id"]
                )
            )
            parent_row.status = "succeeded"
            parent_row.completed_at = datetime.now(timezone.utc)
            parent_task.status = "succeeded"
            parent_task.completed_at = datetime.now(timezone.utc)

            child_row = session.get(MarketDataCollectionJob, child["id"])
            child_tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask)
                    .where(MarketDataCollectionTask.job_id == child["id"])
                    .order_by(MarketDataCollectionTask.dimension)
                )
            )
            child_row.status = "failed"
            child_row.error_summary = "legacy worker failure"
            child_row.progress_json = {"completed": 1, "total": 2}
            child_row.completed_at = datetime.now(timezone.utc)
            child_tasks[0].status = "failed"
            child_tasks[0].completed_at = datetime.now(timezone.utc)
            child_tasks[1].status = "running"
            child_tasks[1].attempt_count = 1
            child_tasks[1].lease_owner = "dead-worker"
            child_tasks[1].lease_until = datetime.now(timezone.utc) + timedelta(
                minutes=5
            )

        submitted: list[str] = []
        self.coordinator.submit = submitted.append  # type: ignore[method-assign]

        self.assertEqual(self.coordinator.resume_pending(), [])
        self.assertEqual(submitted, [])

        repaired = self.coordinator.get_job(
            child["id"], tenant_id="tenant", user_id="user"
        )
        tasks = {task["dimension_id"]: task for task in repaired["tasks"]}
        self.assertEqual(repaired["status"], "failed")
        self.assertEqual(repaired["error_summary"], "legacy worker failure")
        self.assertEqual(repaired["progress"]["completed"], 2)
        self.assertEqual(repaired["progress"]["total"], 2)
        self.assertEqual(repaired["progress"]["by_status"], {"failed": 2})
        self.assertEqual(tasks["1_financials"]["status"], "failed")
        self.assertEqual(
            tasks["1_financials"]["error_code"],
            "terminal_job_task_reconciled",
        )
        self.assertTrue(tasks["1_financials"]["retryable"])
        with self.database.session() as session:
            task = session.get(MarketDataCollectionTask, tasks["1_financials"]["id"])
            self.assertIsNone(task.lease_owner)
            self.assertIsNone(task.lease_until)
            self.assertIsNotNone(task.completed_at)

    def test_idempotency_replay_never_resubmits_the_existing_job(self) -> None:
        today = datetime.now(timezone.utc).date()
        original = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            idempotency_key="durable-replay",
            submit=False,
        )
        with patch.object(self.coordinator, "submit") as submit:
            replayed = self.coordinator.create_job(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                symbols=["600519.SH"],
                start_date=today,
                end_date=today,
                dimensions=["0_basic"],
                idempotency_key="durable-replay",
                submit=True,
            )
        self.assertEqual(replayed["id"], original["id"])
        submit.assert_not_called()

        self.coordinator.run_job_now(original["id"])
        with patch.object(self.coordinator, "submit") as submit:
            terminal_replay = self.coordinator.create_job(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                symbols=["600519.SH"],
                start_date=today,
                end_date=today,
                dimensions=["0_basic"],
                idempotency_key="durable-replay",
                submit=True,
            )
        self.assertEqual(terminal_replay["status"], "succeeded")
        submit.assert_not_called()

    def test_idempotency_replay_normalizes_symbol_and_dimension_order(self) -> None:
        today = datetime.now(timezone.utc).date()
        original = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.sh", "000858.SZ"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic", "1_financials"],
            idempotency_key="normalized-replay",
            submit=False,
        )

        replayed = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="other-actor",
            symbols=["000858.sz", "600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["1_financials", "0_basic"],
            idempotency_key="normalized-replay",
            submit=False,
        )

        self.assertEqual(replayed["id"], original["id"])

    def test_idempotency_key_rejects_every_material_request_change(self) -> None:
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        base = {
            "tenant_id": "tenant",
            "user_id": "user",
            "actor_id": "actor",
            "symbols": ["600519.SH"],
            "start_date": today,
            "end_date": today,
            "dimensions": ["0_basic"],
            "mode": "missing_or_stale",
            "max_concurrency": 5,
            "publish_when_complete": False,
            "idempotency_key": "conflicting-replay",
            "submit": False,
        }
        self.coordinator.create_job(**base)
        changes = {
            "symbols": {"symbols": ["000858.SZ"]},
            "date": {"start_date": yesterday, "end_date": yesterday},
            "dimensions": {"dimensions": ["1_financials"]},
            "mode": {"mode": "force_all"},
            "concurrency": {"max_concurrency": 2},
            "publish": {"publish_when_complete": True},
        }

        for label, change in changes.items():
            with self.subTest(label=label):
                request = {**base, **change}
                with self.assertRaises(CollectionJobError) as captured:
                    self.coordinator.create_job(**request)
                self.assertEqual(
                    captured.exception.code, "collection_idempotency_conflict"
                )
                self.assertEqual(captured.exception.status_code, 409)

    def test_idempotency_key_rejects_an_existing_non_equity_job_type(self) -> None:
        today = datetime.now(timezone.utc).date()
        request = {
            "tenant_id": "tenant",
            "user_id": "user",
            "actor_id": "actor",
            "symbols": ["600519.SH"],
            "start_date": today,
            "end_date": today,
            "dimensions": ["0_basic"],
            "idempotency_key": "type-conflict",
            "submit": False,
        }
        job = self.coordinator.create_job(**request)
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            row.job_type = "fund_holding_expansion"

        with self.assertRaises(CollectionJobError) as captured:
            self.coordinator.create_job(**request)

        self.assertEqual(captured.exception.code, "collection_idempotency_conflict")
        self.assertEqual(captured.exception.status_code, 409)

    def test_concurrent_file_sqlite_create_is_idempotent_and_conflict_stable(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = StrategyDatabase(
                f"sqlite:///{Path(temporary_directory) / 'collection.sqlite3'}"
            )
            database.create_schema()
            first = MarketDataCollectionCoordinator(database, collector=self.collector)
            second = MarketDataCollectionCoordinator(database, collector=self.collector)
            try:
                today = datetime.now(timezone.utc).date()
                original_first = first._find_idempotent_job
                original_second = second._find_idempotent_job

                def synchronize_initial_miss(coordinator, original, barrier):
                    def synchronized(session, **kwargs):
                        existing = original(session, **kwargs)
                        if existing is None:
                            barrier.wait(timeout=5)
                        return existing

                    coordinator._find_idempotent_job = synchronized  # type: ignore[method-assign]

                barrier = Barrier(2)
                synchronize_initial_miss(first, original_first, barrier)
                synchronize_initial_miss(second, original_second, barrier)
                request = {
                    "tenant_id": "tenant",
                    "user_id": "user",
                    "actor_id": "actor",
                    "symbols": ["600519.SH"],
                    "start_date": today,
                    "end_date": today,
                    "dimensions": ["0_basic"],
                    "idempotency_key": "concurrent-same",
                    "submit": False,
                }
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(coordinator.create_job, **request)
                        for coordinator in (first, second)
                    ]
                    results = [future.result(timeout=10) for future in futures]
                self.assertEqual(results[0]["id"], results[1]["id"])
                with database.session() as session:
                    self.assertEqual(
                        session.scalar(
                            select(func.count()).select_from(MarketDataCollectionJob)
                        ),
                        1,
                    )

                barrier = Barrier(2)
                synchronize_initial_miss(first, original_first, barrier)
                synchronize_initial_miss(second, original_second, barrier)

                def create_or_error(coordinator, symbol):
                    try:
                        result = coordinator.create_job(
                            **{
                                **request,
                                "symbols": [symbol],
                                "idempotency_key": "concurrent-conflict",
                            }
                        )
                        return ("ok", result["id"])
                    except CollectionJobError as exc:
                        return ("error", exc.code, exc.status_code)

                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(create_or_error, first, "600519.SH"),
                        executor.submit(create_or_error, second, "000858.SZ"),
                    ]
                    outcomes = [future.result(timeout=10) for future in futures]
                self.assertEqual([item[0] for item in outcomes].count("ok"), 1)
                self.assertIn(
                    ("error", "collection_idempotency_conflict", 409), outcomes
                )
            finally:
                first.close()
                second.close()
                database.close()

    def test_worker_claims_only_queued_jobs(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            row.status = CollectionStatus.SUCCEEDED.value
            row.completed_at = datetime.now(timezone.utc)

        self.coordinator.run_job_now(job["id"])

        persisted = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(persisted["tasks"][0]["status"], "queued")
        self.assertEqual(self.collector.calls, [])

    def test_restart_resumes_only_an_expired_job_lease(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            row.status = CollectionStatus.RUNNING.value
            row.lease_owner = "live-worker"
            row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            task.status = CollectionStatus.RUNNING.value
            task.lease_owner = "live-worker"
            task.lease_until = row.lease_until
        with patch.object(self.coordinator, "submit") as submit:
            self.assertEqual(self.coordinator.resume_pending(), [])
        submit.assert_not_called()

        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        with patch.object(self.coordinator, "submit") as submit:
            self.assertEqual(self.coordinator.resume_pending(), [job["id"]])
        submit.assert_called_once_with(job["id"])
        persisted = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(persisted["status"], "queued")
        self.assertEqual(persisted["tasks"][0]["status"], "queued")

    def test_collection_heartbeat_renews_only_the_current_owner(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        original_until = datetime.now(timezone.utc) + timedelta(seconds=1)
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            row.status = CollectionStatus.RUNNING.value
            row.lease_owner = "current-worker"
            row.lease_until = original_until
            task.status = CollectionStatus.RUNNING.value
            task.lease_owner = "current-worker"
            task.lease_until = original_until

        self.assertTrue(
            self.coordinator._renew_collection_lease(job["id"], "current-worker")
        )
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            self.assertGreater(
                row.lease_until.replace(tzinfo=timezone.utc), original_until
            )
            self.assertEqual(task.lease_until, row.lease_until)
            row.lease_owner = "replacement-worker"
            task.lease_owner = "replacement-worker"

        self.assertFalse(
            self.coordinator._renew_collection_lease(job["id"], "current-worker")
        )

    def test_heartbeat_thread_renews_and_signals_owner_loss(self) -> None:
        renewed = Event()

        def renew_current_owner(job_id, worker_id):
            self.assertEqual((job_id, worker_id), ("job", "worker"))
            renewed.set()
            return True

        with (
            patch(
                "research_system.market_data.collection."
                "COLLECTION_LEASE_HEARTBEAT_SECONDS",
                0.001,
            ),
            patch.object(
                self.coordinator,
                "_renew_collection_lease",
                side_effect=renew_current_owner,
            ),
            self.coordinator._lease_heartbeat(
                "job",
                "worker",
                recovery=False,
                lease_deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
            ) as signals,
        ):
            self.assertTrue(renewed.wait(timeout=1))
            self.assertFalse(signals.lost.is_set())

        with (
            patch(
                "research_system.market_data.collection."
                "COLLECTION_LEASE_HEARTBEAT_SECONDS",
                0.001,
            ),
            patch.object(
                self.coordinator,
                "_renew_agent_recovery_lease",
                return_value=False,
            ) as renew_agent,
            self.coordinator._lease_heartbeat(
                "job",
                "agent",
                recovery=True,
                lease_deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
            ) as signals,
        ):
            self.assertTrue(signals.lost.wait(timeout=1))
        renew_agent.assert_called_with("job", "agent")

        with (
            patch(
                "research_system.market_data.collection."
                "COLLECTION_LEASE_HEARTBEAT_SECONDS",
                0.001,
            ),
            patch.object(
                self.coordinator,
                "_renew_collection_lease",
                side_effect=RuntimeError("database unavailable"),
            ),
            self.coordinator._lease_heartbeat(
                "job",
                "worker",
                recovery=False,
                lease_deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
            ) as signals,
        ):
            self.assertTrue(signals.lost.wait(timeout=1))

    def test_stale_collection_owner_cannot_finish_or_fail_reclaimed_job(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            row.status = CollectionStatus.RUNNING.value
            row.lease_owner = "replacement-worker"
            row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=30)
            task.status = CollectionStatus.RUNNING.value
            task.lease_owner = "replacement-worker"
            task.lease_until = row.lease_until

        self.coordinator._finish_job(job["id"], worker_id="stale-worker")
        self.coordinator._fail_job(
            job["id"], RuntimeError("old worker"), worker_id="stale-worker"
        )

        persisted = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(persisted["status"], "running")
        self.assertEqual(persisted["tasks"][0]["status"], "running")
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            task = session.get(MarketDataCollectionTask, persisted["tasks"][0]["id"])
            self.assertEqual(row.lease_owner, "replacement-worker")
            self.assertEqual(task.lease_owner, "replacement-worker")

    def test_heartbeat_errors_past_deadline_prevent_worker_final_write(self) -> None:
        started = Event()
        release = Event()
        lease_lost = Event()
        delegate = self.collector

        def blocking_collector(**kwargs):
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return delegate(**kwargs)

        coordinator = MarketDataCollectionCoordinator(
            self.database, collector=blocking_collector
        )
        heartbeat = coordinator._heartbeat_lease

        def expire_while_provider_is_running(job_id, worker_id, stop, signals, *, recovery, lease_deadline):
            # Exercise loss during a provider call, not whether DB setup happens
            # to finish within 30 ms on this machine/CI worker.
            self.assertTrue(started.wait(timeout=5))
            deadline = datetime.now(timezone.utc) + timedelta(milliseconds=30)
            with self.database.session() as session:
                row = session.get(MarketDataCollectionJob, job_id)
                row.lease_until = deadline
                for task in session.scalars(select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job_id)):
                    task.lease_until = deadline
            heartbeat(job_id, worker_id, stop, signals, recovery=recovery, lease_deadline=deadline)
            if signals.lost.is_set():
                lease_lost.set()
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )

        with (
            patch.object(coordinator, "_heartbeat_lease", side_effect=expire_while_provider_is_running),
            patch(
                "research_system.market_data.collection."
                "COLLECTION_LEASE_HEARTBEAT_SECONDS",
                0.005,
            ),
            patch.object(
                coordinator,
                "_renew_collection_lease",
                side_effect=RuntimeError("database unavailable"),
            ),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            future = executor.submit(coordinator.run_job_now, job["id"])
            self.assertTrue(started.wait(timeout=5))
            self.assertTrue(lease_lost.wait(timeout=5))
            release.set()
            future.result(timeout=5)

        persisted = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertEqual(persisted["status"], "running")
        self.assertEqual(persisted["tasks"][0]["status"], "running")
        with self.database.session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MarketDataSnapshot)),
                0,
            )
        with patch.object(coordinator, "submit") as submit:
            self.assertEqual(coordinator.resume_pending(), [job["id"]])
        submit.assert_called_once_with(job["id"])

    def test_expired_same_owner_cannot_renew_or_write_terminal_state(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            row.status = CollectionStatus.RUNNING.value
            row.lease_owner = "expired-worker"
            row.lease_until = expired
            task.status = CollectionStatus.RUNNING.value
            task.lease_owner = "expired-worker"
            task.lease_until = expired

        self.assertFalse(
            self.coordinator._renew_collection_lease(job["id"], "expired-worker")
        )
        self.coordinator._finish_job(job["id"], worker_id="expired-worker")
        self.coordinator._fail_job(
            job["id"], RuntimeError("expired"), worker_id="expired-worker"
        )
        persisted = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(persisted["status"], "running")

        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            checkpoint["agent_recovery"] = {
                "status": "running",
                "lease_owner": "expired-agent",
                "lease_until": expired.isoformat(),
            }
            row.checkpoint_json = checkpoint
        self.assertFalse(
            self.coordinator._renew_agent_recovery_lease(job["id"], "expired-agent")
        )
        self.coordinator._fail_agent_recovery(
            job["id"], "expired", worker_id="expired-agent"
        )
        persisted = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )["agent_recovery"]
        self.assertEqual(persisted["status"], "running")
        self.assertEqual(persisted["lease_owner"], "expired-agent")

    def test_cross_coordinator_cancel_during_provider_prevents_ingest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = StrategyDatabase(
                f"sqlite:///{Path(temporary_directory) / 'cancel.sqlite3'}"
            )
            database.create_schema()
            started = Event()
            release = Event()
            delegate = _Collector()

            def blocking_collector(**kwargs):
                started.set()
                self.assertTrue(release.wait(timeout=5))
                return delegate(**kwargs)

            worker = MarketDataCollectionCoordinator(
                database, collector=blocking_collector
            )
            controller = MarketDataCollectionCoordinator(database, collector=delegate)
            try:
                today = datetime.now(timezone.utc).date()
                job = worker.create_job(
                    tenant_id="tenant",
                    user_id="user",
                    actor_id="actor",
                    symbols=["600519.SH"],
                    start_date=today,
                    end_date=today,
                    dimensions=["0_basic"],
                    submit=False,
                )
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(worker.run_job_now, job["id"])
                    self.assertTrue(started.wait(timeout=5))
                    cancelled = controller.cancel(
                        job["id"],
                        tenant_id="tenant",
                        user_id="user",
                        actor_id="controller",
                    )
                    self.assertEqual(cancelled["status"], "cancelled")
                    release.set()
                    future.result(timeout=5)

                persisted = controller.get_job(
                    job["id"], tenant_id="tenant", user_id="user"
                )
                self.assertEqual(persisted["status"], "cancelled")
                self.assertEqual(persisted["tasks"][0]["status"], "cancelled")
                with database.session() as session:
                    self.assertEqual(
                        session.scalar(
                            select(func.count()).select_from(MarketDataSnapshot)
                        ),
                        0,
                    )
            finally:
                release.set()
                worker.close()
                controller.close()
                database.close()

    def test_restart_terminalizes_a_hanging_cancel_request(self) -> None:
        today = datetime.now(timezone.utc).date()
        job = self.coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            row.status = CollectionStatus.RUNNING.value
            row.cancel_requested = True
            row.lease_owner = "crashed-worker"
            row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            task = session.scalar(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job["id"]
                )
            )
            task.status = CollectionStatus.RUNNING.value
            task.lease_owner = "crashed-worker"
            task.lease_until = row.lease_until

        self.assertEqual(self.coordinator.resume_pending(), [])

        persisted = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(persisted["status"], "cancelled")
        self.assertEqual(persisted["tasks"][0]["status"], "cancelled")
        self.assertIsNotNone(persisted["completed_at"])

    def test_agent_completion_observes_cross_process_cancelling_checkpoint(
        self,
    ) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            checkpoint["agent_recovery"] = {"status": "queued"}
            row.checkpoint_json = checkpoint

        def recover_then_cancel(*args, **kwargs):
            del args, kwargs
            with self.database.session() as session:
                row = session.get(MarketDataCollectionJob, job["id"])
                checkpoint = dict(row.checkpoint_json or {})
                recovery = dict(checkpoint["agent_recovery"])
                recovery["status"] = "cancelling"
                checkpoint["agent_recovery"] = recovery
                row.checkpoint_json = checkpoint
            return {
                "attempted": 1,
                "succeeded": 1,
                "failed": 0,
                "provider_recovery": {},
            }

        with patch(
            "research_system.market_data.collection.recover_collection_job_draft",
            side_effect=recover_then_cancel,
        ):
            coordinator.run_agent_recovery_now(job["id"])

        persisted = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertEqual(persisted["agent_recovery"]["status"], "cancelled")
        self.assertEqual(
            persisted["checkpoint"]["publication"]["error_code"],
            "agent_recovery_cancelled",
        )

    def test_selected_agent_resume_respects_lease_and_finalizes_cancel(self) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            checkpoint["agent_recovery"] = {
                "status": "running",
                "lease_until": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            }
            row.checkpoint_json = checkpoint
        with patch.object(coordinator, "_submit_agent_recovery") as submit:
            self.assertEqual(coordinator.resume_agent_recovery_jobs([job["id"]]), 0)
        submit.assert_not_called()

        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            recovery = dict(checkpoint["agent_recovery"])
            recovery["lease_until"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            checkpoint["agent_recovery"] = recovery
            row.checkpoint_json = checkpoint
        with patch.object(coordinator, "_submit_agent_recovery") as submit:
            self.assertEqual(coordinator.resume_agent_recovery_jobs([job["id"]]), 1)
        submit.assert_called_once_with(job["id"])

        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            recovery = dict(checkpoint["agent_recovery"])
            recovery["status"] = "cancelling"
            checkpoint["agent_recovery"] = recovery
            row.checkpoint_json = checkpoint
        self.assertEqual(coordinator.resume_agent_recovery_jobs([job["id"]]), 0)
        persisted = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertEqual(persisted["agent_recovery"]["status"], "cancelled")

    def test_agent_heartbeat_and_completion_are_fenced_by_lease_owner(self) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        original_until = datetime.now(timezone.utc) + timedelta(seconds=1)
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            checkpoint["agent_recovery"] = {
                "status": "running",
                "lease_owner": "current-agent",
                "lease_until": original_until.isoformat(),
            }
            row.checkpoint_json = checkpoint

        self.assertTrue(
            coordinator._renew_agent_recovery_lease(job["id"], "current-agent")
        )
        renewed = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")[
            "agent_recovery"
        ]
        self.assertGreater(
            datetime.fromisoformat(renewed["lease_until"]), original_until
        )

        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job["id"])
            checkpoint = dict(row.checkpoint_json or {})
            checkpoint["agent_recovery"] = {
                "status": "queued",
                "requested_by": "actor",
            }
            row.checkpoint_json = checkpoint

        def transfer_agent_lease(*args, **kwargs):
            del args, kwargs
            with self.database.session() as session:
                row = session.get(MarketDataCollectionJob, job["id"])
                checkpoint = dict(row.checkpoint_json or {})
                recovery = dict(checkpoint["agent_recovery"])
                recovery.update(
                    {
                        "status": "running",
                        "lease_owner": "replacement-agent",
                        "lease_until": (
                            datetime.now(timezone.utc) + timedelta(minutes=30)
                        ).isoformat(),
                    }
                )
                checkpoint["agent_recovery"] = recovery
                row.checkpoint_json = checkpoint
            return {
                "attempted": 1,
                "succeeded": 1,
                "failed": 0,
                "provider_recovery": {},
            }

        with patch(
            "research_system.market_data.collection.recover_collection_job_draft",
            side_effect=transfer_agent_lease,
        ):
            coordinator.run_agent_recovery_now(job["id"])

        persisted = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")[
            "agent_recovery"
        ]
        self.assertEqual(persisted["status"], "running")
        self.assertEqual(persisted["lease_owner"], "replacement-agent")
        self.assertFalse(
            coordinator._renew_agent_recovery_lease(job["id"], "current-agent")
        )

    def test_delayed_cancelled_future_cannot_cancel_new_agent_owner(self) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        old = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
            max_background_jobs=1,
        )
        replacement = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(replacement.close)
        blocker_started = Event()
        release_blocker = Event()
        release_replacement = Event()
        try:
            today = datetime.now(timezone.utc).date()
            job = old.create_job(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                symbols=["600519.SH"],
                start_date=today,
                end_date=today,
                dimensions="all",
                submit=False,
            )
            old.run_job_now(job["id"])

            def occupy_old_executor():
                blocker_started.set()
                self.assertTrue(release_blocker.wait(timeout=5))

            blocker = old._executor.submit(occupy_old_executor)
            self.assertTrue(blocker_started.wait(timeout=5))
            old.start_agent_recovery(
                job["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="old",
            )
            delayed = old._recovery_futures[job["id"]]
            self.assertFalse(delayed.running())
            old.close()

            replacement_started = Event()

            def blocking_recovery(*args, **kwargs):
                del args, kwargs
                replacement_started.set()
                self.assertTrue(release_replacement.wait(timeout=5))
                return {
                    "attempted": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "provider_recovery": {},
                }

            with (
                patch(
                    "research_system.market_data.collection."
                    "recover_collection_job_draft",
                    side_effect=blocking_recovery,
                ),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                current = executor.submit(replacement.run_agent_recovery_now, job["id"])
                self.assertTrue(replacement_started.wait(timeout=5))
                running = replacement.get_job(
                    job["id"], tenant_id="tenant", user_id="user"
                )["agent_recovery"]
                self.assertEqual(running["status"], "running")
                replacement_owner = running["lease_owner"]

                release_blocker.set()
                blocker.result(timeout=5)
                delayed.result(timeout=5)
                after_old_future = replacement.get_job(
                    job["id"], tenant_id="tenant", user_id="user"
                )["agent_recovery"]
                self.assertEqual(after_old_future["status"], "running")
                self.assertEqual(after_old_future["lease_owner"], replacement_owner)

                release_replacement.set()
                current.result(timeout=5)
            completed = replacement.get_job(
                job["id"], tenant_id="tenant", user_id="user"
            )["agent_recovery"]
            self.assertEqual(completed["status"], "succeeded")
        finally:
            release_blocker.set()
            release_replacement.set()
            old.close()

    def test_candidate_refresh_lock_preserves_concurrent_agent_cancellation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = StrategyDatabase(
                f"sqlite:///{Path(temporary_directory) / 'candidate.sqlite3'}"
            )
            database.create_schema()
            registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
            refresher = MarketDataCollectionCoordinator(
                database, collector=_Collector(), registry=registry
            )
            controller = MarketDataCollectionCoordinator(
                database, collector=_Collector(), registry=registry
            )
            release = Event()
            try:
                today = datetime.now(timezone.utc).date()
                job = refresher.create_job(
                    tenant_id="tenant",
                    user_id="user",
                    actor_id="actor",
                    symbols=["600519.SH"],
                    start_date=today,
                    end_date=today,
                    dimensions="all",
                    submit=False,
                )
                refresher.run_job_now(job["id"])
                with database.session() as session:
                    row = session.get(MarketDataCollectionJob, job["id"])
                    checkpoint = dict(row.checkpoint_json or {})
                    checkpoint["agent_recovery"] = {
                        "status": "running",
                        "lease_owner": "agent-owner",
                        "lease_until": (
                            datetime.now(timezone.utc) + timedelta(minutes=30)
                        ).isoformat(),
                        "custom_progress": {"stage": "web"},
                    }
                    row.checkpoint_json = checkpoint

                entered = Event()
                original_candidate = refresher._publication_candidate

                def blocking_candidate(*args, **kwargs):
                    entered.set()
                    self.assertTrue(release.wait(timeout=5))
                    return original_candidate(*args, **kwargs)

                with (
                    patch.object(
                        refresher,
                        "_publication_candidate",
                        side_effect=blocking_candidate,
                    ),
                    ThreadPoolExecutor(max_workers=2) as executor,
                ):
                    refresh = executor.submit(
                        refresher._refresh_publication_candidate, job["id"]
                    )
                    self.assertTrue(entered.wait(timeout=5))
                    cancel = executor.submit(
                        controller.cancel,
                        job["id"],
                        tenant_id="tenant",
                        user_id="user",
                        actor_id="controller",
                    )
                    sleep(0.05)
                    self.assertFalse(cancel.done())
                    release.set()
                    self.assertIsNotNone(refresh.result(timeout=5))
                    cancel.result(timeout=5)

                persisted = controller.get_job(
                    job["id"], tenant_id="tenant", user_id="user"
                )
                recovery = persisted["agent_recovery"]
                self.assertEqual(recovery["status"], "cancelling")
                self.assertEqual(recovery["lease_owner"], "agent-owner")
                self.assertEqual(recovery["custom_progress"], {"stage": "web"})
                self.assertIsNotNone(
                    persisted["checkpoint"].get("publication_candidate")
                )
            finally:
                release.set()
                refresher.close()
                controller.close()
                database.close()

    def test_stale_freeze_hash_refreshes_candidate_before_conflict(self) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        old_hash = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")[
            "publication_candidate"
        ]["proposal_hash"]
        fresh_candidate = {
            "version": "market-data-freeze-proposal-v1",
            "job_id": job["id"],
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "bundles": [],
            "proposal_hash": "fresh-proposal",
            "bundle_count": 0,
            "missing_dimensions": [],
            "degraded": False,
            "quality": "strict",
        }
        with (
            patch.object(
                coordinator, "_publication_candidate", return_value=fresh_candidate
            ),
            self.assertRaises(CollectionJobError) as captured,
        ):
            coordinator.confirm_freeze(
                job["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                proposal_hash=old_hash,
            )
        self.assertEqual(captured.exception.code, "collection_freeze_proposal_changed")
        refreshed = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        self.assertEqual(
            refreshed["publication_candidate"]["proposal_hash"], "fresh-proposal"
        )

    def test_published_freeze_rejects_a_different_proposal_hash(self) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        candidate = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")[
            "publication_candidate"
        ]
        coordinator.confirm_freeze(
            job["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            proposal_hash=candidate["proposal_hash"],
            acknowledge_degraded=True,
        )

        with self.assertRaises(CollectionJobError) as captured:
            coordinator.confirm_freeze(
                job["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                proposal_hash="not-the-published-proposal",
            )
        self.assertEqual(captured.exception.code, "collection_freeze_proposal_changed")

    def test_completed_background_worker_releases_future_and_cancel_event(self) -> None:
        started = Event()
        release = Event()
        delegate = self.collector

        def blocking_collector(**kwargs):
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return delegate(**kwargs)

        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=blocking_collector,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            submit=False,
        )
        coordinator.submit(job["id"])
        self.assertTrue(started.wait(timeout=5))
        future = coordinator._futures[job["id"]]
        release.set()
        future.result(timeout=5)
        deadline = monotonic() + 5
        while job["id"] in coordinator._futures and monotonic() < deadline:
            sleep(0.01)
        self.assertNotIn(job["id"], coordinator._futures)
        self.assertNotIn(job["id"], coordinator._cancellations)

    def test_completed_agent_worker_releases_future_and_cancel_event(self) -> None:
        registry = DimensionRegistry([DEFAULT_DIMENSION_REGISTRY[0]])
        coordinator = MarketDataCollectionCoordinator(
            self.database,
            collector=self.collector,
            registry=registry,
        )
        self.addCleanup(coordinator.close)
        today = datetime.now(timezone.utc).date()
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions="all",
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        started = Event()
        release = Event()

        def blocking_recovery(*args, **kwargs):
            del args, kwargs
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return {
                "attempted": 0,
                "succeeded": 0,
                "failed": 0,
                "provider_recovery": {},
            }

        with patch(
            "research_system.market_data.collection.recover_collection_job_draft",
            side_effect=blocking_recovery,
        ):
            coordinator.start_agent_recovery(
                job["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
            )
            self.assertTrue(started.wait(timeout=5))
            future = coordinator._recovery_futures[job["id"]]
            release.set()
            future.result(timeout=5)
        deadline = monotonic() + 5
        while job["id"] in coordinator._recovery_futures and monotonic() < deadline:
            sleep(0.01)
        self.assertNotIn(job["id"], coordinator._recovery_futures)
        self.assertNotIn(job["id"], coordinator._cancellations)


if __name__ == "__main__":
    unittest.main()
