from __future__ import annotations

from research_system.cli.chat_transcript import TranscriptKind, TranscriptMessage, make_transcript_message
from research_system.cli.chat_types import TuiSession


TURN_SCOPED_SLOT_KEYS = {"status"}


def append_transcript_message(session: TuiSession, *, kind: TranscriptKind, text: str) -> None:
    session.output_lines.append(make_transcript_message(kind=kind, text=text))


def replace_transcript_message(session: TuiSession, *, kind: TranscriptKind, text: str, slot_key: str) -> None:
    existing_id = session.active_message_ids.get(slot_key)
    message = make_transcript_message(kind=kind, text=text, slot_key=slot_key, stream_key=slot_key)
    if existing_id is None:
        session.output_lines.append(message)
        session.active_message_ids[slot_key] = message.message_id
        return

    for index, existing in enumerate(session.output_lines):
        if existing.message_id != existing_id:
            continue
        session.output_lines[index] = make_transcript_message(
            kind=kind,
            text=text,
            message_id=existing.message_id,
            role=existing.role,
            update_mode=existing.update_mode,
            scope=existing.scope,
            slot_key=slot_key,
            stream_key=slot_key,
        )
        return

    session.output_lines.append(message)
    session.active_message_ids[slot_key] = message.message_id


def append_stream_transcript_message(session: TuiSession, *, kind: TranscriptKind, text: str, slot_key: str) -> None:
    existing_id = session.active_message_ids.get(slot_key)
    message = make_transcript_message(kind=kind, text=text, slot_key=slot_key, stream_key=slot_key)
    if existing_id is None:
        session.output_lines.append(message)
        session.active_message_ids[slot_key] = message.message_id
        return

    for index, existing in enumerate(session.output_lines):
        if existing.message_id != existing_id:
            continue
        session.output_lines[index] = make_transcript_message(
            kind=kind,
            text=f"{existing.text}{text}",
            message_id=existing.message_id,
            role=existing.role,
            update_mode=existing.update_mode,
            scope=existing.scope,
            slot_key=slot_key,
            stream_key=slot_key,
        )
        return

    session.output_lines.append(message)
    session.active_message_ids[slot_key] = message.message_id


def clear_stream_slot(session: TuiSession, slot_key: str) -> None:
    session.active_message_ids.pop(slot_key, None)


def reset_turn_scoped_slots(session: TuiSession) -> None:
    expired_ids = {session.active_message_ids.get(slot_key) for slot_key in TURN_SCOPED_SLOT_KEYS}
    for slot_key in TURN_SCOPED_SLOT_KEYS:
        session.active_message_ids.pop(slot_key, None)
    if expired_ids:
        session.view_state.closed_message_ids.update({message_id for message_id in expired_ids if message_id})


def record_transcript_event(session: TuiSession, *, kind: TranscriptKind, text: str) -> None:
    message = make_transcript_message(kind=kind, text=text)
    if message.kind == "input":
        reset_turn_scoped_slots(session)
        session.output_lines.append(message)
        return
    if message.update_mode == "replace" and message.slot_key is not None:
        replace_transcript_message(session, kind=message.kind, text=message.text, slot_key=message.slot_key)
        return
    if message.update_mode == "stream" and message.slot_key is not None:
        append_stream_transcript_message(session, kind="answer_stream", text=message.text, slot_key=message.slot_key)
        return
    session.output_lines.append(message)


def is_message_closed(session: TuiSession, message: TranscriptMessage) -> bool:
    return message.message_id in session.view_state.closed_message_ids
