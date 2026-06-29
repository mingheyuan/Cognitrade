from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Literal

from research_system.cli.chat_transcript import TranscriptKind, make_transcript_message


TranscriptUpdateAction = Literal["append", "replace", "stream_append", "stream_done", "reset_turn"]
SESSION_PATCH_UNSET = object()


@dataclass(frozen=True)
class ShellStatePatch:
    current_run_id: str | None = None
    current_phase: str | None = None
    current_gate: str | None = None
    run_status_label: str | None = None
    running: bool | None = None
    stop_requested: bool | None = None
    commands_popup_visible: bool | None = None
    focus_target: str | None = None
    help_mode: str | None = None
    input_mode: str | None = None
    answer_stream_open: bool | None = None


@dataclass(frozen=True)
class SessionStatePatch:
    current_intent: object = SESSION_PATCH_UNSET
    review_target_run_id: object = SESSION_PATCH_UNSET
    last_run_id: object = SESSION_PATCH_UNSET
    thinking_enabled: object = SESSION_PATCH_UNSET
    events_mode: object = SESSION_PATCH_UNSET
    pending_clarification_query: object = SESSION_PATCH_UNSET
    pending_clarification_intent: object = SESSION_PATCH_UNSET
    pending_clarification_run_id: object = SESSION_PATCH_UNSET
    pending_clarification_question: object = SESSION_PATCH_UNSET


@dataclass(frozen=True)
class TranscriptUpdate:
    action: TranscriptUpdateAction
    kind: TranscriptKind | None = None
    text: str = ""
    slot_key: str | None = None
    state_patch: ShellStatePatch = field(default_factory=ShellStatePatch)
    session_patch: SessionStatePatch = field(default_factory=SessionStatePatch)


def append_update(
    kind: TranscriptKind,
    text: str,
    *,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return TranscriptUpdate(
        action="append",
        kind=kind,
        text=text,
        state_patch=state_patch or ShellStatePatch(),
        session_patch=session_patch or SessionStatePatch(),
    )


def meta_update(
    text: str,
    *,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return append_update("meta", text, state_patch=state_patch, session_patch=session_patch)


def input_update(
    text: str,
    *,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return append_update("input", text, state_patch=state_patch, session_patch=session_patch)


def thinking_update(
    text: str,
    *,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return append_update("thinking", text, state_patch=state_patch, session_patch=session_patch)


def answer_update(
    text: str,
    *,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return append_update("answer", text, state_patch=state_patch, session_patch=session_patch)


def warning_update(
    text: str,
    *,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return append_update(
        "warning",
        text,
        state_patch=state_patch or ShellStatePatch(run_status_label="warning"),
        session_patch=session_patch,
    )


def replace_update(
    kind: TranscriptKind,
    text: str,
    *,
    slot_key: str | None = None,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return TranscriptUpdate(
        action="replace",
        kind=kind,
        text=text,
        slot_key=slot_key or kind,
        state_patch=state_patch or ShellStatePatch(),
        session_patch=session_patch or SessionStatePatch(),
    )


def stream_append_update(
    text: str,
    *,
    kind: TranscriptKind = "answer_stream",
    slot_key: str = "answer_stream",
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return TranscriptUpdate(
        action="stream_append",
        kind=kind,
        text=text,
        slot_key=slot_key,
        state_patch=state_patch or ShellStatePatch(),
        session_patch=session_patch or SessionStatePatch(),
    )


def stream_done_update(
    *,
    slot_key: str = "answer_stream",
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return TranscriptUpdate(
        action="stream_done",
        slot_key=slot_key,
        state_patch=state_patch or ShellStatePatch(),
        session_patch=session_patch or SessionStatePatch(),
    )


def reset_turn_update(
    *,
    state_patch: ShellStatePatch | None = None,
    session_patch: SessionStatePatch | None = None,
) -> TranscriptUpdate:
    return TranscriptUpdate(
        action="reset_turn",
        state_patch=state_patch or ShellStatePatch(),
        session_patch=session_patch or SessionStatePatch(),
    )


def state_patch_update(patch: ShellStatePatch) -> TranscriptUpdate:
    return TranscriptUpdate(action="append", state_patch=patch)


def session_patch_update(patch: SessionStatePatch) -> TranscriptUpdate:
    return TranscriptUpdate(action="append", session_patch=patch)


def infer_shell_state_patch(kind: str, text: str) -> ShellStatePatch:
    if kind == "status":
        if text.startswith("gate: "):
            return ShellStatePatch(current_gate=text.removeprefix("gate: ").strip() or "pending")
        stripped = text.strip()
        if stripped:
            return ShellStatePatch(current_phase=stripped)
    if kind == "meta":
        if text.startswith("status: "):
            return ShellStatePatch(run_status_label=text.split(": ", 1)[1].strip() or None)
        if text.startswith("run_id: "):
            return ShellStatePatch(current_run_id=text.split(": ", 1)[1].strip() or None)
    if kind == "warning":
        return ShellStatePatch(run_status_label="warning")
    return ShellStatePatch()


def default_update_for_text(kind: str, text: str) -> TranscriptUpdate:
    if kind == "answer_done":
        return stream_done_update()
    message = make_transcript_message(kind=kind, text=text)
    inferred_patch = infer_shell_state_patch(kind, text)
    if message.update_mode == "stream":
        return stream_append_update(
            text,
            kind="answer_stream",
            slot_key=message.slot_key or "answer_stream",
            state_patch=inferred_patch,
        )
    if message.update_mode == "replace":
        return replace_update(
            message.kind,
            message.text,
            slot_key=message.slot_key,
            state_patch=inferred_patch,
        )
    return append_update(message.kind, message.text, state_patch=inferred_patch)


def dispatch_transcript_update(output_writer, update: TranscriptUpdate) -> None:
    if output_writer is not None:
        try:
            output_writer(update)
            return
        except TypeError:
            if update.kind is not None:
                output_writer(update.kind, update.text)
                return
            raise

    if update.action == "stream_append":
        sys.stdout.write(update.text)
        sys.stdout.flush()
        return
    if update.action == "stream_done":
        print()
        return
    if update.kind in {"thinking", "status", "event", "answer"}:
        print(update.kind, flush=True)
        print(f"  {update.text}", flush=True)
        return
    if update.text:
        print(update.text, flush=True)
