from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.vendors.http_json_source_registry import HttpJsonSourceRegistry
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class HttpJsonSourceRegistryTests(unittest.TestCase):
    def test_registry_loads_http_json_endpoints_from_workspace_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.http_json_sources_path.write_text(
                "\n".join(
                    [
                        "sources:",
                        "  - source_id: eastmoney",
                        "    stage: collect",
                        "    suitable_intents: ['stock_check']",
                        "    endpoints:",
                        "      - endpoint_path: /api/market-signal",
                        "        http_method: GET",
                        "        summary: Lookup one market signal via HTTP JSON",
                        "        timeout_seconds: 8.5",
                        "        candidate_subagent_type: candidate_collect_market_signal_http",
                        "        capabilities: ['market_signal_extension']",
                        "        input_json_schema:",
                        "          type: object",
                        "          properties:",
                        "            symbol:",
                        "              type: string",
                        "          required: ['symbol']",
                        "        output_json_schema:",
                        "          type: object",
                        "          properties:",
                        "            signal:",
                        "              type: string",
                        "          required: ['signal']",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            registry = HttpJsonSourceRegistry(settings=settings)

            endpoint = registry.get("eastmoney", "/api/market-signal", "GET")
            self.assertIsNotNone(endpoint)
            assert endpoint is not None
            self.assertEqual(endpoint.http_method, "GET")
            self.assertEqual(endpoint.timeout_seconds, 8.5)
            self.assertEqual(endpoint.stage, "collect")
            self.assertEqual(endpoint.suitable_intents, ("stock_check",))
            self.assertEqual(
                endpoint.candidate_subagent_type,
                "candidate_collect_market_signal_http",
            )
            self.assertEqual(
                endpoint.input_json_schema,
                {
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
            )
            self.assertEqual(
                endpoint.output_json_schema,
                {
                    "type": "object",
                    "properties": {"signal": {"type": "string"}},
                    "required": ["signal"],
                },
            )


if __name__ == "__main__":
    unittest.main()
