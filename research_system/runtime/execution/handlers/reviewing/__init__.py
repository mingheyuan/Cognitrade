from __future__ import annotations

from importlib import import_module

__all__ = [
    "add_review_findings",
    "add_review_summary",
    "collect_review_request_context",
    "collect_screen_review_context",
    "emit_reviewer_completed",
    "emit_reviewer_requested",
    "emit_screen_reviewer_completed",
    "emit_screen_reviewer_requested",
    "extend_reviewer_trace",
    "extend_screen_reviewer_trace",
    "run_reviewer",
    "run_reviewer_flow",
    "run_screen_reviewer",
    "run_screen_reviewer_flow",
]

_EXPORTS = {
    "add_review_findings": (
        "research_system.runtime.execution.handlers.reviewing.review_entries",
        "add_review_findings",
    ),
    "add_review_summary": (
        "research_system.runtime.execution.handlers.reviewing.review_entries",
        "add_review_summary",
    ),
    "collect_review_request_context": (
        "research_system.runtime.execution.handlers.reviewing.review_context",
        "collect_review_request_context",
    ),
    "collect_screen_review_context": (
        "research_system.runtime.execution.handlers.reviewing.review_context",
        "collect_screen_review_context",
    ),
    "emit_reviewer_completed": (
        "research_system.runtime.execution.handlers.reviewing.review_events",
        "emit_reviewer_completed",
    ),
    "emit_reviewer_requested": (
        "research_system.runtime.execution.handlers.reviewing.review_events",
        "emit_reviewer_requested",
    ),
    "emit_screen_reviewer_completed": (
        "research_system.runtime.execution.handlers.reviewing.review_events",
        "emit_screen_reviewer_completed",
    ),
    "emit_screen_reviewer_requested": (
        "research_system.runtime.execution.handlers.reviewing.review_events",
        "emit_screen_reviewer_requested",
    ),
    "extend_reviewer_trace": (
        "research_system.runtime.execution.handlers.reviewing.review_trace",
        "extend_reviewer_trace",
    ),
    "extend_screen_reviewer_trace": (
        "research_system.runtime.execution.handlers.reviewing.review_trace",
        "extend_screen_reviewer_trace",
    ),
    "run_reviewer": (
        "research_system.runtime.execution.handlers.reviewing.review",
        "run_reviewer",
    ),
    "run_reviewer_flow": (
        "research_system.runtime.execution.handlers.reviewing.review_flow",
        "run_reviewer_flow",
    ),
    "run_screen_reviewer": (
        "research_system.runtime.execution.handlers.reviewing.review",
        "run_screen_reviewer",
    ),
    "run_screen_reviewer_flow": (
        "research_system.runtime.execution.handlers.reviewing.review_flow",
        "run_screen_reviewer_flow",
    ),
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
