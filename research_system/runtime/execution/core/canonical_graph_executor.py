from __future__ import annotations

from typing import Any


class CanonicalGraphExecutor:
    """Retained type-level compatibility shell for the retired Graph entry."""

    def __init__(self, settings) -> None:
        self.settings = settings

    def execute(self, *, context: Any, request: Any, graph: Any) -> None:
        del context, request, graph
        raise RuntimeError(
            "CanonicalGraphExecutor is deprecated: root execution must enter "
            "AgentOS.handle_request and TurnRunner"
        )


__all__ = ["CanonicalGraphExecutor"]
