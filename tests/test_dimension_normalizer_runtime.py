from __future__ import annotations

import unittest

from research_system.services.methodologies.dimensions import (
    DimensionNormalizer,
    DimensionNormalizerIndex,
    DimensionTaxonomyEntry,
    build_default_dimension_taxonomy,
    build_dimension_entries,
    build_dimension_index,
    default_dimension_patterns,
    merge_text_lists,
    normalize_dimension_text,
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

if __name__ == "__main__":
    unittest.main()
