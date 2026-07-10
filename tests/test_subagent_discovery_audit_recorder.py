from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.orchestration.subagent_discovery_audit_recorder import (
    SubagentDiscoveryAuditRecorder,
)


class SubagentDiscoveryAuditRecorderTests(unittest.TestCase):
    def test_record_appends_only_requested_discovery_audit_rows(self) -> None:
        context = SimpleNamespace(subagent_discovery_stage_decisions=[], dynamic_trace=[])
        emitted: list[dict] = []
        recorder = SubagentDiscoveryAuditRecorder(
            runtime_binding_getter=lambda: {
                "context": context,
                "support": None,
                "request_id": "req-test",
                "intent": "stock_check",
            },
            emit_event=lambda **kwargs: emitted.append(dict(kwargs)),
        )

        recorder.record(
            event_type="subagent_tool_discovery_candidate_decided",
            message="candidate selected",
            payload={"capability": "market_signal_extension"},
            extra_context_attrs=["subagent_discovery_stage_decisions"],
        )

        self.assertEqual(len(context.subagent_discovery_stage_decisions), 1)
        self.assertEqual(
            context.subagent_discovery_stage_decisions[0]["event"],
            "subagent_tool_discovery_candidate_decided",
        )
        self.assertEqual(len(emitted), 1)


if __name__ == "__main__":
    unittest.main()
