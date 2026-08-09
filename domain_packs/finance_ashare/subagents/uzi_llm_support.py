"""Shared LLM request policy for the UZI research stages."""

from __future__ import annotations

import inspect
from threading import BoundedSemaphore, Lock
from typing import Any


DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS = 480.0
DEFAULT_UZI_LLM_MAX_CONCURRENCY = 5
_SEMAPHORE_LOCK = Lock()
_SEMAPHORES: dict[int, BoundedSemaphore] = {}


def parse_uzi_structured(provider: Any, **kwargs: Any) -> Any:
    """Run a UZI structured request with its dedicated long-form budget.

    Test and alternative providers are allowed to expose the older ``parse``
    signature.  The timeout override is only supplied when the provider
    explicitly accepts it (or accepts arbitrary keyword arguments).
    """

    parse = provider.parse
    settings = getattr(provider, "settings", None)
    timeout_seconds = float(
        getattr(
            settings,
            "uzi_llm_request_timeout_seconds",
            DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS,
        )
    )
    max_concurrency = int(
        getattr(settings, "uzi_llm_max_concurrency", DEFAULT_UZI_LLM_MAX_CONCURRENCY)
    )
    if max_concurrency <= 0:
        raise ValueError("uzi_llm_max_concurrency must be positive")
    parameters: tuple[inspect.Parameter, ...] = ()
    try:
        parameters = tuple(inspect.signature(parse).parameters.values())
        supports_timeout = any(
            parameter.name == "timeout_seconds"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_timeout = False

    def supports(parameter_name: str) -> bool:
        return any(
            parameter.name == parameter_name
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    if supports_timeout:
        kwargs["timeout_seconds"] = timeout_seconds
    if "reasoning_effort" in kwargs and not supports("reasoning_effort"):
        kwargs.pop("reasoning_effort")
    supports_max_retries = supports("max_retries")
    if supports_max_retries:
        # UZI owns recovery through suggested_fix → recompute → self-review.
        # Hidden SDK retries would multiply the 480s request budget and make a
        # failed request look like a stalled graph.
        kwargs["max_retries"] = 0
    with _uzi_semaphore(max_concurrency):
        return parse(**kwargs)


def _uzi_semaphore(limit: int) -> BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        semaphore = _SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = BoundedSemaphore(limit)
            _SEMAPHORES[limit] = semaphore
        return semaphore


__all__ = [
    "DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UZI_LLM_MAX_CONCURRENCY",
    "parse_uzi_structured",
]
