"""Shared LLM request policy for the UZI research stages."""

from __future__ import annotations

import inspect
from typing import Any


DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS = 480.0


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
    try:
        parameters = inspect.signature(parse).parameters.values()
        supports_timeout = any(
            parameter.name == "timeout_seconds"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_timeout = False
    if supports_timeout:
        kwargs["timeout_seconds"] = timeout_seconds
    return parse(**kwargs)


__all__ = [
    "DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS",
    "parse_uzi_structured",
]
