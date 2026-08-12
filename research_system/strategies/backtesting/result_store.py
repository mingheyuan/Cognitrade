from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredObject:
    uri: str
    content_hash: str
    size_bytes: int


class ContentAddressedResultStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_json(self, payload: dict[str, Any]) -> StoredObject:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        parent = self.root / digest[:2]
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / f"{digest}.json"
        if not destination.exists():
            temporary = parent / f".{digest}.tmp"
            temporary.write_bytes(content)
            temporary.replace(destination)
        return StoredObject(
            uri=str(destination.resolve()),
            content_hash=digest,
            size_bytes=len(content),
        )

    @staticmethod
    def get_json(uri: str) -> dict[str, Any]:
        path = Path(uri)
        return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["ContentAddressedResultStore", "StoredObject"]
