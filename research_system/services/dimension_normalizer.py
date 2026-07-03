from __future__ import annotations

from typing import Any

from research_system.services.dimension_normalizer_defaults import (
    DimensionTaxonomyEntry,
    build_default_dimension_taxonomy,
)
from research_system.services.dimension_normalizer_index import (
    build_dimension_entries,
    build_dimension_index,
)
from research_system.services.dimension_normalizer_text import (
    default_dimension_patterns,
    merge_text_lists,
    normalize_dimension_text,
)


class DimensionNormalizer:
    def __init__(self, taxonomy_config: dict[str, Any] | None = None) -> None:
        self.entries = build_dimension_entries(
            taxonomy_config,
            default_entries=build_default_dimension_taxonomy(),
            normalize_text_fn=normalize_dimension_text,
            default_patterns_fn=default_dimension_patterns,
        )
        self.index = build_dimension_index(self.entries, normalize_text_fn=normalize_dimension_text)

    def extract_dimensions(self, text: str) -> list[tuple[str, str]]:
        return self.index.extract_dimensions(text)

    def normalize_dimension(
        self,
        *,
        name: str,
        category: str | None = None,
        alias_hits: list[str] | None = None,
        normalization_notes: list[str] | None = None,
    ) -> dict[str, Any]:
        entry = self.index.find_entry(name, alias_hits=alias_hits or [])
        notes = [str(item).strip() for item in normalization_notes or [] if str(item).strip()]
        if entry is None:
            canonical_name = name.strip()
            dimension_family = normalize_dimension_text(canonical_name).replace(" ", "_") or "other_dimension"
            if "taxonomy_unmatched" not in notes:
                notes.append("taxonomy_unmatched")
            scenario_tags: list[str] = []
            methodology_tags: list[str] = []
        else:
            canonical_name = entry.canonical_name
            dimension_family = entry.family
            category = entry.category or category
            note = f"taxonomy_family={dimension_family}"
            if note not in notes:
                notes.append(note)
            scenario_tags = list(entry.scenario_tags)
            methodology_tags = list(entry.methodology_tags)
        canonical_category = str(category or "other").strip() or "other"
        dimension_tags = merge_text_lists(
            [canonical_name, dimension_family],
            scenario_tags,
            methodology_tags,
            alias_hits or [],
        )
        return {
            "dimension_name": canonical_name,
            "canonical_dimension_name": canonical_name,
            "dimension_family": dimension_family,
            "dimension_category": canonical_category,
            "canonical_aliases": list(entry.aliases) if entry is not None else [],
            "scenario_tags": scenario_tags,
            "methodology_tags": methodology_tags,
            "dimension_tags": dimension_tags,
            "dimension_key": self.dimension_key(canonical_name, family=dimension_family),
            "normalization_notes": notes,
        }

    def canonical_dimension_name(self, name: str) -> str:
        normalized = self.normalize_dimension(name=name)
        return str(normalized["canonical_dimension_name"])

    def dimension_key(self, name: str, *, family: str | None = None) -> str:
        key = family or normalize_dimension_text(name).replace(" ", "_")
        return key or "other_dimension"

    def normalize_text(self, text: str) -> str:
        return normalize_dimension_text(text)

