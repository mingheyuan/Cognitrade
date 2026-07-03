from __future__ import annotations

from typing import Any, Callable

from research_system.services.methodologies.dimensions.dimension_normalizer_defaults import DimensionTaxonomyEntry


def build_dimension_entries(
    taxonomy_config: dict[str, Any] | None,
    *,
    default_entries: list[DimensionTaxonomyEntry],
    normalize_text_fn: Callable[[str], str],
    default_patterns_fn: Callable[[str, tuple[str, ...]], list[str]],
) -> list[DimensionTaxonomyEntry]:
    entries: list[DimensionTaxonomyEntry] = []
    raw_dimensions = list((taxonomy_config or {}).get("dimensions") or [])
    alias_index: dict[str, list[str]] = {}
    pattern_index: dict[str, list[str]] = {}

    for item in list((taxonomy_config or {}).get("aliases") or []):
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
        aliases = [
            str(alias).strip()
            for alias in item.get("aliases") or []
            if str(alias).strip()
        ]
        if canonical_name and aliases:
            alias_index.setdefault(canonical_name, []).extend(aliases)

    for item in list((taxonomy_config or {}).get("patterns") or []):
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
        patterns = [
            str(pattern).strip()
            for pattern in item.get("patterns") or []
            if str(pattern).strip()
        ]
        if canonical_name and patterns:
            pattern_index.setdefault(canonical_name, []).extend(patterns)

    for item in raw_dimensions:
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
        category = str(item.get("category") or "other").strip() or "other"
        if not canonical_name:
            continue
        family = str(item.get("family") or normalize_text_fn(canonical_name).replace(" ", "_") or canonical_name).strip()
        aliases = tuple(
            str(alias).strip()
            for alias in [*(item.get("aliases") or []), *alias_index.get(canonical_name, [])]
            if str(alias).strip()
        )
        patterns = tuple(
            str(pattern).strip()
            for pattern in (
                item.get("patterns")
                or pattern_index.get(canonical_name)
                or default_patterns_fn(canonical_name, aliases)
            )
            if str(pattern).strip()
        )
        scenario_tags = tuple(
            str(tag).strip()
            for tag in item.get("scenario_tags") or []
            if str(tag).strip()
        )
        methodology_tags = tuple(
            str(tag).strip()
            for tag in item.get("methodology_tags") or []
            if str(tag).strip()
        )
        entries.append(
            DimensionTaxonomyEntry(
                canonical_name=canonical_name,
                family=family,
                category=category,
                aliases=aliases,
                patterns=patterns,
                scenario_tags=scenario_tags,
                methodology_tags=methodology_tags,
            )
        )

    seen_families = {entry.family for entry in entries}
    for entry in default_entries:
        if entry.family not in seen_families:
            entries.append(entry)
    return entries


__all__ = ["build_dimension_entries"]
