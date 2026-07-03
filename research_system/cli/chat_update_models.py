from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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
    output_mode: str | None = None
    output_panel: str | None = None
    trace_payload: dict | None = None
    events_payload: dict | None = None
    subagent_payload: dict | None = None
    answer_stream_open: bool | None = None


@dataclass(frozen=True)
class SessionStatePatch:
    thread_id: object = SESSION_PATCH_UNSET
    thread_source: object = SESSION_PATCH_UNSET
    parent_thread_id: object = SESSION_PATCH_UNSET
    current_intent: object = SESSION_PATCH_UNSET
    review_target_run_id: object = SESSION_PATCH_UNSET
    last_run_id: object = SESSION_PATCH_UNSET
    thinking_enabled: object = SESSION_PATCH_UNSET
    events_mode: object = SESSION_PATCH_UNSET
    last_user_query: object = SESSION_PATCH_UNSET
    last_result_intent: object = SESSION_PATCH_UNSET
    last_result_summary: object = SESSION_PATCH_UNSET
    last_result_conclusion: object = SESSION_PATCH_UNSET
    last_result_follow_up: object = SESSION_PATCH_UNSET
    pending_clarification_query: object = SESSION_PATCH_UNSET
    pending_clarification_intent: object = SESSION_PATCH_UNSET
    pending_clarification_run_id: object = SESSION_PATCH_UNSET
    pending_clarification_question: object = SESSION_PATCH_UNSET


@dataclass(frozen=True)
class TranscriptUpdate:
    action: TranscriptUpdateAction
    kind: str | None = None
    text: str = ""
    slot_key: str | None = None
    state_patch: ShellStatePatch = field(default_factory=ShellStatePatch)
    session_patch: SessionStatePatch = field(default_factory=SessionStatePatch)


__all__ = [
    "SESSION_PATCH_UNSET",
    "SessionStatePatch",
    "ShellStatePatch",
    "TranscriptUpdate",
    "TranscriptUpdateAction",
]
