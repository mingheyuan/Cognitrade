from __future__ import annotations

from importlib import import_module

__all__ = [
    "ensure_workspace_layout",
    "required_workspace_directories",
]

_EXPORTS = {
    "ensure_workspace_layout": ("research_system.runtime.workspace.layout", "ensure_workspace_layout"),
    "required_workspace_directories": (
        "research_system.runtime.workspace.layout",
        "required_workspace_directories",
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
