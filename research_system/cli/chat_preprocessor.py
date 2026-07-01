from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.cli.chat_commands import (
    merge_pending_clarification,
    should_resume_pending_clarification,
)
from research_system.cli.chat_follow_up import (
    is_short_term_memory_query,
    looks_like_follow_up_research_query,
    resolve_follow_up_research_request,
    short_term_memory_reply,
)
from research_system.cli.chat_memory import (
    build_chat_conversation_context,
    ensure_session_thread,
    hydrate_session_from_persistent_memory,
)


@dataclass(frozen=True)
class PreparedChatRunRequest:
    query: str
    intent: str | None
    run_id: str | None
    conversation_context: list[dict[str, Any]]
    explicit_run_request: bool
    memory_reply: str | None = None


def prepare_chat_run_request(
    *,
    parsed: dict,
    state,
    settings,
    conversation_context: list[dict[str, Any]] | None = None,
) -> PreparedChatRunRequest:
    ensure_session_thread(settings=settings, state=state)
    resolved_context = list(
        conversation_context or build_chat_conversation_context(settings=settings, state=state, query=str(parsed.get("query") or ""))
    )
    explicit_intent = parsed.get("intent")
    intent = explicit_intent or state.current_intent
    run_id = parsed.get("run_id")
    query = str(parsed.get("query") or "")

    if is_short_term_memory_query(query):
        if not state.last_user_query and not state.last_result_summary and not state.last_result_conclusion:
            hydrate_session_from_persistent_memory(settings=settings, state=state)
        return PreparedChatRunRequest(
            query=query,
            intent=intent,
            run_id=run_id,
            conversation_context=resolved_context,
            explicit_run_request=bool(parsed.get("intent") or parsed.get("run_id") or parsed.get("explicit_command")),
            memory_reply=short_term_memory_reply(state),
        )

    if should_resume_pending_clarification(query=query, intent=intent, state=state):
        query, intent, run_id = merge_pending_clarification(query=query, intent=intent, run_id=run_id, state=state)

    should_hydrate_follow_up = looks_like_follow_up_research_query(query) and not (
        state.last_user_query or state.last_result_summary or state.last_result_conclusion
    )
    if should_hydrate_follow_up and not (explicit_intent is None and state.current_intent == "review_thesis"):
        hydrate_session_from_persistent_memory(settings=settings, state=state)
        resolved_context = list(
            conversation_context or build_chat_conversation_context(settings=settings, state=state, query=query)
        )

    follow_up = resolve_follow_up_research_request(
        query=query,
        parsed_intent=explicit_intent,
        parsed_run_id=str(run_id) if run_id else None,
        state=state,
        conversation_context=resolved_context,
    )

    resolved_query = follow_up.query
    if resolved_query != query and not conversation_context:
        resolved_context = list(build_chat_conversation_context(settings=settings, state=state, query=resolved_query))

    return PreparedChatRunRequest(
        query=resolved_query,
        intent=follow_up.intent or intent,
        run_id=follow_up.run_id or run_id,
        conversation_context=resolved_context,
        explicit_run_request=bool(parsed.get("intent") or parsed.get("run_id") or parsed.get("explicit_command")),
    )
