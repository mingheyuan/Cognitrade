from __future__ import annotations

from importlib import import_module

__all__ = [
    "Settings",
    "ensure_workspace_layout",
    "run_checks",
]

_EXPORTS = {
    "Settings": ("research_system.runtime.platform.settings", "Settings"),
    "ensure_workspace_layout": ("research_system.runtime.platform.settings", "ensure_workspace_layout"),
    "run_checks": ("research_system.runtime.platform.checks", "run_checks"),
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
