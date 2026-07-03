from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatPresenterLiveAccumulator",
    "ChatPresenterLivePayloads",
    "ChatPresenterPayloadState",
    "ChatPresenterSummaries",
    "ChatStreamPresenter",
    "DisplayChunk",
    "PresenterState",
]

_EXPORTS = {
    "ChatPresenterLiveAccumulator": (
        "research_system.runtime.presentation.presenter.chat_presenter_live_accumulator",
        "ChatPresenterLiveAccumulator",
    ),
    "ChatPresenterLivePayloads": (
        "research_system.runtime.presentation.presenter.chat_presenter_live_payloads",
        "ChatPresenterLivePayloads",
    ),
    "ChatPresenterPayloadState": (
        "research_system.runtime.presentation.presenter.chat_presenter_payload_state",
        "ChatPresenterPayloadState",
    ),
    "ChatPresenterSummaries": (
        "research_system.runtime.presentation.presenter.chat_presenter_summaries",
        "ChatPresenterSummaries",
    ),
    "ChatStreamPresenter": (
        "research_system.runtime.presentation.presenter.chat_presenter",
        "ChatStreamPresenter",
    ),
    "DisplayChunk": (
        "research_system.runtime.presentation.presenter.chat_presenter",
        "DisplayChunk",
    ),
    "PresenterState": (
        "research_system.runtime.presentation.presenter.chat_presenter_payload_state",
        "PresenterState",
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
