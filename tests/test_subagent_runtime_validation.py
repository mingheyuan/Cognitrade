from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.orchestration.subagent_runtime_validation import (
    SUPPORTED_RUNTIME_VALIDATION_TYPES,
    SubagentRuntimeValidationSupport,
)
from research_system.schemas.subagents import SubagentCatalogEntry


class SubagentRuntimeValidationTests(unittest.TestCase):
    def test_validation_types_cover_current_runtime_registration_families(self) -> None:
        self.assertEqual(
            SUPPORTED_RUNTIME_VALIDATION_TYPES,
            ("snapshot_skill_runner", "skill_runner", "http_json_runner", "openapi_http_runner"),
        )

    def test_snapshot_skill_validation_rejects_non_collect_stage(self) -> None:
        support = SubagentRuntimeValidationSupport(type("_Host", (), {"orchestrator_error_cls": RuntimeError})())
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_report_signal_extension",
            stage="report",
            capabilities=["report_signal_extension"],
        )
        with self.assertRaises(RuntimeError):
            support.validate_registration(
                registration_type="snapshot_skill_runner",
                candidate_entry=candidate,
                skill_name="market_snapshot_skill",
                snapshot_type="market",
            )

    def test_skill_validation_requires_runtime_contract(self) -> None:
        support = SubagentRuntimeValidationSupport(
            type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"analysis_skill": object()})
                    ),
                },
            )()
        )
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_report_signal_extension",
            stage="report",
            capabilities=["report_signal_extension"],
        )
        with self.assertRaises(RuntimeError):
            support.validate_registration(
                registration_type="skill_runner",
                candidate_entry=candidate,
                skill_name="analysis_skill",
            )

    def test_contract_validation_emits_runtime_contract_note(self) -> None:
        support = SubagentRuntimeValidationSupport(type("_Host", (), {"orchestrator_error_cls": RuntimeError})())
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_http_extension",
            stage="collect",
            capabilities=["market_signal_http_extension"],
            input_json_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
            output_json_schema={
                "type": "object",
                "properties": {"signal": {"type": "string"}},
                "required": ["signal"],
            },
        )
        notes = support.validate_registration(
            registration_type="http_json_runner",
            candidate_entry=candidate,
        )
        self.assertEqual(notes, ["validated_runtime_contract_schema"])


if __name__ == "__main__":
    unittest.main()
