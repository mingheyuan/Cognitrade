from __future__ import annotations

from research_system.services.stock_target_resolution import infer_query_intent_hint

REVIEW_MARKERS = (
    "\u590d\u6838",
    "\u56de\u770b",
    "\u68c0\u67e5\u4e0a\u4e00\u8f6e",
    "\u68c0\u67e5\u4e0a\u6b21",
    "\u9a8c\u8bc1\u4e0a\u4e00\u8f6e",
    "\u8bc4\u4f30\u4e0a\u4e00\u8f6e",
    "\u4e0a\u4e00\u8f6e\u7ed3\u8bba",
    "\u4e0a\u6b21\u7ed3\u8bba",
    "review the previous",
    "review previous",
    "review last run",
    "validate previous",
)
REFERENTIAL_MARKERS = (
    "\u5b83",
    "\u5b83\u4eec",
    "\u8fd9\u53ea",
    "\u8fd9\u4e24\u53ea",
    "\u8fd9\u4e09\u53ea",
    "\u8fd9\u51e0\u53ea",
    "\u90a3\u53ea",
    "\u90a3\u4e24\u53ea",
    "\u90a3\u4e09\u53ea",
    "\u90a3\u51e0\u53ea",
    "\u90a3\u4e24\u4e2a",
    "\u90a3\u4e09\u4e2a",
    "\u524d\u8005",
    "\u540e\u8005",
    "\u4e0a\u4e00\u8f6e",
    "\u4e0a\u6b21",
    "\u4e0a\u4e00\u4e2a\u7ed3\u8bba",
    "\u4e0a\u4e00\u8f6e\u7814\u7a76",
    "\u4e0a\u6b21\u7814\u7a76",
    "that one",
    "those two",
    "those three",
    "previous one",
    "last one",
    "last run",
)
SHORT_CONTINUATION_MARKERS = (
    "\u7ee7\u7eed",
    "\u63a5\u7740",
    "\u518d\u8bf4",
    "\u518d\u5c55\u5f00",
    "\u5c55\u5f00",
    "\u7ec6\u8bf4",
    "\u8865\u5145",
    "\u8be6\u7ec6\u4e00\u70b9",
    "\u8be6\u7ec6\u4e9b",
    "continue",
    "go on",
    "expand",
    "more detail",
    "dig deeper",
)
RELATIVE_COMPARISON_MARKERS = (
    "\u8c01\u66f4",
    "\u54ea\u4e2a\u66f4",
    "\u54ea\u53ea\u66f4",
    "\u66f4\u4fbf\u5b9c",
    "\u66f4\u7a33\u5065",
    "\u66f4\u503c\u5f97",
    "\u600e\u4e48\u6392",
    "\u6392\u4e00\u4e0b",
    "which is better",
    "which one is better",
    "which is cheaper",
    "which is safer",
    "who is more",
    "which is more",
    "rank them",
)
SHORT_TERM_MEMORY_MARKERS = (
    "\u521a\u521a\u6211\u4eec\u5728\u804a\u4ec0\u4e48",
    "\u521a\u521a\u804a\u5230\u54ea\u4e86",
    "\u6211\u4eec\u521a\u521a\u5728\u804a\u4ec0\u4e48",
    "\u4e0a\u4e00\u53e5\u662f\u4ec0\u4e48",
    "\u4e0a\u4e00\u8f6e\u804a\u4ec0\u4e48",
    "what were we just talking about",
    "what did we just discuss",
    "what were we talking about",
    "what was the last topic",
)


def looks_like_follow_up_research_query(query: str) -> bool:
    stripped = query.strip()
    lowered = stripped.lower()
    return any(marker in stripped for marker in REVIEW_MARKERS[:8]) or any(
        marker in lowered for marker in REVIEW_MARKERS[8:]
    ) or is_context_dependent_follow_up(stripped)


def looks_like_review_follow_up(query: str) -> bool:
    lowered = query.lower()
    return any(marker in query for marker in REVIEW_MARKERS[:8]) or any(
        marker in lowered for marker in REVIEW_MARKERS[8:]
    )


def is_context_dependent_follow_up(query: str) -> bool:
    lowered = query.lower()
    stripped = query.strip()
    if has_stock_code(stripped):
        return False
    if contains_referential_marker(stripped, lowered):
        return True
    if contains_short_continuation_marker(stripped, lowered) and len(stripped) <= 10:
        return True
    if contains_relative_comparison_marker(stripped, lowered):
        if len(stripped) <= 18:
            return True
        if stripped.isascii() and len(stripped.split()) <= 6:
            return True
    return False


def contains_referential_marker(query: str, lowered: str) -> bool:
    return any(marker in query for marker in REFERENTIAL_MARKERS[:19]) or any(
        marker in lowered for marker in REFERENTIAL_MARKERS[19:]
    )


def contains_short_continuation_marker(query: str, lowered: str) -> bool:
    return any(marker in query for marker in SHORT_CONTINUATION_MARKERS[:9]) or any(
        marker in lowered for marker in SHORT_CONTINUATION_MARKERS[9:]
    )


def contains_relative_comparison_marker(query: str, lowered: str) -> bool:
    return any(marker in query for marker in RELATIVE_COMPARISON_MARKERS[:8]) or any(
        marker in lowered for marker in RELATIVE_COMPARISON_MARKERS[8:]
    )


def has_stock_code(query: str) -> bool:
    digits = "".join(ch for ch in query if ch.isdigit())
    return len(digits) >= 6


def is_short_term_memory_query(query: str) -> bool:
    stripped = query.strip()
    lowered = stripped.lower()
    return any(marker in stripped for marker in SHORT_TERM_MEMORY_MARKERS[:5]) or any(
        marker in lowered for marker in SHORT_TERM_MEMORY_MARKERS[5:]
    )


def query_intent_hint(query: str) -> str | None:
    stripped = query.strip()
    return infer_query_intent_hint(stripped)
