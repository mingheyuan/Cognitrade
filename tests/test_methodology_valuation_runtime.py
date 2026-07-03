from __future__ import annotations

import unittest

from research_system.services.methodologies.valuation import (
    CashflowResilienceService,
    CompsService,
    DCFService,
)
from research_system.services.methodologies.valuation.cashflow_resilience_service import (
    CashflowResilienceService as PackageCashflowResilienceService,
)
from research_system.services.methodologies.valuation.comps_service import CompsService as PackageCompsService
from research_system.services.methodologies.valuation.dcf_service import DCFService as PackageDCFService


class MethodologyValuationRuntimeTests(unittest.TestCase):
    def test_valuation_package_exports(self) -> None:
        self.assertIs(CompsService, PackageCompsService)
        self.assertIs(DCFService, PackageDCFService)
        self.assertIs(CashflowResilienceService, PackageCashflowResilienceService)


if __name__ == "__main__":
    unittest.main()
