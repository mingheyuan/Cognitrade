from __future__ import annotations

import unittest

import research_system.schemas as schemas_package
import research_system.taxonomy as taxonomy_package
from research_system import schemas, taxonomy
from research_system.schemas import (
    distillation,
    proposals,
    protocol,
    retrieval,
    review,
    runtime,
    snapshots,
    subagent_runtime_registration,
    subagent_runtime_registration_hints,
    subagents,
    task_requests,
    valuation,
)
from research_system.taxonomy.loader import load_dimensions


class SchemaTaxonomyPackagesRuntimeTests(unittest.TestCase):
    def test_root_package_exports_schema_and_taxonomy_modules(self) -> None:
        import research_system

        self.assertIs(research_system.schemas, schemas)
        self.assertIs(research_system.taxonomy, taxonomy)

    def test_schemas_package_exports_domain_modules(self) -> None:
        self.assertIs(schemas_package.distillation, distillation)
        self.assertIs(schemas_package.proposals, proposals)
        self.assertIs(schemas_package.protocol, protocol)
        self.assertIs(schemas_package.retrieval, retrieval)
        self.assertIs(schemas_package.review, review)
        self.assertIs(schemas_package.runtime, runtime)
        self.assertIs(schemas_package.snapshots, snapshots)
        self.assertIs(schemas_package.subagent_runtime_registration, subagent_runtime_registration)
        self.assertIs(schemas_package.subagent_runtime_registration_hints, subagent_runtime_registration_hints)
        self.assertIs(schemas_package.subagents, subagents)
        self.assertIs(schemas_package.task_requests, task_requests)
        self.assertIs(schemas_package.valuation, valuation)

    def test_taxonomy_package_exports_loader(self) -> None:
        self.assertIs(taxonomy_package.load_dimensions, load_dimensions)


if __name__ == "__main__":
    unittest.main()
