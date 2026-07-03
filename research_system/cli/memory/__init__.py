from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatThreadStore",
    "archive_session_thread",
    "build_chat_conversation_context",
    "build_relevant_memory_context",
    "build_thread_context",
    "current_thread_summary",
    "ensure_session_thread",
    "fork_session_thread",
    "hydrate_session_from_persistent_memory",
    "list_chat_threads",
    "remember_basic_chat_turn",
    "remember_chat_research_result",
    "rename_session_thread",
    "resume_session_thread",
    "start_new_session_thread",
    "switch_session_thread",
    "thread_preview_payload",
    "unarchive_session_thread",
]

_EXPORTS = {
    "ChatThreadStore": ("research_system.cli.memory.chat_thread_store", "ChatThreadStore"),
    "archive_session_thread": ("research_system.cli.memory.chat_memory", "archive_session_thread"),
    "build_chat_conversation_context": ("research_system.cli.memory.chat_memory", "build_chat_conversation_context"),
    "build_relevant_memory_context": ("research_system.cli.memory.chat_memory", "build_relevant_memory_context"),
    "build_thread_context": ("research_system.cli.memory.chat_memory", "build_thread_context"),
    "current_thread_summary": ("research_system.cli.memory.chat_memory", "current_thread_summary"),
    "ensure_session_thread": ("research_system.cli.memory.chat_memory", "ensure_session_thread"),
    "fork_session_thread": ("research_system.cli.memory.chat_memory", "fork_session_thread"),
    "hydrate_session_from_persistent_memory": (
        "research_system.cli.memory.chat_memory",
        "hydrate_session_from_persistent_memory",
    ),
    "list_chat_threads": ("research_system.cli.memory.chat_memory", "list_chat_threads"),
    "remember_basic_chat_turn": ("research_system.cli.memory.chat_memory", "remember_basic_chat_turn"),
    "remember_chat_research_result": ("research_system.cli.memory.chat_memory", "remember_chat_research_result"),
    "rename_session_thread": ("research_system.cli.memory.chat_memory", "rename_session_thread"),
    "resume_session_thread": ("research_system.cli.memory.chat_memory", "resume_session_thread"),
    "start_new_session_thread": ("research_system.cli.memory.chat_memory", "start_new_session_thread"),
    "switch_session_thread": ("research_system.cli.memory.chat_memory", "switch_session_thread"),
    "thread_preview_payload": ("research_system.cli.memory.chat_memory", "thread_preview_payload"),
    "unarchive_session_thread": ("research_system.cli.memory.chat_memory", "unarchive_session_thread"),
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
