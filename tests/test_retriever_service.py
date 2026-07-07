from __future__ import annotations

import unittest
import threading
from unittest.mock import Mock

from research_system.agents.retriever.service import RetrievalDecision, RetrieverService
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.retrieval import (
    FallbackRetrievalDecisionSubagent,
    MethodologyMemoryRecallSubagent,
    QdrantEvidenceSearchSubagent,
    RetrievalDecisionSubagent,
)


class _StubLLMProvider:
    def parse(self, **kwargs):
        raise AssertionError("LLM parse should not be called in deterministic retriever tests")


class RetrieverServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = object.__new__(RetrieverService)
        self.service.memory_service = Mock()
        self.service.memory_service.read_recent_methodology_memory.return_value = []
        self.service.retrieval_policy = {}
        self.service.qdrant = None
        self.service.qdrant_available = False
        self.service.qdrant_error = "disabled_for_test"
        self.service.indexer_qdrant = None
        self.service.searcher_qdrant = None
        self.service.registry = SubagentRegistry(
            [
                RetrievalDecisionSubagent(
                    llm_provider=_StubLLMProvider(),
                    structured_retrieval_summary=self.service._structured_retrieval_summary,
                    merge_retrieval_summary=self.service._merge_retrieval_summary,
                    combine_degrade_reason=self.service._combine_degrade_reason,
                    fallback_summary=self.service._fallback_summary,
                    select_compare_evidences=self.service._select_compare_evidences,
                    select_screening_evidences=self.service._select_screening_evidences,
                    select_stock_check_evidences=self.service._select_stock_check_evidences,
                    select_methodology_evolution_evidences=self.service._select_methodology_evolution_evidences,
                    select_distillation_evidences=self.service._select_distillation_evidences,
                    select_review_evidences=self.service._select_review_evidences,
                    retrieval_decision_schema=RetrievalDecision,
                ),
                FallbackRetrievalDecisionSubagent(
                    combine_degrade_reason=self.service._combine_degrade_reason,
                    fallback_summary=self.service._fallback_summary,
                ),
                QdrantEvidenceSearchSubagent(
                    search_qdrant_evidences=self.service._search_qdrant_evidences,
                ),
                MethodologyMemoryRecallSubagent(
                    recall_methodology_memory=self.service._recall_methodology_memory_rows,
                ),
            ]
        )

    def test_dedupe_prefers_current_run_sector_snapshot(self) -> None:
        current_run_id = "01CURRENTRUN"
        historical_item = {
            "evidence_id": "old-snapshot",
            "title": "sector_snapshot:创新药",
            "summary": "sector snapshot for 创新药\nsnapshot_date: 2026-06-27\nlatest_hist: {'pct_change': -2.0}",
            "source_refs": [{"type": "skill", "skill": "sector_snapshot_skill"}],
            "metadata": {"source_type": "sector_snapshot"},
            "score": 0.98,
        }
        current_item = {
            "evidence_id": "current-snapshot",
            "title": "sector",
            "summary": "sector snapshot for 创新药\nsnapshot_date: 2026-06-28\nsignal_scorecard: {'setup_label': 'weak_tape_but_catalysts_alive'}",
            "retrieval_hint": {
                "hint_type": "sector_signal_scorecard",
                "sector": "创新药",
                "setup_label": "weak_tape_but_catalysts_alive",
            },
            "source_refs": [
                {
                    "type": "skill",
                    "skill": "sector_snapshot_skill",
                    "run_id": current_run_id,
                }
            ],
            "metadata": {"source_type": "sector_snapshot"},
            "score": 1.0,
        }

        deduped = self.service._dedupe_evidences(
            evidences=[historical_item, current_item],
            current_run_id=current_run_id,
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["evidence_id"], "current-snapshot")

    def test_merge_evidence_candidates_keeps_blackboard_priority_over_qdrant(self) -> None:
        existing = {
            "evidence_id": "shared-evidence",
            "title": "verified_snapshot",
            "summary": "current blackboard evidence",
            "source_refs": [],
            "metadata": {
                "record_type": "verified_snapshot",
                "memory_priority_rank": -1,
            },
            "score": 1.0,
        }
        qdrant_candidate = {
            "evidence_id": "shared-evidence",
            "title": "verified_snapshot",
            "summary": "qdrant copy",
            "source_refs": [],
            "metadata": {
                "record_type": "verified_snapshot",
                "memory_priority_rank": 4,
            },
            "score": 9.0,
        }

        merged = self.service._merge_evidence_candidates(
            evidence_map={"shared-evidence": existing},
            evidences=[qdrant_candidate],
        )

        self.assertEqual(merged["shared-evidence"]["summary"], "current blackboard evidence")
        self.assertEqual(merged["shared-evidence"]["score"], 1.0)

    def test_structured_retrieval_summary_uses_signal_scorecard_fields(self) -> None:
        evidence = {
            "evidence_id": "sector-snapshot",
            "title": "sector",
            "summary": "sector snapshot for 创新药\nsnapshot_date: 2026-06-28",
            "retrieval_hint": {
                "hint_type": "sector_signal_scorecard",
                "sector": "创新药",
                "setup_label": "weak_tape_but_catalysts_alive",
                "market_tone": "weak",
                "benchmark_view": "neutral",
                "turnover_state": "normal",
                "breadth_state": "broad_weak",
                "policy_signal_strength": "strong",
                "regulatory_signal_strength": "strong",
                "latest_pct_change": -3.85,
                "latest_excess_pct_change": -0.82,
                "up_ratio": 0.0647,
                "median_pct_change": -4.04,
                "average_turnover_rate": 2.5609,
                "top_policy_title": "创新药出海下一程怎么走",
                "top_regulatory_title": "APH03867片获得临床试验受理",
            },
            "source_refs": [],
            "metadata": {"source_type": "sector_snapshot"},
        }

        summary = self.service._structured_retrieval_summary(
            intent="sector_research",
            evidences=[evidence],
        )

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("sector=创新药", summary)
        self.assertIn("setup=weak_tape_but_catalysts_alive", summary)
        self.assertIn("breadth_state=broad_weak", summary)
        self.assertIn("policy_signal_strength=strong", summary)
        self.assertIn("top_regulatory_signal=APH03867片获得临床试验受理", summary)

    def test_dedupe_prefers_current_run_market_snapshot(self) -> None:
        current_run_id = "01MARKETRUN"
        historical_item = {
            "evidence_id": "old-market",
            "title": "market_snapshot:000001",
            "summary": "market snapshot for 000001\nsnapshot_date: 2026-06-27\nlatest_bar: {'close': 12.1}",
            "source_refs": [{"type": "skill", "skill": "market_snapshot_skill"}],
            "metadata": {"source_type": "market_snapshot"},
            "score": 0.97,
        }
        current_item = {
            "evidence_id": "current-market",
            "title": "market",
            "summary": "market snapshot for 000001\nsnapshot_date: 2026-06-28\nlatest_bar: {'close': 12.34}",
            "source_refs": [
                {
                    "type": "skill",
                    "skill": "market_snapshot_skill",
                    "run_id": current_run_id,
                }
            ],
            "metadata": {"source_type": "market_snapshot"},
            "score": 1.0,
        }

        deduped = self.service._dedupe_evidences(
            evidences=[historical_item, current_item],
            current_run_id=current_run_id,
        )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["evidence_id"], "current-market")

    def test_distillation_summary_includes_review_findings(self) -> None:
        evidences = [
            {
                "evidence_id": "summary-1",
                "title": "distillation_summary",
                "summary": "summary text",
                "source_refs": [],
                "metadata": {"source_type": "distillation_summary"},
            },
            {
                "evidence_id": "scorecard-1",
                "title": "distillation_scorecard",
                "summary": "distillation_scorecard\n{'setup_label': 'mixed_attribution', 'author_ratio': 0.5, 'review_strength': 'high'}",
                "source_refs": [],
                "metadata": {"source_type": "distillation_scorecard"},
            },
            {
                "evidence_id": "logic-1",
                "title": "logic_block:claim",
                "summary": "logic_block type=claim source_record_id=src-1 speaker_type=reply\ntext: logic summary\nattribution_label: reply_to_other_user",
                "source_refs": [],
                "metadata": {"source_type": "logic_block"},
            },
            {
                "evidence_id": "dimension-1",
                "title": "distilled_dimension:internet",
                "summary": "dimension summary",
                "source_refs": [],
                "metadata": {"source_type": "distilled_dimension"},
            },
            {
                "evidence_id": "review-1",
                "title": "review_finding:fallback_distillation",
                "summary": "review_finding type=fallback_distillation severity=high verdict=needs_review\nreason: fallback path used",
                "source_refs": [],
                "metadata": {"source_type": "review_finding"},
            },
        ]

        summary = self.service._structured_retrieval_summary(
            intent="distill_kol_logic",
            evidences=evidences,
        )
        selected = self.service._select_distillation_evidences(
            evidences=evidences,
            top_k=5,
        )

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("speaker_type_counts={'reply': 1}", summary)
        self.assertIn("scorecard={'setup_label': 'mixed_attribution', 'author_ratio': 0.5, 'review_strength': 'high'}", summary)
        self.assertIn("review_findings=1", summary)
        self.assertIn("review_types=['fallback_distillation']", summary)
        selected_types = [item["metadata"]["source_type"] for item in selected]
        self.assertIn("review_finding", selected_types)
        self.assertIn("distilled_dimension", selected_types)
        self.assertIn("distillation_scorecard", selected_types)

    def test_stock_check_summary_and_selection_include_methodology(self) -> None:
        evidences = [
            {
                "evidence_id": "method-dcf",
                "title": "dcf:000001",
                "summary": "methodology_result type=dcf symbol=000001\nvaluation_band={'low': 8.0, 'base': 9.0, 'high': 10.0}",
                "retrieval_hint": {
                    "hint_type": "methodology_result",
                    "methodology_type": "dcf",
                    "valuation_band_label": "8.0|9.0|10.0",
                },
                "source_refs": [],
                "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf"},
                "score": 1.0,
            },
            {
                "evidence_id": "market-1",
                "title": "market_snapshot:000001",
                "summary": "market snapshot for 000001\nsnapshot_date: 2026-06-28\nlatest_bar: {'close': 12.1}",
                "source_refs": [],
                "metadata": {"source_type": "market_snapshot"},
                "score": 0.8,
            },
        ]

        summary = self.service._structured_retrieval_summary(intent="stock_check", evidences=evidences)
        selected = self.service._select_stock_check_evidences(evidences=evidences, top_k=2)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("methodology_types=['dcf']", summary)
        self.assertEqual(selected[0]["metadata"]["source_type"], "methodology_dcf")

    def test_stock_check_summary_includes_methodology_memory(self) -> None:
        self.service.memory_service.read_recent_methodology_memory.return_value = [
            {
                "memory_type": "methodology_summary",
                "content": {
                    "title": "Promote dimension: policy catalyst",
                    "summary": "Policy catalysts worked in recent event-driven runs.",
                    "benchmark_scope": "event_driven",
                },
            }
        ]
        evidences = [
            {
                "evidence_id": "method-dcf",
                "title": "dcf:000001",
                "summary": "methodology_result type=dcf symbol=000001\nvaluation_band={'low': 8.0, 'base': 9.0, 'high': 10.0}",
                "retrieval_hint": {
                    "hint_type": "methodology_result",
                    "methodology_type": "dcf",
                    "valuation_band_label": "8.0|9.0|10.0",
                },
                "source_refs": [],
                "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf"},
                "score": 1.0,
            }
        ]

        summary = self.service._structured_retrieval_summary(intent="stock_check", evidences=evidences)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("methodology_memory=", summary)
        self.service.memory_service.search_methodology_memory.assert_not_called()

    def test_stock_check_selection_prioritizes_cashflow_when_query_and_thread_hint_request_resilience(self) -> None:
        evidences = [
            {
                "evidence_id": "method-dcf",
                "title": "dcf:000001",
                "summary": "methodology_result type=dcf symbol=000001\nvaluation_band={'low': 8.0, 'base': 9.0, 'high': 10.0}",
                "retrieval_hint": {
                    "hint_type": "methodology_result",
                    "methodology_type": "dcf",
                    "valuation_band_label": "8.0|9.0|10.0",
                },
                "source_refs": [],
                "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf"},
                "score": 1.0,
            },
            {
                "evidence_id": "method-cashflow",
                "title": "cashflow_resilience:000001",
                "summary": "methodology_result type=cashflow_resilience symbol=000001\nvaluation_band={}",
                "retrieval_hint": {
                    "hint_type": "methodology_result",
                    "methodology_type": "cashflow_resilience",
                    "valuation_band_label": None,
                },
                "source_refs": [],
                "metadata": {
                    "source_type": "methodology_cashflow_resilience",
                    "methodology_type": "cashflow_resilience",
                },
                "score": 0.6,
            },
            {
                "evidence_id": "market-1",
                "title": "market_snapshot:000001",
                "summary": "market snapshot for 000001\nsnapshot_date: 2026-06-28\nlatest_bar: {'close': 12.1}",
                "source_refs": [],
                "metadata": {"source_type": "market_snapshot"},
                "score": 0.8,
            },
        ]

        selected = self.service._select_stock_check_evidences(
            evidences=evidences,
            top_k=2,
            query="who is more resilient?",
            planning_context_hints={
                "recommended_next_step": "supplement same-basis cashflow and valuation assumptions",
                "thread_topic": "compare maotai and byd",
            },
        )

        self.assertEqual(selected[0]["metadata"]["methodology_type"], "cashflow_resilience")

    def test_compare_selection_prioritizes_comps_when_thread_hint_requests_same_basis_comparison(self) -> None:
        evidences = [
            {
                "evidence_id": "compare-request",
                "title": "compare_request",
                "summary": "symbols=['600519', '002594']",
                "source_refs": [],
                "metadata": {"source_type": "compare_request"},
                "score": 1.0,
            },
            {
                "evidence_id": "method-dcf",
                "title": "dcf:600519",
                "summary": "methodology_result type=dcf symbol=600519\nvaluation_band={'low': 8.0, 'base': 9.0, 'high': 10.0}",
                "retrieval_hint": {
                    "hint_type": "methodology_result",
                    "methodology_type": "dcf",
                    "valuation_band_label": "8.0|9.0|10.0",
                },
                "source_refs": [],
                "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf"},
                "score": 0.9,
            },
            {
                "evidence_id": "method-comps",
                "title": "comps:600519",
                "summary": "methodology_result type=comps symbol=600519\nvaluation_band={'low': 7.0, 'base': 8.0, 'high': 9.0}",
                "retrieval_hint": {
                    "hint_type": "methodology_result",
                    "methodology_type": "comps",
                    "valuation_band_label": "7.0|8.0|9.0",
                },
                "source_refs": [],
                "metadata": {"source_type": "methodology_comps", "methodology_type": "comps"},
                "score": 0.7,
            },
        ]

        selected = self.service._select_compare_evidences(
            evidences=evidences,
            top_k=3,
            query="continue the previous comparison",
            planning_context_hints={
                "recommended_next_step": "supplement same-basis cashflow and valuation assumptions",
                "thread_topic": "compare maotai and byd",
            },
        )

        self.assertEqual(selected[0]["metadata"]["source_type"], "compare_request")
        self.assertEqual(selected[1]["metadata"]["methodology_type"], "comps")

    def test_methodology_preferences_fall_back_to_retrieval_policy_preferred_methodologies(self) -> None:
        self.service.retrieval_policy = {"preferred_methodologies": ["dcf", "cashflow_resilience"]}

        preferences = self.service._methodology_preferences(query=None, planning_context_hints=None)

        self.assertEqual(preferences, ["dcf", "cashflow_resilience"])

    def test_methodology_preferences_keep_query_hint_priority_before_retrieval_policy_defaults(self) -> None:
        self.service.retrieval_policy = {"preferred_methodologies": ["dcf", "cashflow_resilience"]}

        preferences = self.service._methodology_preferences(
            query="who is more resilient?",
            planning_context_hints={
                "recommended_next_step": "supplement same-basis cashflow and valuation assumptions",
            },
        )

        self.assertEqual(preferences[0], "cashflow_resilience")
        self.assertIn("dcf", preferences)

    def test_stock_check_selection_uses_retrieval_policy_preferred_methodologies_when_query_is_neutral(self) -> None:
        self.service.retrieval_policy = {"preferred_methodologies": ["comps", "dcf"]}
        evidences = [
            {
                "evidence_id": "method-dcf",
                "title": "dcf:000001",
                "summary": "methodology_result type=dcf symbol=000001",
                "source_refs": [],
                "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf"},
                "score": 1.0,
            },
            {
                "evidence_id": "method-comps",
                "title": "comps:000001",
                "summary": "methodology_result type=comps symbol=000001",
                "source_refs": [],
                "metadata": {"source_type": "methodology_comps", "methodology_type": "comps"},
                "score": 0.5,
            },
        ]

        selected = self.service._select_stock_check_evidences(
            evidences=evidences,
            top_k=2,
            query="continue the previous work",
            planning_context_hints={},
        )

        self.assertEqual(selected[0]["metadata"]["methodology_type"], "comps")

    def test_methodology_preferences_can_fall_back_to_memory_benchmark_scope(self) -> None:
        self.service.memory_service.read_recent_methodology_memory.return_value = [
            {
                "memory_type": "dimension_experience",
                "content": {
                    "title": "Cashflow resilience heuristic",
                    "summary": "Prefer stable free cash flow and lower leverage.",
                    "benchmark_scope": "defensive_compounder",
                },
                "metadata": {
                    "memory_scope": "methodology",
                    "entity_scope": "methodology:dimension_experience:defensive_compounder",
                },
            }
        ]

        preferences = self.service._methodology_preferences(query=None, planning_context_hints=None)

        self.assertEqual(preferences, ["cashflow_resilience"])

    def test_methodology_memory_rows_pass_benchmark_scope_hint_into_memory_search(self) -> None:
        self.service.memory_service.search_methodology_memory.return_value = []

        rows = self.service._methodology_memory_rows(
            limit=3,
            query="continue the previous conclusion",
            planning_context_hints={"methodology_benchmark_scope": "defensive_compounder"},
        )

        self.assertEqual(rows, [])
        self.service.memory_service.search_methodology_memory.assert_called_once_with(
            query="continue the previous conclusion",
            limit=3,
            benchmark_scope="defensive_compounder",
        )

    def test_stock_check_selection_uses_memory_benchmark_scope_when_query_is_neutral(self) -> None:
        self.service.memory_service.read_recent_methodology_memory.return_value = [
            {
                "memory_type": "dimension_experience",
                "content": {
                    "title": "Cashflow resilience heuristic",
                    "summary": "Prefer stable free cash flow and lower leverage.",
                    "benchmark_scope": "defensive_compounder",
                    "recorded_at": "2026-06-28T00:00:00+00:00",
                },
                "metadata": {
                    "memory_scope": "methodology",
                    "entity_scope": "methodology:dimension_experience:defensive_compounder",
                },
            }
        ]
        evidences = [
            {
                "evidence_id": "method-dcf",
                "title": "dcf:000001",
                "summary": "methodology_result type=dcf symbol=000001",
                "source_refs": [],
                "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf"},
                "score": 1.0,
            },
            {
                "evidence_id": "method-cashflow",
                "title": "cashflow_resilience:000001",
                "summary": "methodology_result type=cashflow_resilience symbol=000001",
                "source_refs": [],
                "metadata": {
                    "source_type": "methodology_cashflow_resilience",
                    "methodology_type": "cashflow_resilience",
                },
                "score": 0.5,
            },
        ]

        selected = self.service._select_stock_check_evidences(
            evidences=evidences,
            top_k=2,
            query=None,
            planning_context_hints=None,
        )

        self.assertEqual(selected[0]["metadata"]["methodology_type"], "cashflow_resilience")

    def test_methodology_memory_support_rank_uses_match_score_and_freshness(self) -> None:
        self.service.memory_service.read_recent_methodology_memory.return_value = [
            {
                "memory_type": "dimension_experience",
                "match_score": 1.0,
                "content": {
                    "title": "Older DCF heuristic",
                    "summary": "Use DCF for intrinsic value work.",
                    "benchmark_scope": "dcf",
                    "recorded_at": "2026-01-01T00:00:00+00:00",
                },
            },
            {
                "memory_type": "dimension_experience",
                "match_score": 0.8,
                "content": {
                    "title": "Fresh cashflow heuristic",
                    "summary": "Prefer stable free cash flow and lower leverage.",
                    "benchmark_scope": "defensive_compounder",
                    "recorded_at": "2026-06-28T00:00:00+00:00",
                },
            },
        ]

        support = self.service._methodology_memory_support_rank(query=None, planning_context_hints=None)

        self.assertGreater(support["cashflow_resilience"], support["dcf"])

    def test_memory_support_rank_breaks_tie_within_same_preference_group(self) -> None:
        self.service.memory_service.read_recent_methodology_memory.return_value = [
            {
                "memory_type": "dimension_experience",
                "match_score": 2.0,
                "content": {
                    "title": "Fresh same-basis heuristic",
                    "summary": "Prefer relative peer comps in event driven setups.",
                    "benchmark_scope": "event_driven",
                    "recorded_at": "2026-06-28T00:00:00+00:00",
                },
            }
        ]
        evidences = [
            {
                "evidence_id": "method-dcf",
                "title": "dcf:600519",
                "summary": "methodology_result type=dcf symbol=600519",
                "source_refs": [],
                "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf"},
                "score": 1.0,
            },
            {
                "evidence_id": "method-comps",
                "title": "comps:600519",
                "summary": "methodology_result type=comps symbol=600519",
                "source_refs": [],
                "metadata": {"source_type": "methodology_comps", "methodology_type": "comps"},
                "score": 0.5,
            },
        ]

        selected = self.service._select_compare_evidences(
            evidences=evidences,
            top_k=2,
            query="continue the previous comparison",
            planning_context_hints={},
        )

        self.assertEqual(selected[0]["metadata"]["methodology_type"], "comps")

    def test_policy_adjusted_score_uses_score_weights_and_metadata_bonus(self) -> None:
        self.service.retrieval_policy = {
            "score_weights": {
                "semantic_similarity": 2.0,
                "metadata_match": 1.5,
            }
        }
        score = self.service._policy_adjusted_score(
            {
                "score": 0.5,
                "metadata": {
                    "source_type": "methodology_cashflow_resilience",
                    "methodology_type": "cashflow_resilience",
                },
            }
        )

        self.assertAlmostEqual(score, 1.45)

    def test_dedupe_candidates_resort_after_policy_adjustment(self) -> None:
        self.service.retrieval_policy = {
            "rerank_enabled": True,
            "score_weights": {
                "semantic_similarity": 1.0,
                "metadata_match": 2.0,
            }
        }
        outputs = self.service._dedupe_evidence_candidates(
            evidences=[
                {
                    "evidence_id": "snapshot",
                    "score": 0.9,
                    "metadata": {"source_type": "market_snapshot"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "method-cashflow",
                    "score": 0.5,
                    "metadata": {
                        "source_type": "methodology_cashflow_resilience",
                        "methodology_type": "cashflow_resilience",
                    },
                    "source_refs": [],
                },
            ],
            current_run_id=None,
        )

        self.assertEqual(outputs["evidences"][0]["evidence_id"], "method-cashflow")

    def test_dedupe_candidates_respects_final_top_k(self) -> None:
        self.service.retrieval_policy = {"final_top_k": 1, "rerank_enabled": False}

        outputs = self.service._dedupe_evidence_candidates(
            evidences=[
                {
                    "evidence_id": "one",
                    "score": 0.9,
                    "metadata": {"source_type": "market_snapshot"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "two",
                    "score": 0.8,
                    "metadata": {"source_type": "market_snapshot"},
                    "source_refs": [],
                },
            ],
            current_run_id=None,
        )

        self.assertEqual(len(outputs["evidences"]), 1)
        self.assertEqual(outputs["evidences"][0]["evidence_id"], "one")

    def test_dedupe_candidates_can_skip_policy_rerank_when_disabled(self) -> None:
        self.service.retrieval_policy = {
            "rerank_enabled": False,
            "score_weights": {
                "semantic_similarity": 1.0,
                "metadata_match": 5.0,
            },
        }

        outputs = self.service._dedupe_evidence_candidates(
            evidences=[
                {
                    "evidence_id": "snapshot",
                    "score": 0.9,
                    "metadata": {"source_type": "market_snapshot"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "method-cashflow",
                    "score": 0.5,
                    "metadata": {
                        "source_type": "methodology_cashflow_resilience",
                        "methodology_type": "cashflow_resilience",
                    },
                    "source_refs": [],
                },
            ],
            current_run_id=None,
        )

        self.assertEqual(outputs["evidences"][0]["evidence_id"], "snapshot")

    def test_dedupe_candidates_apply_recency_window_to_snapshot_evidence(self) -> None:
        self.service.retrieval_policy = {
            "recency_window_days": 7,
            "rerank_enabled": False,
        }

        outputs = self.service._dedupe_evidence_candidates(
            evidences=[
                {
                    "evidence_id": "recent-snapshot",
                    "score": 0.7,
                    "summary": "market snapshot for 000001\nsnapshot_date: 2026-06-28\nlatest_bar: {'close': 12.3}",
                    "metadata": {"source_type": "market_snapshot"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "stale-snapshot",
                    "score": 0.95,
                    "summary": "market snapshot for 000001\nsnapshot_date: 2026-05-01\nlatest_bar: {'close': 11.1}",
                    "metadata": {"source_type": "market_snapshot"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "method-cashflow",
                    "score": 0.6,
                    "summary": "methodology_result type=cashflow_resilience symbol=000001",
                    "metadata": {
                        "source_type": "methodology_cashflow_resilience",
                        "methodology_type": "cashflow_resilience",
                    },
                    "source_refs": [],
                },
            ],
            current_run_id=None,
        )

        evidence_ids = [item["evidence_id"] for item in outputs["evidences"]]
        self.assertIn("recent-snapshot", evidence_ids)
        self.assertNotIn("stale-snapshot", evidence_ids)
        self.assertIn("method-cashflow", evidence_ids)

    def test_methodology_evolution_summary_includes_pending_reasons_and_status_counts(self) -> None:
        evidences = [
            {
                "evidence_id": "proposal-1",
                "title": "proposal_draft:MethodologyTemplatePatch",
                "summary": "proposal_draft type=MethodologyTemplatePatch status=pending\ntitle=Promote dimension: policy catalyst\nsummary=Promote candidate\nreview_reason=pending_manual_review_dimension_sample_size<2",
                "retrieval_hint": {
                    "hint_type": "proposal_draft",
                    "proposal_type": "MethodologyTemplatePatch",
                    "status": "pending",
                },
                "source_refs": [],
                "metadata": {"source_type": "proposal_draft"},
                "score": 1.0,
            },
            {
                "evidence_id": "proposal-2",
                "title": "proposal_draft:MemoryLearningUpdate",
                "summary": "proposal_draft type=MemoryLearningUpdate status=approved\ntitle=Capture review failure pattern\nsummary=Persist pattern\nreview_reason=auto_approve_review_pattern_threshold_met",
                "retrieval_hint": {
                    "hint_type": "proposal_draft",
                    "proposal_type": "MemoryLearningUpdate",
                    "status": "approved",
                },
                "source_refs": [],
                "metadata": {"source_type": "proposal_draft"},
                "score": 0.9,
            },
        ]

        summary = self.service._structured_retrieval_summary(intent="methodology_evolution", evidences=evidences)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("status_counts={'pending': 1, 'approved': 1}", summary)
        self.assertIn("pending_reasons=['pending_manual_review_dimension_sample_size<2'", summary)

    def test_stock_compare_summary_and_selection_include_bank_scorecard(self) -> None:
        evidences = [
            {
                "evidence_id": "compare-request",
                "title": "stock_compare input request",
                "summary": "stock_compare input request\n{'symbols': ['000001', '600036'], 'compare_targets': [{'symbol': '000001', 'name': '平安银行'}, {'symbol': '600036', 'name': '招商银行'}]}",
                "source_refs": [],
                "metadata": {"source_type": "compare_request"},
                "score": 1.0,
            },
            {
                "evidence_id": "bank-scorecard",
                "title": "bank_compare_scorecard",
                "summary": "bank_compare_scorecard\n{'candidate_count': 2, 'metric_weights': {'pb': 0.24, 'roe': 0.24}, 'ranked_candidates': [{'symbol': '600036', 'name': '招商银行', 'score': 0.91, 'pb': 0.78}, {'symbol': '000001', 'name': '平安银行', 'score': 0.62, 'pb': 0.42}], 'summary': 'bank peer comparison ranked 2 banks using same-basis banking metrics and valuation anchors'}",
                "retrieval_hint": {
                    "hint_type": "bank_compare_scorecard",
                    "candidate_count": 2,
                    "ranked_candidates": [{"symbol": "600036", "name": "招商银行", "score": 0.91, "pb": 0.78}],
                    "summary": "bank peer comparison ranked 2 banks using same-basis banking metrics and valuation anchors",
                },
                "source_refs": [],
                "metadata": {"source_type": "bank_compare_scorecard"},
                "score": 0.95,
            },
            {
                "evidence_id": "method-comps",
                "title": "comps:600036",
                "summary": "methodology_result type=comps symbol=600036",
                "source_refs": [],
                "metadata": {"source_type": "methodology_comps", "methodology_type": "comps"},
                "score": 0.8,
            },
        ]

        summary = self.service._structured_retrieval_summary(intent="stock_compare", evidences=evidences)
        selected = self.service._select_compare_evidences(evidences=evidences, top_k=3)

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("bank_candidate_count=2", summary)
        self.assertIn("bank_top_tier=['招商银行', '平安银行']", summary)
        self.assertIn("valuation anchors", summary)
        self.assertEqual(selected[0]["metadata"]["source_type"], "compare_request")
        self.assertEqual(selected[1]["metadata"]["source_type"], "bank_compare_scorecard")

    def test_run_recalls_qdrant_and_methodology_memory_concurrently(self) -> None:
        barrier = threading.Barrier(2, timeout=1.0)

        class _ConcurrentQdrant:
            available = True

            def upsert_entry(self, entry) -> None:
                return None

            def search(self, *, query_text, top_k, metadata_filters):
                barrier.wait()
                return []

        service = self.service
        service.qdrant = _ConcurrentQdrant()
        service.indexer_qdrant = service.qdrant
        service.searcher_qdrant = service.qdrant
        service.qdrant_available = True
        service.qdrant_error = None
        service.memory_service.search_methodology_memory.side_effect = lambda **kwargs: (barrier.wait(), [])[1]

        outputs = service._run_impl(
            intent="stock_check",
            query="which bank is more resilient on cash flow",
            blackboard_entries=[],
            planning_context_hints={"methodology_benchmark_scope": "cashflow_resilience"},
            allow_llm_decision=False,
            runtime_degraded_reason="runtime_policy_fallback",
        )

        trace_by_path = {
            item.get("agent_path"): item
            for item in list(outputs.get("subagent_trace") or [])
        }
        self.assertEqual(trace_by_path["retriever/search/qdrant_evidence_searcher"]["status"], "completed")
        self.assertEqual(trace_by_path["retriever/search/methodology_memory_recaller"]["status"], "completed")
        self.service.memory_service.search_methodology_memory.assert_called_once()

    def test_run_emits_nested_retrieval_trace_context(self) -> None:
        service = self.service
        service.disable_qdrant_for_test("disabled_for_test")
        outputs = service.run(
            intent="stock_compare",
            query="compare banks",
            blackboard_entries=[
                {
                    "entry_id": "src-1",
                    "run_id": "01TRACE",
                    "kind": "source_record",
                    "phase": "collect",
                    "agent": "collector",
                    "payload": {"title": "compare request", "content": "compare", "source_type": "compare_request"},
                    "source_refs": [],
                },
                {
                    "entry_id": "snap-1",
                    "run_id": "01TRACE",
                    "kind": "verified_snapshot",
                    "phase": "collect",
                    "agent": "collector",
                    "payload": {"title": "bank compare scorecard", "content": "scorecard", "source_type": "bank_compare_scorecard"},
                    "source_refs": [],
                },
            ],
        )

        trace = list(outputs.get("subagent_trace") or [])
        self.assertEqual(
            [item.get("agent_path") for item in trace],
            [
                "retriever/indexing/blackboard_evidence_indexer",
                "retriever/search/qdrant_evidence_searcher",
                "retriever/search/methodology_memory_recaller",
                "retriever/decision/retrieval_decider",
            ],
        )

    def test_search_qdrant_evidences_reports_hit_count(self) -> None:
        class _StubQdrant:
            def search(self, *, query_text, top_k, metadata_filters):
                return [
                    {
                        "score": 0.9,
                        "payload": {
                            "entry_id": "entry-1",
                            "document_title": "market_snapshot:000001",
                            "content": "market snapshot for 000001",
                            "source_refs": [],
                            "phase": "collect",
                            "agent": "collector",
                            "record_type": "verified_snapshot",
                            "source_type": "market_snapshot",
                            "symbol": "000001",
                            "sector": None,
                            "dimension_family": None,
                            "dimension_key": None,
                            "scenario_tags": [],
                            "methodology_tags": [],
                            "dimension_tags": [],
                            "methodology_type": None,
                            "valuation_band_label": None,
                        },
                    }
                ]

        self.service.searcher_qdrant = _StubQdrant()
        self.service.qdrant_error = None

        outputs = self.service._search_qdrant_evidences(
            intent="stock_check",
            query="check 000001",
            top_k=3,
            symbol="000001",
            sector=None,
            current_run_id="01RUN",
        )

        self.assertEqual(outputs["hit_count"], 1)
        self.assertEqual(outputs["evidences"][0]["evidence_id"], "entry-1")


if __name__ == "__main__":
    unittest.main()
