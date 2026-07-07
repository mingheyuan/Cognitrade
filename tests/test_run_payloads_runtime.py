from __future__ import annotations

import unittest

from research_system.services.payloads.runs.run_payload_fields import (
    events_payload_fields,
    metrics_payload_fields,
    replay_payload_fields,
    run_payload_fields,
    steps_payload_fields,
    subagent_payload_fields,
    thread_payload_fields,
    trace_payload_fields,
)
from research_system.services.payloads.runs.run_payload_renderers import (
    render_event_row_text,
    render_labeled_field_line,
    render_run_row_text,
    render_status_counts_text,
    render_trace_row_text,
)
from research_system.services.payloads.runs.run_payload_runtime import latest_run_context


class RunPayloadsRuntimeTests(unittest.TestCase):
    def test_run_payload_support_exports_are_available(self) -> None:
        self.assertTrue(callable(render_labeled_field_line))
        self.assertTrue(callable(render_run_row_text))
        self.assertTrue(callable(render_status_counts_text))
        self.assertTrue(callable(render_event_row_text))
        self.assertTrue(callable(render_trace_row_text))
        self.assertTrue(callable(run_payload_fields))
        self.assertTrue(callable(replay_payload_fields))
        self.assertTrue(callable(trace_payload_fields))
        self.assertTrue(callable(events_payload_fields))
        self.assertTrue(callable(metrics_payload_fields))
        self.assertTrue(callable(steps_payload_fields))
        self.assertTrue(callable(subagent_payload_fields))
        self.assertTrue(callable(thread_payload_fields))
        self.assertTrue(callable(latest_run_context))


if __name__ == "__main__":
    unittest.main()
