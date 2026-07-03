from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from research_system.services.methodologies.dimensions.dimension_normalizer_defaults import DimensionTaxonomyEntry


@dataclass
class DimensionNormalizerIndex:
    alias_index: dict[str, DimensionTaxonomyEntry]
    patterns: list[tuple[re.Pattern[str], DimensionTaxonomyEntry]]
    normalize_text_fn: Callable[[str], str]

    def find_entry(self, name: str, *, alias_hits: list[str]) -> DimensionTaxonomyEntry | None:
        normalized = self.normalize_text_fn(name)
        if normalized in self.alias_index:
            return self.alias_index[normalized]
        for alias in alias_hits:
            alias_normalized = self.normalize_text_fn(alias)
            if alias_normalized in self.alias_index:
                return self.alias_index[alias_normalized]
        for pattern, entry in self.patterns:
            if pattern.search(name):
                return entry
        return None

    def matched_entries(self, text: str) -> list[DimensionTaxonomyEntry]:
        matched: list[DimensionTaxonomyEntry] = []
        seen: set[str] = set()
        for pattern, entry in self.patterns:
            if pattern.search(text) and entry.family not in seen:
                seen.add(entry.family)
                matched.append(entry)
        return matched

    def extract_dimensions(self, text: str) -> list[tuple[str, str]]:
        if not text.strip():
            return []
        matches: list[tuple[str, str]] = []
        seen: set[str] = set()
        for entry in self.matched_entries(text):
            if entry.family in seen:
                continue
            seen.add(entry.family)
            matches.append((entry.canonical_name, entry.category))
        return matches


__all__ = ["DimensionNormalizerIndex"]
