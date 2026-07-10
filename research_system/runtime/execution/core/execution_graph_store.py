from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.execution_graph import ExecutionGraph


def persist_execution_graph(*, context: Any, graph: ExecutionGraph) -> ExecutionGraph:
    context.execution_graph = graph.model_copy(deep=True)
    write_json(_execution_graph_path(context.paths), graph.model_dump(mode="json"))
    return context.execution_graph


def _execution_graph_path(paths: Any) -> Path:
    configured = getattr(paths, "execution_graph", None)
    if configured is not None:
        return Path(configured)
    return Path(paths.root) / "execution_graph.json"


__all__ = ["persist_execution_graph"]
