from __future__ import annotations

from research_system.cli.chat_memory import (
    compact_session_thread,
    current_thread_summary,
    has_recent_chat_memory,
    hydrate_session_from_persistent_memory,
    list_chat_threads,
    start_new_session_thread,
    switch_session_thread,
)
from research_system.cli.chat_commands import resolve_chat_run_id
from research_system.cli.cli_command_payloads import PROPOSAL_COMMANDS, RUN_ID_COMMANDS, STATUS_FILTER_COMMANDS, build_cli_command_payload
from research_system.cli.chat_state import (
    build_new_thread_session_patch,
    build_show_last_session_patch,
    clear_recent_session_patch,
    mirror_session_state_patch,
)
from research_system.cli.chat_updates import SessionStatePatch, ShellStatePatch, meta_update, warning_update


def _handle_noop_action(**_: object) -> None:
    return None


def _handle_error_action(*, parsed: dict, emit_text, output_writer=None, **_: object) -> None:
    emit_text(output_writer, meta_update(str(parsed.get("message") or "Invalid command.")))
    return None


def _handle_commands_action(*, emit_text, output_writer=None, print_chat_help_fn=None, **_: object) -> str | None:
    if output_writer is not None:
        emit_text(output_writer, meta_update("commands popup toggled"))
        return "commands_popup"
    if print_chat_help_fn is not None:
        print_chat_help_fn(output_writer=output_writer)
    return None


def _handle_help_action(*, print_chat_help_fn=None, output_writer=None, **_: object) -> None:
    if print_chat_help_fn is not None:
        print_chat_help_fn(output_writer=output_writer)
    return None


def _handle_new_thread_action(*, state, settings, emit_text, output_writer=None, **_: object) -> None:
    thread_id = start_new_session_thread(settings=settings, state=state)
    emit_text(
        output_writer,
        meta_update(
            f"new thread: {thread_id}",
            session_patch=build_new_thread_session_patch(thread_id=thread_id),
        ),
    )
    return None


def _handle_show_thread_action(*, state, settings, emit_text, output_writer=None, **_: object) -> None:
    summary = current_thread_summary(settings=settings, state=state)
    emit_text(output_writer, meta_update(f"thread_id: {summary.get('thread_id') or 'unset'}"))
    emit_text(output_writer, meta_update(f"thread_turns: {summary.get('turn_count') or 0}"))
    emit_text(output_writer, meta_update(f"thread_lifetime_turns: {summary.get('lifetime_turn_count') or 0}"))
    if summary.get("topic_title"):
        emit_text(output_writer, meta_update(f"thread_topic: {summary.get('topic_title')}"))
    if summary.get("topic_intent"):
        emit_text(output_writer, meta_update(f"thread_intent: {summary.get('topic_intent')}"))
    if summary.get("thread_status"):
        emit_text(output_writer, meta_update(f"thread_status: {summary.get('thread_status')}"))
    if summary.get("open_question"):
        emit_text(output_writer, meta_update(f"thread_open_question: {summary.get('open_question')}"))
    if summary.get("recommended_next_step"):
        emit_text(output_writer, meta_update(f"thread_next_step: {summary.get('recommended_next_step')}"))
    emit_text(output_writer, meta_update(f"thread_compacted: {'yes' if summary.get('has_compact_summary') else 'no'}"))
    emit_text(output_writer, meta_update(f"thread_checkpointed: {'yes' if summary.get('has_checkpoint') else 'no'}"))
    return None


def _handle_compact_thread_action(*, state, settings, emit_text, output_writer=None, **_: object) -> None:
    payload = compact_session_thread(settings=settings, state=state)
    source_turn_count = int(payload.get("source_turn_count") or 0)
    emit_text(output_writer, meta_update(f"thread compacted: {state.thread_id or 'unset'}"))
    emit_text(output_writer, meta_update(f"summary_turns: {source_turn_count}"))
    return None


def _handle_exit_action(**_: object) -> str:
    return "exit"


def _handle_set_intent_action(*, parsed: dict, emit_text, output_writer=None, **_: object) -> None:
    next_intent = parsed.get("intent")
    emit_text(
        output_writer,
        meta_update(
            f"intent: {next_intent or 'auto'}",
            session_patch=SessionStatePatch(current_intent=next_intent),
        ),
    )
    return None


def _handle_set_run_id_action(*, parsed: dict, emit_text, output_writer=None, **_: object) -> None:
    next_run_id = parsed.get("run_id")
    emit_text(
        output_writer,
        meta_update(
            f"review_target_run_id: {next_run_id or 'unset'}",
            session_patch=SessionStatePatch(review_target_run_id=next_run_id),
        ),
    )
    return None


def _handle_clear_action(*, emit_text, output_writer=None, **_: object) -> None:
    emit_text(
        output_writer,
        meta_update(
            "session defaults cleared",
            session_patch=clear_recent_session_patch(include_defaults=True),
        ),
    )
    return None


def _handle_set_thinking_action(*, parsed: dict, emit_text, output_writer=None, **_: object) -> None:
    enabled = bool(parsed.get("enabled"))
    emit_text(
        output_writer,
        meta_update(
            f"thinking: {'on' if enabled else 'off'}",
            session_patch=SessionStatePatch(thinking_enabled=enabled),
        ),
    )
    return None


def _handle_set_events_action(*, parsed: dict, emit_text, output_writer=None, **_: object) -> None:
    events_mode = str(parsed.get("events_mode") or "compact")
    emit_text(
        output_writer,
        meta_update(
            f"events: {events_mode}",
            session_patch=SessionStatePatch(events_mode=events_mode),
        ),
    )
    return None


def _handle_set_thread_action(*, parsed: dict, state, settings, emit_text, output_writer=None, **_: object) -> None:
    next_thread_id = str(parsed.get("thread_id") or "").strip()
    if not next_thread_id:
        emit_text(output_writer, warning_update("thread_id is required"))
        return None
    switch_session_thread(settings=settings, state=state, thread_id=next_thread_id)
    emit_text(
        output_writer,
        meta_update(
            f"thread: {next_thread_id}",
            session_patch=mirror_session_state_patch(state, thread_id=next_thread_id),
        ),
    )
    return None


def _handle_stop_action(
    *,
    state,
    settings,
    emit_text,
    cli_command_runner=None,
    payload_printer=None,
    output_writer=None,
    stop_callback=None,
    **_: object,
) -> None:
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


def _handle_show_last_action(*, state, settings, emit_text, output_writer=None, **_: object) -> None:
    if not state.last_run_id and not has_recent_chat_memory(state):
        hydrate_session_from_persistent_memory(settings=settings, state=state)
    run_id = state.last_run_id
    emit_text(
        output_writer,
        meta_update(
            f"last_run_id: {run_id or 'unset'}",
            state_patch=ShellStatePatch(current_run_id=run_id),
            session_patch=build_show_last_session_patch(state),
        ),
    )
    emit_text(output_writer, meta_update(f"intent: {state.current_intent or 'auto'}"))
    emit_text(output_writer, meta_update(f"review_target_run_id: {state.review_target_run_id or 'unset'}"))
    emit_text(output_writer, meta_update(f"thread_id: {state.thread_id or 'unset'}"))
    emit_text(output_writer, meta_update(f"thinking: {'on' if state.thinking_enabled else 'off'}"))
    emit_text(output_writer, meta_update(f"events: {state.events_mode}"))
    return None


_META_ACTION_HANDLERS = {
    "noop": _handle_noop_action,
    "error": _handle_error_action,
    "commands": _handle_commands_action,
    "help": _handle_help_action,
    "new_thread": _handle_new_thread_action,
    "show_thread": _handle_show_thread_action,
    "compact_thread": _handle_compact_thread_action,
    "exit": _handle_exit_action,
    "set_intent": _handle_set_intent_action,
    "set_run_id": _handle_set_run_id_action,
    "clear": _handle_clear_action,
    "set_thinking": _handle_set_thinking_action,
    "set_events": _handle_set_events_action,
    "set_thread": _handle_set_thread_action,
    "stop": _handle_stop_action,
    "show_last": _handle_show_last_action,
}


def _handle_run_id_cli_action(
    *,
    action: str | None,
    state,
    settings,
    parsed: dict,
    emit_text,
    cli_command_runner,
    payload_printer,
    output_writer=None,
) -> bool:
    resolved_run_id = resolve_chat_run_id(str(parsed.get("run_id") or ""), state)
    if not resolved_run_id:
        emit_text(output_writer, warning_update("run_id is required. Use an explicit run_id or `last` after a successful run."))
        return True
    payload = cli_command_runner(
        settings,
        **build_cli_command_payload(command=str(action), run_id=resolved_run_id),
    )
    payload_printer(payload, as_json=False, output_writer=output_writer)
    emit_text(
        output_writer,
        meta_update(
            "",
            session_patch=SessionStatePatch(last_run_id=resolved_run_id),
        ),
    )
    return True


def _handle_threads_cli_action(
    *,
    state,
    settings,
    parsed: dict,
    emit_text,
    output_writer=None,
    **_: object,
) -> bool:
    rows = list_chat_threads(settings=settings, limit=int(parsed.get("limit") or 10))
    if not rows:
        emit_text(output_writer, meta_update("No chat threads."))
        return True
    for row in rows:
        thread_id = str(row.get("thread_id") or "unknown")
        turn_count = int(row.get("turn_count") or 0)
        last_turn_at = str(row.get("last_turn_at") or "").strip() or "-"
        topic_title = str(row.get("topic_title") or "").strip()
        topic_intent = str(row.get("topic_intent") or "").strip()
        active_marker = "*" if thread_id == str(getattr(state, "thread_id", None) or "") else "-"
        topic_part = f"  topic={topic_title}" if topic_title else ""
        intent_part = f"  intent={topic_intent}" if topic_intent else ""
        emit_text(output_writer, meta_update(f"{active_marker} {thread_id}  turns={turn_count}  last={last_turn_at}{intent_part}{topic_part}"))
    return True


def _handle_status_like_cli_action(
    *,
    action: str | None,
    settings,
    parsed: dict,
    cli_command_runner,
    payload_printer,
    output_writer=None,
    **_: object,
) -> bool:
    kwargs = build_cli_command_payload(
        command=str(action),
        limit=int(parsed.get("limit") or 20),
        status_filter=parsed.get("status"),
    )
    payload = cli_command_runner(settings, **kwargs)
    payload_printer(payload, as_json=False, output_writer=output_writer)
    return True


def _handle_proposals_cli_action(
    *,
    settings,
    parsed: dict,
    cli_command_runner,
    payload_printer,
    output_writer=None,
    **_: object,
) -> bool:
    payload = cli_command_runner(
        settings,
        **build_cli_command_payload(
            command="proposals",
            limit=int(parsed.get("limit") or 50),
            status=parsed.get("status"),
        ),
    )
    payload_printer(payload, as_json=False, output_writer=output_writer)
    return True


def _handle_proposal_detail_cli_action(
    *,
    settings,
    parsed: dict,
    cli_command_runner,
    payload_printer,
    output_writer=None,
    **_: object,
) -> bool:
    payload = cli_command_runner(
        settings,
        **build_cli_command_payload(
            command="proposal",
            proposal_id=str(parsed.get("proposal_id") or "").strip(),
        ),
    )
    payload_printer(payload, as_json=False, output_writer=output_writer)
    return True


def _handle_review_proposal_cli_action(
    *,
    settings,
    parsed: dict,
    cli_command_runner,
    payload_printer,
    output_writer=None,
    **_: object,
) -> bool:
    payload = cli_command_runner(
        settings,
        **build_cli_command_payload(
            command="review-proposal",
            proposal_id=str(parsed.get("proposal_id") or "").strip(),
            decision=str(parsed.get("decision") or "").strip(),
            reviewer="manual_chat_review",
            reason=parsed.get("reason"),
        ),
    )
    payload_printer(payload, as_json=False, output_writer=output_writer)
    return True


_CLI_ACTION_HANDLERS = {
    "threads": _handle_threads_cli_action,
    "proposals": _handle_proposals_cli_action,
    "proposal": _handle_proposal_detail_cli_action,
    "review-proposal": _handle_review_proposal_cli_action,
}


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
    shared_handler = _META_ACTION_HANDLERS.get(action or "")
    if shared_handler is not None:
        return shared_handler(
            parsed=parsed,
            state=state,
            settings=settings,
            emit_text=emit_text,
            cli_command_runner=cli_command_runner,
            payload_printer=payload_printer,
            output_writer=output_writer,
            stop_callback=stop_callback,
            print_chat_help_fn=print_chat_help_fn,
        )
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
    if action in RUN_ID_COMMANDS:
        return _handle_run_id_cli_action(
            action=action,
            parsed=parsed,
            state=state,
            settings=settings,
            emit_text=emit_text,
            cli_command_runner=cli_command_runner,
            payload_printer=payload_printer,
            output_writer=output_writer,
        )

    if action in STATUS_FILTER_COMMANDS:
        return _handle_status_like_cli_action(
            action=action,
            parsed=parsed,
            state=state,
            settings=settings,
            emit_text=emit_text,
            cli_command_runner=cli_command_runner,
            payload_printer=payload_printer,
            output_writer=output_writer,
        )

    if action in PROPOSAL_COMMANDS:
        shared_handler = _CLI_ACTION_HANDLERS.get(action or "")
        if shared_handler is not None:
            return shared_handler(
                action=action,
                parsed=parsed,
                state=state,
                settings=settings,
                emit_text=emit_text,
                cli_command_runner=cli_command_runner,
                payload_printer=payload_printer,
                output_writer=output_writer,
            )

    shared_handler = _CLI_ACTION_HANDLERS.get(action or "")
    if shared_handler is not None:
        return shared_handler(
            action=action,
            parsed=parsed,
            state=state,
            settings=settings,
            emit_text=emit_text,
            cli_command_runner=cli_command_runner,
            payload_printer=payload_printer,
            output_writer=output_writer,
        )

    return False
