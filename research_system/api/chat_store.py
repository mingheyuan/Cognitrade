from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import (
    append_jsonl,
    read_json,
    read_jsonl,
    write_json,
)


_CONTEXT_TURN_LIMIT = 6
_COMPACT_RETAIN_TURNS = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_id() -> str:
    return f"thread_{uuid.uuid4().hex[:12]}"


def _compact_text(value: Any, *, limit: int) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


class ChatThreadStore:
    """Canonical persistence and query layer for versioned chat APIs."""

    def __init__(self, settings: Settings) -> None:
        self.root = settings.memory_root / "chat_threads"
        self.index_path = self.root / "index.json"
        self.metadata_root = self.root / "metadata"
        self.turns_root = self.root / "turns"
        self.checkpoints_root = self.root / "checkpoints"
        self.summaries_root = self.root / "summaries"
        self.summary_history_root = self.root / "summary_history"
        self.archives_root = self.root / "archives"

    def create_thread(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        thread_id = self.ensure_thread(None)
        self.write_thread_metadata(
            thread_id=thread_id,
            metadata={
                "thread_source": "new",
                "parent_thread_id": None,
                "forked_from_thread_id": None,
                "fork_turn_count": 0,
                "archived": False,
                "archived_at": None,
                "unarchived_at": None,
                **({"user_id": user_id} if user_id else {}),
                **({"tenant_id": tenant_id} if tenant_id else {}),
            },
        )
        return thread_id

    def ensure_thread(self, thread_id: str | None) -> str:
        resolved = str(thread_id or "").strip() or _thread_id()
        self._upsert_index(
            thread_id=resolved,
            last_turn_at=None,
            turn_count_delta=0,
            lifetime_turn_count_delta=0,
        )
        return resolved

    def has_thread(self, *, thread_id: str) -> bool:
        resolved = str(thread_id or "").strip()
        return bool(resolved) and any(
            str(row.get("thread_id") or "") == resolved
            for row in self._read_index()
        )

    def thread_exists(self, thread_id: str) -> bool:
        if self.has_thread(thread_id=thread_id):
            return True
        return any(
            path.exists()
            for path in (
                self.metadata_root / f"{thread_id}.json",
                self.turns_root / f"{thread_id}.jsonl",
                self.checkpoints_root / f"{thread_id}.json",
                self.summaries_root / f"{thread_id}.json",
                self.archives_root / f"{thread_id}.jsonl",
            )
        )

    def list_threads(
        self,
        *,
        limit: int = 20,
        scope: str = "active",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index_row in self._read_index():
            thread_id = str(index_row.get("thread_id") or "").strip()
            if not thread_id:
                continue
            metadata = self.load_thread_metadata(thread_id=thread_id) or {}
            archived = bool(metadata.get("archived"))
            if scope == "active" and archived:
                continue
            if scope == "archived" and not archived:
                continue
            summary = self.load_thread_summary(thread_id=thread_id) or {}
            summary_metadata = (
                summary.get("metadata")
                if isinstance(summary.get("metadata"), dict)
                else {}
            )
            rows.append(
                {
                    **index_row,
                    "thread_label": self._optional(metadata.get("thread_label")),
                    "topic_title": self._topic_title(
                        summary_metadata=summary_metadata,
                        metadata=metadata,
                    ),
                    "topic_intent": self._optional(
                        summary_metadata.get("last_result_intent")
                    ),
                    "thread_source": self._optional(metadata.get("thread_source"))
                    or "new",
                    "parent_thread_id": self._optional(
                        metadata.get("parent_thread_id")
                    ),
                    "fork_turn_count": int(metadata.get("fork_turn_count") or 0),
                    "archived": archived,
                    "archived_at": self._optional(metadata.get("archived_at")),
                }
            )
        rows.sort(key=lambda item: str(item.get("last_turn_at") or ""), reverse=True)
        return rows[: max(0, limit)]

    def thread_detail(self, thread_id: str) -> dict[str, Any]:
        metadata = self.load_thread_metadata(thread_id=thread_id) or {}
        summary = self.load_thread_summary(thread_id=thread_id) or {}
        checkpoint = self.load_thread_checkpoint(thread_id=thread_id)
        active_turns = self.read_all_turns(thread_id=thread_id)
        archived_turns = self.read_archived_turns(thread_id=thread_id)
        turns = sorted(
            [*archived_turns, *active_turns],
            key=lambda row: (
                int(row.get("turn_seq") or 0),
                str(row.get("recorded_at") or ""),
            ),
        )
        summary_metadata = (
            summary.get("metadata")
            if isinstance(summary.get("metadata"), dict)
            else {}
        )
        index_row = self._index_row(thread_id)
        return {
            "thread_id": thread_id,
            "thread_label": self._optional(metadata.get("thread_label")),
            "topic_title": self._topic_title(
                summary_metadata=summary_metadata,
                metadata=metadata,
            ),
            "topic_intent": self._optional(
                summary_metadata.get("last_result_intent")
            ),
            "thread_source": self._optional(metadata.get("thread_source")) or "new",
            "parent_thread_id": self._optional(metadata.get("parent_thread_id")),
            "fork_turn_count": int(metadata.get("fork_turn_count") or 0),
            "archived": bool(metadata.get("archived")),
            "archived_at": self._optional(metadata.get("archived_at")),
            "unarchived_at": self._optional(metadata.get("unarchived_at")),
            "has_checkpoint": bool(
                isinstance(checkpoint, dict)
                and isinstance(checkpoint.get("session_state"), dict)
            ),
            "has_compact_summary": bool(
                self._optional(summary.get("summary_text"))
            ),
            "summary_text": self._optional(summary.get("summary_text")),
            "summary_turn_count": int(summary.get("covered_turn_count") or 0),
            "turn_count": int(index_row.get("turn_count") or len(active_turns)),
            "lifetime_turn_count": int(
                index_row.get("lifetime_turn_count") or len(turns)
            ),
            "turns": turns,
        }

    def preview_thread(
        self,
        thread_id: str,
        *,
        turn_limit: int,
    ) -> dict[str, Any]:
        metadata = self.load_thread_metadata(thread_id=thread_id) or {}
        summary = self.load_thread_summary(thread_id=thread_id) or {}
        checkpoint = self.load_thread_checkpoint(thread_id=thread_id)
        return {
            "thread_id": thread_id,
            "thread_label": self._optional(metadata.get("thread_label")),
            "recent_turns": self.read_recent_turns(
                thread_id=thread_id,
                limit=turn_limit,
            ),
            "summary_text": self._optional(summary.get("summary_text")),
            "summary_turn_count": int(summary.get("covered_turn_count") or 0),
            "has_checkpoint": bool(
                isinstance(checkpoint, dict)
                and isinstance(checkpoint.get("session_state"), dict)
            ),
        }

    def rename_thread(self, thread_id: str, thread_label: str) -> dict[str, str]:
        normalized = re.sub(r"\s+", " ", str(thread_label or "").strip())
        metadata = self.load_thread_metadata(thread_id=thread_id) or {}
        metadata["thread_label"] = normalized
        self.write_thread_metadata(thread_id=thread_id, metadata=metadata)
        return {"thread_id": thread_id, "thread_label": normalized}

    def fork_thread(self, thread_id: str) -> str:
        child_id = self.ensure_thread(None)
        turns = []
        for index, row in enumerate(
            self.read_all_turns(thread_id=thread_id),
            start=1,
        ):
            turns.append(
                {
                    **row,
                    "thread_id": child_id,
                    "turn_seq": index,
                }
            )
        if turns:
            self.replace_turns(thread_id=child_id, rows=turns)

        summary = self.load_thread_summary(thread_id=thread_id)
        if summary:
            summary_metadata = (
                dict(summary.get("metadata"))
                if isinstance(summary.get("metadata"), dict)
                else {}
            )
            summary_metadata.setdefault("forked_from_thread_id", thread_id)
            self.write_thread_summary(
                thread_id=child_id,
                summary_text=str(summary.get("summary_text") or ""),
                last_turn_at=self._optional(summary.get("last_turn_at")),
                source_turn_count=summary.get("source_turn_count"),
                covered_turn_count=summary.get("covered_turn_count"),
                delta_turn_count=summary.get("delta_turn_count"),
                metadata=summary_metadata,
            )

        checkpoint = self.load_thread_checkpoint(thread_id=thread_id)
        if checkpoint and isinstance(checkpoint.get("session_state"), dict):
            session_state = {
                **checkpoint["session_state"],
                "thread_id": child_id,
            }
            self.write_thread_checkpoint(
                thread_id=child_id,
                session_state=session_state,
            )

        parent_metadata = self.load_thread_metadata(thread_id=thread_id) or {}
        self.write_thread_metadata(
            thread_id=child_id,
            metadata={
                **parent_metadata,
                "thread_id": child_id,
                "thread_source": "fork",
                "parent_thread_id": thread_id,
                "forked_from_thread_id": thread_id,
                "fork_turn_count": len(turns),
                "archived": False,
                "archived_at": None,
                "unarchived_at": None,
            },
        )
        return child_id

    def archive_thread(self, thread_id: str) -> dict[str, Any]:
        archived_at = _now()
        metadata = self.load_thread_metadata(thread_id=thread_id) or {}
        metadata.update(
            {
                "archived": True,
                "archived_at": archived_at,
                "unarchived_at": None,
            }
        )
        metadata = self.write_thread_metadata(
            thread_id=thread_id,
            metadata=metadata,
        )
        return {
            "thread_id": thread_id,
            "archived_at": archived_at,
            "next_thread_id": None,
            "metadata": metadata,
        }

    def unarchive_thread(self, thread_id: str) -> dict[str, Any]:
        unarchived_at = _now()
        metadata = self.load_thread_metadata(thread_id=thread_id) or {}
        metadata.update(
            {
                "archived": False,
                "unarchived_at": unarchived_at,
            }
        )
        metadata = self.write_thread_metadata(
            thread_id=thread_id,
            metadata=metadata,
        )
        return {
            "thread_id": thread_id,
            "unarchived_at": unarchived_at,
            "metadata": metadata,
        }

    def compact_thread(self, thread_id: str) -> dict[str, Any]:
        turns = self.read_all_turns(thread_id=thread_id)
        summaries = self.read_recent_summaries(thread_id=thread_id, limit=2)
        latest_summary = summaries[-1] if summaries else {}
        covered_before = int(latest_summary.get("covered_turn_count") or 0)
        delta_turns = [
            turn
            for turn in turns
            if int(turn.get("turn_seq") or 0) > covered_before
        ]
        if not delta_turns and turns:
            delta_turns = turns[-min(len(turns), _COMPACT_RETAIN_TURNS) :]
        profile = self._profile_from_turns(turns)
        summary_text = self._build_summary_text(
            profile=profile,
            turns=delta_turns,
            previous_summaries=summaries,
            covered_before=covered_before,
            total_turn_count=len(turns),
        )
        covered_turn_count = max(
            (int(turn.get("turn_seq") or 0) for turn in turns),
            default=covered_before,
        )
        payload = self.write_thread_summary(
            thread_id=thread_id,
            summary_text=summary_text,
            last_turn_at=self._optional(
                turns[-1].get("recorded_at") if turns else None
            ),
            source_turn_count=len(turns),
            covered_turn_count=covered_turn_count,
            delta_turn_count=len(delta_turns),
            metadata={
                **profile,
                "previous_summary_count": len(summaries),
                "delta_turn_seq_start": min(
                    (int(turn.get("turn_seq") or 0) for turn in delta_turns),
                    default=None,
                ),
                "delta_turn_seq_end": max(
                    (int(turn.get("turn_seq") or 0) for turn in delta_turns),
                    default=None,
                ),
                "topic_title": self._topic_from_profile(
                    profile,
                    latest_summary=latest_summary,
                ),
            },
        )
        if len(turns) > _COMPACT_RETAIN_TURNS:
            self.archive_turns(
                thread_id=thread_id,
                rows=turns[:-_COMPACT_RETAIN_TURNS],
            )
            self.replace_turns(
                thread_id=thread_id,
                rows=turns[-_COMPACT_RETAIN_TURNS:],
            )
        return payload

    def build_context(self, thread_id: str) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        turns = self.read_recent_turns(
            thread_id=thread_id,
            limit=_CONTEXT_TURN_LIMIT,
        )
        profile = self._profile_from_turns(turns)
        metadata = self.load_thread_metadata(thread_id=thread_id) or {}
        summaries = self.read_recent_summaries(thread_id=thread_id, limit=2)
        latest_summary = summaries[-1] if summaries else {}
        latest_summary_metadata = (
            latest_summary.get("metadata")
            if isinstance(latest_summary.get("metadata"), dict)
            else {}
        )
        profile = {**profile, **latest_summary_metadata}
        topic_title = self._topic_title(
            summary_metadata=profile,
            metadata=metadata,
        )
        if any(
            profile.get(key)
            for key in (
                "last_user_query",
                "last_result_summary",
                "last_result_follow_up",
                "last_run_id",
                "pending_clarification_question",
            )
        ):
            contexts.append(
                {
                    "type": "chat_thread_profile",
                    "thread_id": thread_id,
                    "thread_label": self._optional(metadata.get("thread_label")),
                    "topic_title": topic_title,
                    "topic_intent": self._optional(
                        profile.get("last_result_intent")
                    ),
                    "last_user_query": profile.get("last_user_query"),
                    "last_result_summary": profile.get("last_result_summary"),
                    "last_result_follow_up": profile.get("last_result_follow_up"),
                    "last_run_id": profile.get("last_run_id"),
                    "open_question": profile.get(
                        "pending_clarification_question"
                    ),
                    "thread_source": self._optional(
                        metadata.get("thread_source")
                    )
                    or "new",
                    "parent_thread_id": self._optional(
                        metadata.get("parent_thread_id")
                    ),
                    "fork_turn_count": int(metadata.get("fork_turn_count") or 0),
                    "archived": bool(metadata.get("archived")),
                }
            )
        for index, summary in enumerate(summaries):
            summary_text = self._optional(summary.get("summary_text"))
            if summary_text:
                contexts.append(
                    {
                        "type": "chat_thread_summary",
                        "thread_id": thread_id,
                        "summary": summary_text,
                        "summary_index": index,
                        "last_turn_at": self._optional(
                            summary.get("last_turn_at")
                        ),
                        "source_turn_count": summary.get("source_turn_count"),
                        "covered_turn_count": summary.get("covered_turn_count"),
                        "delta_turn_count": summary.get("delta_turn_count"),
                        "metadata": summary.get("metadata")
                        if isinstance(summary.get("metadata"), dict)
                        else {},
                    }
                )
        for turn in turns:
            content = self._optional(turn.get("content"))
            if not content:
                continue
            contexts.append(
                {
                    "type": "chat_thread_turn",
                    "thread_id": thread_id,
                    "role": self._optional(turn.get("role")),
                    "turn_type": self._optional(turn.get("turn_type")),
                    "content": content,
                    "intent": self._optional(turn.get("intent")),
                    "run_id": self._optional(turn.get("run_id")),
                    "recorded_at": self._optional(turn.get("recorded_at")),
                    "metadata": turn.get("metadata")
                    if isinstance(turn.get("metadata"), dict)
                    else {},
                }
            )
        return contexts

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
        turn = {
            "thread_id": thread_id,
            "turn_seq": self._next_turn_seq(thread_id),
            "role": role,
            "content": content,
            "turn_type": turn_type,
            "intent": intent,
            "run_id": run_id,
            "metadata": metadata or {},
            "recorded_at": recorded_at,
        }
        append_jsonl(self.turns_root / f"{thread_id}.jsonl", turn)
        self._upsert_index(
            thread_id=thread_id,
            last_turn_at=recorded_at,
            turn_count_delta=1,
            lifetime_turn_count_delta=1,
        )
        return turn

    def read_recent_turns(
        self,
        *,
        thread_id: str,
        limit: int = _CONTEXT_TURN_LIMIT,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return self.read_all_turns(thread_id=thread_id)[-limit:]

    def read_all_turns(self, *, thread_id: str) -> list[dict[str, Any]]:
        return read_jsonl(self.turns_root / f"{thread_id}.jsonl")

    def replace_turns(
        self,
        *,
        thread_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        path = self.turns_root / f"{thread_id}.jsonl"
        if not rows:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=True))
                    handle.write("\n")
        self._upsert_index(
            thread_id=thread_id,
            last_turn_at=self._optional(
                rows[-1].get("recorded_at") if rows else None
            ),
            turn_count_delta=0,
            lifetime_turn_count_delta=0,
            replace_turn_count=len(rows),
        )

    def archive_turns(
        self,
        *,
        thread_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        for row in rows:
            append_jsonl(self.archives_root / f"{thread_id}.jsonl", row)

    def read_archived_turns(
        self,
        *,
        thread_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = read_jsonl(self.archives_root / f"{thread_id}.jsonl")
        if limit is None:
            return rows
        return rows[-limit:] if limit > 0 else []

    def load_thread_metadata(self, *, thread_id: str) -> dict[str, Any] | None:
        payload = read_json(self.metadata_root / f"{thread_id}.json")
        return payload if isinstance(payload, dict) else None

    def write_thread_metadata(
        self,
        *,
        thread_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {"thread_id": thread_id, **metadata}
        write_json(self.metadata_root / f"{thread_id}.json", payload)
        return payload

    def load_thread_checkpoint(self, *, thread_id: str) -> dict[str, Any] | None:
        payload = read_json(self.checkpoints_root / f"{thread_id}.json")
        return payload if isinstance(payload, dict) else None

    def write_thread_checkpoint(
        self,
        *,
        thread_id: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {"thread_id": thread_id, "session_state": session_state}
        write_json(self.checkpoints_root / f"{thread_id}.json", payload)
        return payload

    def load_thread_summary(self, *, thread_id: str) -> dict[str, Any] | None:
        history = self.read_recent_summaries(thread_id=thread_id, limit=1)
        if history:
            return history[-1]
        payload = read_json(self.summaries_root / f"{thread_id}.json")
        return payload if isinstance(payload, dict) else None

    def write_thread_summary(
        self,
        *,
        thread_id: str,
        summary_text: str,
        last_turn_at: str | None,
        source_turn_count: int | None,
        covered_turn_count: int | None,
        delta_turn_count: int | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "thread_id": thread_id,
            "summary_text": summary_text,
            "last_turn_at": last_turn_at,
            "source_turn_count": source_turn_count,
            "covered_turn_count": covered_turn_count,
            "delta_turn_count": delta_turn_count,
            "metadata": metadata,
        }
        write_json(self.summaries_root / f"{thread_id}.json", payload)
        append_jsonl(
            self.summary_history_root / f"{thread_id}.jsonl",
            payload,
        )
        return payload

    def read_recent_summaries(
        self,
        *,
        thread_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return read_jsonl(
            self.summary_history_root / f"{thread_id}.jsonl"
        )[-limit:]

    def _read_index(self) -> list[dict[str, Any]]:
        payload = read_json(self.index_path) or {}
        rows = payload.get("threads")
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(
            rows,
            list,
        ) else []

    def _index_row(self, thread_id: str) -> dict[str, Any]:
        return next(
            (
                row
                for row in self._read_index()
                if str(row.get("thread_id") or "") == thread_id
            ),
            {},
        )

    def _upsert_index(
        self,
        *,
        thread_id: str,
        last_turn_at: str | None,
        turn_count_delta: int,
        lifetime_turn_count_delta: int,
        replace_turn_count: int | None = None,
    ) -> None:
        rows = self._read_index()
        current = next(
            (
                row
                for row in rows
                if str(row.get("thread_id") or "") == thread_id
            ),
            {},
        )
        retained = [
            row
            for row in rows
            if str(row.get("thread_id") or "") != thread_id
        ]
        current_turn_count = int(current.get("turn_count") or 0)
        current_lifetime_count = int(current.get("lifetime_turn_count") or 0)
        retained.append(
            {
                "thread_id": thread_id,
                "last_turn_at": last_turn_at or current.get("last_turn_at"),
                "turn_count": replace_turn_count
                if replace_turn_count is not None
                else max(0, current_turn_count + turn_count_delta),
                "lifetime_turn_count": max(
                    0,
                    current_lifetime_count + lifetime_turn_count_delta,
                ),
            }
        )
        write_json(self.index_path, {"threads": retained})

    def _next_turn_seq(self, thread_id: str) -> int:
        return int(self._index_row(thread_id).get("lifetime_turn_count") or 0) + 1

    @staticmethod
    def _optional(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    def _topic_title(
        self,
        *,
        summary_metadata: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str | None:
        explicit = self._optional(
            summary_metadata.get("topic_title") or metadata.get("topic_title")
        )
        if explicit:
            return explicit
        return self._optional(
            _compact_text(
                summary_metadata.get("last_user_query")
                or summary_metadata.get("last_result_summary"),
                limit=72,
            )
        )

    @staticmethod
    def _profile_from_turns(turns: list[dict[str, Any]]) -> dict[str, Any]:
        profile: dict[str, Any] = {}
        for turn in turns:
            role = str(turn.get("role") or "")
            content = str(turn.get("content") or "").strip()
            metadata = (
                turn.get("metadata")
                if isinstance(turn.get("metadata"), dict)
                else {}
            )
            if role == "user" and content:
                profile["last_user_query"] = content
            if role == "assistant" and content:
                profile["last_result_summary"] = (
                    str(metadata.get("summary") or "").strip() or content
                )
                follow_up = str(metadata.get("follow_up") or "").strip()
                if follow_up:
                    profile["last_result_follow_up"] = follow_up
                if metadata.get("clarification_pending"):
                    profile["pending_clarification_question"] = content
                else:
                    profile.pop("pending_clarification_question", None)
            if str(turn.get("turn_type") or "").startswith("research_"):
                profile["last_result_intent"] = turn.get("intent")
                profile["last_run_id"] = turn.get("run_id")
        return profile

    def _topic_from_profile(
        self,
        profile: dict[str, Any],
        *,
        latest_summary: dict[str, Any],
    ) -> str | None:
        latest_metadata = (
            latest_summary.get("metadata")
            if isinstance(latest_summary.get("metadata"), dict)
            else {}
        )
        return self._optional(
            latest_metadata.get("topic_title")
            or _compact_text(
                profile.get("last_user_query")
                or profile.get("last_result_summary"),
                limit=72,
            )
        )

    @staticmethod
    def _build_summary_text(
        *,
        profile: dict[str, Any],
        turns: list[dict[str, Any]],
        previous_summaries: list[dict[str, Any]],
        covered_before: int,
        total_turn_count: int,
    ) -> str:
        labels = (
            ("recent_query", "last_user_query"),
            ("recent_intent", "last_result_intent"),
            ("recent_summary", "last_result_summary"),
            ("recent_follow_up", "last_result_follow_up"),
            ("recent_run_id", "last_run_id"),
            ("open_question", "pending_clarification_question"),
        )
        lines = [
            f"{label}: {profile[key]}"
            for label, key in labels
            if profile.get(key)
        ]
        if covered_before:
            lines.append(f"covered_turns_before: {covered_before}")
        lines.extend(
            (
                f"covered_turns_after: {total_turn_count}",
                f"delta_turn_count: {len(turns)}",
            )
        )
        if previous_summaries:
            previous = _compact_text(
                previous_summaries[-1].get("summary_text"),
                limit=180,
            )
            if previous:
                lines.append(f"previous_summary: {previous}")
        descriptions = []
        for turn in turns[-_COMPACT_RETAIN_TURNS:]:
            content = _compact_text(turn.get("content"), limit=120)
            if content:
                descriptions.append(
                    f"{turn.get('role') or 'unknown'}/"
                    f"{turn.get('turn_type') or 'turn'}: {content}"
                )
        if descriptions:
            lines.append("recent_turns:")
            lines.extend(descriptions)
        return "\n".join(lines).strip() or "recent_turns: empty"


__all__ = ["ChatThreadStore"]
