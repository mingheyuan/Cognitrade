from __future__ import annotations

import unittest

from research_system.services.payloads import runs as runs_payloads_package
from research_system.services.payloads.runs.run_payload_common_fields import (
    append_intent_query_summary_status as package_append_intent_query_summary_status,
    append_run_id as package_append_run_id,
    request_summary as package_request_summary,
)
from research_system.services.payloads.runs.run_payload_fields import (
    events_payload_fields as package_events_payload_fields,
    metrics_payload_fields as package_metrics_payload_fields,
    replay_payload_fields as package_replay_payload_fields,
    run_payload_fields as package_run_payload_fields,
    steps_payload_fields as package_steps_payload_fields,
    subagent_payload_fields as package_subagent_payload_fields,
    thread_payload_fields as package_thread_payload_fields,
    trace_payload_fields as package_trace_payload_fields,
)
from research_system.services.payloads.runs.run_payload_renderers import (
    render_event_row_text as package_render_event_row_text,
    render_labeled_field_line as package_render_labeled_field_line,
    render_run_row_text as package_render_run_row_text,
    render_status_counts_text as package_render_status_counts_text,
    render_trace_row_text as package_render_trace_row_text,
)
from research_system.services.payloads.status_payloads import (
    group_doctor_checks as package_group_doctor_checks,
    render_doctor_check_line as package_render_doctor_check_line,
    status_header_fields as package_status_header_fields,
)
from research_system.services.payloads.runs.run_payload_runtime import latest_run_context as package_latest_run_context


class PayloadsPackageRuntimeTests(unittest.TestCase):
    def test_run_payload_field_exports_match_package_exports(self) -> None:
        self.assertIs(runs_payloads_package.append_run_id, package_append_run_id)
        self.assertIs(
            runs_payloads_package.append_intent_query_summary_status,
            package_append_intent_query_summary_status,
        )
        self.assertIs(runs_payloads_package.request_summary, package_request_summary)
        self.assertIs(runs_payloads_package.run_payload_fields, package_run_payload_fields)
        self.assertIs(runs_payloads_package.replay_payload_fields, package_replay_payload_fields)
        self.assertIs(runs_payloads_package.trace_payload_fields, package_trace_payload_fields)
        self.assertIs(runs_payloads_package.events_payload_fields, package_events_payload_fields)
        self.assertIs(runs_payloads_package.metrics_payload_fields, package_metrics_payload_fields)
        self.assertIs(runs_payloads_package.steps_payload_fields, package_steps_payload_fields)
        self.assertIs(runs_payloads_package.subagent_payload_fields, package_subagent_payload_fields)
        self.assertIs(runs_payloads_package.thread_payload_fields, package_thread_payload_fields)

    def test_run_payload_renderer_exports_match_package_exports(self) -> None:
        self.assertIs(runs_payloads_package.render_labeled_field_line, package_render_labeled_field_line)
        self.assertIs(runs_payloads_package.render_run_row_text, package_render_run_row_text)
        self.assertIs(runs_payloads_package.render_status_counts_text, package_render_status_counts_text)
        self.assertIs(runs_payloads_package.render_event_row_text, package_render_event_row_text)
        self.assertIs(runs_payloads_package.render_trace_row_text, package_render_trace_row_text)
        self.assertIs(runs_payloads_package.latest_run_context, package_latest_run_context)

    def test_status_payload_exports_match_package_exports(self) -> None:
        self.assertTrue(callable(package_status_header_fields))
        self.assertTrue(callable(package_group_doctor_checks))
        self.assertTrue(callable(package_render_doctor_check_line))

    def test_runs_payload_package_exports_match_compatibility_exports(self) -> None:
        self.assertIs(runs_payloads_package.append_run_id, package_append_run_id)
        self.assertIs(
            runs_payloads_package.append_intent_query_summary_status,
            package_append_intent_query_summary_status,
        )
        self.assertIs(runs_payloads_package.request_summary, package_request_summary)
        self.assertIs(runs_payloads_package.run_payload_fields, package_run_payload_fields)
        self.assertIs(runs_payloads_package.replay_payload_fields, package_replay_payload_fields)
        self.assertIs(runs_payloads_package.trace_payload_fields, package_trace_payload_fields)
        self.assertIs(runs_payloads_package.events_payload_fields, package_events_payload_fields)
        self.assertIs(runs_payloads_package.metrics_payload_fields, package_metrics_payload_fields)
        self.assertIs(runs_payloads_package.steps_payload_fields, package_steps_payload_fields)
        self.assertIs(runs_payloads_package.subagent_payload_fields, package_subagent_payload_fields)
        self.assertIs(runs_payloads_package.thread_payload_fields, package_thread_payload_fields)
        self.assertIs(runs_payloads_package.render_labeled_field_line, package_render_labeled_field_line)
        self.assertIs(runs_payloads_package.render_run_row_text, package_render_run_row_text)
        self.assertIs(runs_payloads_package.render_status_counts_text, package_render_status_counts_text)
        self.assertIs(runs_payloads_package.render_event_row_text, package_render_event_row_text)
        self.assertIs(runs_payloads_package.render_trace_row_text, package_render_trace_row_text)
        self.assertIs(runs_payloads_package.latest_run_context, package_latest_run_context)


if __name__ == "__main__":
    unittest.main()
