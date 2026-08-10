from __future__ import annotations

import unittest

from research_system.runtime.presentation.display import (
    build_display_summary,
    localize_public_error_fields,
    localize_runtime_error,
    localize_runtime_event,
)


class DisplaySummaryTests(unittest.TestCase):
    def test_prefers_review_summary_text_for_review_thesis(self) -> None:
        payload = build_display_summary(
            result_payload={
                "intent": "review_thesis",
                "report": {
                    "conclusion": "overall_verdict=confirmed. posterior review completed",
                    "review_summary": {
                        "summary_text": "overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector_vs_hs300",
                    },
                },
            },
            summary_payload={"status": "completed", "message": "Run completed successfully"},
        )

        self.assertEqual(
            payload,
            "overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector_vs_hs300",
        )

    def test_falls_back_to_conclusion_when_review_summary_missing(self) -> None:
        payload = build_display_summary(
            result_payload={
                "intent": "stock_check",
                "report": {
                    "conclusion": "A concise stock conclusion.",
                },
            },
            summary_payload={"status": "completed", "message": "Run completed successfully"},
        )

        self.assertEqual(payload, "A concise stock conclusion.")

    def test_localizes_public_error_fields_without_changing_identifiers(self) -> None:
        payload = localize_public_error_fields(
            {
                "status": "failed",
                "message": "provider execution failed",
                "error": "Connection aborted: RemoteDisconnected",
                "diagnostics": "Traceback: provider process failed",
                "error_type": "RemoteDisconnected",
                "field": "research_profile",
            }
        )

        self.assertRegex(payload["message"], r"[\u3400-\u9fff]")
        self.assertRegex(payload["error"], r"[\u3400-\u9fff]")
        self.assertRegex(payload["diagnostics"], r"[\u3400-\u9fff]")
        self.assertEqual(payload["error_type"], "RemoteDisconnected")
        self.assertEqual(payload["field"], "research_profile")

    def test_localizes_degraded_provider_reason(self) -> None:
        payload = localize_public_error_fields(
            {
                "availability": "provider_unavailable",
                "degrade_reason": "provider_failure:RemoteDisconnected",
                "source_id": "em_data",
            }
        )

        self.assertRegex(payload["degrade_reason"], r"[\u3400-\u9fff]")
        self.assertEqual(payload["source_id"], "em_data")

    def test_redacts_technical_details_from_mixed_language_errors(self) -> None:
        message = localize_runtime_error("行情加载失败：RemoteDisconnected")

        self.assertRegex(message, r"[\u3400-\u9fff]")
        self.assertNotIn("RemoteDisconnected", message)

    def test_failed_parent_does_not_rewrite_successful_child_details(self) -> None:
        payload = localize_public_error_fields(
            {
                "status": "failed",
                "checks": [
                    {"status": "passed", "detail": "https://provider.example/health"},
                    {"status": "failed", "detail": "Traceback: provider process failed"},
                ],
            }
        )

        self.assertEqual(payload["checks"][0]["detail"], "https://provider.example/health")
        self.assertRegex(payload["checks"][1]["detail"], r"[\u3400-\u9fff]")
        self.assertNotIn("Traceback", payload["checks"][1]["detail"])

    def test_localizes_failure_actions_and_partial_delivery_reasons(self) -> None:
        failed = localize_runtime_event(
            {
                "event_type": "graph_node_failed",
                "message": "provider execution failed",
                "current_action": "Retry failed provider request",
            }
        )
        partial = localize_runtime_event(
            {
                "event_type": "assistant_message_status",
                "delivery_status": "synthesis_unavailable",
                "reason": "provider_failure:RemoteDisconnected",
            }
        )

        for value in (failed["message"], failed["current_action"], partial["reason"]):
            self.assertRegex(value, r"[\u3400-\u9fff]")
        self.assertNotIn("RemoteDisconnected", partial["reason"])


if __name__ == "__main__":
    unittest.main()
