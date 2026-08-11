from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
import os
from threading import Lock
from typing import Iterator


_INSTALL_LOCK = Lock()
_UZI_REQUEST_ACTIVE: ContextVar[bool] = ContextVar(
    "cognitrade_uzi_request_active",
    default=False,
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
            if (
                _UZI_REQUEST_ACTIVE.get()
                and ("timeout" not in kwargs or kwargs.get("timeout") is None)
            ):
                kwargs["timeout"] = int(os.environ.get("UZI_HTTP_TIMEOUT", "20"))
            return original_request(session, method, url, **kwargs)

        def context_propagating_submit(executor, function, /, *args, **kwargs):
            if not _UZI_REQUEST_ACTIVE.get():
                return original_submit(executor, function, *args, **kwargs)

            def run_with_uzi_context():
                token = _UZI_REQUEST_ACTIVE.set(True)
                try:
                    return function(*args, **kwargs)
                finally:
                    _UZI_REQUEST_ACTIVE.reset(token)

            return original_submit(executor, run_with_uzi_context)

        requests.sessions.Session.request = scoped_request
        ThreadPoolExecutor.submit = context_propagating_submit
        _INSTALLED = True


@contextmanager
def uzi_request_timeout_scope() -> Iterator[None]:
    """Apply the pinned 20s default only to requests made by this UZI thread."""

    _install_scoped_guard()
    token = _UZI_REQUEST_ACTIVE.set(True)
    try:
        yield
    finally:
        _UZI_REQUEST_ACTIVE.reset(token)


__all__ = ["uzi_request_timeout_scope"]
