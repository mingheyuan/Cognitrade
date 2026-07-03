from __future__ import annotations

import unittest

from research_system.services.dimension_normalizer import DimensionNormalizer
from research_system.services.dimension_normalizer_defaults import DimensionTaxonomyEntry, build_default_dimension_taxonomy
from research_system.services.dimension_normalizer_index import DimensionNormalizerIndex, build_dimension_entries, build_dimension_index
from research_system.services.dimension_normalizer_text import default_dimension_patterns, merge_text_lists, normalize_dimension_text
from research_system.services.methodologies import (
    CashflowResilienceService as PackageCashflowResilienceService,
    CompsService as PackageCompsService,
    DCFService as PackageDCFService,
)
from research_system.services.methodologies.dimension_normalizer import DimensionNormalizer as PackageDimensionNormalizer
from research_system.services.methodologies.dimension_normalizer_defaults import (
    DimensionTaxonomyEntry as PackageDimensionTaxonomyEntry,
    build_default_dimension_taxonomy as package_build_default_dimension_taxonomy,
)
from research_system.services.methodologies.dimension_normalizer_index import (
    DimensionNormalizerIndex as PackageDimensionNormalizerIndex,
    build_dimension_entries as package_build_dimension_entries,
    build_dimension_index as package_build_dimension_index,
)
from research_system.services.methodologies.dimension_normalizer_text import (
    default_dimension_patterns as package_default_dimension_patterns,
    merge_text_lists as package_merge_text_lists,
    normalize_dimension_text as package_normalize_dimension_text,
)
from research_system.services.methodologies.dimensions import (
    DimensionNormalizer as PackageDimensionsNormalizer,
    DimensionNormalizerIndex as PackageDimensionsNormalizerIndex,
    DimensionTaxonomyEntry as PackageDimensionsTaxonomyEntry,
    build_default_dimension_taxonomy as package_dimensions_build_default_dimension_taxonomy,
    build_dimension_entries as package_dimensions_build_dimension_entries,
    build_dimension_index as package_dimensions_build_dimension_index,
    default_dimension_patterns as package_dimensions_default_dimension_patterns,
    merge_text_lists as package_dimensions_merge_text_lists,
    normalize_dimension_text as package_dimensions_normalize_dimension_text,
)
from research_system.services.methodologies.methodology_evolver import MethodologyEvolver as PackageMethodologyEvolver
from research_system.services.methodologies.planning_context import (
    PlanningContextView as PackagePlanningContextView,
    build_planning_context_view as package_build_planning_context_view,
)
from research_system.services.methodologies.valuation.cashflow_resilience_service import (
    CashflowResilienceService as PackageCashflowResilienceServiceModule,
)
from research_system.services.methodologies.valuation.comps_service import CompsService as PackageCompsServiceModule
from research_system.services.methodologies.valuation.dcf_service import DCFService as PackageDCFServiceModule
from research_system.services.methodologies.cashflow_resilience_service import CashflowResilienceService
from research_system.services.methodologies.comps_service import CompsService
from research_system.services.methodologies.dcf_service import DCFService
from research_system.services.methodology_evolver import MethodologyEvolver
from research_system.services.planning_context import PlanningContextView, build_planning_context_view


class MethodologiesPackageRuntimeTests(unittest.TestCase):
    def test_valuation_services_top_level_exports_match_package_exports(self) -> None:
        self.assertIs(CompsService, PackageCompsService)
        self.assertIs(DCFService, PackageDCFService)
        self.assertIs(CashflowResilienceService, PackageCashflowResilienceService)
        self.assertIs(PackageCompsService, PackageCompsServiceModule)
        self.assertIs(PackageDCFService, PackageDCFServiceModule)
        self.assertIs(PackageCashflowResilienceService, PackageCashflowResilienceServiceModule)

    def test_methodology_evolver_top_level_export_matches_package_export(self) -> None:
        self.assertIs(MethodologyEvolver, PackageMethodologyEvolver)

    def test_planning_context_top_level_export_matches_package_export(self) -> None:
        self.assertIs(PlanningContextView, PackagePlanningContextView)
        self.assertIs(build_planning_context_view, package_build_planning_context_view)

    def test_dimension_normalizer_top_level_exports_match_package_exports(self) -> None:
        self.assertIs(DimensionNormalizer, PackageDimensionNormalizer)
        self.assertIs(DimensionTaxonomyEntry, PackageDimensionTaxonomyEntry)
        self.assertIs(build_default_dimension_taxonomy, package_build_default_dimension_taxonomy)
        self.assertIs(DimensionNormalizerIndex, PackageDimensionNormalizerIndex)
        self.assertIs(build_dimension_entries, package_build_dimension_entries)
        self.assertIs(build_dimension_index, package_build_dimension_index)
        self.assertIs(default_dimension_patterns, package_default_dimension_patterns)
        self.assertIs(merge_text_lists, package_merge_text_lists)
        self.assertIs(normalize_dimension_text, package_normalize_dimension_text)
        self.assertIs(PackageDimensionNormalizer, PackageDimensionsNormalizer)
        self.assertIs(PackageDimensionTaxonomyEntry, PackageDimensionsTaxonomyEntry)
        self.assertIs(package_build_default_dimension_taxonomy, package_dimensions_build_default_dimension_taxonomy)
        self.assertIs(PackageDimensionNormalizerIndex, PackageDimensionsNormalizerIndex)
        self.assertIs(package_build_dimension_entries, package_dimensions_build_dimension_entries)
        self.assertIs(package_build_dimension_index, package_dimensions_build_dimension_index)
        self.assertIs(package_default_dimension_patterns, package_dimensions_default_dimension_patterns)
        self.assertIs(package_merge_text_lists, package_dimensions_merge_text_lists)
        self.assertIs(package_normalize_dimension_text, package_dimensions_normalize_dimension_text)


if __name__ == "__main__":
    unittest.main()
