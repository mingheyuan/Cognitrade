from __future__ import annotations

from importlib import import_module

__all__ = [
    "CheckpointStore",
    "RunPaths",
    "RunRetentionService",
    "RunStore",
    "append_jsonl",
    "read_json",
    "read_jsonl",
    "write_json",
    "write_json_array",
]

_EXPORTS = {
    "CheckpointStore": ("research_system.runtime.storage.runs.checkpoint_store", "CheckpointStore"),
    "RunPaths": ("research_system.runtime.storage.runs.run_store", "RunPaths"),
    "RunRetentionService": ("research_system.runtime.storage.runs.retention", "RunRetentionService"),
    "RunStore": ("research_system.runtime.storage.runs.run_store", "RunStore"),
    "append_jsonl": ("research_system.runtime.storage.jsonl", "append_jsonl"),
    "read_json": ("research_system.runtime.storage.jsonl", "read_json"),
    "read_jsonl": ("research_system.runtime.storage.jsonl", "read_jsonl"),
    "write_json": ("research_system.runtime.storage.jsonl", "write_json"),
    "write_json_array": ("research_system.runtime.storage.jsonl", "write_json_array"),
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
