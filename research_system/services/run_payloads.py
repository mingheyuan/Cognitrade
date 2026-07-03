from __future__ import annotations

from research_system.services.run_payload_fields import (
    events_payload_fields,
    replay_payload_fields,
    run_payload_fields,
    steps_payload_fields,
    subagent_payload_fields,
    trace_payload_fields,
)
from research_system.services.run_payload_renderers import (
    render_event_row_text,
    render_labeled_field_line,
    render_run_row_text,
    render_status_counts_text,
    render_trace_row_text,
)
from research_system.services.run_payload_runtime import latest_run_context

