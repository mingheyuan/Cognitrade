from __future__ import annotations

from research_system.cli.runtime.chat_action_runtime import ChatActionRuntime
from research_system.cli.commands.chat_commands import resolve_chat_run_id
from research_system.cli.memory.chat_memory import list_chat_threads
from research_system.cli.state.chat_updates import SessionStatePatch
from research_system.cli.commands.cli_command_payloads import (
    PROPOSAL_COMMANDS,
    RUN_ID_COMMANDS,
    RUN_ID_QUERY_COMMANDS,
    STATUS_FILTER_COMMANDS,
    SUBAGENT_DISCOVERY_COMMANDS,
    SUBAGENT_COMMANDS,
    build_cli_command_payload,
)


def _emit_last_run_patch(runtime: ChatActionRuntime, run_id: str) -> None:
    runtime.emit_meta("", session_patch=SessionStatePatch(last_run_id=run_id))


def _payload_run_id(payload: object, *, fallback: str) -> str:
    if isinstance(payload, dict):
        resolved = str(payload.get("run_id") or "").strip()
        if resolved:
            return resolved
    return fallback


def _resolve_required_run_id(runtime: ChatActionRuntime) -> str | None:
    resolved_run_id = resolve_chat_run_id(str(runtime.parsed.get("run_id") or ""), runtime.state)
    if resolved_run_id:
        return resolved_run_id
    runtime.emit_warning("run_id is required. Use an explicit run_id or `last` after a successful run.")
    return None


def _handle_run_id_cli_action(runtime: ChatActionRuntime) -> bool:
    resolved_run_id = _resolve_required_run_id(runtime)
    if not resolved_run_id:
        return True
    payload = runtime.run_cli_command(
        **build_cli_command_payload(command=str(runtime.action), run_id=resolved_run_id),
    )
    runtime.print_payload(payload)
    _emit_last_run_patch(runtime, _payload_run_id(payload, fallback=resolved_run_id))
    return True


def _handle_threads_cli_action(runtime: ChatActionRuntime) -> bool:
    rows = list_chat_threads(
        settings=runtime.settings,
        limit=int(runtime.parsed.get("limit") or 10),
        scope=str(runtime.parsed.get("scope") or "active"),
    )
    if not rows:
        runtime.emit_meta("No chat threads.")
        return True
    for row in rows:
        thread_id = str(row.get("thread_id") or "unknown")
        turn_count = int(row.get("turn_count") or 0)
        last_turn_at = str(row.get("last_turn_at") or "").strip() or "-"
        topic_title = str(row.get("topic_title") or "").strip()
        thread_label = str(row.get("thread_label") or "").strip()
        topic_intent = str(row.get("topic_intent") or "").strip()
        thread_source = str(row.get("thread_source") or "").strip()
        parent_thread_id = str(row.get("parent_thread_id") or "").strip()
        active_marker = "*" if thread_id == str(getattr(runtime.state, "thread_id", None) or "") else "-"
        name_part = f"  name={thread_label}" if thread_label else ""
        topic_part = f"  topic={topic_title}" if topic_title else ""
        intent_part = f"  intent={topic_intent}" if topic_intent else ""
        source_part = ""
        if thread_source == "fork":
            source_part = f"  source=fork:{parent_thread_id or '?'}"
        archived_part = "  status=archived" if bool(row.get("archived")) else ""
        runtime.emit_meta(
            f"{active_marker} {thread_id}  turns={turn_count}  last={last_turn_at}{name_part}{intent_part}{topic_part}{source_part}{archived_part}"
        )
    return True


def _handle_subagent_cli_action(runtime: ChatActionRuntime) -> bool:
    resolved_run_id = _resolve_required_run_id(runtime)
    if not resolved_run_id:
        return True
    call_id = str(runtime.parsed.get("call_id") or "").strip()
    if not call_id:
        runtime.emit_warning("call_id is required")
        return True
    payload = runtime.run_cli_command(
        **build_cli_command_payload(command="subagent-read", run_id=resolved_run_id, decision=call_id),
    )
    runtime.print_payload(payload)
    _emit_last_run_patch(runtime, _payload_run_id(payload, fallback=resolved_run_id))
    return True


def _handle_run_thread_cli_action(runtime: ChatActionRuntime) -> bool:
    resolved_run_id = _resolve_required_run_id(runtime)
    if not resolved_run_id:
        return True
    thread_id = str(runtime.parsed.get("thread_id") or "").strip()
    if not thread_id:
        runtime.emit_warning("thread_id is required")
        return True
    payload = runtime.run_cli_command(
        **build_cli_command_payload(command="run-thread", run_id=resolved_run_id, query=thread_id),
    )
    runtime.print_payload(payload)
    _emit_last_run_patch(runtime, _payload_run_id(payload, fallback=resolved_run_id))
    return True


def _handle_rerun_from_cli_action(runtime: ChatActionRuntime) -> bool:
    resolved_run_id = _resolve_required_run_id(runtime)
    if not resolved_run_id:
        return True
    node_id = str(runtime.parsed.get("node_id") or "").strip()
    if not node_id:
        runtime.emit_warning("node_id is required")
        return True
    payload = runtime.run_cli_command(
        **build_cli_command_payload(command="rerun-from", run_id=resolved_run_id, query=node_id),
    )
    runtime.print_payload(payload)
    _emit_last_run_patch(runtime, _payload_run_id(payload, fallback=resolved_run_id))
    return True


def _handle_status_like_cli_action(runtime: ChatActionRuntime) -> bool:
    kwargs = build_cli_command_payload(
        command=str(runtime.action),
        limit=int(runtime.parsed.get("limit") or 20),
        status_filter=runtime.parsed.get("status"),
    )
    payload = runtime.run_cli_command(**kwargs)
    runtime.print_payload(payload)
    return True


def _handle_proposals_cli_action(runtime: ChatActionRuntime) -> bool:
    payload = runtime.run_cli_command(
        **build_cli_command_payload(
            command="proposals",
            limit=int(runtime.parsed.get("limit") or 50),
            status=runtime.parsed.get("status"),
        ),
    )
    runtime.print_payload(payload)
    return True


def _handle_proposal_detail_cli_action(runtime: ChatActionRuntime) -> bool:
    payload = runtime.run_cli_command(
        **build_cli_command_payload(
            command="proposal",
            proposal_id=str(runtime.parsed.get("proposal_id") or "").strip(),
        ),
    )
    runtime.print_payload(payload)
    return True


def _handle_review_proposal_cli_action(runtime: ChatActionRuntime) -> bool:
    payload = runtime.run_cli_command(
        **build_cli_command_payload(
            command="review-proposal",
            proposal_id=str(runtime.parsed.get("proposal_id") or "").strip(),
            decision=str(runtime.parsed.get("decision") or "").strip(),
            reviewer="manual_chat_review",
            reason=runtime.parsed.get("reason"),
        ),
    )
    runtime.print_payload(payload)
    return True


def _handle_subagent_discoveries_cli_action(runtime: ChatActionRuntime) -> bool:
    payload = runtime.run_cli_command(
        **build_cli_command_payload(
            command="subagent-discoveries",
            limit=int(runtime.parsed.get("limit") or 50),
            status=runtime.parsed.get("status"),
        ),
    )
    runtime.print_payload(payload)
    return True


def _handle_subagent_discovery_detail_cli_action(runtime: ChatActionRuntime) -> bool:
    payload = runtime.run_cli_command(
        **build_cli_command_payload(
            command="subagent-discovery",
            ticket_id=str(runtime.parsed.get("ticket_id") or "").strip(),
        ),
    )
    runtime.print_payload(payload)
    return True


def _handle_review_subagent_discovery_cli_action(runtime: ChatActionRuntime) -> bool:
    payload = runtime.run_cli_command(
        **build_cli_command_payload(
            command="review-subagent-discovery",
            ticket_id=str(runtime.parsed.get("ticket_id") or "").strip(),
            decision=str(runtime.parsed.get("decision") or "").strip(),
            reviewer="manual_chat_review",
            reason=runtime.parsed.get("reason"),
        ),
    )
    runtime.print_payload(payload)
    return True


def _handle_activate_subagent_discovery_cli_action(runtime: ChatActionRuntime) -> bool:
    payload = runtime.run_cli_command(
        **build_cli_command_payload(
            command="activate-subagent-discovery",
            ticket_id=str(runtime.parsed.get("ticket_id") or "").strip(),
            delegate_subagent_type=str(runtime.parsed.get("delegate_subagent_type") or "").strip(),
            candidate_subagent_type=str(runtime.parsed.get("candidate_subagent_type") or "").strip() or None,
            activated_by="manual_chat_activation",
            registration_template_id=str(runtime.parsed.get("registration_template_id") or "").strip() or None,
            registration_type=str(runtime.parsed.get("registration_type") or "").strip() or None,
            skill_name=str(runtime.parsed.get("skill_name") or "").strip() or None,
            snapshot_type=str(runtime.parsed.get("snapshot_type") or "").strip() or None,
            source_id=str(runtime.parsed.get("source_id") or "").strip() or None,
            tool_name=str(runtime.parsed.get("tool_name") or "").strip() or None,
            endpoint_path=str(runtime.parsed.get("endpoint_path") or "").strip() or None,
            http_method=str(runtime.parsed.get("http_method") or "").strip() or None,
            openapi_spec_uri=str(runtime.parsed.get("openapi_spec_uri") or "").strip() or None,
            openapi_operation_id=str(runtime.parsed.get("openapi_operation_id") or "").strip() or None,
            timeout_seconds=runtime.parsed.get("timeout_seconds"),
        ),
    )
    runtime.print_payload(payload)
    return True


_CLI_ACTION_HANDLERS = {
    "threads": _handle_threads_cli_action,
    "subagent-read": _handle_subagent_cli_action,
    "run-thread": _handle_run_thread_cli_action,
    "run-steps": _handle_status_like_cli_action,
    "rerun-from": _handle_rerun_from_cli_action,
    "proposals": _handle_proposals_cli_action,
    "proposal": _handle_proposal_detail_cli_action,
    "review-proposal": _handle_review_proposal_cli_action,
    "subagent-discoveries": _handle_subagent_discoveries_cli_action,
    "subagent-discovery": _handle_subagent_discovery_detail_cli_action,
    "review-subagent-discovery": _handle_review_subagent_discovery_cli_action,
    "activate-subagent-discovery": _handle_activate_subagent_discovery_cli_action,
}


def handle_chat_cli_action(runtime: ChatActionRuntime) -> bool:
    if runtime.action in RUN_ID_COMMANDS:
        return _handle_run_id_cli_action(runtime)

    if runtime.action in RUN_ID_QUERY_COMMANDS:
        handler = _CLI_ACTION_HANDLERS.get(runtime.action or "")
        if handler is not None:
            return handler(runtime)

    if runtime.action in SUBAGENT_COMMANDS:
        handler = _CLI_ACTION_HANDLERS.get(runtime.action or "")
        if handler is not None:
            return handler(runtime)

    if runtime.action in STATUS_FILTER_COMMANDS:
        return _handle_status_like_cli_action(runtime)

    if runtime.action in PROPOSAL_COMMANDS:
        handler = _CLI_ACTION_HANDLERS.get(runtime.action or "")
        if handler is not None:
            return handler(runtime)

    if runtime.action in SUBAGENT_DISCOVERY_COMMANDS:
        handler = _CLI_ACTION_HANDLERS.get(runtime.action or "")
        if handler is not None:
            return handler(runtime)

    handler = _CLI_ACTION_HANDLERS.get(runtime.action or "")
    if handler is not None:
        return handler(runtime)

    return False
