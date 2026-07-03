from __future__ import annotations

from importlib import import_module

__all__ = [
    "FollowUpResolution",
    "is_short_term_memory_query",
    "looks_like_follow_up_research_query",
    "resolve_follow_up_research_request",
    "short_term_memory_reply",
]

_EXPORTS = {
    "FollowUpResolution": ("research_system.cli.follow_up.chat_follow_up_resolution_support", "FollowUpResolution"),
    "is_short_term_memory_query": ("research_system.cli.follow_up.chat_follow_up_markers", "is_short_term_memory_query"),
    "looks_like_follow_up_research_query": (
        "research_system.cli.follow_up.chat_follow_up_markers",
        "looks_like_follow_up_research_query",
    ),
    "resolve_follow_up_research_request": (
        "research_system.cli.follow_up.chat_follow_up_resolution_support",
        "resolve_follow_up_research_request",
    ),
    "short_term_memory_reply": ("research_system.cli.follow_up.chat_follow_up_memory_support", "short_term_memory_reply"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
