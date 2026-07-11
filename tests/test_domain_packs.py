from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.domain_packs.skill_runtime import PackSkillRuntime
from research_system.runtime.execution.core.execution_graph import ExecutionGraphScheduler
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.schemas.domain_pack import (
    ArtifactTypeDefinition,
    CapabilityDefinition,
    CapabilityPlanningRequest,
    DomainPackManifest,
    IntentDefinition,
    PackIntakeDefinition,
)
from research_system.schemas.artifacts import ArtifactDraft
from research_system.runtime.platform.settings import Settings


class DomainPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")

    def test_pack_intake_rejects_unknown_capability_or_artifact(self) -> None:
        with self.assertRaises(ValueError):
            DomainPackManifest(
                domain_id="test",
                version="1",
                description="test",
                artifact_types=[ArtifactTypeDefinition(artifact_type="input")],
                capabilities=[
                    CapabilityDefinition(
                        subagent_type="worker",
                        capability="test.worker",
                        consumes=[{"artifact_type": "input"}],
                        produces=[{"artifact_type": "input"}],
                    )
                ],
                intake=PackIntakeDefinition(
                    implementation="subagents.runtime:resolve",
                    intents=[
                        IntentDefinition(
                            intent_id="test_intent",
                            candidate_capabilities=["test.missing"],
                            output_artifact_types=["missing_artifact"],
                        )
                    ],
                ),
            )

    def test_loads_finance_and_non_finance_packs(self) -> None:
        self.assertEqual(
            [manifest.domain_id for manifest in self.registry.list()],
            ["finance_ashare", "legal_contract"],
        )
        self.assertTrue(
            self.registry.capabilities(
                domain_id="legal_contract",
                produces="legal_review_report",
            )
        )
        self.assertEqual(
            {
                skill.skill_name
                for skill in self.registry.skill_definitions(domain_id="finance_ashare")
            },
            {
                "market_snapshot_skill",
                "fundamental_snapshot_skill",
                "notice_snapshot_skill",
                "sector_snapshot_skill",
                "lhb_snapshot_skill",
                "capital_flow_snapshot_skill",
                "sentiment_snapshot_skill",
                "events_snapshot_skill",
                "policy_snapshot_skill",
            },
        )
        self.assertEqual(self.registry.skill_definitions(domain_id="legal_contract"), [])

    def test_pack_skill_resolves_from_pack_root(self) -> None:
        skill_class = self.registry.resolve_skill(
            domain_id="finance_ashare",
            skill_name="market_snapshot_skill",
        )
        self.assertTrue(skill_class.__module__.startswith("cognitrade_domain_pack_finance_ashare_"))
        self.assertNotIn("research_system.skills", skill_class.__module__)
        self.assertEqual(skill_class.skill_name, "market_snapshot_skill")

    def test_pack_skill_runtime_executes_declared_skill_in_pack_scope(self) -> None:
        settings = Settings.from_env(Path(__file__).resolve().parents[1])
        runtime = PackSkillRuntime(
            registry=self.registry,
            settings=settings,
            domain_id="finance_ashare",
        )
        result = runtime.run("market_snapshot_skill", query="market status", symbol=None)
        self.assertEqual(result["snapshot_type"], "market")
        self.assertEqual(result["degrade_reason"], "symbol_missing")

    def test_capability_planner_builds_non_finance_graph_without_domain_branches(self) -> None:
        graph = CapabilityPlanner(self.registry).build_graph(
            CapabilityPlanningRequest(
                domain_id="legal_contract",
                objective="Review this contract for risks and obligations.",
                available_artifact_types=["contract_document"],
                required_artifact_types=["legal_review_report"],
            )
        )

        node_map = {node.node_id: node for node in graph.nodes}
        self.assertEqual(graph.domain_id, "legal_contract")
        self.assertEqual(graph.governance.required_gate_ids, ["legal.citation_required"])
        self.assertEqual(node_map["legal_clause_extractor"].depends_on, [])
        self.assertEqual(
            set(node_map["legal_report_composer"].depends_on),
            {"legal_risk_reviewer", "legal_obligation_analyzer"},
        )
        scheduler = ExecutionGraphScheduler()
        self.assertEqual(
            [node.node_id for node in scheduler.ready_leaf_nodes(graph)],
            ["legal_clause_extractor"],
        )
        self.assertEqual(
            {
                node.node_id
                for node in scheduler.ready_leaf_nodes(
                    graph,
                    completed_node_ids=["legal_clause_extractor"],
                )
            },
            {"legal_risk_reviewer", "legal_obligation_analyzer"},
        )

    def test_capability_planner_builds_finance_graph_from_same_core(self) -> None:
        graph = CapabilityPlanner(self.registry).build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="Research an A-share company.",
                available_artifact_types=["user_request"],
                required_artifact_types=["research_report"],
                allowed_tools=["financial_data_provider"],
            )
        )

        self.assertEqual(
            [node.executor for node in graph.nodes if node.node_kind == "subagent"],
            [
                "query_normalizer",
                "stock_snapshot_collector",
                "retrieval_decider",
                "financial_analyst",
                "llm_report_composer",
            ],
        )

    def test_finance_pack_selects_distillation_graph_from_intent_policy(self) -> None:
        graph = CapabilityPlanner(self.registry).build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="Distill investment logic from source records.",
                available_artifact_types=["user_request"],
                required_artifact_types=["research_report"],
                allowed_tools=["financial_data_provider"],
                metadata={
                    "intent": "distill_kol_logic",
                    "intent_inputs": {
                        "source_records": [
                            {"id": "record-1", "text": "Focus on cash flow."}
                        ]
                    },
                },
            )
        )

        self.assertEqual(
            [node.executor for node in graph.nodes if node.node_kind == "subagent"],
            [
                "query_normalizer",
                "distillation_source_collector",
                "kol_logic_distiller",
                "distillation_reviewer",
                "distillation_report_composer",
            ],
        )

    def test_installing_an_unseen_third_domain_requires_no_core_change(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pack = root / "audit_sample"
            for directory in DomainPackRegistry.REQUIRED_DIRECTORIES:
                (pack / directory).mkdir(parents=True, exist_ok=True)
            (pack / "domain.yaml").write_text(
                """
domain_id: audit_sample
version: 1.0.0
description: Runtime-installed audit proof pack.
artifact_types:
  - artifact_type: audit_request
  - artifact_type: audit_report
capabilities:
  - subagent_type: audit_executor
    capability: audit.execute
    implementation: subagents.runtime:execute
    consumes:
      - artifact_type: audit_request
    produces:
      - artifact_type: audit_report
quality_gates:
  - gate_id: audit.sources
    artifact_type: audit_report
    blocking: true
report_contracts:
  - contract_id: audit.report.v1
    artifact_type: audit_report
    required_sections: [summary]
    required_source_refs: true
    required_gate_ids: [audit.sources]
governance:
  max_nodes: 8
  max_depth: 4
  max_concurrency: 2
  max_retries_per_node: 0
""".strip(),
                encoding="utf-8",
            )
            (pack / "artifact_schemas" / "audit_request.json").write_text(
                '{"type":"object","required":["scope"]}', encoding="utf-8"
            )
            (pack / "artifact_schemas" / "audit_report.json").write_text(
                '{"type":"object","required":["summary"]}', encoding="utf-8"
            )
            (pack / "subagents" / "runtime.py").write_text(
                """
from research_system.schemas.artifacts import ArtifactDraft

def execute(context):
    request = context.require_artifact("audit_request")
    return ArtifactDraft(
        artifact_type="audit_report",
        content={"summary": request.content["scope"]},
        source_refs=list(request.source_refs),
        parent_artifact_ids=[request.artifact_id],
        confidence=0.9,
    )
""".strip(),
                encoding="utf-8",
            )

            registry = DomainPackRegistry.load(root)
            graph = CapabilityPlanner(registry).build_graph(
                CapabilityPlanningRequest(
                    domain_id="audit_sample",
                    objective="Audit a process.",
                    available_artifact_types=["audit_request"],
                    required_artifact_types=["audit_report"],
                )
            )
            runner = ExecutionGraphRunner(
                registry=registry,
                run_root=root / "run",
            )
            result = runner.run(
                graph=graph,
                run_id="audit-run",
                thread_id="audit-thread",
                request_id="audit-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="audit_request",
                        content={"scope": "procurement"},
                        source_refs=[{"type": "document", "id": "audit-1"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(result.snapshot.graph.domain_id, "audit_sample")
            self.assertEqual(len(result.final_artifact_ids), 1)


if __name__ == "__main__":
    unittest.main()
