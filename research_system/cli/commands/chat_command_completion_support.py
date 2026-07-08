from __future__ import annotations

import shlex

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


CHAT_COMMANDS = [
    "/commands",
    "/help",
    "/exit",
    "/new",
    "/fork",
    "/rename",
    "/thread-read",
    "/thread-archive",
    "/thread-unarchive",
    "/thread",
    "/threads",
    "/compact",
    "/intent",
    "/run-id",
    "/thinking",
    "/events",
    "/events-read",
    "/metrics",
    "/run-thread",
    "/subagent-read",
    "/run-steps",
    "/rerun-from",
    "/rerun-subagent",
    "/run",
    "/review",
    "/history",
    "/proposals",
    "/proposal",
    "/review-proposal",
    "/subagent-discoveries",
    "/subagent-discovery",
    "/review-subagent-discovery",
    "/activate-subagent-discovery",
    "/status",
    "/doctor",
    "/check",
    "/replay",
    "/trace",
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
    def __init__(self, session) -> None:
        self.session = session

    def get_completions(self, document: Document, complete_event):
        current_token = chat_completion_current_token(document.text_before_cursor)
        for candidate in chat_completion_candidates(document.text_before_cursor, self.session.state):
            if current_token and not candidate.lower().startswith(current_token.lower()):
                continue
            yield Completion(candidate, start_position=-len(current_token))


def chat_completion_current_token(text_before_cursor: str) -> str:
    stripped = text_before_cursor.rstrip()
    if not stripped:
        return ""
    if text_before_cursor.endswith(" "):
        return ""
    return stripped.split()[-1]


def chat_completion_candidates(text_before_cursor: str, state) -> list[str]:
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
        return ["auto", "stock_check", "stock_compare", "stock_screen", "sector_research", "distill_kol_logic", "review_thesis"]
    if command == "/thinking":
        return ["on", "off"]
    if command == "/events":
        return ["off", "compact", "full", *unique_run_ids]
    if command == "/events-read":
        return unique_run_ids
    if command == "/metrics":
        return unique_run_ids
    if command == "/run-thread":
        return unique_run_ids if len(parts) <= 2 else []
    if command == "/subagent-read":
        return unique_run_ids
    if command == "/run-steps":
        return unique_run_ids
    if command == "/rerun-from":
        return unique_run_ids if len(parts) <= 2 else []
    if command == "/rerun-subagent":
        return unique_run_ids if len(parts) <= 2 else []
    if command == "/run-id":
        return ["clear", *recent_run_ids]
    if command in {"/replay", "/resume", "/cancel", "/trace"}:
        return unique_run_ids
    if command == "/history":
        return ["10", "20", "50", "completed", "failed", "cancelled", "timed_out", "needs_clarification"]
    if command == "/proposals":
        return ["10", "20", "50", "pending", "approved", "rejected"]
    if command == "/proposal":
        return []
    if command == "/review-proposal":
        if len(parts) == 2 and text_before_cursor.endswith(" "):
            return ["approve", "reject"]
        if len(parts) <= 2:
            return []
        if len(parts) == 3:
            return ["approve", "reject"]
        return []
    if command == "/subagent-discoveries":
        return ["10", "20", "50", "pending", "approved", "rejected"]
    if command == "/subagent-discovery":
        return []
    if command == "/review-subagent-discovery":
        if len(parts) == 2 and text_before_cursor.endswith(" "):
            return ["approve", "reject"]
        if len(parts) <= 2:
            return []
        if len(parts) == 3:
            return ["approve", "reject"]
        return []
    if command == "/activate-subagent-discovery":
        return []
    if command == "/thread":
        return []
    if command == "/rename":
        return []
    if command == "/thread-read":
        return []
    if command == "/threads":
        return ["5", "10", "20", "active", "all", "archived"]
    if command == "/compact":
        return []
    if command == "/review":
        if len(parts) <= 2:
            return unique_run_ids
        return []
    return []
