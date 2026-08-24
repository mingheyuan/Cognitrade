from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select

from research_system.market_data.collection import (
    CollectionJobError,
    MarketDataCollectionCoordinator,
)
from research_system.market_data.contracts import SnapshotIngestInput
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSubscription,
)
from research_system.strategies.database import StrategyDatabase


class _Collector:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        retrieved_at = datetime.now(timezone.utc).isoformat()
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
                        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
                        "quality_status": "available",
                        "facts": {
                            "availability": "available",
                            "_uzi_raw_result": {
                                "data": {
                                    "code": symbol,
                                    "industry": "白酒",
                                    "price": 100.0,
                                    "market_cap_raw": 1_000_000.0,
                                }
                            },
                        },
                        "source": {"provider_id": "test.market", "chain_position": 0},
                    }
                )
        return rows


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

    def test_creation_is_persistent_but_does_not_collect_when_not_submitted(self) -> None:
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

    def test_creation_rejects_concurrency_outside_public_one_to_five_contract(self) -> None:
        today = datetime.now(timezone.utc).date()
        for concurrency in (0, 6):
            with self.subTest(max_concurrency=concurrency), self.assertRaises(
                CollectionJobError
            ) as captured:
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
        completed = self.coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["tasks"][0]["status"], "succeeded")
        self.assertEqual(len(self.collector.calls), 1)
        self.assertEqual(self.collector.calls[0]["max_workers"], 5)
        with self.database.session() as session:
            result = session.scalar(select(MarketDataCollectionTaskResult))
            self.assertIsNotNone(result.snapshot_id)
            self.assertEqual(result.status, "succeeded")

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
        self.assertEqual(completed["progress"]["by_status"], {"failed": 1, "succeeded": 1})
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

    def test_unexpected_ingestion_failure_is_isolated_and_safely_summarized(self) -> None:
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
        self.assertEqual(
            tasks["600519.SH"]["error_code"], "ingestion_unexpected_error"
        )
        self.assertEqual(
            tasks["600519.SH"]["error_message"],
            "ingestion failed with RuntimeError",
        )
        self.assertNotIn("sensitive-provider-detail", tasks["600519.SH"]["error_message"])
        self.assertTrue(tasks["600519.SH"]["retryable"])
        self.assertEqual(tasks["000858.SZ"]["status"], "succeeded")

    def test_worker_failure_terminalizes_active_tasks_and_updates_progress(self) -> None:
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

        self.coordinator._fail_job(
            job["id"], RuntimeError("sensitive-worker-detail")
        )

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

        self.coordinator._finish_job(
            job["id"], cancelled=True
        )

        completed = self.coordinator.get_job(
            job["id"], tenant_id="tenant", user_id="user"
        )
        self.assertEqual(completed["status"], "cancelled")
        self.assertEqual(completed["progress"]["completed"], 2)
        self.assertEqual(completed["progress"]["by_status"], {"cancelled": 2})
        self.assertTrue(all(task["status"] == "cancelled" for task in completed["tasks"]))

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
        self.assertTrue(all(task["status"] == "cancelled" for task in completed["tasks"]))

    def test_historical_gap_never_calls_current_only_uzi_provider(self) -> None:
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

        self.assertEqual(self.collector.calls, [])
        with self.database.session() as session:
            task = session.scalar(select(MarketDataCollectionTask))
            result = session.scalar(select(MarketDataCollectionTaskResult))
            self.assertEqual(task.status, "failed")
            self.assertEqual(result.provider_status, "pit_not_supported")
            self.assertFalse(result.retryable)

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

    def test_restart_leaves_parented_equity_jobs_to_the_parent_coordinator(self) -> None:
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

    def test_restart_reconciles_active_tasks_owned_by_a_terminal_parented_job(self) -> None:
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
            child_tasks[1].lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)

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


if __name__ == "__main__":
    unittest.main()
