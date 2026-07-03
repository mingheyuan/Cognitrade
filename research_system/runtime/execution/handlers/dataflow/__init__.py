from __future__ import annotations

from importlib import import_module

__all__ = [
    "add_collector_outputs",
    "add_retrieved_evidence",
    "add_stock_methodology_entries",
    "collect_blackboard_entries_json",
    "handle_collector_wait",
    "run_collector",
    "run_retriever",
]

_EXPORTS = {
    "add_collector_outputs": (
        "research_system.runtime.execution.handlers.dataflow.collector_entries",
        "add_collector_outputs",
    ),
    "add_retrieved_evidence": (
        "research_system.runtime.execution.handlers.dataflow.retriever_entries",
        "add_retrieved_evidence",
    ),
    "add_stock_methodology_entries": (
        "research_system.runtime.execution.handlers.dataflow.collector_entries",
        "add_stock_methodology_entries",
    ),
    "collect_blackboard_entries_json": (
        "research_system.runtime.execution.handlers.dataflow.data_context",
        "collect_blackboard_entries_json",
    ),
    "handle_collector_wait": (
        "research_system.runtime.execution.handlers.dataflow.collector_wait",
        "handle_collector_wait",
    ),
    "run_collector": (
        "research_system.runtime.execution.handlers.dataflow.data",
        "run_collector",
    ),
    "run_retriever": (
        "research_system.runtime.execution.handlers.dataflow.data",
        "run_retriever",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
