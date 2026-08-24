from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.models import ApiMutationRecord
from research_system.strategies.service import OwnerScope


@dataclass(frozen=True)
class MutationReservation:
    record_id: str


@dataclass(frozen=True)
class MutationReplay:
    status_code: int
    payload: Any
    headers: dict[str, str]


class ApiMutationIdempotencyService:
    def __init__(self, database: StrategyDatabase, *, lease_seconds: int = 300) -> None:
        self.database = database
        self.lease_seconds = max(30, int(lease_seconds))

    def reserve(
        self,
        scope: OwnerScope,
        *,
        idempotency_key: str,
        method: str,
        request_target: str,
        request_hash: str,
    ) -> MutationReservation | MutationReplay:
        key = str(idempotency_key or "").strip()
        if (
            not key
            or len(key) > 128
            or any(ord(character) < 33 or ord(character) > 126 for character in key)
        ):
            raise StrategyDomainError(
                "invalid_idempotency_key",
                "Idempotency-Key 必须是 1 至 128 个可见 ASCII 字符",
                status_code=400,
            )
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=self.lease_seconds)
        try:
            with self.database.session() as session:
                row = session.scalar(
                    select(ApiMutationRecord).where(
                        ApiMutationRecord.tenant_id == scope.tenant_id,
                        ApiMutationRecord.user_id == scope.user_id,
                        ApiMutationRecord.idempotency_key == key,
                    )
                )
                if row is not None:
                    self._assert_same_request(
                        row,
                        method=method,
                        request_target=request_target,
                        request_hash=request_hash,
                    )
                    if row.status == "completed" and row.response_status is not None:
                        return MutationReplay(
                            status_code=row.response_status,
                            payload=row.response_json,
                            headers=dict(row.response_headers_json or {}),
                        )
                    row_lease = row.lease_until
                    if row_lease.tzinfo is None:
                        row_lease = row_lease.replace(tzinfo=UTC)
                    if row_lease > now:
                        raise StrategyConflict(
                            "idempotency_request_in_progress",
                            "同一幂等请求仍在处理中，请稍后重试",
                        )
                    row.status = "in_progress"
                    row.lease_until = lease_until
                    row.response_status = None
                    row.response_json = None
                    row.response_headers_json = {}
                    row.completed_at = None
                    session.flush()
                    return MutationReservation(row.id)
                row = ApiMutationRecord(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    idempotency_key=key,
                    method=method,
                    request_target=request_target,
                    request_hash=request_hash,
                    status="in_progress",
                    lease_until=lease_until,
                    response_headers_json={},
                )
                session.add(row)
                session.flush()
                return MutationReservation(row.id)
        except IntegrityError:
            return self.reserve(
                scope,
                idempotency_key=key,
                method=method,
                request_target=request_target,
                request_hash=request_hash,
            )

    def complete(
        self,
        scope: OwnerScope,
        reservation: MutationReservation,
        *,
        status_code: int,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        with self.database.session() as session:
            row = session.scalar(
                select(ApiMutationRecord).where(
                    ApiMutationRecord.id == reservation.record_id,
                    ApiMutationRecord.tenant_id == scope.tenant_id,
                    ApiMutationRecord.user_id == scope.user_id,
                )
            )
            if row is None:
                raise StrategyConflict("idempotency_reservation_lost", "幂等请求租约已丢失")
            row.status = "completed"
            row.response_status = int(status_code)
            row.response_json = payload
            row.response_headers_json = dict(headers or {})
            row.completed_at = datetime.now(UTC)

    def release(self, scope: OwnerScope, reservation: MutationReservation) -> None:
        with self.database.session() as session:
            row = session.scalar(
                select(ApiMutationRecord).where(
                    ApiMutationRecord.id == reservation.record_id,
                    ApiMutationRecord.tenant_id == scope.tenant_id,
                    ApiMutationRecord.user_id == scope.user_id,
                    ApiMutationRecord.status == "in_progress",
                )
            )
            if row is not None:
                session.delete(row)

    @staticmethod
    def _assert_same_request(
        row: ApiMutationRecord,
        *,
        method: str,
        request_target: str,
        request_hash: str,
    ) -> None:
        if (
            row.method != method
            or row.request_target != request_target
            or row.request_hash != request_hash
        ):
            raise StrategyConflict(
                "idempotency_key_reused",
                "同一 Idempotency-Key 不能用于不同请求",
            )


__all__ = [
    "ApiMutationIdempotencyService",
    "MutationReplay",
    "MutationReservation",
]
