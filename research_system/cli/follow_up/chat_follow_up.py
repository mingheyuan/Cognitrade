from __future__ import annotations

from research_system.cli.follow_up.chat_follow_up_markers import (
    is_short_term_memory_query as markers_is_short_term_memory_query,
    looks_like_follow_up_research_query as markers_looks_like_follow_up_research_query,
)
from research_system.cli.follow_up.chat_follow_up_memory_support import (
    short_term_memory_reply as memory_short_term_memory_reply,
)
from research_system.cli.follow_up.chat_follow_up_resolution_support import (
    FollowUpResolution,
    resolve_follow_up_research_request as resolution_resolve_follow_up_research_request,
)


def looks_like_follow_up_research_query(query: str) -> bool:
    return markers_looks_like_follow_up_research_query(query)


def resolve_follow_up_research_request(
    *,
    query: str,
    parsed_intent: str | None,
    parsed_run_id: str | None,
    state,
    conversation_context: list[dict[str, Any]] | None = None,
) -> FollowUpResolution:
    return resolution_resolve_follow_up_research_request(
        query=query,
        parsed_intent=parsed_intent,
        parsed_run_id=parsed_run_id,
        state=state,
        conversation_context=conversation_context,
    )


def is_short_term_memory_query(query: str) -> bool:
    return markers_is_short_term_memory_query(query)


def short_term_memory_reply(state) -> str:
    return memory_short_term_memory_reply(state)
