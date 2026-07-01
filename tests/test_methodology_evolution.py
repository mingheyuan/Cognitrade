from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.jsonl import append_jsonl, read_json, read_jsonl, write_json
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry
from research_system.services.memory_service import MemoryService
from research_system.adapters.storage.mem0_adapter import Mem0Adapter


class MethodologyEvolutionTests(unittest.TestCase):
    def test_manual_proposal_review_approve_updates_manifest_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)

            target_run_id = "01TARGETRUN"
            run_root = settings.runs_root / target_run_id
            run_root.mkdir(parents=True, exist_ok=True)
            append_jsonl(
                run_root / "blackboard.jsonl",
                BlackboardEntry(
                    run_id=target_run_id,
                    request_id="req-target",
                    kind="distilled_dimension",
                    agent="distiller",
                    phase="distill",
                    payload={
                        "dimension_id": "dim-1",
                        "dimension_name": "policy catalyst",
                        "canonical_dimension_name": "policy catalyst",
                        "dimension_category": "catalyst",
                        "dimension_family": "event_driven",
                        "scenario_tags": ["short_term"],
                        "methodology_tags": ["event_driven"],
                        "supported_claim_block_ids": ["logic-1"],
                        "evidence_block_ids": [],
                        "alias_hits": ["policy"],
                        "is_new_candidate": True,
                        "confidence": 0.7,
                        "normalization_notes": [],
                    },
                    source_refs=[{"type": "distillation", "run_id": target_run_id}],
                ).model_dump(mode="json"),
            )
            write_json(run_root / "run_state.json", {"run_id": target_run_id, "status": "completed", "intent": "distill_kol_logic"})
            write_json(run_root / "result.json", {"intent": "distill_kol_logic", "report": {"conclusion": "done"}})

            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="methodology_evolution",
                        user_query="promote methodology learnings",
                        constraints={"target_run_id": target_run_id, "auto_approve": True},
                    )
                )
                self.assertEqual(response.status, "completed")
                pending_files = list((settings.proposals_root / "pending").glob("*.json"))
                self.assertTrue(pending_files)
                proposal_id = pending_files[0].stem

                reviewed = orchestrator.review_proposal(
                    proposal_id,
                    decision="approve",
                    reviewer="manual_reviewer",
                    reason="manual approval after review",
                )

            self.assertEqual(reviewed["status"], "approved")
            approved_files = list((settings.proposals_root / "approved").glob("*.json"))
            self.assertTrue(approved_files)
            approved_payload = read_json(approved_files[0]) or {}
            self.assertEqual(approved_payload.get("reviewer"), "manual_reviewer")
            self.assertEqual(approved_payload.get("review_reason"), "manual approval after review")
            self.assertEqual(len(approved_payload.get("review_history") or []), 2)
            self.assertEqual((approved_payload.get("review_history") or [])[0].get("source"), "auto_gate")
            self.assertEqual((approved_payload.get("review_history") or [])[1].get("source"), "manual_cli_review")
            manifest_text = settings.methodology_manifest_path.read_text(encoding="utf-8")
            self.assertIn(proposal_id, manifest_text)
            retrieval_policy_text = settings.retrieval_policy_path.read_text(encoding="utf-8")
            self.assertIn(proposal_id, retrieval_policy_text)
            memory_exports = list(read_jsonl(settings.memory_root / "exports" / "mem0_exports.jsonl"))
            self.assertTrue(memory_exports)

    def test_methodology_evolution_keeps_dimension_proposal_pending_when_sample_size_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)

            target_run_id = "01TARGETRUN"
            run_root = settings.runs_root / target_run_id
            run_root.mkdir(parents=True, exist_ok=True)
            append_jsonl(
                run_root / "blackboard.jsonl",
                BlackboardEntry(
                    run_id=target_run_id,
                    request_id="req-target",
                    kind="distilled_dimension",
                    agent="distiller",
                    phase="distill",
                    payload={
                        "dimension_id": "dim-1",
                        "dimension_name": "policy catalyst",
                        "canonical_dimension_name": "policy catalyst",
                        "dimension_category": "catalyst",
                        "dimension_family": "event_driven",
                        "scenario_tags": ["short_term"],
                        "methodology_tags": ["event_driven"],
                        "supported_claim_block_ids": ["logic-1"],
                        "evidence_block_ids": [],
                        "alias_hits": ["policy"],
                        "is_new_candidate": True,
                        "confidence": 0.7,
                        "normalization_notes": [],
                    },
                    source_refs=[{"type": "distillation", "run_id": target_run_id}],
                ).model_dump(mode="json"),
            )
            write_json(run_root / "run_state.json", {"run_id": target_run_id, "status": "completed", "intent": "distill_kol_logic"})
            write_json(run_root / "result.json", {"intent": "distill_kol_logic", "report": {"conclusion": "done"}})

            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="methodology_evolution",
                        user_query="promote methodology learnings",
                        constraints={"target_run_id": target_run_id, "auto_approve": True},
                    )
                )

            self.assertEqual(response.status, "completed")
            approved_files = list((settings.proposals_root / "approved").glob("*.json"))
            pending_files = list((settings.proposals_root / "pending").glob("*.json"))
            self.assertFalse(approved_files)
            self.assertTrue(pending_files)
            pending_payload = read_json(pending_files[0])
            self.assertEqual((pending_payload or {}).get("status"), "pending")
            self.assertIn("pending_manual_review_dimension_sample_size<2", str((pending_payload or {}).get("review_reason") or ""))
            self.assertEqual(len((pending_payload or {}).get("review_history") or []), 1)
            self.assertEqual(((pending_payload or {}).get("review_history") or [])[0].get("source"), "auto_gate")

    def test_methodology_evolution_keeps_single_run_dimension_and_valuation_proposals_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)

            target_run_id = "01TARGETRUN"
            run_root = settings.runs_root / target_run_id
            run_root.mkdir(parents=True, exist_ok=True)
            append_jsonl(
                run_root / "blackboard.jsonl",
                BlackboardEntry(
                    run_id=target_run_id,
                    request_id="req-target",
                    kind="distilled_dimension",
                    agent="distiller",
                    phase="distill",
                    payload={
                        "dimension_id": "dim-1",
                        "dimension_name": "policy catalyst",
                        "canonical_dimension_name": "policy catalyst",
                        "dimension_category": "catalyst",
                        "dimension_family": "event_driven",
                        "scenario_tags": ["short_term"],
                        "methodology_tags": ["event_driven"],
                        "supported_claim_block_ids": ["logic-1"],
                        "evidence_block_ids": [],
                        "alias_hits": ["policy"],
                        "is_new_candidate": True,
                        "confidence": 0.7,
                        "normalization_notes": [],
                    },
                    source_refs=[{"type": "distillation", "run_id": target_run_id}],
                ).model_dump(mode="json"),
            )
            append_jsonl(
                run_root / "blackboard.jsonl",
                BlackboardEntry(
                    run_id=target_run_id,
                    request_id="req-target",
                    kind="source_record",
                    agent="collector",
                    phase="collect",
                    payload={
                        "title": "methodology_dcf:000001",
                        "content": "{}",
                        "source_type": "methodology_dcf",
                        "methodology_type": "dcf",
                        "symbol": "000001",
                        "assumption_tags": ["light_dcf"],
                        "sensitivity_tags": ["wacc"],
                        "valuation_band_label": "8.0|9.0|10.0",
                        "methodology_result": {
                            "outputs": {"intrinsic_value_proxy": 9.0},
                            "warnings": [],
                        },
                    },
                    source_refs=[{"type": "stock_check_methodology", "run_id": target_run_id}],
                ).model_dump(mode="json"),
            )
            write_json(run_root / "run_state.json", {"run_id": target_run_id, "status": "completed", "intent": "stock_check"})
            write_json(run_root / "result.json", {"intent": "stock_check", "report": {"conclusion": "done"}})

            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="methodology_evolution",
                        user_query="promote methodology learnings",
                        constraints={"target_run_id": target_run_id, "auto_approve": True},
                    )
                )

            self.assertEqual(response.status, "completed")
            report = (response.result or {}).get("report") or {}
            self.assertIn("conclusion", report)

            pending_files = list((settings.proposals_root / "pending").glob("*.json"))
            approved_files = list((settings.proposals_root / "approved").glob("*.json"))
            self.assertTrue(pending_files)
            self.assertFalse(approved_files)
            pending_payloads = [read_json(path) for path in pending_files]
            self.assertTrue(
                any("pending_manual_review" in str((payload or {}).get("review_reason") or "") for payload in pending_payloads)
            )

            memory_exports = list(read_jsonl(settings.memory_root / "exports" / "mem0_exports.jsonl"))
            self.assertFalse(memory_exports)

    def test_methodology_evolution_supports_target_run_ids_and_generates_memory_learning_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)

            for target_run_id, severity, verdict in (
                ("01TARGETA", "high", "rejected"),
                ("01TARGETB", "medium", "confirmed"),
            ):
                run_root = settings.runs_root / target_run_id
                run_root.mkdir(parents=True, exist_ok=True)
                append_jsonl(
                    run_root / "blackboard.jsonl",
                    BlackboardEntry(
                        run_id=target_run_id,
                        request_id=f"req-{target_run_id}",
                        kind="review_finding",
                        agent="reviewer",
                        phase="review",
                        payload={
                            "finding_type": "review_pattern",
                            "severity": severity,
                            "verdict": verdict,
                        },
                        source_refs=[{"type": "review", "run_id": target_run_id}],
                    ).model_dump(mode="json"),
                )
                write_json(run_root / "run_state.json", {"run_id": target_run_id, "status": "completed", "intent": "review_thesis"})
                write_json(run_root / "result.json", {"intent": "review_thesis", "report": {"conclusion": "done"}})

            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="methodology_evolution",
                        user_query="aggregate recurring review patterns",
                        constraints={
                            "target_run_ids": ["01TARGETA", "01TARGETB"],
                            "auto_approve": True,
                        },
                    )
                )

            self.assertEqual(response.status, "completed")
            approved_files = list((settings.proposals_root / "approved").glob("*.json"))
            self.assertTrue(approved_files)
            approved_payloads = [read_json(path) for path in approved_files]
            proposal_types = {
                str((payload or {}).get("proposal_type") or "")
                for payload in approved_payloads
                if isinstance(payload, dict)
            }
            self.assertIn("MemoryLearningUpdate", proposal_types)

            memory_exports = list(read_jsonl(settings.memory_root / "exports" / "mem0_exports.jsonl"))
            self.assertTrue(memory_exports)
            self.assertTrue(
                any(str(item.get("memory_type") or "") == "error_pattern" for item in memory_exports)
            )

    def test_methodology_evolution_keeps_low_confidence_dimension_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)

            for target_run_id in ("01TARGETA", "01TARGETB"):
                run_root = settings.runs_root / target_run_id
                run_root.mkdir(parents=True, exist_ok=True)
                append_jsonl(
                    run_root / "blackboard.jsonl",
                    BlackboardEntry(
                        run_id=target_run_id,
                        request_id=f"req-{target_run_id}",
                        kind="distilled_dimension",
                        agent="distiller",
                        phase="distill",
                        payload={
                            "dimension_id": f"dim-{target_run_id}",
                            "dimension_name": "policy catalyst",
                            "canonical_dimension_name": "policy catalyst",
                            "dimension_category": "catalyst",
                            "dimension_family": "event_driven",
                            "scenario_tags": ["short_term"],
                            "methodology_tags": ["event_driven"],
                            "supported_claim_block_ids": ["logic-1"],
                            "evidence_block_ids": [],
                            "alias_hits": ["policy"],
                            "is_new_candidate": True,
                            "confidence": 0.2,
                            "normalization_notes": [],
                        },
                        source_refs=[{"type": "distillation", "run_id": target_run_id}],
                    ).model_dump(mode="json"),
                )
                write_json(run_root / "run_state.json", {"run_id": target_run_id, "status": "completed", "intent": "distill_kol_logic"})
                write_json(run_root / "result.json", {"intent": "distill_kol_logic", "report": {"conclusion": "done"}})

            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="methodology_evolution",
                        user_query="promote policy catalyst learnings",
                        constraints={
                            "target_run_ids": ["01TARGETA", "01TARGETB"],
                            "auto_approve": True,
                        },
                    )
                )

            self.assertEqual(response.status, "completed")
            pending_files = list((settings.proposals_root / "pending").glob("*.json"))
            self.assertTrue(pending_files)
            pending_payload = read_json(pending_files[0]) or {}
            self.assertIn("pending_manual_review_dimension_confidence<0.5", str(pending_payload.get("review_reason") or ""))

    def test_methodology_evolution_keeps_stale_memory_supported_dimension_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            memory_service = MemoryService(Mem0Adapter(settings))

            memory_service.write_methodology_memory(
                memory_type="dimension_experience",
                content={
                    "title": "policy catalyst",
                    "summary": "older event-driven memory",
                    "benchmark_scope": "event_driven",
                    "recorded_at": "2025-01-01T00:00:00+00:00",
                },
                metadata={"status": "approved", "recorded_at": "2025-01-01T00:00:00+00:00"},
            )

            for target_run_id in ("01TARGETA", "01TARGETB"):
                run_root = settings.runs_root / target_run_id
                run_root.mkdir(parents=True, exist_ok=True)
                append_jsonl(
                    run_root / "blackboard.jsonl",
                    BlackboardEntry(
                        run_id=target_run_id,
                        request_id=f"req-{target_run_id}",
                        kind="distilled_dimension",
                        agent="distiller",
                        phase="distill",
                        payload={
                            "dimension_id": f"dim-{target_run_id}",
                            "dimension_name": "policy catalyst",
                            "canonical_dimension_name": "policy catalyst",
                            "dimension_category": "catalyst",
                            "dimension_family": "event_driven",
                            "scenario_tags": ["short_term"],
                            "methodology_tags": ["event_driven"],
                            "supported_claim_block_ids": ["logic-1"],
                            "evidence_block_ids": [],
                            "alias_hits": ["policy"],
                            "is_new_candidate": True,
                            "confidence": 0.8,
                            "normalization_notes": [],
                        },
                        source_refs=[{"type": "distillation", "run_id": target_run_id}],
                    ).model_dump(mode="json"),
                )
                write_json(run_root / "run_state.json", {"run_id": target_run_id, "status": "completed", "intent": "distill_kol_logic"})
                write_json(run_root / "result.json", {"intent": "distill_kol_logic", "report": {"conclusion": "done"}})

            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="methodology_evolution",
                        user_query="promote policy catalyst learnings",
                        constraints={
                            "target_run_ids": ["01TARGETA", "01TARGETB"],
                            "auto_approve": True,
                            "max_memory_support_age_days": 30,
                        },
                    )
                )

            self.assertEqual(response.status, "completed")
            pending_files = list((settings.proposals_root / "pending").glob("*.json"))
            self.assertTrue(pending_files)
            pending_payload = read_json(pending_files[0]) or {}
            self.assertIn("pending_manual_review_memory_support_stale>30d", str(pending_payload.get("review_reason") or ""))

    def test_methodology_evolution_keeps_single_scope_memory_supported_dimension_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            memory_service = MemoryService(Mem0Adapter(settings))

            memory_service.write_methodology_memory(
                memory_type="dimension_experience",
                content={
                    "title": "policy catalyst",
                    "summary": "only one event-driven memory scope",
                    "benchmark_scope": "event_driven",
                    "recorded_at": "2026-06-20T00:00:00+00:00",
                },
                metadata={"status": "approved", "recorded_at": "2026-06-20T00:00:00+00:00"},
            )

            for target_run_id in ("01TARGETA", "01TARGETB"):
                run_root = settings.runs_root / target_run_id
                run_root.mkdir(parents=True, exist_ok=True)
                append_jsonl(
                    run_root / "blackboard.jsonl",
                    BlackboardEntry(
                        run_id=target_run_id,
                        request_id=f"req-{target_run_id}",
                        kind="distilled_dimension",
                        agent="distiller",
                        phase="distill",
                        payload={
                            "dimension_id": f"dim-{target_run_id}",
                            "dimension_name": "policy catalyst",
                            "canonical_dimension_name": "policy catalyst",
                            "dimension_category": "catalyst",
                            "dimension_family": "event_driven",
                            "scenario_tags": ["short_term"],
                            "methodology_tags": ["event_driven"],
                            "supported_claim_block_ids": ["logic-1"],
                            "evidence_block_ids": [],
                            "alias_hits": ["policy"],
                            "is_new_candidate": True,
                            "confidence": 0.8,
                            "normalization_notes": [],
                        },
                        source_refs=[{"type": "distillation", "run_id": target_run_id}],
                    ).model_dump(mode="json"),
                )
                write_json(run_root / "run_state.json", {"run_id": target_run_id, "status": "completed", "intent": "distill_kol_logic"})
                write_json(run_root / "result.json", {"intent": "distill_kol_logic", "report": {"conclusion": "done"}})

            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="methodology_evolution",
                        user_query="promote policy catalyst learnings",
                        constraints={
                            "target_run_ids": ["01TARGETA", "01TARGETB"],
                            "auto_approve": True,
                            "min_dimension_memory_support_entity_scope_count": 2,
                        },
                    )
                )

            self.assertEqual(response.status, "completed")
            pending_files = list((settings.proposals_root / "pending").glob("*.json"))
            self.assertTrue(pending_files)
            pending_payload = read_json(pending_files[0]) or {}
            self.assertIn(
                "pending_manual_review_memory_support_entity_scope_count<2",
                str(pending_payload.get("review_reason") or ""),
            )


if __name__ == "__main__":
    unittest.main()
