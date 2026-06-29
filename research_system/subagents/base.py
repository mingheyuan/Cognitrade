from __future__ import annotations

from typing import Any, Protocol


class SupportsSubagent(Protocol):
    subagent_type: str

    def run(self, **kwargs: Any) -> dict[str, Any]:
        ...
