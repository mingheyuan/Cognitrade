from __future__ import annotations

import uuid
from typing import Any

from research_system.runtime.jsonl import read_json, write_json


def generate_thread_id() -> str:
    return f"thread_{uuid.uuid4().hex[:12]}"


class ChatThreadStoreIndexSupport:
    def __init__(self, store) -> None:
        self.store = store

    def ensure_thread(self, thread_id: str | None) -> str:
        resolved = str(thread_id or "").strip() or generate_thread_id()
        self.upsert_index_record(
            thread_id=resolved,
            last_turn_at=None,
            turn_count_delta=0,
            lifetime_turn_count_delta=0,
        )
        return resolved

    def has_thread(self, *, thread_id: str) -> bool:
        resolved = str(thread_id or "").strip()
        if not resolved:
            return False
        for row in self.read_index_rows():
            if str(row.get("thread_id") or "") == resolved:
                return True
        return False

    def list_threads(
        self,
        *,
        limit: int = 20,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self.read_index_rows()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            thread_id = str(row.get("thread_id") or "").strip()
            metadata = self.store.load_thread_metadata(thread_id=thread_id) or {}
            archived = bool(metadata.get("archived"))
            if archived_only and not archived:
                continue
            if not include_archived and not archived_only and archived:
                continue
            filtered.append(row)
        filtered.sort(key=lambda item: str(item.get("last_turn_at") or ""), reverse=True)
        if limit <= 0:
            return []
        return filtered[:limit]

    def upsert_index_record(
        self,
        *,
        thread_id: str,
        last_turn_at: str | None,
        turn_count_delta: int,
        lifetime_turn_count_delta: int,
        replace_turn_count: int | None = None,
    ) -> None:
        self.store.root.mkdir(parents=True, exist_ok=True)
        payload = read_json(self.store.index_path) or {}
        rows = payload.get("threads") or []
        if not isinstance(rows, list):
            rows = []

        existing: dict[str, Any] | None = None
        retained: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("thread_id") or "") == thread_id and existing is None:
                existing = dict(row)
                continue
            retained.append(dict(row))

        current_turn_count = int(existing.get("turn_count") or 0) if existing else 0
        current_lifetime_turn_count = int(existing.get("lifetime_turn_count") or 0) if existing else 0
        turn_count = replace_turn_count if replace_turn_count is not None else max(0, current_turn_count + turn_count_delta)
        lifetime_turn_count = max(0, current_lifetime_turn_count + lifetime_turn_count_delta)
        retained.append(
            {
                "thread_id": thread_id,
                "last_turn_at": last_turn_at or (existing or {}).get("last_turn_at"),
                "turn_count": turn_count,
                "lifetime_turn_count": lifetime_turn_count,
            }
        )
        write_json(self.store.index_path, {"threads": retained})

    def read_index_rows(self) -> list[dict[str, Any]]:
        payload = read_json(self.store.index_path) or {}
        rows = payload.get("threads") or []
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def next_turn_seq(self, *, thread_id: str) -> int:
        payload = read_json(self.store.index_path) or {}
        rows = payload.get("threads") or []
        if not isinstance(rows, list):
            return 1
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("thread_id") or "") != thread_id:
                continue
            return int(row.get("lifetime_turn_count") or 0) + 1
        return 1
