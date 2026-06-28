from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable


def append_jsonl(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=True))
        handle.write("\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(item, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    _replace_with_retry(temp_path, path)


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read().strip()
            if not content:
                return None
            return json.loads(content)
    except json.JSONDecodeError:
        return None


def write_json_array(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(list(rows), handle, ensure_ascii=True, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    _replace_with_retry(temp_path, path)


def _replace_with_retry(temp_path: Path, target_path: Path, retries: int = 5, delay_seconds: float = 0.05) -> None:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            temp_path.replace(target_path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(delay_seconds * (attempt + 1))

    try:
        content = temp_path.read_text(encoding="utf-8")
        with target_path.open("w", encoding="utf-8") as handle:
            handle.write(content)
        temp_path.unlink(missing_ok=True)
        return
    except Exception:
        pass

    if last_error is not None:
        raise last_error
