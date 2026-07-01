from __future__ import annotations

import unittest

from research_system.services.methodology_evolver import MethodologyEvolver


class MethodologyEvolverTests(unittest.TestCase):
    def test_build_candidates_attaches_matching_memory_context(self) -> None:
        evolver = MethodologyEvolver()
        blackboard_entries = [
            {
                "kind": "distilled_dimension",
                "entry_id": "dim-1",
                "payload": {
                    "dimension_name": "policy catalyst",
                    "canonical_dimension_name": "policy catalyst",
                    "dimension_family": "event_driven",
                    "scenario_tags": ["short_term"],
                    "methodology_tags": ["event_driven"],
                    "supported_claim_block_ids": ["logic-1"],
                    "evidence_block_ids": [],
                    "confidence": 0.7,
                },
            }
        ]
        memory_rows = [
            {
                "memory_type": "methodology_summary",
                "content": {
                    "title": "Promote dimension: policy catalyst",
                    "summary": "Policy catalysts worked in recent event-driven runs.",
                },
            }
        ]

        candidates = evolver.build_candidates(
            run_id="01RUN",
            blackboard_entries=blackboard_entries,
            methodology_memory_rows=memory_rows,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidate_type"], "dimension")
        self.assertTrue(candidates[0]["memory_context"])
        self.assertEqual(candidates[0]["memory_context"][0]["memory_type"], "methodology_summary")

    def test_build_candidates_from_runs_aggregates_dimension_and_review_pattern(self) -> None:
        evolver = MethodologyEvolver()
        runs = [
            {
                "run_id": "01RUNA",
                "blackboard_entries": [
                    {
                        "kind": "distilled_dimension",
                        "entry_id": "dim-1",
                        "payload": {
                            "dimension_name": "policy catalyst",
                            "canonical_dimension_name": "policy catalyst",
                            "dimension_family": "event_driven",
                            "scenario_tags": ["short_term"],
                            "methodology_tags": ["event_driven"],
                            "supported_claim_block_ids": ["logic-1"],
                            "evidence_block_ids": ["logic-2"],
                            "confidence": 0.7,
                        },
                    },
                    {
                        "kind": "review_finding",
                        "payload": {"severity": "high", "verdict": "rejected"},
                    },
                ],
            },
            {
                "run_id": "01RUNB",
                "blackboard_entries": [
                    {
                        "kind": "distilled_dimension",
                        "entry_id": "dim-2",
                        "payload": {
                            "dimension_name": "policy catalyst",
                            "canonical_dimension_name": "policy catalyst",
                            "dimension_family": "event_driven",
                            "scenario_tags": ["mid_term"],
                            "methodology_tags": ["event_driven", "policy"],
                            "supported_claim_block_ids": ["logic-3"],
                            "evidence_block_ids": [],
                            "confidence": 0.9,
                        },
                    },
                    {
                        "kind": "review_finding",
                        "payload": {"severity": "medium", "verdict": "confirmed"},
                    },
                ],
            },
        ]

        candidates = evolver.build_candidates_from_runs(runs=runs, methodology_memory_rows=[])

        dimension = next(item for item in candidates if item["candidate_type"] == "dimension")
        review_pattern = next(item for item in candidates if item["candidate_type"] == "review_pattern")
        self.assertEqual(dimension["sample_size"], 2)
        self.assertEqual(sorted(dimension["related_run_ids"]), ["01RUNA", "01RUNB"])
        self.assertEqual(dimension["aggregated_evidence_summary"]["supported_claim_count"], 2)
        self.assertEqual(review_pattern["sample_size"], 2)
        self.assertEqual(review_pattern["severity_counts"]["high"], 1)
        self.assertEqual(review_pattern["verdict_counts"]["confirmed"], 1)

    def test_build_candidates_from_runs_aggregates_memory_match_scores(self) -> None:
        evolver = MethodologyEvolver()
        runs = [
            {
                "run_id": "01RUNA",
                "blackboard_entries": [
                    {
                        "kind": "distilled_dimension",
                        "entry_id": "dim-1",
                        "payload": {
                            "dimension_name": "policy catalyst",
                            "canonical_dimension_name": "policy catalyst",
                            "dimension_family": "event_driven",
                            "scenario_tags": ["short_term"],
                            "methodology_tags": ["event_driven"],
                            "supported_claim_block_ids": ["logic-1"],
                            "evidence_block_ids": [],
                            "confidence": 0.7,
                        },
                    }
                ],
            },
            {
                "run_id": "01RUNB",
                "blackboard_entries": [
                    {
                        "kind": "distilled_dimension",
                        "entry_id": "dim-2",
                        "payload": {
                            "dimension_name": "policy catalyst",
                            "canonical_dimension_name": "policy catalyst",
                            "dimension_family": "event_driven",
                            "scenario_tags": ["mid_term"],
                            "methodology_tags": ["event_driven"],
                            "supported_claim_block_ids": ["logic-2"],
                            "evidence_block_ids": [],
                            "confidence": 0.9,
                        },
                    }
                ],
            },
        ]
        memory_search_rows = [
            {
                "memory_type": "methodology_summary",
                "match_score": 3.0,
                "content": {
                    "title": "policy catalyst",
                    "summary": "recent memory one",
                    "benchmark_scope": "event_driven",
                },
                "metadata": {
                    "entity_scope": "methodology:methodology_summary:event_driven:a",
                    "snapshot_id": "methodology_snapshot:methodology_summary:event_driven:a",
                    "recorded_at": "2026-06-01T00:00:00+00:00",
                },
            },
            {
                "memory_type": "methodology_summary",
                "match_score": 5.0,
                "content": {
                    "title": "policy catalyst",
                    "summary": "recent memory two",
                    "benchmark_scope": "event_driven",
                },
                "metadata": {
                    "entity_scope": "methodology:methodology_summary:event_driven:b",
                    "snapshot_id": "methodology_snapshot:methodology_summary:event_driven:b",
                    "recorded_at": "2026-06-02T00:00:00+00:00",
                },
            },
        ]

        candidates = evolver.build_candidates_from_runs(
            runs=runs,
            methodology_memory_rows=[],
            methodology_memory_search_rows=memory_search_rows,
        )

        dimension = next(item for item in candidates if item["candidate_type"] == "dimension")
        self.assertEqual(dimension["memory_match_score"], 4.0)
        self.assertEqual((dimension.get("memory_support") or {}).get("context_count"), 2)
        self.assertEqual((dimension.get("memory_support") or {}).get("entity_scope_count"), 2)

    def test_build_candidates_include_protocol_fields_in_memory_context(self) -> None:
        evolver = MethodologyEvolver()
        candidates = evolver.build_candidates(
            run_id="01RUN",
            blackboard_entries=[
                {
                    "kind": "distilled_dimension",
                    "entry_id": "dim-1",
                    "payload": {
                        "dimension_name": "policy catalyst",
                        "canonical_dimension_name": "policy catalyst",
                        "dimension_family": "event_driven",
                        "scenario_tags": ["short_term"],
                        "methodology_tags": ["event_driven"],
                        "supported_claim_block_ids": ["logic-1"],
                        "evidence_block_ids": [],
                        "confidence": 0.7,
                    },
                }
            ],
            methodology_memory_search_rows=[
                {
                    "memory_type": "dimension_experience",
                    "match_score": 4.0,
                    "content": {
                        "title": "policy catalyst",
                        "summary": "fresh event-driven memory",
                        "benchmark_scope": "event_driven",
                        "snapshot_id": "methodology_snapshot:dimension_experience:event_driven",
                        "recorded_at": "2026-06-01T00:00:00+00:00",
                    },
                    "metadata": {
                        "memory_scope": "methodology",
                        "entity_scope": "methodology:dimension_experience:event_driven",
                        "schema_version": "memory_record_v2",
                        "snapshot_version": "methodology_v2",
                    },
                }
            ],
        )

        memory_context = candidates[0]["memory_context"][0]
        self.assertEqual(memory_context["memory_scope"], "methodology")
        self.assertEqual(memory_context["entity_scope"], "methodology:dimension_experience:event_driven")
        self.assertEqual(memory_context["schema_version"], "memory_record_v2")
        self.assertEqual(memory_context["snapshot_version"], "methodology_v2")


if __name__ == "__main__":
    unittest.main()
