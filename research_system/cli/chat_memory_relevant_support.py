from __future__ import annotations

from typing import Any

from research_system.adapters.storage.mem0_adapter import Mem0Adapter
from research_system.cli.chat_memory_candidate_dedupe import dedupe_relevant_candidates
from research_system.cli.chat_memory_external_candidates import (
    build_cross_thread_memory_candidates,
    build_methodology_memory_candidates,
)
from research_system.cli.chat_memory_match_support import (
    extract_query_terms,
)
from research_system.cli.chat_memory_recent_support import build_recent_research_context
from research_system.cli.chat_memory_thread_candidates import (
    build_archive_candidates,
    build_summary_candidates,
    build_thread_profile_candidates,
)
from research_system.cli.chat_thread_queries import (
    build_thread_context,
    current_thread_summary,
    ensure_session_thread,
)
from research_system.cli.chat_thread_store import ChatThreadStore
from research_system.runtime.settings import Settings
from research_system.services.memory_service import MemoryService

_RELEVANT_MEMORY_LIMIT = 4


def build_chat_conversation_context(*, settings: Settings, state, query: str | None = None) -> list[dict[str, Any]]:
    return [
        *build_thread_context(settings=settings, state=state),
        *build_recent_research_context(settings=settings, state=state),
        *build_relevant_memory_context(settings=settings, state=state, query=query),
    ]


def build_relevant_memory_context(
    *,
    settings: Settings,
    state,
    query: str | None,
    limit: int = _RELEVANT_MEMORY_LIMIT,
) -> list[dict[str, Any]]:
    normalized_query = str(query or "").strip()
    if not normalized_query or limit <= 0:
        return []
    query_terms = extract_query_terms(normalized_query)
    if not query_terms:
        return []

    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    thread_profile = current_thread_summary(settings=settings, state=state)
    memory_service = MemoryService(Mem0Adapter(settings))

    candidates: list[tuple[float, dict[str, Any]]] = []
    candidates.extend(build_thread_profile_candidates(query_terms=query_terms, thread_profile=thread_profile))
    candidates.extend(
        build_summary_candidates(
            store=store,
            thread_id=thread_id,
            query_terms=query_terms,
        )
    )
    candidates.extend(
        build_archive_candidates(
            store=store,
            thread_id=thread_id,
            query_terms=query_terms,
        )
    )
    candidates.extend(
        build_cross_thread_memory_candidates(
            memory_service=memory_service,
            normalized_query=normalized_query,
            thread_id=thread_id,
        )
    )
    candidates.extend(
        build_methodology_memory_candidates(
            memory_service=memory_service,
            normalized_query=normalized_query,
        )
    )

    if not candidates:
        return []
    return dedupe_relevant_candidates(candidates=candidates, limit=limit)
