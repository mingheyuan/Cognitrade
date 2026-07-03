from __future__ import annotations

from typing import Any

from research_system.cli.chat_memory_match_support import (
    context_signature as support_context_signature,
    extract_query_terms as support_extract_query_terms,
    score_query_match as support_score_query_match,
    trim_context_text as support_trim_context_text,
)
from research_system.cli.chat_memory_recent_support import (
    build_recent_research_context as support_build_recent_research_context,
    context_from_record as support_context_from_record,
    has_recent_chat_memory as support_has_recent_chat_memory,
    load_latest_run_memory as support_load_latest_run_memory,
    state_memory_record as support_state_memory_record,
)
from research_system.cli.chat_memory_relevant_support import (
    build_chat_conversation_context as support_build_chat_conversation_context,
    build_relevant_memory_context as support_build_relevant_memory_context,
)
from research_system.runtime.settings import Settings


def build_recent_research_context(*, settings: Settings, state, limit: int = 1) -> list[dict[str, Any]]:
    return support_build_recent_research_context(settings=settings, state=state, limit=limit)


def build_chat_conversation_context(*, settings: Settings, state, query: str | None = None) -> list[dict[str, Any]]:
    return support_build_chat_conversation_context(settings=settings, state=state, query=query)


def build_relevant_memory_context(
    *,
    settings: Settings,
    state,
    query: str | None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    return support_build_relevant_memory_context(settings=settings, state=state, query=query, limit=limit)


def has_recent_chat_memory(state) -> bool:
    return support_has_recent_chat_memory(state)


def extract_query_terms(query: str) -> set[str]:
    return support_extract_query_terms(query)


def score_query_match(*, query_terms: set[str], blob: str) -> tuple[list[str], float]:
    return support_score_query_match(query_terms=query_terms, blob=blob)


def trim_context_text(text: str, limit: int = 220) -> str:
    return support_trim_context_text(text, limit=limit)


def context_signature(context: dict[str, Any]) -> tuple[Any, ...]:
    return support_context_signature(context)


def load_latest_run_memory(settings: Settings) -> dict[str, Any] | None:
    return support_load_latest_run_memory(settings)


def state_memory_record(state) -> dict[str, Any]:
    return support_state_memory_record(state)


def context_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    return support_context_from_record(record)
