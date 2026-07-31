from __future__ import annotations

import unittest

import research_system.adapters as adapters_package
import research_system.runtime as runtime_package
import research_system.services as services_package
from research_system import adapters, runtime, services


class TopLevelPackagesRuntimeTests(unittest.TestCase):
    def test_adapters_package_exports_domain_modules(self) -> None:
        self.assertIs(adapters_package.sources, adapters.sources)
        self.assertIs(adapters_package.storage, adapters.storage)
        self.assertIs(adapters_package.vendors, adapters.vendors)

    def test_runtime_package_exports_domain_modules(self) -> None:
        self.assertIs(runtime_package.config, runtime.config)
        self.assertIs(runtime_package.core, runtime.core)
        self.assertIs(runtime_package.diagnostics, runtime.diagnostics)
        self.assertIs(runtime_package.execution, runtime.execution)
        self.assertIs(runtime_package.planning, runtime.planning)
        self.assertIs(runtime_package.platform, runtime.platform)
        self.assertIs(runtime_package.presentation, runtime.presentation)
        self.assertIs(runtime_package.providers, runtime.providers)
        self.assertIs(runtime_package.storage, runtime.storage)
        self.assertIs(runtime_package.workspace, runtime.workspace)

    def test_services_package_exports_domain_modules(self) -> None:
        self.assertIs(services_package.memory, services.memory)
        self.assertIs(services_package.methodologies, services.methodologies)


if __name__ == "__main__":
    unittest.main()
