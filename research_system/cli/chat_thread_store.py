from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from research_system.runtime.jsonl import append_jsonl, read_json, read_jsonl, write_json
from research_system.runtime.settings import Settings


def generate_thread_id() -> str:
    return f"thread_{uuid.uuid4().hex[:12]}"


class ChatThreadStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.memory_root / "chat_threads"
        self.index_path = self.root / "index.json"
        self.metadata_root = self.root / "metadata"
        self.turns_root = self.root / "turns"
        self.checkpoints_root = self.root / "checkpoints"
        self.summaries_root = self.root / "summaries"
        self.summary_history_root = self.root / "summary_history"
        self.archives_root = self.root / "archives"

    def ensure_thread(self, thread_id: str | None) -> str:
        resolved = str(thread_id or "").strip() or generate_thread_id()
        self._upsert_index_record(
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
        for row in self._read_index_rows():
            if str(row.get("thread_id") or "") == resolved:
                return True
        return False

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
        next_turn_seq = self._next_turn_seq(thread_id=thread_id)
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
        path = self.turns_root / f"{thread_id}.jsonl"
        append_jsonl(path, record)
        self._upsert_index_record(
            thread_id=thread_id,
            last_turn_at=recorded_at,
            turn_count_delta=1,
            lifetime_turn_count_delta=1,
        )
        return record

    def read_recent_turns(self, *, thread_id: str, limit: int = 6) -> list[dict[str, Any]]:
        path = self.turns_root / f"{thread_id}.jsonl"
        rows = read_jsonl(path)
        if limit <= 0:
            return []
        return rows[-limit:]

    def read_all_turns(self, *, thread_id: str) -> list[dict[str, Any]]:
        path = self.turns_root / f"{thread_id}.jsonl"
        return read_jsonl(path)

    def replace_turns(self, *, thread_id: str, rows: list[dict[str, Any]]) -> None:
        path = self.turns_root / f"{thread_id}.jsonl"
        if not rows:
            if path.exists():
                path.unlink()
            self._upsert_index_record(
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
        self._upsert_index_record(
            thread_id=thread_id,
            last_turn_at=last_turn_at,
            turn_count_delta=0,
            lifetime_turn_count_delta=0,
            replace_turn_count=len(rows),
        )

    def list_threads(
        self,
        *,
        limit: int = 20,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self._read_index_rows()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            thread_id = str(row.get("thread_id") or "").strip()
            metadata = self.load_thread_metadata(thread_id=thread_id) or {}
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

    def load_thread_summary(self, *, thread_id: str) -> dict[str, Any] | None:
        history = self.read_recent_summaries(thread_id=thread_id, limit=1)
        if history:
            return history[-1]
        path = self.summaries_root / f"{thread_id}.json"
        payload = read_json(path)
        return payload if isinstance(payload, dict) else None

    def load_thread_checkpoint(self, *, thread_id: str) -> dict[str, Any] | None:
        path = self.checkpoints_root / f"{thread_id}.json"
        payload = read_json(path)
        return payload if isinstance(payload, dict) else None

    def load_thread_metadata(self, *, thread_id: str) -> dict[str, Any] | None:
        path = self.metadata_root / f"{thread_id}.json"
        payload = read_json(path)
        return payload if isinstance(payload, dict) else None

    def ensure_thread_metadata(self, *, thread_id: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = self.load_thread_metadata(thread_id=thread_id) or {"thread_id": thread_id}
        changed = False
        for key, value in dict(defaults or {}).items():
            if existing.get(key) in (None, "") and value not in (None, ""):
                existing[key] = value
                changed = True
        if changed or existing.get("thread_id") != thread_id:
            existing["thread_id"] = thread_id
            self.write_thread_metadata(thread_id=thread_id, metadata=existing)
        return existing

    def write_thread_metadata(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        path = self.metadata_root / f"{thread_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"thread_id": thread_id, **dict(metadata)}
        write_json(path, payload)
        return payload

    def archive_thread(
        self,
        *,
        thread_id: str,
        archived_at: str,
        archived_reason: str | None = None,
    ) -> dict[str, Any]:
        metadata = self.ensure_thread_metadata(
            thread_id=thread_id,
            defaults={
                "archived": False,
                "archived_at": None,
                "unarchived_at": None,
            },
        )
        metadata["archived"] = True
        metadata["archived_at"] = archived_at
        metadata["unarchived_at"] = None
        if archived_reason not in (None, ""):
            metadata["archived_reason"] = archived_reason
        return self.write_thread_metadata(thread_id=thread_id, metadata=metadata)

    def unarchive_thread(
        self,
        *,
        thread_id: str,
        unarchived_at: str,
    ) -> dict[str, Any]:
        metadata = self.ensure_thread_metadata(
            thread_id=thread_id,
            defaults={
                "archived": False,
                "archived_at": None,
                "unarchived_at": None,
            },
        )
        metadata["archived"] = False
        metadata["unarchived_at"] = unarchived_at
        return self.write_thread_metadata(thread_id=thread_id, metadata=metadata)

    def write_thread_checkpoint(
        self,
        *,
        thread_id: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        path = self.checkpoints_root / f"{thread_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "thread_id": thread_id,
            "session_state": session_state,
        }
        write_json(path, payload)
        return payload

    def write_thread_summary(
        self,
        *,
        thread_id: str,
        summary_text: str,
        last_turn_at: str | None = None,
        source_turn_count: int | None = None,
        metadata: dict[str, Any] | None = None,
        covered_turn_count: int | None = None,
        delta_turn_count: int | None = None,
    ) -> dict[str, Any]:
        path = self.summaries_root / f"{thread_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "thread_id": thread_id,
            "summary_text": summary_text,
            "last_turn_at": last_turn_at,
            "source_turn_count": source_turn_count,
            "covered_turn_count": covered_turn_count,
            "delta_turn_count": delta_turn_count,
            "metadata": metadata or {},
        }
        write_json(path, payload)
        append_jsonl(self.summary_history_root / f"{thread_id}.jsonl", payload)
        return payload

    def read_recent_summaries(self, *, thread_id: str, limit: int = 2) -> list[dict[str, Any]]:
        path = self.summary_history_root / f"{thread_id}.jsonl"
        rows = read_jsonl(path)
        if limit <= 0:
            return []
        return rows[-limit:]

    def archive_turns(self, *, thread_id: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        path = self.archives_root / f"{thread_id}.jsonl"
        for row in rows:
            append_jsonl(path, row)

    def read_archived_turns(self, *, thread_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.archives_root / f"{thread_id}.jsonl"
        rows = read_jsonl(path)
        if limit is None:
            return rows
        if limit <= 0:
            return []
        return rows[-limit:]

    def _upsert_index_record(
        self,
        *,
        thread_id: str,
        last_turn_at: str | None,
        turn_count_delta: int,
        lifetime_turn_count_delta: int,
        replace_turn_count: int | None = None,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = read_json(self.index_path) or {}
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
        write_json(self.index_path, {"threads": retained})

    def _read_index_rows(self) -> list[dict[str, Any]]:
        payload = read_json(self.index_path) or {}
        rows = payload.get("threads") or []
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _next_turn_seq(self, *, thread_id: str) -> int:
        payload = read_json(self.index_path) or {}
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
