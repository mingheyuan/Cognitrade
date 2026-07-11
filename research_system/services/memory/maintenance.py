from __future__ import annotations

from threading import Event, Thread
from typing import Any


class MemoryExpirationScheduler:
    """Run governed Memory expiration without owning the Memory source of truth."""

    def __init__(self, memory_store: Any, *, interval_seconds: float = 300.0) -> None:
        if float(interval_seconds) <= 0:
            raise ValueError("memory expiration interval must be positive")
        self.memory_store = memory_store
        self.interval_seconds = float(interval_seconds)
        self._stop = Event()
        self._thread: Thread | None = None

    def run_once(self) -> int:
        return len(self.memory_store.expire_records())

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            name="agent-os-memory-expiration",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, min(self.interval_seconds, 10.0)))
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.run_once()
            except Exception:
                # Memory expiration is maintenance work; the API process must
                # remain available and the next interval retries the operation.
                continue


__all__ = ["MemoryExpirationScheduler"]
