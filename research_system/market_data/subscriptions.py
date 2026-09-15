from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone
from threading import Event, Thread
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update

from research_system.market_data.collection import (
    DEFAULT_COLLECTION_CONCURRENCY,
    MAX_COLLECTION_CONCURRENCY,
    MIN_COLLECTION_CONCURRENCY,
    MarketDataCollectionCoordinator,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.models import MarketDataSubscription
from research_system.strategies.database import StrategyDatabase

LOGGER = logging.getLogger(__name__)


class SubscriptionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class MarketDataSubscriptionService:
    def __init__(
        self,
        database: StrategyDatabase,
        collection: MarketDataCollectionCoordinator,
        *,
        automatic_collection_enabled: bool = False,
        registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
        markets: Iterable[str] | None = None,
    ) -> None:
        self.database = database
        self.collection = collection
        self.automatic_collection_enabled = bool(automatic_collection_enabled)
        self.registry = registry
        self.markets = frozenset(
            str(item or "").strip().upper() for item in (markets or []) if str(item or "").strip()
        )

    def list(
        self, *, tenant_id: str, user_id: str, symbol: str | None = None
    ) -> dict[str, Any]:
        with self.database.session() as session:
            filters = [
                MarketDataSubscription.tenant_id == tenant_id,
                MarketDataSubscription.user_id == user_id,
            ]
            if symbol:
                filters.append(MarketDataSubscription.symbol == str(symbol).strip().upper())
            rows = list(
                session.scalars(
                    select(MarketDataSubscription)
                    .where(*filters)
                    .order_by(MarketDataSubscription.symbol)
                )
            )
            return {
                "items": [self._payload(row) for row in rows],
                "total_count": len(rows),
                "scheduler_enabled": self.automatic_collection_enabled,
            }

    def upsert(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        symbol: str,
        market: str,
        exchange_timezone: str,
        enabled: bool,
        dimensions: list[str] | str,
        local_time: str,
        max_concurrency: int = DEFAULT_COLLECTION_CONCURRENCY,
    ) -> dict[str, Any]:
        del actor_id
        normalized_symbol = str(symbol or "").strip().upper()
        normalized_market = str(market or "").strip().upper()
        if not normalized_symbol:
            raise SubscriptionError("subscription_symbol_missing", "归档订阅缺少标的")
        resolved_dimensions = self._dimensions(dimensions)
        run_time = self._time(local_time)
        concurrency = int(max_concurrency)
        if not MIN_COLLECTION_CONCURRENCY <= concurrency <= MAX_COLLECTION_CONCURRENCY:
            raise SubscriptionError(
                "subscription_concurrency_invalid",
                "订阅采集并发必须在 1 到 5 之间",
                status_code=422,
            )
        try:
            zone = ZoneInfo(exchange_timezone)
        except ZoneInfoNotFoundError as exc:
            raise SubscriptionError("subscription_timezone_invalid", "交易所时区无效") from exc
        now = datetime.now(timezone.utc)
        next_run = self._next_run(now, zone=zone, run_time=run_time) if enabled else None
        with self.database.session() as session:
            row = session.scalar(
                select(MarketDataSubscription).where(
                    MarketDataSubscription.tenant_id == tenant_id,
                    MarketDataSubscription.user_id == user_id,
                    MarketDataSubscription.symbol == normalized_symbol,
                    MarketDataSubscription.market == normalized_market,
                )
            )
            if row is None:
                row = MarketDataSubscription(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    symbol=normalized_symbol,
                    market=normalized_market,
                    exchange_timezone=exchange_timezone,
                )
                session.add(row)
            row.enabled = bool(enabled)
            row.dimensions_json = resolved_dimensions
            row.schedule_json = {
                "local_time": run_time.strftime("%H:%M"),
                "timezone": exchange_timezone,
            }
            row.max_concurrency = concurrency
            row.next_run_at = next_run
            row.updated_at = now
            session.flush()
            return self._payload(row)

    def tick(self, *, now: datetime | None = None, limit: int = 100) -> dict[str, Any]:
        current = self._aware(now or datetime.now(timezone.utc))
        if not self.automatic_collection_enabled:
            return {
                "scheduler_enabled": False,
                "created_jobs": [],
                "message": "自动归档总开关未启用；手动采集不受影响",
            }
        with self.database.session() as session:
            due_filters = [
                MarketDataSubscription.enabled.is_(True),
                MarketDataSubscription.next_run_at.is_not(None),
                MarketDataSubscription.next_run_at <= current,
            ]
            if self.markets:
                due_filters.append(MarketDataSubscription.market.in_(self.markets))
            rows = list(
                session.scalars(
                    select(MarketDataSubscription)
                    .where(*due_filters)
                    .order_by(MarketDataSubscription.next_run_at)
                    .limit(max(1, min(int(limit), 1000)))
                )
            )
            candidates = [
                (row.id, row.tenant_id, row.user_id)
                for row in rows
            ]
        created: list[str] = []
        for subscription_id, tenant_id, user_id in candidates:
            spec = self._claim_due_subscription(
                subscription_id=subscription_id,
                tenant_id=tenant_id,
                user_id=user_id,
                current=current,
            )
            if spec is None:
                continue
            if not self._authorize_claim_for_creation(spec, current=current):
                self._release_claim(spec, current=current)
                continue
            zone = ZoneInfo(spec["exchange_timezone"])
            local_date = current.astimezone(zone).date()
            try:
                job = self.collection.create_job(
                    tenant_id=spec["tenant_id"],
                    user_id=spec["user_id"],
                    actor_id="market_data_subscription",
                    symbols=[spec["symbol"]],
                    start_date=local_date,
                    end_date=local_date,
                    dimensions=spec["dimensions"],
                    mode="missing_or_stale",
                    max_concurrency=spec["max_concurrency"],
                    publish_when_complete=True,
                    idempotency_key=f"subscription:{spec['id']}:{local_date.isoformat()}",
                    origin="subscription",
                    subscription_id=spec["id"],
                    subscription_claim_until=spec["claim_until"],
                )
            except Exception as exc:  # noqa: BLE001 - one subscription must not stop the tick
                self._record_tick_failure(spec, exc, current=current)
                continue
            created.append(job["id"])
            run_time = self._time(str(spec["schedule"].get("local_time") or "23:15"))
            with self.database.session() as session:
                row = session.get(MarketDataSubscription, spec["id"])
                if not self._claim_still_owned(row, spec):
                    continue
                row.last_job_id = job["id"]
                row.last_status = "queued"
                row.last_error = None
                row.next_run_at = self._next_run(
                    current + timedelta(seconds=1),
                    zone=zone,
                    run_time=run_time,
                )
                row.updated_at = current
        return {
            "scheduler_enabled": self.automatic_collection_enabled,
            "created_jobs": created,
        }

    def _claim_due_subscription(
        self,
        *,
        subscription_id: str,
        tenant_id: str,
        user_id: str,
        current: datetime,
    ) -> dict[str, Any] | None:
        """Atomically claim one still-enabled due row before job creation."""

        if not self.automatic_collection_enabled:
            return None
        claim_until = current + timedelta(minutes=15)
        filters = [
            MarketDataSubscription.id == subscription_id,
            MarketDataSubscription.tenant_id == tenant_id,
            MarketDataSubscription.user_id == user_id,
            MarketDataSubscription.enabled.is_(True),
            MarketDataSubscription.next_run_at.is_not(None),
            MarketDataSubscription.next_run_at <= current,
        ]
        if self.markets:
            filters.append(MarketDataSubscription.market.in_(self.markets))
        with self.database.session() as session:
            result = session.execute(
                update(MarketDataSubscription)
                .where(*filters)
                .values(
                    next_run_at=claim_until,
                    last_status="claiming",
                    last_error=None,
                    updated_at=current,
                )
            )
            if result.rowcount != 1:
                return None
            row = session.get(MarketDataSubscription, subscription_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "user_id": row.user_id,
                "symbol": row.symbol,
                "market": row.market,
                "exchange_timezone": row.exchange_timezone,
                "dimensions": list(row.dimensions_json or []),
                "max_concurrency": max(
                    MIN_COLLECTION_CONCURRENCY,
                    min(int(row.max_concurrency), MAX_COLLECTION_CONCURRENCY),
                ),
                "schedule": dict(row.schedule_json or {}),
                "claim_until": claim_until,
            }

    def _release_claim(self, spec: dict[str, Any], *, current: datetime) -> None:
        with self.database.session() as session:
            row = session.get(MarketDataSubscription, spec["id"])
            if not self._claim_still_owned(row, spec):
                return
            row.next_run_at = current
            row.last_status = "skipped"
            row.updated_at = current

    def _authorize_claim_for_creation(
        self,
        spec: dict[str, Any],
        *,
        current: datetime,
    ) -> bool:
        """Recheck opt-in and advance the durable claim immediately before create."""

        if not self.automatic_collection_enabled:
            return False
        filters = [
            MarketDataSubscription.id == spec["id"],
            MarketDataSubscription.tenant_id == spec["tenant_id"],
            MarketDataSubscription.user_id == spec["user_id"],
            MarketDataSubscription.enabled.is_(True),
            MarketDataSubscription.next_run_at == spec["claim_until"],
            MarketDataSubscription.last_status == "claiming",
        ]
        if self.markets:
            filters.append(MarketDataSubscription.market.in_(self.markets))
        with self.database.session() as session:
            result = session.execute(
                update(MarketDataSubscription)
                .where(*filters)
                .values(last_status="authorized", updated_at=current)
            )
            return result.rowcount == 1

    def _record_tick_failure(
        self,
        spec: dict[str, Any],
        exc: Exception,
        *,
        current: datetime,
    ) -> None:
        with self.database.session() as session:
            row = session.get(MarketDataSubscription, spec["id"])
            if not self._claim_still_owned(row, spec):
                return
            row.last_status = "failed"
            row.last_error = f"{exc.__class__.__name__}: archive job creation failed"
            row.next_run_at = current + timedelta(minutes=15)
            row.updated_at = current

    def _claim_still_owned(
        self,
        row: MarketDataSubscription | None,
        spec: dict[str, Any],
    ) -> bool:
        return bool(
            row is not None
            and row.enabled
            and row.tenant_id == spec["tenant_id"]
            and row.user_id == spec["user_id"]
            and self._same_instant(row.next_run_at, spec["claim_until"])
        )

    @classmethod
    def _same_instant(cls, left: datetime | None, right: datetime | None) -> bool:
        if left is None or right is None:
            return False
        return cls._aware(left) == cls._aware(right)

    def _dimensions(self, values: list[str] | str) -> list[str]:
        if isinstance(values, str):
            if values.strip().lower() == "all":
                return list(self.registry.dimension_ids)
            values = [values]
        normalized = list(dict.fromkeys(str(value or "").strip() for value in values))
        unknown = [value for value in normalized if value not in self.registry.dimension_ids]
        if unknown:
            raise SubscriptionError(
                "subscription_dimensions_invalid",
                f"未知归档维度：{', '.join(unknown)}",
            )
        return normalized

    @staticmethod
    def _time(value: str) -> time:
        try:
            return time.fromisoformat(str(value or ""))
        except ValueError as exc:
            raise SubscriptionError("subscription_time_invalid", "归档时间必须为 HH:MM") from exc

    @staticmethod
    def _next_run(current: datetime, *, zone: ZoneInfo, run_time: time) -> datetime:
        local_now = current.astimezone(zone)
        candidate = datetime.combine(local_now.date(), run_time, tzinfo=zone)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _payload(self, row: MarketDataSubscription) -> dict[str, Any]:
        return {
            "id": row.id,
            "symbol": row.symbol,
            "market": row.market,
            "exchange_timezone": row.exchange_timezone,
            "enabled": row.enabled,
            "dimensions": list(row.dimensions_json or []),
            "schedule": dict(row.schedule_json or {}),
            "max_concurrency": max(
                MIN_COLLECTION_CONCURRENCY,
                min(int(row.max_concurrency), MAX_COLLECTION_CONCURRENCY),
            ),
            "next_run_at": self._iso(row.next_run_at),
            "last_job_id": row.last_job_id,
            "last_status": row.last_status,
            "last_error": row.last_error,
            "scheduler_enabled": self.automatic_collection_enabled,
            "updated_at": self._iso(row.updated_at),
        }

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


class MarketDataSubscriptionScheduler:
    """Small lifecycle-owned poller for explicitly enabled subscriptions."""

    def __init__(
        self,
        service: MarketDataSubscriptionService,
        *,
        interval_seconds: float = 60.0,
    ) -> None:
        self.service = service
        self.interval_seconds = max(5.0, float(interval_seconds))
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if not self.service.automatic_collection_enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            name="market-data-subscriptions",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self.interval_seconds + 1.0, 10.0))
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.tick()
            except Exception:
                LOGGER.exception("Market-data subscription tick failed")
            self._stop.wait(self.interval_seconds)


__all__ = [
    "MarketDataSubscriptionScheduler",
    "MarketDataSubscriptionService",
    "SubscriptionError",
]
