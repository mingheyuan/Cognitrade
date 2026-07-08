from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.vendors.skill_source_registry import SkillSourceRegistry
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class SkillSourceRegistryTests(unittest.TestCase):
    def test_registry_loads_skill_sources_from_workspace_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.skill_sources_path.write_text(
                "\n".join(
                    [
                        "sources:",
                        "  - skill_name: market_snapshot_skill",
                        "    registration_type: snapshot_skill_runner",
                        "    snapshot_type: market",
                        "    summary: Collect one market snapshot through the local skill runtime",
                        "    stage: collect",
                        "    candidate_subagent_type: candidate_collect_market_snapshot_skill",
                        "    capabilities: ['market_signal_extension']",
                        "    suitable_intents: ['stock_check']",
                        "  - skill_name: analysis_skill",
                        "    registration_type: skill_runner",
                        "    summary: Run one local analysis skill",
                        "    stage: report",
                        "    capabilities: ['report_signal_extension']",
                        "    suitable_intents: ['review_thesis']",
                        "    input_json_schema:",
                        "      type: object",
                        "      properties:",
                        "        query:",
                        "          type: string",
                        "      required: ['query']",
                        "    output_json_schema:",
                        "      type: object",
                        "      properties:",
                        "        summary:",
                        "          type: string",
                        "      required: ['summary']",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            registry = SkillSourceRegistry(settings=settings)

            snapshot_source = registry.get("market_snapshot_skill")
            self.assertIsNotNone(snapshot_source)
            assert snapshot_source is not None
            self.assertEqual(snapshot_source.registration_type, "snapshot_skill_runner")
            self.assertEqual(snapshot_source.snapshot_type, "market")
            self.assertEqual(snapshot_source.capabilities, ("market_signal_extension",))
            self.assertEqual(snapshot_source.suitable_intents, ("stock_check",))
            self.assertEqual(
                snapshot_source.candidate_subagent_type,
                "candidate_collect_market_snapshot_skill",
            )

            generic_source = registry.get("analysis_skill")
            self.assertIsNotNone(generic_source)
            assert generic_source is not None
            self.assertEqual(generic_source.registration_type, "skill_runner")
            self.assertEqual(generic_source.stage, "report")
            self.assertEqual(
                generic_source.input_json_schema,
                {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
            self.assertEqual(
                generic_source.output_json_schema,
                {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            )


if __name__ == "__main__":
    unittest.main()
