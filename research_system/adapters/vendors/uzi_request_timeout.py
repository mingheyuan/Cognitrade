from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import monotonic

_INSTALL_LOCK = Lock()
_UZI_REQUEST_BUDGET: ContextVar[tuple[float, float | None] | None] = ContextVar(
    "cognitrade_uzi_request_budget",
    default=None,
)
_INSTALLED = False


def _install_scoped_guard() -> None:
    """Install UZI's default requests timeout without changing peer traffic."""

    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        import requests

        original_request = requests.sessions.Session.request
        original_submit = ThreadPoolExecutor.submit

        def scoped_request(session, method, url, **kwargs):
            budget = _UZI_REQUEST_BUDGET.get()
            if budget is not None:
                default_timeout, deadline = budget
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise requests.Timeout("UZI HTTP request scope deadline exceeded")
                configured = kwargs.get("timeout")
                if configured is None:
                    configured = default_timeout
                if remaining is not None and isinstance(configured, (int, float)):
                    configured = min(float(configured), max(remaining, 0.001))
                kwargs["timeout"] = configured
            return original_request(session, method, url, **kwargs)

        def context_propagating_submit(executor, function, /, *args, **kwargs):
            budget = _UZI_REQUEST_BUDGET.get()
            if budget is None:
                return original_submit(executor, function, *args, **kwargs)

            def run_with_uzi_context():
                token = _UZI_REQUEST_BUDGET.set(budget)
                try:
                    return function(*args, **kwargs)
                finally:
                    _UZI_REQUEST_BUDGET.reset(token)

            return original_submit(executor, run_with_uzi_context)

        requests.sessions.Session.request = scoped_request
        ThreadPoolExecutor.submit = context_propagating_submit
        _INSTALLED = True


@contextmanager
def uzi_request_timeout_scope(
    *,
    timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> Iterator[None]:
    """Apply a request timeout and optional aggregate network deadline."""

    _install_scoped_guard()
    request_timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else os.environ.get("UZI_HTTP_TIMEOUT", "20")
    )
    if request_timeout <= 0:
        raise ValueError("UZI HTTP timeout must be positive")
    if total_timeout_seconds is not None and total_timeout_seconds <= 0:
        raise ValueError("UZI HTTP total timeout must be positive")
    deadline = (
        monotonic() + float(total_timeout_seconds)
        if total_timeout_seconds is not None
        else None
    )
    token = _UZI_REQUEST_BUDGET.set((request_timeout, deadline))
    try:
        yield
    finally:
        _UZI_REQUEST_BUDGET.reset(token)


__all__ = ["uzi_request_timeout_scope"]
