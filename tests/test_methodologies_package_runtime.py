from __future__ import annotations

import unittest

from research_system.services.methodologies import (
    CashflowResilienceService as PackageCashflowResilienceService,
    CompsService as PackageCompsService,
    DCFService as PackageDCFService,
)
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
from research_system.services.methodologies.planning import (
    PlanningContextView as PackagePlanningContextView,
    build_planning_context_view as package_build_planning_context_view,
)
from research_system.services.methodologies.valuation.cashflow_resilience_service import (
    CashflowResilienceService as PackageCashflowResilienceServiceModule,
)
from research_system.services.methodologies.valuation.comps_service import CompsService as PackageCompsServiceModule
from research_system.services.methodologies.valuation.dcf_service import DCFService as PackageDCFServiceModule
from research_system.services.methodologies.valuation.cashflow_resilience_service import CashflowResilienceService
from research_system.services.methodologies.valuation.comps_service import CompsService
from research_system.services.methodologies.valuation.dcf_service import DCFService
from research_system.services.methodologies.evolver import MethodologyEvolver
from research_system.services.methodologies.planning import PlanningContextView, build_planning_context_view


class MethodologiesPackageRuntimeTests(unittest.TestCase):
    def test_valuation_services_top_level_exports_match_package_exports(self) -> None:
        self.assertIs(CompsService, PackageCompsService)
        self.assertIs(DCFService, PackageDCFService)
        self.assertIs(CashflowResilienceService, PackageCashflowResilienceService)
        self.assertIs(PackageCompsService, PackageCompsServiceModule)
        self.assertIs(PackageDCFService, PackageDCFServiceModule)
        self.assertIs(PackageCashflowResilienceService, PackageCashflowResilienceServiceModule)

    def test_methodology_evolver_top_level_export_matches_package_export(self) -> None:
        self.assertTrue(callable(MethodologyEvolver))

    def test_planning_context_top_level_export_matches_package_export(self) -> None:
        self.assertIs(PlanningContextView, PackagePlanningContextView)
        self.assertIs(build_planning_context_view, package_build_planning_context_view)

    def test_dimension_normalizer_top_level_exports_match_package_exports(self) -> None:
        self.assertTrue(callable(DimensionNormalizer))
        self.assertTrue(callable(build_default_dimension_taxonomy))
        self.assertTrue(callable(build_dimension_entries))
        self.assertTrue(callable(build_dimension_index))
        self.assertTrue(callable(default_dimension_patterns))
        self.assertTrue(callable(merge_text_lists))
        self.assertTrue(callable(normalize_dimension_text))
        self.assertTrue(issubclass(DimensionTaxonomyEntry, object))
        self.assertTrue(issubclass(DimensionNormalizerIndex, object))


if __name__ == "__main__":
    unittest.main()
