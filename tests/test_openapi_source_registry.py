from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.vendors.openapi_source_registry import OpenApiSourceRegistry
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class OpenApiSourceRegistryTests(unittest.TestCase):
    def test_registry_loads_openapi_operations_from_workspace_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            spec_path = settings.config_root / "eastmoney.openapi.yml"
            spec_path.write_text(
                "\n".join(
                    [
                        "openapi: 3.0.0",
                        "paths:",
                        "  /api/market-signal:",
                        "    get:",
                        "      operationId: getMarketSignal",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            settings.openapi_sources_path.write_text(
                "\n".join(
                    [
                        "sources:",
                        "  - source_id: eastmoney",
                        "    spec_uri: eastmoney.openapi.yml",
                        "    operations:",
                        "      - operation_id: getMarketSignal",
                        "        summary: Lookup one market signal",
                        "        read_only: true",
                        "        stage: collect",
                        "        candidate_subagent_type: candidate_collect_market_signal_openapi",
                        "        capabilities: ['market_signal_extension']",
                        "        suitable_intents: ['stock_check']",
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

            registry = OpenApiSourceRegistry(settings=settings)

            operation = registry.get("eastmoney", "getMarketSignal")
            self.assertIsNotNone(operation)
            assert operation is not None
            self.assertTrue(operation.read_only)
            self.assertEqual(operation.summary, "Lookup one market signal")
            self.assertEqual(operation.stage, "collect")
            self.assertEqual(
                operation.candidate_subagent_type,
                "candidate_collect_market_signal_openapi",
            )
            self.assertEqual(operation.capabilities, ("market_signal_extension",))
            self.assertEqual(operation.suitable_intents, ("stock_check",))
            self.assertEqual(operation.spec_uri, str(spec_path.resolve()))
            self.assertEqual(
                operation.input_json_schema,
                {
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
            )
            self.assertEqual(
                operation.output_json_schema,
                {
                    "type": "object",
                    "properties": {"signal": {"type": "string"}},
                    "required": ["signal"],
                },
            )


if __name__ == "__main__":
    unittest.main()
