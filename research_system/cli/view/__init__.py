from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatViewComponents",
    "CommandsPopupView",
    "HintBarView",
    "ShellSceneView",
    "StatusBarItem",
    "TranscriptOutputArea",
    "TranscriptRenderBlock",
    "build_chat_application",
    "build_chat_view",
    "build_output_blocks",
    "build_shell_scene_view",
    "build_threads_blocks",
    "build_trace_blocks",
    "build_tui_blocks",
    "sync_chat_view_from_scene",
]

_EXPORTS = {
    "ChatViewComponents": ("research_system.cli.view.chat_view_models", "ChatViewComponents"),
    "CommandsPopupView": ("research_system.cli.view.chat_scene_models", "CommandsPopupView"),
    "HintBarView": ("research_system.cli.view.chat_scene_models", "HintBarView"),
    "ShellSceneView": ("research_system.cli.view.chat_scene_models", "ShellSceneView"),
    "StatusBarItem": ("research_system.cli.view.chat_scene_models", "StatusBarItem"),
    "TranscriptOutputArea": ("research_system.cli.view.chat_output_area", "TranscriptOutputArea"),
    "TranscriptRenderBlock": ("research_system.cli.view.chat_scene_models", "TranscriptRenderBlock"),
    "build_chat_application": ("research_system.cli.view.chat_view", "build_chat_application"),
    "build_chat_view": ("research_system.cli.view.chat_view", "build_chat_view"),
    "build_output_blocks": ("research_system.cli.view.chat_scene", "build_output_blocks"),
    "build_shell_scene_view": ("research_system.cli.view.chat_scene", "build_shell_scene_view"),
    "build_threads_blocks": ("research_system.cli.view.chat_scene", "build_threads_blocks"),
    "build_trace_blocks": ("research_system.cli.view.chat_trace_scene", "build_trace_blocks"),
    "build_tui_blocks": ("research_system.cli.view.chat_scene", "build_tui_blocks"),
    "sync_chat_view_from_scene": ("research_system.cli.view.chat_view", "sync_chat_view_from_scene"),
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
