from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from research_system.runtime.id_utils import generate_ulid


TranscriptKind = Literal[
    "thinking",
    "status",
    "answer",
    "answer_stream",
    "answer_delta",
    "answer_done",
    "warning",
    "event",
    "meta",
    "input",
]

TranscriptRole = Literal["thought", "status", "answer", "system", "user"]
TranscriptUpdateMode = Literal["append", "replace", "stream"]
TranscriptScope = Literal["persistent", "turn"]


@dataclass(frozen=True)
class TranscriptMessage:
    kind: TranscriptKind
    text: str
    message_id: str
    role: TranscriptRole
    update_mode: TranscriptUpdateMode
    scope: TranscriptScope
    slot_key: str | None = None
    stream_key: str | None = None


def transcript_role_for_kind(kind: TranscriptKind) -> TranscriptRole:
    if kind == "thinking":
        return "thought"
    if kind == "status":
        return "status"
    if kind in {"answer", "answer_stream", "answer_delta", "answer_done"}:
        return "answer"
    if kind == "input":
        return "user"
    return "system"


def transcript_update_mode_for_kind(kind: TranscriptKind) -> TranscriptUpdateMode:
    if kind in {"status"}:
        return "replace"
    if kind in {"answer_stream", "answer_delta", "answer_done"}:
        return "stream"
    return "append"


def transcript_scope_for_kind(kind: TranscriptKind) -> TranscriptScope:
    if kind in {"status", "answer_stream", "answer_delta", "answer_done"}:
        return "turn"
    return "persistent"


def default_slot_key_for_kind(kind: TranscriptKind) -> str | None:
    if kind in {"status"}:
        return kind
    if kind in {"answer_stream", "answer_delta", "answer_done"}:
        return "answer_stream"
    return None


def display_block_title_for_message(message: TranscriptMessage) -> str | None:
    if message.role == "thought":
        return "thinking"
    if message.role == "status":
        return "status"
    if message.role == "answer":
        return "answer"
    if message.kind == "warning":
        return "warning"
    if message.kind == "event":
        return "event"
    return None


def renders_as_inline_message(message: TranscriptMessage) -> bool:
    return message.kind in {"meta", "input"}


def make_transcript_message(
    *,
    kind: TranscriptKind,
    text: str,
    message_id: str | None = None,
    role: TranscriptRole | None = None,
    update_mode: TranscriptUpdateMode | None = None,
    scope: TranscriptScope | None = None,
    slot_key: str | None = None,
    stream_key: str | None = None,
) -> TranscriptMessage:
    resolved_slot_key = slot_key if slot_key is not None else default_slot_key_for_kind(kind)
    resolved_stream_key = stream_key if stream_key is not None else resolved_slot_key
    return TranscriptMessage(
        kind=kind,
        text=text,
        message_id=message_id or generate_ulid(),
        role=role or transcript_role_for_kind(kind),
        update_mode=update_mode or transcript_update_mode_for_kind(kind),
        scope=scope or transcript_scope_for_kind(kind),
        slot_key=resolved_slot_key,
        stream_key=resolved_stream_key,
    )
