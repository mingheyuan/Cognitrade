from __future__ import annotations

import unittest

from research_system.runtime.agent_handlers_review_context import (
    collect_review_request_context,
    collect_screen_review_context,
)
from research_system.runtime.agent_handlers_review_entries import (
    add_review_findings,
    add_review_summary,
)
from research_system.runtime.agent_handlers_review_events import (
    emit_reviewer_completed,
    emit_reviewer_requested,
    emit_screen_reviewer_completed,
    emit_screen_reviewer_requested,
)
from research_system.runtime.agent_handlers_review_flow import (
    run_reviewer_flow,
    run_screen_reviewer_flow,
)
from research_system.runtime.agent_handlers_review_trace import (
    extend_reviewer_trace,
    extend_screen_reviewer_trace,
)


class AgentHandlersReviewRuntimeTests(unittest.TestCase):
    def test_review_support_exports_are_available(self) -> None:
        self.assertTrue(callable(collect_review_request_context))
        self.assertTrue(callable(collect_screen_review_context))
        self.assertTrue(callable(run_reviewer_flow))
        self.assertTrue(callable(run_screen_reviewer_flow))
        self.assertTrue(callable(extend_reviewer_trace))
        self.assertTrue(callable(extend_screen_reviewer_trace))
        self.assertTrue(callable(emit_reviewer_requested))
        self.assertTrue(callable(emit_reviewer_completed))
        self.assertTrue(callable(emit_screen_reviewer_requested))
        self.assertTrue(callable(emit_screen_reviewer_completed))
        self.assertTrue(callable(add_review_findings))
        self.assertTrue(callable(add_review_summary))


if __name__ == "__main__":
    unittest.main()
