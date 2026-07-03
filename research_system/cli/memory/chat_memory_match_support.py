from __future__ import annotations

import re
from typing import Any

_ENGLISH_QUERY_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "better",
    "compare",
    "continue",
    "for",
    "from",
    "how",
    "into",
    "just",
    "last",
    "more",
    "previous",
    "review",
    "than",
    "that",
    "the",
    "they",
    "this",
    "those",
    "what",
    "which",
    "who",
    "with",
}


def extract_query_terms(query: str) -> set[str]:
    terms: set[str] = set()
    lowered = query.lower()
    for token in re.findall(r"[a-z0-9]{2,}", lowered):
        if token not in _ENGLISH_QUERY_STOPWORDS:
            terms.add(token)
    for token in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        terms.add(token)
        if len(token) <= 8:
            for index in range(0, len(token) - 1):
                terms.add(token[index:index + 2])
    return {term for term in terms if term.strip()}


def score_query_match(*, query_terms: set[str], blob: str) -> tuple[list[str], float]:
    if not query_terms:
        return [], 0.0
    lowered_blob = blob.lower()
    matched_terms: list[str] = []
    score = 0.0
    for term in sorted(query_terms):
        probe = term.lower()
        if probe not in lowered_blob:
            continue
        matched_terms.append(term)
        if len(term) >= 8:
            score += 3.0
        elif len(term) >= 4:
            score += 2.0
        else:
            score += 1.0
    return matched_terms, score


def trim_context_text(text: str, limit: int = 220) -> str:
    compact = text.replace("\n", " ").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def context_signature(context: dict[str, Any]) -> tuple[Any, ...]:
    return (
        context.get("type"),
        context.get("thread_id"),
        context.get("run_id"),
        context.get("memory_type"),
        context.get("title"),
        context.get("summary"),
        context.get("content"),
        tuple(context.get("matched_terms") or []),
    )
