from __future__ import annotations

import unittest

import research_system.schemas as schemas_package
from research_system import schemas
from research_system.schemas import (
    protocol,
    runtime,
    snapshots,
    subagents,
)


class SchemaTaxonomyPackagesRuntimeTests(unittest.TestCase):
    def test_root_package_exports_schema_modules(self) -> None:
        import research_system

        self.assertIs(research_system.schemas, schemas)

    def test_schemas_package_exports_domain_modules(self) -> None:
        self.assertIs(schemas_package.protocol, protocol)
        self.assertIs(schemas_package.runtime, runtime)
        self.assertIs(schemas_package.snapshots, snapshots)
        self.assertIs(schemas_package.subagents, subagents)


if __name__ == "__main__":
    unittest.main()
