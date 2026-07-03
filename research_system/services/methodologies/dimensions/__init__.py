from __future__ import annotations

from importlib import import_module

__all__ = [
    "DimensionNormalizer",
    "DimensionNormalizerIndex",
    "DimensionTaxonomyEntry",
    "build_default_dimension_taxonomy",
    "build_dimension_entries",
    "build_dimension_index",
    "default_dimension_patterns",
    "merge_text_lists",
    "normalize_dimension_text",
]

_EXPORTS = {
    "DimensionNormalizer": ("research_system.services.methodologies.dimensions.dimension_normalizer", "DimensionNormalizer"),
    "DimensionNormalizerIndex": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_query_support",
        "DimensionNormalizerIndex",
    ),
    "DimensionTaxonomyEntry": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_defaults",
        "DimensionTaxonomyEntry",
    ),
    "build_default_dimension_taxonomy": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_defaults",
        "build_default_dimension_taxonomy",
    ),
    "build_dimension_entries": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_entry_builder",
        "build_dimension_entries",
    ),
    "build_dimension_index": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_index_builder",
        "build_dimension_index",
    ),
    "default_dimension_patterns": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_text",
        "default_dimension_patterns",
    ),
    "merge_text_lists": ("research_system.services.methodologies.dimensions.dimension_normalizer_text", "merge_text_lists"),
    "normalize_dimension_text": (
        "research_system.services.methodologies.dimensions.dimension_normalizer_text",
        "normalize_dimension_text",
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
