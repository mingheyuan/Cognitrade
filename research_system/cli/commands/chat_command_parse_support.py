from __future__ import annotations

import shlex


def parse_chat_input(line: str) -> dict[str, object]:
    stripped = line.strip()
    if not stripped:
        return {"action": "noop"}
    if not stripped.startswith("/"):
        return {"action": "run", "query": stripped}
    try:
        parts = shlex.split(stripped)
    except ValueError as exc:
        return {"action": "error", "message": f"command parse error: {exc}"}
    if not parts:
        return {"action": "noop"}

    command = parts[0].lower()
    if command in {"/exit", "/quit"}:
        return {"action": "exit"}
    if command == "/commands":
        return {"action": "commands"}
    if command == "/help":
        return {"action": "help"}
    if command == "/new":
        return {"action": "new_thread"}
    if command == "/fork":
        return {"action": "fork_thread"}
    if command == "/rename":
        if len(parts) < 2:
            return {"action": "error", "message": "usage: /rename <thread_name>"}
        thread_label = " ".join(part.strip() for part in parts[1:] if part.strip())
        return {"action": "rename_thread", "thread_label": thread_label}
    if command == "/thread-read":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /thread-read <thread_id>"}
        return {"action": "read_thread", "thread_id": parts[1].strip()}
    if command == "/thread-archive":
        if len(parts) == 1:
            return {"action": "archive_thread"}
        if len(parts) == 2:
            return {"action": "archive_thread", "thread_id": parts[1].strip()}
        return {"action": "error", "message": "usage: /thread-archive [thread_id]"}
    if command == "/thread-unarchive":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /thread-unarchive <thread_id>"}
        return {"action": "unarchive_thread", "thread_id": parts[1].strip()}
    if command == "/thread":
        if len(parts) == 1:
            return {"action": "show_thread"}
        if len(parts) == 2:
            return {"action": "set_thread", "thread_id": parts[1].strip()}
        return {"action": "error", "message": "usage: /thread [thread_id]"}
    if command == "/threads":
        return _parse_threads_command(parts)
    if command == "/compact":
        return {"action": "compact_thread"}
    if command == "/last":
        return {"action": "show_last"}
    if command == "/clear":
        return {"action": "clear"}
    if command == "/stop":
        return {"action": "stop"}
    if command == "/intent":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /intent <intent|auto>"}
        value = parts[1].strip()
        return {"action": "set_intent", "intent": None if value == "auto" else value}
    if command == "/run-id":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /run-id <run_id|clear>"}
        value = parts[1].strip()
        return {"action": "set_run_id", "run_id": None if value == "clear" else value}
    if command == "/thinking":
        if len(parts) != 2 or parts[1] not in {"on", "off"}:
            return {"action": "error", "message": "usage: /thinking <on|off>"}
        return {"action": "set_thinking", "enabled": parts[1] == "on"}
    if command == "/events":
        if len(parts) == 2 and parts[1] in {"off", "compact", "full"}:
            return {"action": "set_events", "events_mode": parts[1]}
        if len(parts) == 2:
            return {"action": "events", "run_id": parts[1]}
        return {"action": "error", "message": "usage: /events <off|compact|full> | /events <run_id|last>"}
    if command == "/events-read":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /events-read <run_id|last>"}
        return {"action": "events", "run_id": parts[1]}
    if command == "/metrics":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /metrics <run_id|last>"}
        return {"action": "metrics", "run_id": parts[1]}
    if command == "/run-thread":
        if len(parts) != 3:
            return {"action": "error", "message": "usage: /run-thread <run_id|last> <thread_id>"}
        return {"action": "run-thread", "run_id": parts[1], "thread_id": parts[2]}
    if command == "/subagent-read":
        if len(parts) != 3:
            return {"action": "error", "message": "usage: /subagent-read <run_id|last> <call_id>"}
        return {"action": "subagent-read", "run_id": parts[1], "call_id": parts[2]}
    if command == "/run-steps":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /run-steps <run_id|last>"}
        return {"action": "run-steps", "run_id": parts[1]}
    if command == "/rerun-from":
        if len(parts) != 3:
            return {"action": "error", "message": "usage: /rerun-from <run_id|last> <node_id>"}
        return {"action": "rerun-from", "run_id": parts[1], "node_id": parts[2]}
    if command == "/rerun-subagent":
        if len(parts) != 5:
            return {
                "action": "error",
                "message": "usage: /rerun-subagent <run_id|last> <action_node_id> <dag_id> <subagent_node_id>",
            }
        return {
            "action": "rerun-subagent",
            "run_id": parts[1],
            "action_node_id": parts[2],
            "dag_id": parts[3],
            "subagent_node_id": parts[4],
        }
    if command == "/history":
        return _parse_history_command(parts)
    if command == "/proposals":
        return _parse_proposals_command(parts)
    if command == "/proposal":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /proposal <proposal_id>"}
        return {"action": "proposal", "proposal_id": parts[1].strip()}
    if command == "/review-proposal":
        if len(parts) < 3:
            return {"action": "error", "message": "usage: /review-proposal <proposal_id> <approve|reject> [reason]"}
        decision = parts[2].strip().lower()
        if decision not in {"approve", "reject"}:
            return {"action": "error", "message": "usage: /review-proposal <proposal_id> <approve|reject> [reason]"}
        return {
            "action": "review-proposal",
            "proposal_id": parts[1].strip(),
            "decision": decision,
            "reason": " ".join(parts[3:]).strip() or None,
        }
    if command in {"/status", "/doctor", "/check", "/cleanup"}:
        return {"action": command.removeprefix("/")}
    if command in {"/reindex", "/reindex-qdrant"}:
        return {"action": "reindex-qdrant"}
    if command in {"/replay", "/resume", "/cancel", "/trace"}:
        if len(parts) != 2:
            return {"action": "error", "message": f"usage: {command} <run_id|last>"}
        return {"action": command.removeprefix("/"), "run_id": parts[1]}
    if command == "/run":
        query = stripped[len("/run") :].strip()
        if not query:
            return {"action": "error", "message": "usage: /run <query>"}
        return {"action": "run", "query": query, "explicit_command": True}
    if command == "/review":
        if len(parts) < 3:
            return {"action": "error", "message": "usage: /review <run_id> <query>"}
        return {
            "action": "run",
            "intent": "review_thesis",
            "run_id": parts[1],
            "query": " ".join(parts[2:]).strip(),
        }
    return {"action": "error", "message": f"unknown command: {command}"}


def _parse_threads_command(parts: list[str]) -> dict[str, object]:
    limit = 10
    scope = "active"
    for token in parts[1:]:
        if token.isdigit() and limit == 10:
            limit = int(token)
            continue
        if scope == "active" and token in {"active", "all", "archived"}:
            scope = token
            continue
        return {"action": "error", "message": "usage: /threads [limit] [active|all|archived]"}
    return {"action": "threads", "limit": limit, "scope": scope}


def _parse_history_command(parts: list[str]) -> dict[str, object]:
    limit = 20
    status = None
    for token in parts[1:]:
        if token.isdigit() and limit == 20:
            limit = int(token)
            continue
        if status is None:
            status = token
            continue
        return {"action": "error", "message": "usage: /history [limit] [status]"}
    return {"action": "history", "limit": limit, "status": status}


def _parse_proposals_command(parts: list[str]) -> dict[str, object]:
    limit = 50
    status = None
    for token in parts[1:]:
        if token.isdigit() and limit == 50:
            limit = int(token)
            continue
        if status is None and token in {"pending", "approved", "rejected"}:
            status = token
            continue
        return {"action": "error", "message": "usage: /proposals [limit] [pending|approved|rejected]"}
    return {"action": "proposals", "limit": limit, "status": status}
