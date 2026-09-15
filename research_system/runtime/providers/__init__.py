from __future__ import annotations

from importlib import import_module

__all__ = [
    "CodexProvider",
    "LLMProviderError",
    "LLMUnavailableError",
    "ProviderRegistry",
    "StructuredOutputError",
]

_EXPORTS = {
    "LLMProviderError": ("research_system.runtime.providers.errors", "LLMProviderError"),
    "LLMUnavailableError": ("research_system.runtime.providers.errors", "LLMUnavailableError"),
    "CodexProvider": ("research_system.runtime.providers.codex_provider", "CodexProvider"),
    "ProviderRegistry": ("research_system.runtime.providers.provider_registry", "ProviderRegistry"),
    "StructuredOutputError": ("research_system.runtime.providers.errors", "StructuredOutputError"),
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
