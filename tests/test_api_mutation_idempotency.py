from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict
from research_system.strategies.idempotency import (
    ApiMutationIdempotencyService,
    MutationReplay,
    MutationReservation,
)
from research_system.strategies.models import ApiMutationRecord
from research_system.strategies.service import OwnerScope


class ApiMutationIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = StrategyDatabase("sqlite:///:memory:")
        self.database.create_schema()
        self.owner = OwnerScope("tenant-a", "user-a", "actor-a")
        self.service = ApiMutationIdempotencyService(self.database, lease_seconds=30)

    def tearDown(self) -> None:
        self.database.close()

    def _reserve(self, *, key: str = "mutation-1", digest: str = "a" * 64):
        return self.service.reserve(
            self.owner,
            idempotency_key=key,
            method="POST",
            request_target="/api/v2/finance/strategies",
            request_hash=digest,
        )

    def test_completed_response_replays_after_service_restart(self) -> None:
        reservation = self._reserve()
        self.assertIsInstance(reservation, MutationReservation)
        self.service.complete(
            self.owner,
            reservation,
            status_code=201,
            payload={"id": "strategy-1"},
            headers={"ETag": 'W/"1"'},
        )
        restarted = ApiMutationIdempotencyService(self.database)
        replay = restarted.reserve(
            self.owner,
            idempotency_key="mutation-1",
            method="POST",
            request_target="/api/v2/finance/strategies",
            request_hash="a" * 64,
        )
        self.assertIsInstance(replay, MutationReplay)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.payload, {"id": "strategy-1"})
        self.assertEqual(replay.headers["ETag"], 'W/"1"')

    def test_key_reuse_inflight_and_owner_scope_are_enforced(self) -> None:
        self._reserve()
        with self.assertRaises(StrategyConflict) as in_progress:
            self._reserve()
        self.assertEqual(in_progress.exception.code, "idempotency_request_in_progress")
        with self.assertRaises(StrategyConflict) as reused:
            self._reserve(digest="b" * 64)
        self.assertEqual(reused.exception.code, "idempotency_key_reused")

        other = OwnerScope("tenant-a", "user-b", "actor-b")
        isolated = self.service.reserve(
            other,
            idempotency_key="mutation-1",
            method="POST",
            request_target="/api/v2/finance/strategies",
            request_hash="a" * 64,
        )
        self.assertIsInstance(isolated, MutationReservation)

    def test_expired_lease_can_be_reclaimed_and_failed_reservation_released(self) -> None:
        reservation = self._reserve()
        with self.database.session() as session:
            row = session.scalar(
                select(ApiMutationRecord).where(ApiMutationRecord.id == reservation.record_id)
            )
            row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        reclaimed = self._reserve()
        self.assertEqual(reclaimed.record_id, reservation.record_id)
        self.service.release(self.owner, reclaimed)
        fresh = self._reserve()
        self.assertNotEqual(fresh.record_id, reservation.record_id)


if __name__ == "__main__":
    unittest.main()
