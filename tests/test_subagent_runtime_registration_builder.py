from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.orchestration.subagent_runtime_registration_builder import (
    SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES,
    SubagentRuntimeRegistrationBuilderSupport,
)
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentRuntimeRegistrationTemplate,
)


class SubagentRuntimeRegistrationBuilderTests(unittest.TestCase):
    def test_builder_types_cover_current_runtime_registration_families(self) -> None:
        self.assertEqual(
            SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES,
            ("snapshot_skill_runner", "skill_runner", "http_json_runner", "openapi_http_runner"),
        )

    def test_snapshot_skill_builder_emits_template_note_and_validation_note(self) -> None:
        support = SubagentRuntimeRegistrationBuilderSupport(
            type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"market_snapshot_skill": object()})
                    ),
                },
            )()
        )
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_extension",
            stage="collect",
            capabilities=["market_signal_extension"],
        )
        template = SubagentRuntimeRegistrationTemplate(
            template_id="snapshot:market:market_snapshot_skill",
            registration_type="snapshot_skill_runner",
            defaults={
                "skill_name": "market_snapshot_skill",
                "snapshot_type": "market",
            },
        )

        registration = support.build_registration(
            registration_type="snapshot_skill_runner",
            candidate_entry=candidate,
            template=template,
            skill_name="market_snapshot_skill",
            snapshot_type="market",
        )

        self.assertEqual(registration.registration_type, "snapshot_skill_runner")
        self.assertEqual(registration.skill_name, "market_snapshot_skill")
        self.assertEqual(registration.snapshot_type, "market")
        self.assertIn("registration_template_id=snapshot:market:market_snapshot_skill", registration.notes)
        self.assertIn("validated_via_skill_runtime_registry", registration.notes)

    def test_openapi_builder_resolves_operation_from_candidate_hints(self) -> None:
        support = SubagentRuntimeRegistrationBuilderSupport(
            type("_Host", (), {"orchestrator_error_cls": RuntimeError})()
        )
        candidate = SubagentCatalogEntry(
            subagent_type="candidate_collect_market_signal_openapi_extension",
            stage="collect",
            side_effect_level="read_only",
            capabilities=["market_signal_openapi_extension"],
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
            runtime_registration_hints={
                "openapi": {
                    "source_id": "eastmoney",
                    "spec": {
                        "openapi": "3.0.0",
                        "paths": {
                            "/api/market-signal": {
                                "get": {
                                    "operationId": "getMarketSignal",
                                }
                            }
                        },
                    },
                }
            },
        )

        registration = support.build_registration(
            registration_type="openapi_http_runner",
            candidate_entry=candidate,
            template=None,
            source_id="eastmoney",
            openapi_operation_id="getMarketSignal",
        )

        self.assertEqual(registration.registration_type, "openapi_http_runner")
        self.assertEqual(registration.source_id, "eastmoney")
        self.assertEqual(registration.endpoint_path, "/api/market-signal")
        self.assertEqual(registration.http_method, "GET")
        self.assertEqual(registration.openapi_operation_id, "getMarketSignal")
        self.assertIn("validated_via_openapi_operation_spec", registration.notes)
        self.assertIn("validated_runtime_contract_schema", registration.notes)


if __name__ == "__main__":
    unittest.main()
