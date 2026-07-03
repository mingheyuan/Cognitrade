from __future__ import annotations

import unittest

from research_system.runtime.diagnostics import status as diagnostics_status
from research_system.runtime.diagnostics.provider_health import ProviderHealthService
from research_system.runtime.diagnostics.status_runtime import build_runtime_status


class DiagnosticsStatusPackageRuntimeTests(unittest.TestCase):
    def test_status_package_exports(self) -> None:
        self.assertIs(diagnostics_status.ProviderHealthService, ProviderHealthService)
        self.assertIs(diagnostics_status.build_runtime_status, build_runtime_status)


if __name__ == "__main__":
    unittest.main()
