"""Shared LLM request policy for the UZI research stages."""

from __future__ import annotations

import inspect
from random import random
from threading import BoundedSemaphore, Lock
from time import sleep
from typing import Any

DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS = 480.0
DEFAULT_UZI_LLM_PROFILE_REQUEST_TIMEOUT_SECONDS = {
    "lite": 240.0,
    "medium": 600.0,
    "deep": 900.0,
}
DEFAULT_UZI_LLM_BUY_ZONE_REQUEST_TIMEOUT_SECONDS = 300.0
DEFAULT_UZI_LLM_MAX_CONCURRENCY = 2
DEFAULT_UZI_LLM_TRANSIENT_RETRIES = 6
DEFAULT_UZI_LLM_RETRY_BASE_SECONDS = 2.0
DEFAULT_UZI_LLM_RETRY_MAX_SECONDS = 60.0
_SEMAPHORE_LOCK = Lock()
_SEMAPHORES: dict[int, BoundedSemaphore] = {}


def resolve_uzi_llm_worker_count(provider: Any, task_count: int, *, ceiling: int) -> int:
    """Bound one parallel UZI stage by tasks, stage policy, and provider settings."""
    settings = getattr(provider, "settings", None)
    configured = getattr(settings, "uzi_llm_max_concurrency", ceiling)
    try:
        limit = int(configured)
    except (TypeError, ValueError):
        limit = ceiling
    return max(1, min(task_count or 1, ceiling, max(limit, 1)))


def resolve_uzi_llm_request_timeout(
    provider: Any,
    *,
    research_profile: str | None = None,
    uzi_stage: str | None = None,
) -> float:
    """Resolve the bounded request budget for one UZI model stage."""

    settings = getattr(provider, "settings", None)
    fallback = float(
        getattr(
            settings,
            "uzi_llm_request_timeout_seconds",
            DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS,
        )
    )
    normalized_profile = str(research_profile or "").strip().lower()
    setting_name = {
        "lite": "uzi_llm_lite_request_timeout_seconds",
        "medium": "uzi_llm_medium_request_timeout_seconds",
        "deep": "uzi_llm_deep_request_timeout_seconds",
    }.get(normalized_profile)
    if setting_name is None:
        timeout_seconds = fallback
    else:
        timeout_seconds = float(
            getattr(
                settings,
                setting_name,
                DEFAULT_UZI_LLM_PROFILE_REQUEST_TIMEOUT_SECONDS[normalized_profile],
            )
        )
    if str(uzi_stage or "").startswith("buy_zone_"):
        buy_zone_ceiling = float(
            getattr(
                settings,
                "uzi_llm_buy_zone_request_timeout_seconds",
                DEFAULT_UZI_LLM_BUY_ZONE_REQUEST_TIMEOUT_SECONDS,
            )
        )
        timeout_seconds = min(timeout_seconds, buy_zone_ceiling)
    if timeout_seconds <= 0:
        raise ValueError("UZI LLM request timeout must be positive")
    return timeout_seconds


def parse_uzi_structured(
    provider: Any,
    *,
    research_profile: str | None = None,
    uzi_stage: str | None = None,
    **kwargs: Any,
) -> Any:
    """Run a UZI structured request with its dedicated long-form budget.

    Test and alternative providers are allowed to expose the older ``parse``
    signature.  The timeout override is only supplied when the provider
    explicitly accepts it (or accepts arbitrary keyword arguments).
    """

    parse = provider.parse
    settings = getattr(provider, "settings", None)
    timeout_seconds = resolve_uzi_llm_request_timeout(
        provider,
        research_profile=research_profile,
        uzi_stage=uzi_stage,
    )
    max_concurrency = int(
        getattr(settings, "uzi_llm_max_concurrency", DEFAULT_UZI_LLM_MAX_CONCURRENCY)
    )
    transient_retries = int(
        getattr(
            settings,
            "uzi_llm_transient_retries",
            DEFAULT_UZI_LLM_TRANSIENT_RETRIES,
        )
    )
    retry_base_seconds = float(
        getattr(
            settings,
            "uzi_llm_retry_base_seconds",
            DEFAULT_UZI_LLM_RETRY_BASE_SECONDS,
        )
    )
    retry_max_seconds = float(
        getattr(
            settings,
            "uzi_llm_retry_max_seconds",
            DEFAULT_UZI_LLM_RETRY_MAX_SECONDS,
        )
    )
    if max_concurrency <= 0:
        raise ValueError("uzi_llm_max_concurrency must be positive")
    if transient_retries < 0:
        raise ValueError("uzi_llm_transient_retries must not be negative")
    if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
        raise ValueError(
            "UZI retry delays require 0 < base_seconds <= max_seconds"
        )
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
        # Hidden SDK retries would multiply the stage request budget and make a
        # failed request look like a stalled graph.
        kwargs["max_retries"] = 0
    semaphore = _uzi_semaphore(max_concurrency)
    for attempt in range(transient_retries + 1):
        try:
            with semaphore:
                return parse(**kwargs)
        except Exception as exc:
            if (
                attempt >= transient_retries
                or not _is_transient_upstream_failure(exc)
            ):
                raise
            # Do not occupy a scarce UZI model slot while backing off.  The
            # SDK itself remains max_retries=0 so timeouts are never silently
            # multiplied by the profile-specific request budget.
            delay = min(retry_max_seconds, retry_base_seconds * (2**attempt))
            # Desynchronise concurrent holding workers after a provider-wide
            # overload without obscuring the bounded retry budget.
            jitter = min(delay * 0.25, 5.0) * random()
            sleep(delay + jitter)
    raise AssertionError("unreachable UZI request retry state")


def _is_transient_upstream_failure(exc: Exception) -> bool:
    detail = f"{exc.__class__.__name__}: {exc}".lower()
    if any(
        code in detail
        for code in ("error code: 502", "error code: 503", "error code: 504")
    ):
        return True
    if "timeout" in detail or "timed out" in detail:
        return False
    return any(
        marker in detail
        for marker in (
            "apiconnectionerror",
            "connection error",
            "connection reset",
            "connection aborted",
            "remote disconnected",
            "server disconnected",
            "bad gateway",
            "service unavailable",
            "upstream_error",
            "upstream request failed",
        )
    )


def _uzi_semaphore(limit: int) -> BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        semaphore = _SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = BoundedSemaphore(limit)
            _SEMAPHORES[limit] = semaphore
        return semaphore


__all__ = [
    "DEFAULT_UZI_LLM_BUY_ZONE_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UZI_LLM_MAX_CONCURRENCY",
    "DEFAULT_UZI_LLM_PROFILE_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UZI_LLM_RETRY_BASE_SECONDS",
    "DEFAULT_UZI_LLM_RETRY_MAX_SECONDS",
    "DEFAULT_UZI_LLM_TRANSIENT_RETRIES",
    "parse_uzi_structured",
    "resolve_uzi_llm_request_timeout",
    "resolve_uzi_llm_worker_count",
]
