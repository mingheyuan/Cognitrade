from __future__ import annotations

import unittest

import research_system
from research_system import adapters, runtime, services


class RootPackagesRuntimeTests(unittest.TestCase):
    def test_research_system_exports_domain_packages(self) -> None:
        self.assertIs(research_system.adapters, adapters)
        self.assertIs(research_system.runtime, runtime)
        self.assertIs(research_system.services, services)


if __name__ == "__main__":
    unittest.main()
