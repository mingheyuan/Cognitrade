from __future__ import annotations

import unittest

from research_system.runtime.execution.handlers.distillation.distillation_context import (
    collect_critic_inputs,
    collect_distiller_source_records,
)
from research_system.runtime.execution.handlers.distillation.distillation_entries import (
    add_critic_outputs,
    add_distillation_outputs,
)


class AgentHandlersDistillationRuntimeTests(unittest.TestCase):
    def test_distillation_support_exports_are_available(self) -> None:
        self.assertTrue(callable(collect_distiller_source_records))
        self.assertTrue(callable(collect_critic_inputs))
        self.assertTrue(callable(add_distillation_outputs))
        self.assertTrue(callable(add_critic_outputs))


if __name__ == "__main__":
    unittest.main()
