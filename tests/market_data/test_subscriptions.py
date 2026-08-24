from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from threading import Event

from sqlalchemy import select

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.fund_holding_collection import (
    FundHoldingCollectionCoordinator,
)
from research_system.market_data.fund_holdings_contracts import (
    FUND_TOP_HOLDINGS_REGISTRY,
)
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataSubscription,
)
from research_system.market_data.subscriptions import (
    MarketDataSubscriptionScheduler,
    MarketDataSubscriptionService,
    SubscriptionError,
)
from research_system.strategies.database import StrategyDatabase


class _Collection:
    def __init__(self, database: StrategyDatabase) -> None:
        self.database = database
        self.calls: list[dict] = []

    def create_job(self, **kwargs):
        self.calls.append(dict(kwargs))
        with self.database.session() as session:
            row = MarketDataCollectionJob(
                tenant_id=kwargs["tenant_id"],
                user_id=kwargs["user_id"],
                idempotency_key=kwargs["idempotency_key"],
                origin=kwargs["origin"],
                subscription_id=kwargs["subscription_id"],
                job_type="current_refresh",
                mode=kwargs["mode"],
                status="queued",
                symbols_json=list(kwargs["symbols"]),
                dimensions_json=list(kwargs["dimensions"]),
                start_date=kwargs["start_date"],
                end_date=kwargs["end_date"],
                max_concurrency=kwargs["max_concurrency"],
                publish_when_complete=kwargs["publish_when_complete"],
                request_json={},
                progress_json={},
                checkpoint_json={},
                requested_by=kwargs["actor_id"],
            )
            session.add(row)
            session.flush()
            job_id = row.id
        return {"id": job_id, "status": "queued"}


class _DisableBeforeDelegatingCollection:
    """Disable the claimed row after scheduler authorization, before DB insert."""

    def __init__(self, database: StrategyDatabase, delegate) -> None:
        self.database = database
        self.delegate = delegate
        self.calls = 0

    def create_job(self, **kwargs):
        self.calls += 1
        with self.database.session() as session:
            row = session.get(MarketDataSubscription, kwargs["subscription_id"])
            row.enabled = False
            row.next_run_at = None
            row.updated_at = datetime.now(timezone.utc)
        return self.delegate.create_job(**kwargs)


class _TickService:
    def __init__(self, enabled: bool) -> None:
        self.automatic_collection_enabled = enabled
        self.called = Event()
        self.call_count = 0

    def tick(self):
        self.call_count += 1
        self.called.set()


class MarketDataSubscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = StrategyDatabase("sqlite://")
        self.database.create_schema()
        self.collection = _Collection(self.database)

    def tearDown(self) -> None:
        self.database.close()

    def test_upsert_rejects_concurrency_outside_one_to_five(self) -> None:
        service = MarketDataSubscriptionService(self.database, self.collection)
        for concurrency in (0, 6):
            with self.subTest(max_concurrency=concurrency), self.assertRaises(
                SubscriptionError
            ) as captured:
                service.upsert(
                    tenant_id="tenant",
                    user_id="user",
                    actor_id="actor",
                    symbol="600519.SH",
                    market="CN_EQUITY",
                    exchange_timezone="Asia/Shanghai",
                    enabled=True,
                    dimensions=["0_basic"],
                    local_time="23:15",
                    max_concurrency=concurrency,
                )
            self.assertEqual(captured.exception.code, "subscription_concurrency_invalid")

    def test_due_subscription_creates_job_only_with_both_switches_enabled(self) -> None:
        service = MarketDataSubscriptionService(
            self.database,
            self.collection,
            automatic_collection_enabled=True,
        )
        service.upsert(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbol="600519.SH",
            market="CN_EQUITY",
            exchange_timezone="Asia/Shanghai",
            enabled=True,
            dimensions=["0_basic"],
            local_time="23:15",
            max_concurrency=5,
        )
        current = datetime.now(timezone.utc)
        with self.database.session() as session:
            row = session.scalar(select(MarketDataSubscription))
            row.next_run_at = current - timedelta(seconds=1)

        first = service.tick(now=current)
        second = service.tick(now=current)

        self.assertTrue(first["scheduler_enabled"])
        self.assertEqual(len(first["created_jobs"]), 1)
        self.assertEqual(second["created_jobs"], [])
        self.assertEqual(len(self.collection.calls), 1)
        call = self.collection.calls[0]
        self.assertEqual(call["actor_id"], "market_data_subscription")
        self.assertEqual(call["dimensions"], ["0_basic"])
        self.assertTrue(call["publish_when_complete"])
        self.assertTrue(call["idempotency_key"].startswith("subscription:"))
        self.assertEqual(call["origin"], "subscription")
        self.assertTrue(call["subscription_id"])
        with self.database.session() as session:
            job = session.scalar(select(MarketDataCollectionJob))
            subscription = session.scalar(select(MarketDataSubscription))
            self.assertEqual(job.origin, "subscription")
            self.assertEqual(job.subscription_id, subscription.id)

    def test_disable_between_due_scan_and_claim_never_creates_equity_or_fund_job(
        self,
    ) -> None:
        scenarios = (
            ("CN_EQUITY", "600519.SH", ["0_basic"], None),
            (
                "CN_FUND",
                "020464",
                ["fund_top_holdings"],
                FUND_TOP_HOLDINGS_REGISTRY,
            ),
        )
        for market, symbol, dimensions, registry in scenarios:
            for disable_global in (False, True):
                with self.subTest(market=market, disable_global=disable_global):
                    database = StrategyDatabase("sqlite://")
                    database.create_schema()
                    collection = _Collection(database)
                    kwargs = {
                        "automatic_collection_enabled": True,
                        "markets": (market,),
                    }
                    if registry is not None:
                        kwargs["registry"] = registry
                    service = MarketDataSubscriptionService(
                        database,
                        collection,
                        **kwargs,
                    )
                    service.upsert(
                        tenant_id="tenant",
                        user_id="user",
                        actor_id="actor",
                        symbol=symbol,
                        market=market,
                        exchange_timezone="Asia/Shanghai",
                        enabled=True,
                        dimensions=dimensions,
                        local_time="23:15",
                    )
                    current = datetime.now(timezone.utc)
                    with database.session() as session:
                        row = session.scalar(select(MarketDataSubscription))
                        row.next_run_at = current - timedelta(seconds=1)

                    original_claim = service._claim_due_subscription

                    def disable_then_claim(
                        *,
                        _disable_global=disable_global,
                        _service=service,
                        _database=database,
                        _current=current,
                        _original_claim=original_claim,
                        **claim_kwargs,
                    ):
                        if _disable_global:
                            _service.automatic_collection_enabled = False
                        else:
                            with _database.session() as session:
                                row = session.get(
                                    MarketDataSubscription,
                                    claim_kwargs["subscription_id"],
                                )
                                row.enabled = False
                                row.next_run_at = None
                                row.updated_at = _current
                        return _original_claim(**claim_kwargs)

                    service._claim_due_subscription = disable_then_claim
                    result = service.tick(now=current)

                    self.assertEqual(result["created_jobs"], [])
                    self.assertEqual(collection.calls, [])
                    with database.session() as session:
                        self.assertIsNone(
                            session.scalar(select(MarketDataCollectionJob))
                        )
                    database.close()

    def test_real_fund_scheduler_uses_required_bundle_publication_shape(self) -> None:
        coordinator = FundHoldingCollectionCoordinator(self.database)
        coordinator.submit = lambda _job_id: None  # type: ignore[method-assign]
        try:
            service = MarketDataSubscriptionService(
                self.database,
                coordinator,
                automatic_collection_enabled=True,
                registry=FUND_TOP_HOLDINGS_REGISTRY,
                markets=("CN_FUND",),
            )
            service.upsert(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                symbol="020464",
                market="CN_FUND",
                exchange_timezone="Asia/Shanghai",
                enabled=True,
                dimensions=["fund_top_holdings"],
                local_time="23:15",
            )
            current = datetime.now(timezone.utc)
            with self.database.session() as session:
                row = session.scalar(select(MarketDataSubscription))
                row.next_run_at = current - timedelta(seconds=1)

            result = service.tick(now=current)

            self.assertEqual(len(result["created_jobs"]), 1)
            with self.database.session() as session:
                job = session.get(MarketDataCollectionJob, result["created_jobs"][0])
                self.assertEqual(job.job_type, "fund_holding_expansion")
                self.assertTrue(job.publish_when_complete)
                self.assertEqual(job.request_json["top_n"], 10)
        finally:
            coordinator.close()

    def test_disable_after_authorization_is_atomic_with_equity_and_fund_job_insert(
        self,
    ) -> None:
        scenarios = (
            ("CN_EQUITY", "600519.SH", ["0_basic"], None),
            (
                "CN_FUND",
                "020464",
                ["fund_top_holdings"],
                FUND_TOP_HOLDINGS_REGISTRY,
            ),
        )
        for market, symbol, dimensions, registry in scenarios:
            with self.subTest(market=market):
                database = StrategyDatabase("sqlite://")
                database.create_schema()
                delegate = (
                    FundHoldingCollectionCoordinator(database)
                    if market == "CN_FUND"
                    else MarketDataCollectionCoordinator(database)
                )
                collection = _DisableBeforeDelegatingCollection(database, delegate)
                kwargs = {
                    "automatic_collection_enabled": True,
                    "markets": (market,),
                }
                if registry is not None:
                    kwargs["registry"] = registry
                service = MarketDataSubscriptionService(
                    database,
                    collection,
                    **kwargs,
                )
                try:
                    service.upsert(
                        tenant_id="tenant",
                        user_id="user",
                        actor_id="actor",
                        symbol=symbol,
                        market=market,
                        exchange_timezone="Asia/Shanghai",
                        enabled=True,
                        dimensions=dimensions,
                        local_time="23:15",
                    )
                    current = datetime.now(timezone.utc)
                    with database.session() as session:
                        row = session.scalar(select(MarketDataSubscription))
                        row.next_run_at = current - timedelta(seconds=1)

                    result = service.tick(now=current)

                    self.assertEqual(collection.calls, 1)
                    self.assertEqual(result["created_jobs"], [])
                    with database.session() as session:
                        self.assertIsNone(
                            session.scalar(select(MarketDataCollectionJob))
                        )
                        subscription = session.scalar(
                            select(MarketDataSubscription)
                        )
                        self.assertFalse(subscription.enabled)
                        self.assertIsNone(subscription.next_run_at)
                finally:
                    delegate.close()
                    database.close()

    def test_scheduler_never_starts_when_global_switch_is_off(self) -> None:
        service = _TickService(enabled=False)
        scheduler = MarketDataSubscriptionScheduler(service, interval_seconds=5)

        scheduler.start()

        self.assertIsNone(scheduler._thread)
        self.assertEqual(service.call_count, 0)
        scheduler.stop()

    def test_enabled_scheduler_ticks_and_has_idempotent_lifecycle(self) -> None:
        service = _TickService(enabled=True)
        scheduler = MarketDataSubscriptionScheduler(service, interval_seconds=5)

        scheduler.start()
        first_thread = scheduler._thread
        scheduler.start()

        self.assertTrue(service.called.wait(timeout=1.0))
        self.assertIs(scheduler._thread, first_thread)
        self.assertTrue(first_thread is not None and first_thread.is_alive())
        scheduler.stop()
        self.assertIsNone(scheduler._thread)


if __name__ == "__main__":
    unittest.main()
