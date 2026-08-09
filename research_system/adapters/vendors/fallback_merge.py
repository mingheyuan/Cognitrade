from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any


def merge_provider_facts(
    *,
    primary_facts: dict[str, Any],
    fallback_fact_sets: Iterable[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    merged = deepcopy(primary_facts)
    field_sources: dict[str, str] = dict.fromkeys(primary_facts, "primary")
    fallback_sources: list[str] = []
    missing_fields: list[str] = []

    for source_name, facts in fallback_fact_sets:
        if not facts:
            continue
        fallback_sources.append(source_name)
        for key, value in facts.items():
            if key not in merged or _is_missing_value(merged.get(key)):
                merged[key] = value
                field_sources[key] = source_name

    for key, value in merged.items():
        if _is_missing_value(value):
            missing_fields.append(key)

    merged["_fallback_applied"] = bool(fallback_sources)
    merged["_fallback_sources"] = fallback_sources
    merged["_field_sources"] = field_sources
    merged["_missing_fields"] = missing_fields
    return merged


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if value == "":
        return True
    if isinstance(value, dict):
        return len(value) == 0
    if isinstance(value, list):
        return len(value) == 0
    return False
