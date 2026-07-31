from __future__ import annotations

from importlib import import_module

__all__ = [
    "config",
    "core",
    "diagnostics",
    "domain_packs",
    "execution",
    "planning",
    "platform",
    "presentation",
    "providers",
    "storage",
    "workspace",
    "agent_os",
]

_EXPORTS = {name: f"research_system.runtime.{name}" for name in __all__}


def __getattr__(name: str):
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = import_module(module_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
