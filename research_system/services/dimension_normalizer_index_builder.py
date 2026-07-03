from __future__ import annotations

import re
from typing import Callable

from research_system.services.dimension_normalizer_defaults import DimensionTaxonomyEntry
from research_system.services.dimension_normalizer_query_support import DimensionNormalizerIndex


def build_dimension_index(
    entries: list[DimensionTaxonomyEntry],
    *,
    normalize_text_fn: Callable[[str], str],
) -> DimensionNormalizerIndex:
    alias_index: dict[str, DimensionTaxonomyEntry] = {}
    patterns: list[tuple[re.Pattern[str], DimensionTaxonomyEntry]] = []
    for entry in entries:
        for token in (entry.canonical_name, *entry.aliases):
            normalized = normalize_text_fn(token)
            if normalized and normalized not in alias_index:
                alias_index[normalized] = entry
        for pattern in entry.patterns:
            try:
                compiled = re.compile(pattern, flags=re.IGNORECASE)
            except re.error:
                compiled = re.compile(re.escape(pattern), flags=re.IGNORECASE)
            patterns.append((compiled, entry))
    return DimensionNormalizerIndex(
        alias_index=alias_index,
        patterns=patterns,
        normalize_text_fn=normalize_text_fn,
    )


__all__ = ["build_dimension_index"]
