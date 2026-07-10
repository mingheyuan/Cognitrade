from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator

from research_system.api.config import ApiConfig
from research_system.api.errors import ApiError
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_json


_TERMINAL_STATUSES = {"completed", "cancelled", "failed", "gated"}
_TERMINAL_DRAIN_POLLS = 4


def format_sse_event(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "").strip()
    event_type = str(event.get("event_type") or "message").strip() or "message"
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    parts = []
    if event_id:
        parts.append(f"id: {event_id}")
    parts.extend((f"event: {event_type}", f"data: {data}", ""))
    return "\n".join(parts) + "\n"


class RunEventSource:
    def __init__(self, settings: Settings, config: ApiConfig) -> None:
        self.settings = settings
        self.poll_interval_seconds = config.sse_poll_interval_seconds
        self.heartbeat_seconds = config.sse_heartbeat_seconds

    def prepare(self, run_id: str, *, last_event_id: str | None = None) -> int:
        root = self._run_root(run_id)
        rows = self._read_events(root / "execution_events.jsonl")
        normalized_last_id = str(last_event_id or "").strip()
        if not normalized_last_id:
            return 0
        for index, row in enumerate(rows):
            if str(row.get("event_id") or "") == normalized_last_id:
                return index + 1
        raise ApiError(
            status_code=409,
            code="event_cursor_not_found",
            message=f"Last-Event-ID was not found for run {run_id}",
        )

    async def stream(self, run_id: str, *, cursor: int = 0) -> AsyncIterator[str]:
        root = self._run_root(run_id)
        next_index = max(cursor, 0)
        last_heartbeat = time.monotonic()
        terminal_quiet_polls = 0
        terminal_observed = False
        while True:
            rows = await asyncio.to_thread(
                self._read_events,
                root / "execution_events.jsonl",
            )
            emitted_event = False
            while next_index < len(rows):
                yield format_sse_event(rows[next_index])
                next_index += 1
                last_heartbeat = time.monotonic()
                emitted_event = True

            if await asyncio.to_thread(self._is_terminal, root):
                if not terminal_observed or emitted_event:
                    terminal_observed = True
                    terminal_quiet_polls = 0
                else:
                    terminal_quiet_polls += 1
                    if terminal_quiet_polls >= _TERMINAL_DRAIN_POLLS:
                        return
            else:
                terminal_observed = False
                terminal_quiet_polls = 0

            now = time.monotonic()
            if now - last_heartbeat >= self.heartbeat_seconds:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            await asyncio.sleep(self.poll_interval_seconds)

    def _run_root(self, run_id: str) -> Path:
        root = self.settings.runs_root / run_id
        if not root.is_dir():
            raise ApiError(
                status_code=404,
                code="resource_not_found",
                message="The requested run was not found",
            )
        return root

    @staticmethod
    def _read_events(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not line.endswith("\n"):
                    continue
                raise
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    @staticmethod
    def _is_terminal(root: Path) -> bool:
        state = read_json(root / "execution_snapshot.json") or {}
        return str(state.get("status") or "") in _TERMINAL_STATUSES


__all__ = ["RunEventSource", "format_sse_event"]
