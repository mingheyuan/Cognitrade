from __future__ import annotations

from research_system.cli.chat_commands import resolve_chat_run_id
from research_system.cli.chat_updates import SessionStatePatch, ShellStatePatch, meta_update, warning_update


def handle_chat_meta_action(
    *,
    action: str | None,
    parsed: dict,
    state,
    settings,
    emit_text,
    cli_command_runner=None,
    payload_printer=None,
    output_writer=None,
    stop_callback=None,
    print_chat_help_fn=None,
) -> str | None:
    if action == "noop":
        return None
    if action == "error":
        emit_text(output_writer, meta_update(str(parsed.get("message") or "Invalid command.")))
        return None
    if action == "commands":
        if output_writer is not None:
            emit_text(output_writer, meta_update("commands popup toggled"))
            return "commands_popup"
        if print_chat_help_fn is not None:
            print_chat_help_fn(output_writer=output_writer)
        return None
    if action == "help":
        if print_chat_help_fn is not None:
            print_chat_help_fn(output_writer=output_writer)
        return None
    if action == "exit":
        return "exit"
    if action == "stop":
        if stop_callback is not None:
            stop_callback()
            emit_text(output_writer, meta_update("stop requested"))
            return None
        if cli_command_runner is None:
            raise RuntimeError("cli_command_runner is required when stop_callback is not provided")
        if payload_printer is None:
            raise RuntimeError("payload_printer is required when stop_callback is not provided")
        resolved_run_id = state.last_run_id
        if not resolved_run_id:
            emit_text(output_writer, warning_update("no active or recent run to stop"))
            return None
        payload = cli_command_runner(settings, command="cancel", run_id=resolved_run_id)
        payload_printer(payload, as_json=False, output_writer=output_writer)
        return None
    if action == "set_intent":
        next_intent = parsed.get("intent")
        emit_text(
            output_writer,
            meta_update(
                f"intent: {next_intent or 'auto'}",
                session_patch=SessionStatePatch(current_intent=next_intent),
            ),
        )
        return None
    if action == "set_run_id":
        next_run_id = parsed.get("run_id")
        emit_text(
            output_writer,
            meta_update(
                f"review_target_run_id: {next_run_id or 'unset'}",
                session_patch=SessionStatePatch(review_target_run_id=next_run_id),
            ),
        )
        return None
    if action == "clear":
        emit_text(
            output_writer,
            meta_update(
                "session defaults cleared",
                session_patch=SessionStatePatch(
                    current_intent=None,
                    review_target_run_id=None,
                    pending_clarification_query=None,
                    pending_clarification_intent=None,
                    pending_clarification_run_id=None,
                    pending_clarification_question=None,
                ),
            ),
        )
        return None
    if action == "show_last":
        run_id = state.last_run_id
        emit_text(
            output_writer,
            meta_update(
                f"last_run_id: {run_id or 'unset'}",
                state_patch=ShellStatePatch(current_run_id=run_id),
                session_patch=SessionStatePatch(
                    last_run_id=run_id,
                    current_intent=state.current_intent,
                    review_target_run_id=state.review_target_run_id,
                    thinking_enabled=state.thinking_enabled,
                    events_mode=state.events_mode,
                ),
            ),
        )
        emit_text(output_writer, meta_update(f"intent: {state.current_intent or 'auto'}"))
        emit_text(output_writer, meta_update(f"review_target_run_id: {state.review_target_run_id or 'unset'}"))
        emit_text(output_writer, meta_update(f"thinking: {'on' if state.thinking_enabled else 'off'}"))
        emit_text(output_writer, meta_update(f"events: {state.events_mode}"))
        return None
    if action == "set_thinking":
        enabled = bool(parsed.get("enabled"))
        emit_text(
            output_writer,
            meta_update(
                f"thinking: {'on' if enabled else 'off'}",
                session_patch=SessionStatePatch(thinking_enabled=enabled),
            ),
        )
        return None
    if action == "set_events":
        events_mode = str(parsed.get("events_mode") or "compact")
        emit_text(
            output_writer,
            meta_update(
                f"events: {events_mode}",
                session_patch=SessionStatePatch(events_mode=events_mode),
            ),
        )
        return None
    return "continue"


def handle_chat_cli_action(
    *,
    action: str | None,
    parsed: dict,
    state,
    settings,
    emit_text,
    cli_command_runner,
    payload_printer,
    output_writer=None,
) -> bool:
    if action in {"replay", "resume", "cancel"}:
        resolved_run_id = resolve_chat_run_id(str(parsed.get("run_id") or ""), state)
        if not resolved_run_id:
            emit_text(output_writer, warning_update("run_id is required. Use an explicit run_id or `last` after a successful run."))
            return True
        payload = cli_command_runner(settings, command=action, run_id=resolved_run_id)
        payload_printer(payload, as_json=False, output_writer=output_writer)
        emit_text(
            output_writer,
            meta_update(
                "",
                session_patch=SessionStatePatch(last_run_id=resolved_run_id),
            ),
        )
        return True

    if action in {"history", "status", "doctor", "check", "cleanup", "reindex-qdrant"}:
        payload = cli_command_runner(
            settings,
            command=action,
            limit=int(parsed.get("limit") or 20),
            status_filter=parsed.get("status"),
        )
        payload_printer(payload, as_json=False, output_writer=output_writer)
        return True

    return False
