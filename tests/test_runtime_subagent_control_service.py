from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_subagent_control_service import (
    OrchestratorRuntimeSubagentControlService,
)


class _ProtocolControllerStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def resolve_subagent_capability_gap_escalation(self, **kwargs):
        self.calls.append(("resolve_subagent_capability_gap_escalation", dict(kwargs)))
        return "gap-escalation"

    def resolve_subagent_catalog_entry(self, **kwargs):
        self.calls.append(("resolve_subagent_catalog_entry", dict(kwargs)))
        return "catalog-resolution"

    def select_subagent_catalog_entry(self, **kwargs):
        self.calls.append(("select_subagent_catalog_entry", dict(kwargs)))
        return "catalog-selection"

    def execute_subagent_phase_patch_cycle(self, **kwargs):
        self.calls.append(("execute_subagent_phase_patch_cycle", dict(kwargs)))
        return "patch-cycle"


class RuntimeSubagentControlServiceTests(unittest.TestCase):
    def test_service_delegates_subagent_runtime_control_calls(self) -> None:
        protocol_controller = _ProtocolControllerStub()
        service = OrchestratorRuntimeSubagentControlService(
            controller=SimpleNamespace(protocol_controller=protocol_controller),
        )

        self.assertEqual(
            service.resolve_subagent_capability_gap_escalation(gap="gap"),
            "gap-escalation",
        )
        self.assertEqual(
            service.resolve_subagent_catalog_entry(capability="collect"),
            "catalog-resolution",
        )
        self.assertEqual(
            service.select_subagent_catalog_entry(capability="collect"),
            "catalog-selection",
        )
        self.assertEqual(
            service.execute_subagent_phase_patch_cycle(plan="plan"),
            "patch-cycle",
        )
        self.assertEqual(
            [name for name, _ in protocol_controller.calls],
            [
                "resolve_subagent_capability_gap_escalation",
                "resolve_subagent_catalog_entry",
                "select_subagent_catalog_entry",
                "execute_subagent_phase_patch_cycle",
            ],
        )


if __name__ == "__main__":
    unittest.main()
