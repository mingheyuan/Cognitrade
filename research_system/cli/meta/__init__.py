from __future__ import annotations

from importlib import import_module

__all__ = [
    "handle_archive_thread_action",
    "handle_clear_action",
    "handle_compact_thread_action",
    "handle_fork_thread_action",
    "handle_new_thread_action",
    "handle_read_thread_action",
    "handle_rename_thread_action",
    "handle_set_events_action",
    "handle_set_intent_action",
    "handle_set_run_id_action",
    "handle_set_thread_action",
    "handle_set_thinking_action",
    "handle_show_last_action",
    "handle_show_thread_action",
    "handle_unarchive_thread_action",
]

_EXPORTS = {
    "handle_archive_thread_action": ("research_system.cli.meta.chat_meta_thread_actions", "handle_archive_thread_action"),
    "handle_clear_action": ("research_system.cli.meta.chat_meta_state_actions", "handle_clear_action"),
    "handle_compact_thread_action": ("research_system.cli.meta.chat_meta_thread_actions", "handle_compact_thread_action"),
    "handle_fork_thread_action": ("research_system.cli.meta.chat_meta_thread_actions", "handle_fork_thread_action"),
    "handle_new_thread_action": ("research_system.cli.meta.chat_meta_thread_actions", "handle_new_thread_action"),
    "handle_read_thread_action": ("research_system.cli.meta.chat_meta_thread_actions", "handle_read_thread_action"),
    "handle_rename_thread_action": ("research_system.cli.meta.chat_meta_thread_actions", "handle_rename_thread_action"),
    "handle_set_events_action": ("research_system.cli.meta.chat_meta_state_actions", "handle_set_events_action"),
    "handle_set_intent_action": ("research_system.cli.meta.chat_meta_state_actions", "handle_set_intent_action"),
    "handle_set_run_id_action": ("research_system.cli.meta.chat_meta_state_actions", "handle_set_run_id_action"),
    "handle_set_thread_action": ("research_system.cli.meta.chat_meta_thread_actions", "handle_set_thread_action"),
    "handle_set_thinking_action": ("research_system.cli.meta.chat_meta_state_actions", "handle_set_thinking_action"),
    "handle_show_last_action": ("research_system.cli.meta.chat_meta_display_actions", "handle_show_last_action"),
    "handle_show_thread_action": ("research_system.cli.meta.chat_meta_display_actions", "handle_show_thread_action"),
    "handle_unarchive_thread_action": (
        "research_system.cli.meta.chat_meta_thread_actions",
        "handle_unarchive_thread_action",
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
