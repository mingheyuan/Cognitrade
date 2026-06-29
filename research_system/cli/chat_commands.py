from __future__ import annotations

import shlex

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from research_system.cli.chat_updates import SessionStatePatch
from research_system.cli.chat_types import ChatSessionState, TuiSession


CHAT_COMMANDS = [
    "/commands",
    "/help",
    "/exit",
    "/intent",
    "/run-id",
    "/thinking",
    "/events",
    "/run",
    "/review",
    "/history",
    "/status",
    "/doctor",
    "/check",
    "/replay",
    "/resume",
    "/cancel",
    "/stop",
    "/cleanup",
    "/reindex",
    "/reindex-qdrant",
    "/last",
    "/clear",
]


class ChatCommandCompleter(Completer):
    def __init__(self, session: TuiSession) -> None:
        self.session = session

    def get_completions(self, document: Document, complete_event):
        current_token = chat_completion_current_token(document.text_before_cursor)
        for candidate in chat_completion_candidates(document.text_before_cursor, self.session.state):
            if current_token and not candidate.lower().startswith(current_token.lower()):
                continue
            yield Completion(candidate, start_position=-len(current_token))


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
        if len(parts) != 2 or parts[1] not in {"off", "compact", "full"}:
            return {"action": "error", "message": "usage: /events <off|compact|full>"}
        return {"action": "set_events", "events_mode": parts[1]}
    if command == "/history":
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
    if command in {"/status", "/doctor", "/check", "/cleanup"}:
        return {"action": command.removeprefix("/")}
    if command in {"/reindex", "/reindex-qdrant"}:
        return {"action": "reindex-qdrant"}
    if command in {"/replay", "/resume", "/cancel"}:
        if len(parts) != 2:
            return {"action": "error", "message": f"usage: {command} <run_id|last>"}
        return {"action": command.removeprefix("/"), "run_id": parts[1]}
    if command == "/run":
        query = stripped[len("/run") :].strip()
        if not query:
            return {"action": "error", "message": "usage: /run <query>"}
        return {"action": "run", "query": query}
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


def resolve_chat_run_id(token: str, state: ChatSessionState) -> str | None:
    if token == "last":
        return state.last_run_id
    return token or None


def should_resume_pending_clarification(*, query: str, intent: object, state: ChatSessionState) -> bool:
    return (
        state.pending_clarification_query is not None
        and not str(query).strip().startswith("/")
        and intent is None
    )


def merge_pending_clarification(
    *,
    query: str,
    intent: object,
    run_id: object,
    state: ChatSessionState,
) -> tuple[str, str | None, str | None]:
    merged_query = f"{state.pending_clarification_query}\n补充信息: {query.strip()}"
    merged_intent = str(intent) if isinstance(intent, str) and intent else state.pending_clarification_intent
    merged_run_id = str(run_id) if isinstance(run_id, str) and run_id else state.pending_clarification_run_id
    return merged_query, merged_intent, merged_run_id


def store_pending_clarification(
    *,
    query: str,
    intent: str | None,
    run_id: str | None,
    payload: dict,
) -> SessionStatePatch:
    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        question = clarification.get("question")
        question_text = str(question) if question else None
    else:
        question_text = None
    return SessionStatePatch(
        pending_clarification_query=query,
        pending_clarification_intent=intent,
        pending_clarification_run_id=run_id,
        pending_clarification_question=question_text,
    )


def clear_pending_clarification() -> SessionStatePatch:
    return SessionStatePatch(
        pending_clarification_query=None,
        pending_clarification_intent=None,
        pending_clarification_run_id=None,
        pending_clarification_question=None,
    )


def chat_completion_current_token(text_before_cursor: str) -> str:
    stripped = text_before_cursor.rstrip()
    if not stripped:
        return ""
    if text_before_cursor.endswith(" "):
        return ""
    return stripped.split()[-1]


def chat_completion_candidates(text_before_cursor: str, state: ChatSessionState) -> list[str]:
    stripped = text_before_cursor.lstrip()
    if not stripped.startswith("/"):
        return []
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split()
    if not parts:
        return CHAT_COMMANDS
    command = parts[0].lower()
    if len(parts) == 1 and not text_before_cursor.endswith(" "):
        return CHAT_COMMANDS

    recent_run_ids = [value for value in [state.last_run_id, state.review_target_run_id] if value]
    unique_run_ids: list[str] = []
    for value in ["last", *recent_run_ids]:
        if value and value not in unique_run_ids:
            unique_run_ids.append(value)

    if command == "/intent":
        return ["auto", "stock_check", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis"]
    if command == "/thinking":
        return ["on", "off"]
    if command == "/events":
        return ["off", "compact", "full"]
    if command == "/run-id":
        return ["clear", *recent_run_ids]
    if command in {"/replay", "/resume", "/cancel"}:
        return unique_run_ids
    if command == "/history":
        return ["10", "20", "50", "completed", "failed", "cancelled", "timed_out", "needs_clarification"]
    if command == "/review":
        if len(parts) <= 2:
            return unique_run_ids
        return []
    return []
