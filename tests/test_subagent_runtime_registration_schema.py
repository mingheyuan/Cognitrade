from __future__ import annotations

import unittest

from research_system.schemas.subagent_runtime_registration import (
    SUPPORTED_RUNTIME_REGISTRATION_TYPES,
    runtime_registration_spec,
)
from research_system.schemas.subagents import (
    SubagentRuntimeRegistration,
    SubagentRuntimeRegistrationTemplate,
)


class SubagentRuntimeRegistrationSchemaTests(unittest.TestCase):
    def test_supported_runtime_registration_types_match_spec_registry(self) -> None:
        self.assertEqual(
            SUPPORTED_RUNTIME_REGISTRATION_TYPES,
            tuple(runtime_registration_spec(item).registration_type for item in SUPPORTED_RUNTIME_REGISTRATION_TYPES),
        )

    def test_template_defaults_reject_fields_not_supported_by_registration_type(self) -> None:
        with self.assertRaises(ValueError):
            SubagentRuntimeRegistrationTemplate(
                template_id="snapshot:bad",
                registration_type="snapshot_skill_runner",
                defaults={"skill_name": "market_snapshot_skill", "source_id": "eastmoney"},
            )

    def test_template_defaults_accept_valid_http_json_fields(self) -> None:
        template = SubagentRuntimeRegistrationTemplate(
            template_id="http:eastmoney:get:api_market_signal",
            registration_type="http_json_runner",
            defaults={
                "source_id": "eastmoney",
                "endpoint_path": "/api/market-signal",
                "http_method": "GET",
                "timeout_seconds": 9.5,
            },
        )
        self.assertEqual(template.registration_type, "http_json_runner")
        self.assertEqual(template.defaults["timeout_seconds"], 9.5)

    def test_runtime_registration_rejects_timeout_for_skill_runner(self) -> None:
        with self.assertRaises(ValueError):
            SubagentRuntimeRegistration(
                registration_type="skill_runner",
                skill_name="analysis_skill",
                timeout_seconds=9.5,
            )

    def test_runtime_registration_accepts_openapi_runner_shape(self) -> None:
        registration = SubagentRuntimeRegistration(
            registration_type="openapi_http_runner",
            source_id="eastmoney",
            endpoint_path="/api/market-signal",
            http_method="GET",
            openapi_operation_id="getMarketSignal",
            timeout_seconds=12.5,
        )
        self.assertEqual(registration.registration_type, "openapi_http_runner")
        self.assertEqual(registration.timeout_seconds, 12.5)


if __name__ == "__main__":
    unittest.main()
