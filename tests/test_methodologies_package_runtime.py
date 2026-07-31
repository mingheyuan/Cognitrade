from __future__ import annotations

import unittest

from research_system.services.methodologies import (
    CashflowResilienceService as PackageCashflowResilienceService,
    CompsService as PackageCompsService,
    DCFService as PackageDCFService,
)
from research_system.services.methodologies.valuation.cashflow_resilience_service import (
    CashflowResilienceService as PackageCashflowResilienceServiceModule,
)
from research_system.services.methodologies.valuation.comps_service import CompsService as PackageCompsServiceModule
from research_system.services.methodologies.valuation.dcf_service import DCFService as PackageDCFServiceModule
from research_system.services.methodologies.valuation.cashflow_resilience_service import CashflowResilienceService
from research_system.services.methodologies.valuation.comps_service import CompsService
from research_system.services.methodologies.valuation.dcf_service import DCFService


class MethodologiesPackageRuntimeTests(unittest.TestCase):
    def test_valuation_services_top_level_exports_match_package_exports(self) -> None:
        self.assertIs(CompsService, PackageCompsService)
        self.assertIs(DCFService, PackageDCFService)
        self.assertIs(CashflowResilienceService, PackageCashflowResilienceService)
        self.assertIs(PackageCompsService, PackageCompsServiceModule)
        self.assertIs(PackageDCFService, PackageDCFServiceModule)
        self.assertIs(PackageCashflowResilienceService, PackageCashflowResilienceServiceModule)

if __name__ == "__main__":
    unittest.main()
