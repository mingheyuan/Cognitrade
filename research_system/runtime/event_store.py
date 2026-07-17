from __future__ import annotations

from pathlib import Path
from threading import RLock

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl


_LOCK_GUARD = RLock()
_EVENT_LOCKS: dict[Path, RLock] = {}
_EVENT_SEQUENCES: dict[Path, int] = {}


def append_canonical_event(path: Path, event: dict) -> dict:
    """Append one event and allocate the next sequence for that Run stream."""
    resolved = Path(path).resolve()
    with _LOCK_GUARD:
        lock = _EVENT_LOCKS.setdefault(resolved, RLock())
    with lock:
        payload = dict(event)
        if resolved not in _EVENT_SEQUENCES:
            existing = read_jsonl(resolved)
            _EVENT_SEQUENCES[resolved] = max(
                (int(item.get("sequence") or 0) for item in existing),
                default=0,
            )
        _EVENT_SEQUENCES[resolved] += 1
        payload["sequence"] = _EVENT_SEQUENCES[resolved]
        append_jsonl(resolved, payload)
        return payload


__all__ = ["append_canonical_event"]
