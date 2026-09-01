from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterator

from research_system.runtime.storage.jsonl import append_jsonl


_LOCK_GUARD = RLock()
_EVENT_LOCKS: dict[Path, RLock] = {}


def append_canonical_event(path: Path, event: dict) -> dict:
    """Append one event and allocate the next sequence for that Run stream."""
    resolved = Path(path).resolve()
    with _LOCK_GUARD:
        lock = _EVENT_LOCKS.setdefault(resolved, RLock())
    with lock, _file_lock(resolved.with_name(f".{resolved.name}.lock")):
        payload = dict(event)
        # Reading and decoding the entire JSONL stream made append O(N): large
        # Agent OS runs consequently became O(N^2) and spent most of their
        # deadline replaying old events.  The sidecar is the fast path, while
        # the last durable line keeps multi-process and crash recovery honest.
        sequence_path = resolved.with_name(f".{resolved.name}.sequence")
        current = max(
            _read_sequence(sequence_path),
            _last_event_sequence(resolved),
        )
        next_sequence = current + 1
        payload["sequence"] = next_sequence
        append_jsonl(resolved, payload)
        _write_sequence(sequence_path, next_sequence)
        return payload


def _read_sequence(path: Path) -> int:
    try:
        return max(int(path.read_text(encoding="utf-8").strip() or 0), 0)
    except (OSError, TypeError, ValueError):
        return 0


def _write_sequence(path: Path, sequence: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(str(sequence), encoding="utf-8")
    temporary.replace(path)


def _last_event_sequence(path: Path, *, chunk_size: int = 64 * 1024) -> int:
    """Read only the last valid JSONL record instead of replaying the stream."""

    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            pending = b""
            while position > 0:
                size = min(chunk_size, position)
                position -= size
                handle.seek(position)
                pending = handle.read(size) + pending
                lines = pending.split(b"\n")
                candidates = lines if position == 0 else lines[1:]
                for raw in reversed(candidates):
                    if not raw.strip():
                        continue
                    try:
                        payload = json.loads(raw)
                        return max(int(payload.get("sequence") or 0), 0)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                pending = lines[0]
    except OSError:
        return 0
    return 0


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
