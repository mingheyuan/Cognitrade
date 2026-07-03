from __future__ import annotations

import unittest

import research_system.adapters as adapters_package
import research_system.agents as agents_package
import research_system.cli as cli_package
import research_system.runtime as runtime_package
import research_system.services as services_package
from research_system import adapters, agents, cli, runtime, services


class TopLevelPackagesRuntimeTests(unittest.TestCase):
    def test_agents_package_exports_domain_modules(self) -> None:
        self.assertIs(agents_package.collector, agents.collector)
        self.assertIs(agents_package.critic, agents.critic)
        self.assertIs(agents_package.distiller, agents.distiller)
        self.assertIs(agents_package.reporter, agents.reporter)
        self.assertIs(agents_package.retriever, agents.retriever)
        self.assertIs(agents_package.reviewer, agents.reviewer)
        self.assertIs(agents_package.screen_reviewer, agents.screen_reviewer)
        self.assertIs(agents_package.screener, agents.screener)

    def test_adapters_package_exports_domain_modules(self) -> None:
        self.assertIs(adapters_package.sources, adapters.sources)
        self.assertIs(adapters_package.storage, adapters.storage)
        self.assertIs(adapters_package.vendors, adapters.vendors)

    def test_cli_package_exports_domain_modules(self) -> None:
        self.assertIs(cli_package.application, cli.application)
        self.assertIs(cli_package.commands, cli.commands)
        self.assertIs(cli_package.flow, cli.flow)
        self.assertIs(cli_package.follow_up, cli.follow_up)
        self.assertIs(cli_package.inspect, cli.inspect)
        self.assertIs(cli_package.interactions, cli.interactions)
        self.assertIs(cli_package.keybindings, cli.keybindings)
        self.assertIs(cli_package.memory, cli.memory)
        self.assertIs(cli_package.meta, cli.meta)
        self.assertIs(cli_package.payloads, cli.payloads)
        self.assertIs(cli_package.rendering, cli.rendering)
        self.assertIs(cli_package.runtime, cli.runtime)
        self.assertIs(cli_package.shell, cli.shell)
        self.assertIs(cli_package.state, cli.state)
        self.assertIs(cli_package.transcript, cli.transcript)
        self.assertIs(cli_package.view, cli.view)

    def test_runtime_package_exports_domain_modules(self) -> None:
        self.assertIs(runtime_package.config, runtime.config)
        self.assertIs(runtime_package.core, runtime.core)
        self.assertIs(runtime_package.diagnostics, runtime.diagnostics)
        self.assertIs(runtime_package.execution, runtime.execution)
        self.assertIs(runtime_package.history, runtime.history)
        self.assertIs(runtime_package.methodology, runtime.methodology)
        self.assertIs(runtime_package.orchestration, runtime.orchestration)
        self.assertIs(runtime_package.planning, runtime.planning)
        self.assertIs(runtime_package.platform, runtime.platform)
        self.assertIs(runtime_package.presentation, runtime.presentation)
        self.assertIs(runtime_package.proposals, runtime.proposals)
        self.assertIs(runtime_package.providers, runtime.providers)
        self.assertIs(runtime_package.storage, runtime.storage)
        self.assertIs(runtime_package.supervision, runtime.supervision)
        self.assertIs(runtime_package.workspace, runtime.workspace)

    def test_services_package_exports_domain_modules(self) -> None:
        self.assertIs(services_package.memory, services.memory)
        self.assertIs(services_package.methodologies, services.methodologies)
        self.assertIs(services_package.payloads, services.payloads)
        self.assertIs(services_package.proposals, services.proposals)
        self.assertIs(services_package.reporting, services.reporting)
        self.assertIs(services_package.routing, services.routing)


if __name__ == "__main__":
    unittest.main()
