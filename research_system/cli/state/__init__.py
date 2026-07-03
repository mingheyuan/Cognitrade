from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatSessionState",
    "SESSION_PATCH_UNSET",
    "SessionStatePatch",
    "ShellStatePatch",
    "ShellUiState",
    "ShellViewState",
    "TranscriptUpdate",
    "TuiSession",
    "append_update",
    "apply_session_state_patch_to_state",
    "build_research_result_session_patch",
    "clear_recent_session_patch",
    "default_update_for_text",
    "dispatch_transcript_update",
    "has_session_state_patch",
    "persist_thread_checkpoint",
    "warning_update",
]

_EXPORTS = {
    "ChatSessionState": ("research_system.cli.state.chat_state_models", "ChatSessionState"),
    "SESSION_PATCH_UNSET": ("research_system.cli.state.chat_update_models", "SESSION_PATCH_UNSET"),
    "SessionStatePatch": ("research_system.cli.state.chat_update_models", "SessionStatePatch"),
    "ShellStatePatch": ("research_system.cli.state.chat_update_models", "ShellStatePatch"),
    "ShellUiState": ("research_system.cli.state.chat_state_models", "ShellUiState"),
    "ShellViewState": ("research_system.cli.state.chat_state_models", "ShellViewState"),
    "TranscriptUpdate": ("research_system.cli.state.chat_update_models", "TranscriptUpdate"),
    "TuiSession": ("research_system.cli.state.chat_tui_session", "TuiSession"),
    "append_update": ("research_system.cli.state.chat_update_factories", "append_update"),
    "apply_session_state_patch_to_state": (
        "research_system.cli.state.chat_state",
        "apply_session_state_patch_to_state",
    ),
    "build_research_result_session_patch": (
        "research_system.cli.state.chat_state",
        "build_research_result_session_patch",
    ),
    "clear_recent_session_patch": ("research_system.cli.state.chat_state", "clear_recent_session_patch"),
    "default_update_for_text": ("research_system.cli.state.chat_update_factories", "default_update_for_text"),
    "dispatch_transcript_update": ("research_system.cli.state.chat_update_dispatch", "dispatch_transcript_update"),
    "has_session_state_patch": ("research_system.cli.state.chat_state", "has_session_state_patch"),
    "persist_thread_checkpoint": ("research_system.cli.state.chat_state", "persist_thread_checkpoint"),
    "warning_update": ("research_system.cli.state.chat_update_factories", "warning_update"),
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
