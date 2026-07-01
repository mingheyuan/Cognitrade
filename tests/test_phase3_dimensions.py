from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter
from research_system.agents.distiller.service import DistillerService
from research_system.agents.retriever.service import RetrieverService
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.runtime import BlackboardEntry
from research_system.services.dimension_normalizer import DimensionNormalizer


class _UnavailableProviderStub:
    available = False


class Phase3DimensionNormalizerTests(unittest.TestCase):
    def test_normalizer_maps_alias_to_canonical_family(self) -> None:
        normalizer = DimensionNormalizer(
            {
                "dimensions": [
                    {
                        "name": "AI",
                        "canonical_name": "AI",
                        "family": "technology_ai",
                        "category": "technology",
                        "aliases": ["artificial intelligence"],
                        "scenario_tags": ["technology", "efficiency"],
                        "methodology_tags": ["technology"],
                    }
                ]
            }
        )

        normalized = normalizer.normalize_dimension(
            name="artificial intelligence",
            category="technology",
            alias_hits=["artificial intelligence"],
            normalization_notes=["llm"],
        )

        self.assertEqual(normalized["canonical_dimension_name"], "AI")
        self.assertEqual(normalized["dimension_family"], "technology_ai")
        self.assertIn("technology", normalized["scenario_tags"])
        self.assertIn("taxonomy_family=technology_ai", normalized["normalization_notes"])

    def test_normalizer_merges_alias_and_pattern_configs(self) -> None:
        normalizer = DimensionNormalizer(
            {
                "dimensions": [
                    {
                        "name": "AI",
                        "canonical_name": "AI",
                        "family": "technology_ai",
                        "category": "technology",
                        "scenario_tags": ["technology"],
                        "methodology_tags": ["technology"],
                    }
                ],
                "aliases": [
                    {
                        "canonical_name": "AI",
                        "aliases": ["artificial intelligence"],
                    }
                ],
                "patterns": [
                    {
                        "canonical_name": "AI",
                        "patterns": ["machine intelligence"],
                    }
                ],
            }
        )

        normalized = normalizer.normalize_dimension(
            name="artificial intelligence",
            category="technology",
            alias_hits=["artificial intelligence"],
        )
        extracted = normalizer.extract_dimensions("machine intelligence is improving workflow")

        self.assertEqual(normalized["canonical_dimension_name"], "AI")
        self.assertIn(("AI", "technology"), extracted)


class Phase3DistillerTests(unittest.TestCase):
    def test_distiller_enriches_dimensions_with_family_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            service = DistillerService(_UnavailableProviderStub(), settings)

            result = service.run(
                query="distill AI and innovation drug",
                source_records=[
                    {
                        "source_id": "src-1",
                        "title": "note",
                        "content": "AI improves efficiency.\ninnovation drug clinical progress continues.",
                        "source_type": "inline_text",
                    }
                ],
            )

        names = {item["dimension_name"] for item in result["distilled_dimensions"]}
        self.assertIn("AI", names)
        self.assertIn("创新药", names)
        ai_dimension = next(item for item in result["distilled_dimensions"] if item["dimension_name"] == "AI")
        self.assertEqual(ai_dimension["dimension_family"], "technology_ai")
        self.assertIn("technology", ai_dimension["scenario_tags"])
        self.assertTrue(ai_dimension["dimension_tags"])
        self.assertTrue(ai_dimension["dimension_key"])

    def test_workspace_layout_seeds_dimension_aliases_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)

            self.assertTrue(settings.taxonomy_dimension_aliases_path.exists())


class Phase3QdrantPayloadTests(unittest.TestCase):
    def test_qdrant_payload_contains_dimension_metadata(self) -> None:
        adapter = object.__new__(QdrantAdapter)
        entry = BlackboardEntry(
            run_id="run-1",
            request_id="req-1",
            kind="distilled_dimension",
            agent="distiller",
            phase="distill",
            payload={
                "dimension_id": "technology_ai",
                "dimension_name": "AI",
                "canonical_dimension_name": "AI",
                "dimension_family": "technology_ai",
                "dimension_category": "technology",
                "alias_hits": ["AI", "artificial intelligence"],
                "supported_claim_block_ids": ["block-1"],
                "evidence_block_ids": [],
                "is_new_candidate": True,
                "confidence": 0.8,
                "scenario_tags": ["technology", "efficiency"],
                "methodology_tags": ["technology"],
                "dimension_tags": ["AI", "technology_ai", "technology"],
                "dimension_key": "technology_ai",
                "normalization_notes": ["taxonomy_family=technology_ai"],
            },
        )

        payload = adapter.to_payload(entry)

        self.assertEqual(payload["dimension_family"], "technology_ai")
        self.assertEqual(payload["dimension_key"], "technology_ai")
        self.assertIn("technology", payload["scenario_tags"])
        self.assertIn("methodology_tags: ['technology']", payload["content"])

    def test_qdrant_filter_supports_dimension_family_and_valuation_band(self) -> None:
        adapter = object.__new__(QdrantAdapter)

        query_filter = adapter._build_filter(
            {
                "dimension_family": "technology_ai",
                "valuation_band_label": "8.0|9.0|10.0",
                "unsupported": "ignored",
            }
        )

        self.assertIsNotNone(query_filter)
        assert query_filter is not None
        keys = [condition.key for condition in query_filter.must]
        self.assertEqual(keys, ["dimension_family", "valuation_band_label"])

    def test_qdrant_filter_supports_scenario_tag_match_any(self) -> None:
        adapter = object.__new__(QdrantAdapter)

        query_filter = adapter._build_filter(
            {
                "scenario_tags": ["technology", "efficiency"],
            }
        )

        self.assertIsNotNone(query_filter)
        assert query_filter is not None
        self.assertEqual(len(query_filter.must), 1)
        self.assertEqual(query_filter.must[0].key, "scenario_tags")
        self.assertEqual(query_filter.must[0].match.any, ["technology", "efficiency"])


class Phase3RetrieverSummaryTests(unittest.TestCase):
    def test_retrieval_summary_includes_dimension_families_and_tags(self) -> None:
        service = object.__new__(RetrieverService)
        evidences = [
            {
                "evidence_id": "logic-1",
                "title": "logic_block:claim",
                "summary": "logic_block type=claim source_record_id=src-1 speaker_type=author\ntext: AI improves efficiency",
                "source_refs": [],
                "metadata": {"source_type": "logic_block"},
            },
            {
                "evidence_id": "dimension-1",
                "title": "distilled_dimension:AI",
                "summary": (
                    "distilled_dimension AI category=technology\n"
                    "dimension_family: technology_ai\n"
                    "scenario_tags: ['technology', 'efficiency']\n"
                    "methodology_tags: ['technology']"
                ),
                "retrieval_hint": {
                    "hint_type": "distilled_dimension",
                    "dimension_name": "AI",
                    "dimension_family": "technology_ai",
                    "scenario_tags": ["technology", "efficiency"],
                    "methodology_tags": ["technology"],
                },
                "source_refs": [],
                "metadata": {
                    "source_type": "distilled_dimension",
                    "dimension_family": "technology_ai",
                    "scenario_tags": ["technology", "efficiency"],
                    "methodology_tags": ["technology"],
                },
            },
        ]

        summary = service._structured_retrieval_summary(intent="distill_kol_logic", evidences=evidences)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("dimension_families=['technology_ai']", summary)
        self.assertIn("scenario_tags=['technology', 'efficiency']", summary)


if __name__ == "__main__":
    unittest.main()
