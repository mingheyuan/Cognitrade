from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)
from research_system.runtime.orchestration._subagent_control_stage_recorder import (
    SubagentControlStageRecorder,
)


class _RuntimeRecorderStub(RuntimeControlStageRecorder):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))


class SubagentControlStageRecorderTests(unittest.TestCase):
    def test_record_appends_subagent_rows_and_forwards_runtime_event(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_control_stage_decisions=[],
            subagent_catalog_stage_decisions=[],
            dynamic_trace=[],
        )
        runtime_recorder = _RuntimeRecorderStub()
        recorder = SubagentControlStageRecorder(
            runtime_binding_getter=lambda: {
                "context": runtime_context,
                "support": SimpleNamespace(emit_event=lambda *_args, **_kwargs: None),
                "request_id": "req-test",
                "intent": "stock_check",
            },
            runtime_control_stage_recorder=runtime_recorder,
        )

        recorder.record(
            event_type="subagent_catalog_selection_decided",
            message="catalog selected candidate",
            payload={
                "control_domain": "subagent",
                "control_stage": "catalog_selection",
                "decision_stage": "catalog_selection",
                "intent": "stock_check",
                "stage": "collect",
                "capability": "market_signal_extension",
                "decision_type": "selected",
            },
            phase="collect",
            agent="collector",
            extra_context_attrs=["subagent_catalog_stage_decisions"],
        )

        self.assertEqual(len(runtime_context.subagent_control_stage_decisions), 1)
        self.assertEqual(len(runtime_context.subagent_catalog_stage_decisions), 1)
        self.assertEqual(
            runtime_context.subagent_control_stage_decisions[0]["event"],
            "subagent_catalog_selection_decided",
        )
        self.assertEqual(
            runtime_context.subagent_control_stage_decisions[0]["control_stage"],
            "catalog_selection",
        )
        self.assertEqual(len(runtime_recorder.calls), 1)
        self.assertEqual(
            runtime_recorder.calls[0]["event_type"],
            "subagent_catalog_selection_decided",
        )


if __name__ == "__main__":
    unittest.main()
