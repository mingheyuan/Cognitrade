from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "run_chat_tui": ("research_system.cli.shell.chat_shell", "run_chat_tui"),
    "run_chat_fallback": ("research_system.cli.shell.chat_fallback", "run_chat_fallback"),
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
