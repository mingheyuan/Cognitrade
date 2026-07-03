from __future__ import annotations

import unittest

from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.registry import SubagentRegistry


class _SuccessSubagent:
    subagent_type = "success_subagent"

    def run(self, **kwargs):
        return {"result": "ok", "notes": ["done"]}


class _FailureSubagent:
    subagent_type = "failure_subagent"

    def run(self, **kwargs):
        raise RuntimeError("boom")


class SubagentRegistryTests(unittest.TestCase):
    def test_registry_notifies_started_and_completed(self) -> None:
        registry = SubagentRegistry([_SuccessSubagent()])
        seen: list[tuple[str, str, str | None, str | None]] = []

        registry.add_observer(
            lambda *, stage, invocation, record, outputs, error: seen.append(
                (stage, invocation.subagent_type, record.status if record is not None else None, invocation.invocation_id)
            )
        )

        outputs, record = registry.invoke(
            SubagentInvocation(
                subagent_type="success_subagent",
                objective="Run success case.",
                inputs={},
            )
        )

        self.assertEqual(outputs["result"], "ok")
        self.assertEqual(record.status, "completed")
        self.assertEqual(
            [(stage, subagent_type, status) for stage, subagent_type, status, _ in seen],
            [
                ("started", "success_subagent", None),
                ("completed", "success_subagent", "completed"),
            ],
        )
        self.assertTrue(all(isinstance(item[3], str) and item[3] for item in seen))

    def test_registry_notifies_failed_before_reraising(self) -> None:
        registry = SubagentRegistry([_FailureSubagent()])
        seen: list[tuple[str, str, str | None, str | None]] = []

        registry.add_observer(
            lambda *, stage, invocation, record, outputs, error: seen.append(
                (stage, invocation.subagent_type, record.status if record is not None else None, invocation.invocation_id)
            )
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            registry.invoke(
                SubagentInvocation(
                    subagent_type="failure_subagent",
                    objective="Run failure case.",
                    inputs={},
                )
            )

        self.assertEqual(
            [(stage, subagent_type, status) for stage, subagent_type, status, _ in seen],
            [
                ("started", "failure_subagent", None),
                ("failed", "failure_subagent", "failed"),
            ],
        )
        self.assertTrue(all(isinstance(item[3], str) and item[3] for item in seen))


if __name__ == "__main__":
    unittest.main()
