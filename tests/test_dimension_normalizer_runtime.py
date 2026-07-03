from __future__ import annotations

import unittest

from research_system.services.dimension_normalizer import DimensionNormalizer
from research_system.services.dimension_normalizer_defaults import (
    DimensionTaxonomyEntry,
    build_default_dimension_taxonomy,
)
from research_system.services.dimension_normalizer_index import (
    DimensionNormalizerIndex,
    build_dimension_entries,
    build_dimension_index,
)
from research_system.services.dimension_normalizer_text import (
    default_dimension_patterns,
    merge_text_lists,
    normalize_dimension_text,
)
from research_system.services.methodologies.dimensions import (
    DimensionNormalizer as PackageDimensionNormalizer,
    DimensionNormalizerIndex as PackageDimensionNormalizerIndex,
    DimensionTaxonomyEntry as PackageDimensionTaxonomyEntry,
    build_default_dimension_taxonomy as package_build_default_dimension_taxonomy,
    build_dimension_entries as package_build_dimension_entries,
    build_dimension_index as package_build_dimension_index,
    default_dimension_patterns as package_default_dimension_patterns,
    merge_text_lists as package_merge_text_lists,
    normalize_dimension_text as package_normalize_dimension_text,
)


class DimensionNormalizerRuntimeTests(unittest.TestCase):
    def test_dimension_normalizer_wires_defaults_text_and_index_support(self) -> None:
        normalizer = DimensionNormalizer()

        self.assertTrue(callable(normalize_dimension_text))
        self.assertTrue(callable(default_dimension_patterns))
        self.assertTrue(callable(merge_text_lists))
        self.assertTrue(callable(build_default_dimension_taxonomy))
        self.assertTrue(callable(build_dimension_entries))
        self.assertTrue(callable(build_dimension_index))
        self.assertTrue(normalizer.entries)
        self.assertIsInstance(normalizer.entries[0], DimensionTaxonomyEntry)
        self.assertIsInstance(normalizer.index, DimensionNormalizerIndex)

    def test_dimensions_package_exports_match_top_level_compat_exports(self) -> None:
        self.assertIs(DimensionNormalizer, PackageDimensionNormalizer)
        self.assertIs(DimensionTaxonomyEntry, PackageDimensionTaxonomyEntry)
        self.assertIs(build_default_dimension_taxonomy, package_build_default_dimension_taxonomy)
        self.assertIs(DimensionNormalizerIndex, PackageDimensionNormalizerIndex)
        self.assertIs(build_dimension_entries, package_build_dimension_entries)
        self.assertIs(build_dimension_index, package_build_dimension_index)
        self.assertIs(default_dimension_patterns, package_default_dimension_patterns)
        self.assertIs(merge_text_lists, package_merge_text_lists)
        self.assertIs(normalize_dimension_text, package_normalize_dimension_text)


if __name__ == "__main__":
    unittest.main()
