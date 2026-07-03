from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "handle_chat_line": ("research_system.cli.flow.chat_controller", "handle_chat_line"),
    "answer_basic_chat": ("research_system.cli.flow.chat_dispatch", "answer_basic_chat"),
    "decide_chat_dispatch": ("research_system.cli.flow.chat_dispatch", "decide_chat_dispatch"),
    "print_streamed_basic_chat_answer": (
        "research_system.cli.flow.chat_dispatch",
        "print_streamed_basic_chat_answer",
    ),
    "prepare_chat_run_request": ("research_system.cli.flow.chat_preprocessor", "prepare_chat_run_request"),
    "PreparedChatRunRequest": ("research_system.cli.flow.chat_preprocessor", "PreparedChatRunRequest"),
    "build_chat_presenter": ("research_system.cli.flow.chat_event_adapter", "build_chat_presenter"),
    "chat_event_listener": ("research_system.cli.flow.chat_event_adapter", "chat_event_listener"),
    "run_chat_research": ("research_system.cli.flow.chat_research_runner", "run_chat_research"),
    "print_chat_result_payload": ("research_system.cli.flow.chat_result", "print_chat_result_payload"),
    "stream_chat_rendered_result": ("research_system.cli.flow.chat_result", "stream_chat_rendered_result"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
