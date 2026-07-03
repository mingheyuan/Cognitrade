from __future__ import annotations

import json
from typing import Any

from research_system.runtime.jsonl import append_jsonl, read_jsonl


class ChatThreadStoreTurnSupport:
    def __init__(self, store) -> None:
        self.store = store

    def append_turn(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        turn_type: str,
        intent: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        next_turn_seq = self.store.index_support.next_turn_seq(thread_id=thread_id)
        record = {
            "thread_id": thread_id,
            "turn_seq": next_turn_seq,
            "role": role,
            "content": content,
            "turn_type": turn_type,
            "intent": intent,
            "run_id": run_id,
            "metadata": metadata or {},
            "recorded_at": recorded_at,
        }
        path = self.store.turns_root / f"{thread_id}.jsonl"
        append_jsonl(path, record)
        self.store.index_support.upsert_index_record(
            thread_id=thread_id,
            last_turn_at=recorded_at,
            turn_count_delta=1,
            lifetime_turn_count_delta=1,
        )
        return record

    def read_recent_turns(self, *, thread_id: str, limit: int = 6) -> list[dict[str, Any]]:
        path = self.store.turns_root / f"{thread_id}.jsonl"
        rows = read_jsonl(path)
        if limit <= 0:
            return []
        return rows[-limit:]

    def read_all_turns(self, *, thread_id: str) -> list[dict[str, Any]]:
        path = self.store.turns_root / f"{thread_id}.jsonl"
        return read_jsonl(path)

    def replace_turns(self, *, thread_id: str, rows: list[dict[str, Any]]) -> None:
        path = self.store.turns_root / f"{thread_id}.jsonl"
        if not rows:
            if path.exists():
                path.unlink()
            self.store.index_support.upsert_index_record(
                thread_id=thread_id,
                last_turn_at=None,
                turn_count_delta=0,
                lifetime_turn_count_delta=0,
                replace_turn_count=0,
            )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True))
                handle.write("\n")
        last_turn_at = str(rows[-1].get("recorded_at") or "").strip() or None
        self.store.index_support.upsert_index_record(
            thread_id=thread_id,
            last_turn_at=last_turn_at,
            turn_count_delta=0,
            lifetime_turn_count_delta=0,
            replace_turn_count=len(rows),
        )

    def archive_turns(self, *, thread_id: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        path = self.store.archives_root / f"{thread_id}.jsonl"
        for row in rows:
            append_jsonl(path, row)

    def read_archived_turns(self, *, thread_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.store.archives_root / f"{thread_id}.jsonl"
        rows = read_jsonl(path)
        if limit is None:
            return rows
        if limit <= 0:
            return []
        return rows[-limit:]
