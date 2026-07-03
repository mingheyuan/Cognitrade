from __future__ import annotations

from research_system.cli.runtime.chat_action_runtime import ChatActionRuntime
from research_system.cli.state.chat_state import clear_recent_session_patch
from research_system.cli.state.chat_updates import SessionStatePatch


def handle_set_intent_action(runtime: ChatActionRuntime) -> None:
    next_intent = runtime.parsed.get("intent")
    runtime.emit_meta(
        f"intent: {next_intent or 'auto'}",
        session_patch=SessionStatePatch(current_intent=next_intent),
    )
    return None


def handle_set_run_id_action(runtime: ChatActionRuntime) -> None:
    next_run_id = runtime.parsed.get("run_id")
    runtime.emit_meta(
        f"review_target_run_id: {next_run_id or 'unset'}",
        session_patch=SessionStatePatch(review_target_run_id=next_run_id),
    )
    return None


def handle_clear_action(runtime: ChatActionRuntime) -> None:
    runtime.emit_meta(
        "session defaults cleared",
        session_patch=clear_recent_session_patch(include_defaults=True),
    )
    return None


def handle_set_thinking_action(runtime: ChatActionRuntime) -> None:
    enabled = bool(runtime.parsed.get("enabled"))
    runtime.emit_meta(
        f"thinking: {'on' if enabled else 'off'}",
        session_patch=SessionStatePatch(thinking_enabled=enabled),
    )
    return None


def handle_set_events_action(runtime: ChatActionRuntime) -> None:
    events_mode = str(runtime.parsed.get("events_mode") or "compact")
    runtime.emit_meta(
        f"events: {events_mode}",
        session_patch=SessionStatePatch(events_mode=events_mode),
    )
    return None
