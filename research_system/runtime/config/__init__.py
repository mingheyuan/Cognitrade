from __future__ import annotations

from importlib import import_module

__all__ = [
    "build_settings_from_env",
    "load_settings_env_values",
    "load_settings_yaml_configs",
]

_EXPORTS = {
    "build_settings_from_env": ("research_system.runtime.config.settings_env", "build_settings_from_env"),
    "load_settings_env_values": ("research_system.runtime.config.settings_env", "load_settings_env_values"),
    "load_settings_yaml_configs": ("research_system.runtime.config.settings_yaml_loader", "load_settings_yaml_configs"),
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
