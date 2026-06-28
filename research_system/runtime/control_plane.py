from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_system.runtime.jsonl import read_json, write_json


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RunControlState:
    run_id: str
    cancellation_requested: bool
    cancellation_reason: str | None
    deadline_at: datetime
    external_wait_deadline_at: datetime | None
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "cancellation_requested": self.cancellation_requested,
            "cancellation_reason": self.cancellation_reason,
            "deadline_at": self.deadline_at.isoformat(),
            "external_wait_deadline_at": self.external_wait_deadline_at.isoformat() if self.external_wait_deadline_at else None,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "RunControlState":
        return cls(
            run_id=payload["run_id"],
            cancellation_requested=payload["cancellation_requested"],
            cancellation_reason=payload.get("cancellation_reason"),
            deadline_at=datetime.fromisoformat(payload["deadline_at"]),
            external_wait_deadline_at=(
                datetime.fromisoformat(payload["external_wait_deadline_at"])
                if payload.get("external_wait_deadline_at")
                else None
            ),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
        )


class RunControlPlane:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self, *, run_id: str, run_timeout_seconds: int) -> RunControlState:
        state = RunControlState(
            run_id=run_id,
            cancellation_requested=False,
            cancellation_reason=None,
            deadline_at=utc_now() + timedelta(seconds=run_timeout_seconds),
            external_wait_deadline_at=None,
            updated_at=utc_now(),
        )
        self.save(state)
        return state

    def load(self) -> RunControlState | None:
        payload = read_json(self.path)
        if payload is None:
            return None
        return RunControlState.from_dict(payload)

    def save(self, state: RunControlState) -> None:
        write_json(self.path, state.to_dict())

    def request_cancellation(self, *, reason: str | None = None) -> RunControlState | None:
        state = self.load()
        if state is None:
            return None
        state.cancellation_requested = True
        state.cancellation_reason = reason or "manual_cancel"
        state.updated_at = utc_now()
        self.save(state)
        return state

    def set_wait_deadline(self, *, wait_timeout_seconds: int) -> RunControlState | None:
        state = self.load()
        if state is None:
            return None
        state.external_wait_deadline_at = utc_now() + timedelta(seconds=wait_timeout_seconds)
        state.updated_at = utc_now()
        self.save(state)
        return state

    def clear_wait_deadline(self) -> RunControlState | None:
        state = self.load()
        if state is None:
            return None
        state.external_wait_deadline_at = None
        state.updated_at = utc_now()
        self.save(state)
        return state

    def run_timed_out(self) -> bool:
        state = self.load()
        if state is None:
            return False
        return utc_now() >= state.deadline_at

    def external_wait_timed_out(self) -> bool:
        state = self.load()
        if state is None or state.external_wait_deadline_at is None:
            return False
        return utc_now() >= state.external_wait_deadline_at

    def cancellation_requested(self) -> bool:
        state = self.load()
        if state is None:
            return False
        return state.cancellation_requested
