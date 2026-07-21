from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from threading import RLock
from typing import Iterator

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl


_LOCK_GUARD = RLock()
_EVENT_LOCKS: dict[Path, RLock] = {}


def append_canonical_event(path: Path, event: dict) -> dict:
    """Append one event and allocate the next sequence for that Run stream."""
    resolved = Path(path).resolve()
    with _LOCK_GUARD:
        lock = _EVENT_LOCKS.setdefault(resolved, RLock())
    with lock:
        with _file_lock(resolved.with_name(f".{resolved.name}.lock")):
            payload = dict(event)
            # Re-read while holding the OS lock. A process-local cache cannot
            # observe events appended by another API worker.
            existing = read_jsonl(resolved)
            current = max(
                (int(item.get("sequence") or 0) for item in existing),
                default=0,
            )
            next_sequence = current + 1
            payload["sequence"] = next_sequence
            append_jsonl(resolved, payload)
            return payload


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Hold an OS-level lock that is released when a worker exits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["append_canonical_event"]
