from __future__ import annotations

import unittest

import research_system
import research_system.subagents as subagents_package
from research_system import adapters, agents, cli, runtime, services, skills, subagents
from research_system.subagents.base import SupportsSubagent
from research_system.subagents.registry import SubagentRegistry


class RootPackagesRuntimeTests(unittest.TestCase):
    def test_research_system_exports_domain_packages(self) -> None:
        self.assertIs(research_system.adapters, adapters)
        self.assertIs(research_system.agents, agents)
        self.assertIs(research_system.cli, cli)
        self.assertIs(research_system.runtime, runtime)
        self.assertIs(research_system.services, services)
        self.assertIs(research_system.skills, skills)
        self.assertIs(research_system.subagents, subagents)

    def test_subagents_package_exports_core_symbols_and_modules(self) -> None:
        self.assertIs(subagents_package.SupportsSubagent, SupportsSubagent)
        self.assertIs(subagents_package.SubagentRegistry, SubagentRegistry)
        self.assertIs(subagents_package.base, subagents.base)
        self.assertIs(subagents_package.collector, subagents.collector)
        self.assertIs(subagents_package.critic_review, subagents.critic_review)
        self.assertIs(subagents_package.distillation, subagents.distillation)
        self.assertIs(subagents_package.reporting, subagents.reporting)
        self.assertIs(subagents_package.retrieval, subagents.retrieval)


if __name__ == "__main__":
    unittest.main()
