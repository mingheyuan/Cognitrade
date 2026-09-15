"""Carry the pinned search counter across a stock's isolated source workers.

Only counter storage/locking changes. Native cache hits, allowance checks and
increments still execute their original functions. SQLite transactions release
on child death, unlike a shared semaphore held by a forcibly terminated worker.
This is an ephemeral two-counter file, not a business database or migration.
"""
from __future__ import annotations

import builtins
import importlib
import multiprocessing
import sqlite3
import threading
from collections.abc import Mapping, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from pathlib import Path
from tempfile import TemporaryDirectory
from types import FunctionType, SimpleNamespace
from uuid import uuid4

CURRENT_BUDGET_PATH = ContextVar("uzi_stock_search_budget_path", default=None)
CURRENT_THREAD_BUDGET = ContextVar("uzi_stock_thread_search_budget", default=None)
_KEYS = ("used", "skipped")
_DISPATCH_LOCK = threading.Lock()
_HELPERS = ("_budget_allows", "_budget_mark_used", "_budget_mark_skipped", "get_budget_state")


def native_budget_helper(function):
    """Unwrap only our storage dispatch, not third-party/test decorators."""
    return getattr(function, "_cognitrade_native_budget_helper", function)


class ScopedSearchBudgetClosed(RuntimeError):
    """A timed-out old source may finish, but cannot start another search."""


def install_thread_budget_dispatch(native):
    """Install idempotent storage dispatch without swapping parent counters.

    Already-imported native search aliases look up these helpers at call time.
    Threads without a scoped budget continue to execute the original helper.
    Keep dispatch installed: removing it while timed-out source threads remain
    alive would let them consume the next task's global counter/environment.
    """
    with _DISPATCH_LOCK:
        for name in _HELPERS:
            current = getattr(native, name)
            if hasattr(current, "_cognitrade_native_budget_helper"):
                continue

            def dispatch(*args, _name=name, _original=current, **kwargs):
                budget = CURRENT_THREAD_BUDGET.get()
                return (_original(*args, **kwargs) if budget is None else
                        budget.invoke(_name, _original, *args, **kwargs))

            dispatch._cognitrade_native_budget_helper = current
            setattr(native, name, dispatch)


class ThreadStockSearchBudget:
    """Private storage and fixed cap for one explicit scoped stock wave."""

    def __init__(self, cap):
        self.id = uuid4().hex
        self.cap = int(cap)
        self._state = {"used": 0, "skipped": 0}
        self._lock = threading.RLock()
        self._sealed = False
        self._bound_helpers = {}
        install_thread_budget_dispatch(importlib.import_module("lib.web_search"))

    def invoke(self, name, original, *args, **kwargs):
        with self._lock:
            if self._sealed and name != "get_budget_state":
                # Raising before cached() writes avoids poisoning the native
                # query cache with a late budget-exceeded placeholder.
                raise ScopedSearchBudgetClosed("scoped stock source wave has ended")
            if original not in self._bound_helpers:
                namespace = {**original.__globals__, "_BUDGET_STATE": self._state, "_BUDGET_LOCK": self._lock}
                native_import = builtins.__import__
                scoped_os = SimpleNamespace(environ={"UZI_DDG_BUDGET": str(self.cap)})

                def scoped_import(module, globals=None, locals=None, fromlist=(), level=0):
                    if module == "os" and level == 0:
                        return scoped_os
                    return native_import(module, globals, locals, fromlist, level)

                namespace["__builtins__"] = {**vars(builtins), "__import__": scoped_import}
                bound = FunctionType(original.__code__, namespace, original.__name__, original.__defaults__, original.__closure__)
                bound.__kwdefaults__ = original.__kwdefaults__
                self._bound_helpers[original] = bound
            return self._bound_helpers[original](*args, **kwargs)

    def seal(self):
        with self._lock:
            self._sealed = True

    def receipt(self):
        with self._lock:
            return {"scope_id": self.id, "scope": "stock_collection", "cap": self.cap, **self._state}

    @contextmanager
    def transport(self):
        token = CURRENT_THREAD_BUDGET.set(self)
        try:
            yield
        finally:
            CURRENT_THREAD_BUDGET.reset(token)


def submit_budget_context(executor, function, *args, **kwargs):
    """Propagate only scoped collection calls into nested source executors."""
    if CURRENT_THREAD_BUDGET.get() is None:
        return executor.submit(function, *args, **kwargs)
    return executor.submit(copy_context().run, function, *args, **kwargs)


def normalize_search_budget(receipt):
    """Validate transport metadata without modifying native search semantics."""
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get("scope_id"), str) or not receipt["scope_id"]:
        raise ValueError("invalid search budget scope")
    if receipt.get("scope") not in {"stock_collection", "stock_postfetch"}:
        raise ValueError("invalid search budget scope kind")
    cap = receipt.get("cap")
    if isinstance(cap, bool) or not isinstance(cap, (str, int)):
        raise TypeError("invalid search budget cap")
    cap = int(cap)
    if cap < 0 or any(type(receipt.get(key)) is not int or receipt[key] < 0 for key in _KEYS):
        raise ValueError("invalid search budget counter")
    return {**receipt, "cap": cap}


class NativeCounter(MutableMapping):
    """Native ``_BUDGET_LOCK`` and ``_BUDGET_STATE`` sharing one transaction."""

    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self._local = threading.local()

    def __enter__(self):
        if getattr(self._local, "connection", None) is not None:
            raise RuntimeError("native search budget lock is not reentrant")
        connection = sqlite3.connect(f"file:{self.path}?mode=rw", uri=True, timeout=1)
        try:
            connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            connection.close()
            raise
        self._local.connection = connection
        return self

    def __exit__(self, exc_type, exc, tb):
        connection = self._local.connection
        try:
            connection.rollback() if exc_type else connection.commit()
        finally:
            connection.close()
            del self._local.connection

    def __getitem__(self, key):
        if key not in _KEYS:
            raise KeyError(key)
        row = self._local.connection.execute("SELECT value FROM counters WHERE key=?", (key,)).fetchone()
        return row[0]

    def __setitem__(self, key, value):
        if key not in _KEYS:
            raise KeyError(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError("native search counter must be a nonnegative integer")
        self._local.connection.execute("UPDATE counters SET value=? WHERE key=?", (value, key))

    def __delitem__(self, key):
        raise TypeError("native search counters cannot be removed")

    def __iter__(self):
        return iter(_KEYS)

    def __len__(self):
        return len(_KEYS)

    def read(self):
        with self:
            return dict(self)


class StockSearchBudget:
    """Owned by one ProcessSourceBoundary, closed after all native workers exit."""

    def __init__(self):
        self._directory = TemporaryDirectory(prefix="uzi-stock-search-")
        self.path = str(Path(self._directory.name) / "budget.sqlite3")
        self.id = uuid4().hex
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TABLE counters (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            connection.executemany("INSERT INTO counters VALUES (?, 0)", [(key,) for key in _KEYS])
            connection.commit()
        finally:
            connection.close()

    def read(self):
        return NativeCounter(self.path).read()

    def close(self):
        self._directory.cleanup()

    @contextmanager
    def transport(self):
        token = CURRENT_BUDGET_PATH.set(self.path)
        try:
            yield
        finally:
            CURRENT_BUDGET_PATH.reset(token)


@contextmanager
def bind_native_counter(path):
    """Only invoked inside an isolated source process, never a parent thread."""
    if multiprocessing.parent_process() is None:
        raise RuntimeError("native search counter binding requires an isolated source child")
    native = importlib.import_module("lib.web_search")
    counter = NativeCounter(path)
    previous = native._BUDGET_LOCK, native._BUDGET_STATE
    native._BUDGET_LOCK = native._BUDGET_STATE = counter
    try:
        yield
    finally:
        native._BUDGET_LOCK, native._BUDGET_STATE = previous
