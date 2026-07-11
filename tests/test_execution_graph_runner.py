from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.graph_runner import (
    ExecutionGraphRunner,
    GraphGovernanceError,
)
from research_system.runtime.planning.capability_planner import (
    CapabilityDynamicPlanner,
    CapabilityPlanner,
)
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.execution_graph import (
    ArtifactPort,
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionGraphPatchOperation,
    ExecutionNode,
)


class _LegalLazyPlanner:
    def __init__(self) -> None:
        self.seen_artifact_types: list[str] = []

    def expand_compound(self, *, graph, compound, artifacts):
        self.seen_artifact_types = [artifact.artifact_type for artifact in artifacts]
        nodes = [
            ExecutionNode(
                node_id="extract",
                node_kind="subagent",
                executor="legal_clause_extractor",
                capability="legal.contract.clause.extract",
                parent_node_id=compound.node_id,
                consumes=[ArtifactPort(artifact_type="contract_document")],
                produces=[ArtifactPort(artifact_type="clause_set")],
            ),
            ExecutionNode(
                node_id="risks",
                node_kind="subagent",
                executor="legal_risk_reviewer",
                capability="legal.contract.risk.review",
                parent_node_id=compound.node_id,
                consumes=[ArtifactPort(artifact_type="clause_set")],
                produces=[ArtifactPort(artifact_type="risk_finding_set")],
                depends_on=["extract"],
                parallel_safe=True,
            ),
            ExecutionNode(
                node_id="obligations",
                node_kind="subagent",
                executor="legal_obligation_analyzer",
                capability="legal.contract.obligation.analyze",
                parent_node_id=compound.node_id,
                consumes=[ArtifactPort(artifact_type="clause_set")],
                produces=[ArtifactPort(artifact_type="obligation_set")],
                depends_on=["extract"],
                parallel_safe=True,
            ),
            ExecutionNode(
                node_id="report",
                node_kind="subagent",
                executor="legal_report_composer",
                capability="legal.contract.report.compose",
                parent_node_id=compound.node_id,
                consumes=[
                    ArtifactPort(artifact_type="risk_finding_set"),
                    ArtifactPort(artifact_type="obligation_set"),
                ],
                produces=[ArtifactPort(artifact_type="legal_review_report")],
                depends_on=["risks", "obligations"],
            ),
        ]
        return ExecutionGraphPatch(
            base_revision=graph.revision,
            operations=[
                ExecutionGraphPatchOperation(op_type="insert_node", node=node)
                for node in nodes
            ],
            notes=["Expanded from current Artifact Board state."],
        )


class ExecutionGraphRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")

    def _legal_graph(self):
        return CapabilityPlanner(self.registry).build_graph(
            CapabilityPlanningRequest(
                domain_id="legal_contract",
                objective="Review a contract for risks and obligations.",
                available_artifact_types=["contract_document"],
                required_artifact_types=["legal_review_report"],
            )
        )

    def test_runs_non_finance_pack_on_unified_frontiers_and_artifact_board(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            events: list[dict] = []
            runner = ExecutionGraphRunner(
                registry=self.registry,
                run_root=Path(temporary_directory),
                event_callback=events.append,
            )
            result = runner.run(
                graph=self._legal_graph(),
                run_id="legal-run",
                thread_id="legal-thread",
                request_id="legal-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="contract_document",
                        content={
                            "text": (
                                "Supplier shall deliver monthly. "
                                "Customer may terminate for breach. Liability is capped."
                            )
                        },
                        source_refs=[{"type": "uploaded_contract", "id": "contract-1"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(result.snapshot.graph.nodes[0].status, "completed")
            main_agent_progress = [
                event
                for event in events
                if event["event_type"] == "agent_progress"
                and str(event.get("progress_kind", "")).startswith("main_agent_")
            ]
            self.assertTrue(main_agent_progress)
            self.assertTrue(all(event.get("current_action") for event in main_agent_progress))
            self.assertTrue(all(event.get("next_action") for event in main_agent_progress))
            frontiers = [event["node_ids"] for event in events if event["event_type"] == "graph_frontier_ready"]
            self.assertIn(
                ["legal_risk_reviewer", "legal_obligation_analyzer"],
                frontiers,
            )
            report = runner.board.get(result.final_artifact_ids[0])
            self.assertIsNotNone(report)
            assert report is not None
            self.assertEqual(report.producer.responsibility_node_id, "legal_report_composer")
            self.assertTrue(report.source_refs)
            self.assertEqual(report.artifact_version, 1)
            self.assertGreaterEqual(len(runner.board.lineage(report.artifact_id)), 4)
            self.assertEqual(result.snapshot.gate_evaluations[0].verdict, "pass")
            self.assertEqual(
                result.snapshot.gate_evaluations[0].evaluator,
                "legal.contract.citation.verify",
            )
            self.assertTrue(result.snapshot.gate_evaluations[0].evidence_refs)
            self.assertTrue(
                any(event["event_type"] == "gate_evaluator_completed" for event in events)
            )
            self.assertEqual(runner.replay().model_dump(mode="json"), result.snapshot.model_dump(mode="json"))
            for artifact in runner.board.list_artifacts(run_id="legal-run", status="active"):
                self.assertTrue(artifact.source_refs)
                self.assertGreaterEqual(artifact.artifact_version, 1)
                self.assertTrue(artifact.schema_version)
                self.assertIsNotNone(artifact.confidence)
                self.assertTrue(artifact.producer.responsibility_node_id)

    def test_pause_resume_partial_rerun_and_versioned_supersession(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            runner = ExecutionGraphRunner(
                registry=self.registry,
                run_root=Path(temporary_directory),
            )
            runner.request_pause()
            paused = runner.run(
                graph=self._legal_graph(),
                run_id="legal-run",
                thread_id="legal-thread",
                request_id="legal-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="contract_document",
                        content={"text": "Supplier shall perform. Customer may terminate."},
                        source_refs=[{"type": "uploaded_contract", "id": "contract-1"}],
                    )
                ],
            )
            self.assertEqual(paused.snapshot.status, "paused")
            completed = runner.resume()
            self.assertEqual(completed.snapshot.status, "completed")

            prepared = runner.rerun_from("legal_risk_reviewer")
            self.assertTrue(prepared.invalidated_artifact_ids)
            self.assertEqual(
                runner.board.list_artifacts(
                    run_id="legal-run",
                    artifact_type="legal_review_report",
                    status="active",
                ),
                [],
            )
            rerun = runner.resume()

            self.assertEqual(rerun.snapshot.status, "completed")
            active_reports = runner.board.list_artifacts(
                run_id="legal-run",
                artifact_type="legal_review_report",
                status="active",
            )
            self.assertEqual(len(active_reports), 1)
            self.assertEqual(active_reports[0].artifact_version, 2)

    def test_run_timeout_is_distinct_from_cancellation(self) -> None:
        graph = self._legal_graph().model_copy(
            update={
                "governance": self.registry.get("legal_contract").governance.model_copy(
                    update={"timeout_seconds": 0.000001}
                )
            }
        )
        with TemporaryDirectory() as temporary_directory:
            events: list[dict] = []
            runner = ExecutionGraphRunner(
                registry=self.registry,
                run_root=Path(temporary_directory),
                event_callback=events.append,
            )
            result = runner.run(
                graph=graph,
                run_id="timeout-run",
                thread_id="timeout-thread",
                request_id="timeout-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="contract_document",
                        content={"text": "Supplier shall deliver."},
                        source_refs=[{"type": "contract", "id": "contract-1"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "timed_out")
            self.assertTrue(any(event["event_type"] == "graph_run_timed_out" for event in events))

    def test_governance_rejects_tool_and_approval_bypass(self) -> None:
        graph = self._legal_graph()
        risk_node = next(node for node in graph.nodes if node.node_id == "legal_risk_reviewer")
        risk_node.tools = ["undeclared_tool"]
        with TemporaryDirectory() as temporary_directory:
            runner = ExecutionGraphRunner(
                registry=self.registry,
                run_root=Path(temporary_directory),
            )
            with self.assertRaisesRegex(GraphGovernanceError, "outside sandbox"):
                runner.run(
                    graph=graph,
                    run_id="run",
                    thread_id="thread",
                    request_id="request",
                )

        graph = self._legal_graph()
        risk_node = next(node for node in graph.nodes if node.node_id == "legal_risk_reviewer")
        risk_node.side_effect_level = "write_external"
        risk_node.idempotency_key = "approval-gated-test"
        with TemporaryDirectory() as temporary_directory:
            runner = ExecutionGraphRunner(
                registry=self.registry,
                run_root=Path(temporary_directory),
            )
            waiting = runner.run(
                graph=graph,
                run_id="run",
                thread_id="thread",
                request_id="request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="contract_document",
                        content={"text": "Supplier shall deliver."},
                        source_refs=[{"type": "contract", "id": "contract-1"}],
                    )
                ],
            )
            self.assertEqual(waiting.snapshot.status, "waiting_approval")
            approval_id = next(
                node.approval_id
                for node in waiting.snapshot.graph.nodes
                if node.node_id == "legal_risk_reviewer"
            )
            self.assertIsNotNone(approval_id)
            runner.approvals.approve(
                approval_id=approval_id,
                run_id="run",
                node_id="legal_risk_reviewer",
                capability="legal.contract.risk.review",
                approved_by="reviewer",
                reason="Approved test operation.",
            )
            completed = runner.resume()
            self.assertEqual(completed.snapshot.status, "completed")

    def test_dynamic_planner_expands_arbitrary_subgraph_from_board_at_safe_point(self) -> None:
        planner = _LegalLazyPlanner()
        manifest = self.registry.get("legal_contract")
        graph = ExecutionGraph(
            domain_id="legal_contract",
            objective="Dynamically plan a contract review.",
            governance=manifest.governance.model_copy(
                update={"required_gate_ids": ["legal.citation_required"]}
            ),
            nodes=[
                ExecutionNode(
                    node_id="professional_review",
                    node_kind="compound",
                    capability="legal_contract.dynamic.plan",
                    expansion_policy="lazy",
                )
            ],
        )
        with TemporaryDirectory() as temporary_directory:
            runner = ExecutionGraphRunner(
                registry=self.registry,
                run_root=Path(temporary_directory),
                dynamic_planner=planner,
            )
            result = runner.run(
                graph=graph,
                run_id="dynamic-run",
                thread_id="dynamic-thread",
                request_id="dynamic-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="contract_document",
                        content={"text": "Supplier shall deliver. Customer may terminate."},
                        source_refs=[{"type": "contract", "id": "contract-1"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(result.snapshot.graph.revision, 2)
            self.assertEqual(planner.seen_artifact_types, ["contract_document"])
            self.assertEqual(len(result.snapshot.applied_patch_ids), 1)
            self.assertEqual(
                {node.node_id for node in result.snapshot.graph.nodes},
                {"professional_review", "extract", "risks", "obligations", "report"},
            )

    def test_isolated_capability_failure_replans_to_a_catalog_fallback(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pack_root = root / "packs" / "fallback_test"
            for directory in DomainPackRegistry.REQUIRED_DIRECTORIES:
                (pack_root / directory).mkdir(parents=True, exist_ok=True)
            (pack_root / "domain.yaml").write_text(
                """
domain_id: fallback_test
version: 1.0.0
description: Failure replacement test pack.
artifact_types:
  - artifact_type: control_input
  - artifact_type: control_output
capabilities:
  - subagent_type: primary_worker
    capability: fallback.primary
    implementation: subagents.runtime:primary
    consumes: [{artifact_type: control_input}]
    produces: [{artifact_type: control_output}]
    reliability: 0.99
    failure_mode: isolate
  - subagent_type: fallback_worker
    capability: fallback.secondary
    implementation: subagents.runtime:fallback
    consumes: [{artifact_type: control_input}]
    produces: [{artifact_type: control_output}]
    reliability: 0.80
    cost_class: medium
planner_policies:
  intent_outputs:
    fallback_check: [control_output]
report_contracts:
  - contract_id: fallback.output.v1
    artifact_type: control_output
    required_sections: [value]
    required_source_refs: true
governance:
  max_nodes: 8
  max_depth: 4
  max_concurrency: 2
  max_retries_per_node: 0
""".strip(),
                encoding="utf-8",
            )
            (pack_root / "artifact_schemas" / "control_input.json").write_text(
                '{"type":"object","required":["value"]}',
                encoding="utf-8",
            )
            (pack_root / "artifact_schemas" / "control_output.json").write_text(
                '{"type":"object","required":["value"]}',
                encoding="utf-8",
            )
            (pack_root / "subagents" / "runtime.py").write_text(
                """
from research_system.schemas.artifacts import ArtifactDraft

def primary(context):
    raise RuntimeError("primary provider unavailable")

def fallback(context):
    source = context.require_artifact("control_input")
    return ArtifactDraft(
        artifact_type="control_output",
        content={"value": "fallback"},
        source_refs=list(source.source_refs),
        confidence=0.8,
    )
""".strip(),
                encoding="utf-8",
            )

            registry = DomainPackRegistry.load(root / "packs")
            planner = CapabilityPlanner(registry)
            graph = planner.build_lazy_graph(
                CapabilityPlanningRequest(
                    domain_id="fallback_test",
                    objective="Recover from a failed provider.",
                    available_artifact_types=["control_input"],
                    required_artifact_types=["control_output"],
                )
            )
            events: list[dict] = []
            runner = ExecutionGraphRunner(
                registry=registry,
                run_root=root / "run",
                event_callback=events.append,
                dynamic_planner=CapabilityDynamicPlanner(planner),
            )
            result = runner.run(
                graph=graph,
                run_id="fallback-run",
                thread_id="fallback-thread",
                request_id="fallback-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="control_input",
                        content={"value": "input"},
                        source_refs=[{"type": "test", "id": "input-1"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "completed")
            output = runner.board.get(result.final_artifact_ids[0])
            self.assertIsNotNone(output)
            assert output is not None
            self.assertEqual(output.content["value"], "fallback")
            self.assertTrue(
                any(event["event_type"] == "graph_replan_applied" for event in events)
            )
            self.assertEqual(
                result.snapshot.graph.nodes[-1].capability,
                "fallback.secondary",
            )

    def test_subagent_control_request_is_audited_and_drives_safe_point_replan(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pack_root = root / "packs" / "control_test"
            for directory in DomainPackRegistry.REQUIRED_DIRECTORIES:
                (pack_root / directory).mkdir(parents=True, exist_ok=True)
            (pack_root / "domain.yaml").write_text(
                """
domain_id: control_test
version: 1.0.0
description: Control request protocol test pack.
artifact_types:
  - artifact_type: control_input
  - artifact_type: control_output
  - artifact_type: extra_evidence
capabilities:
  - subagent_type: control_worker
    capability: control.primary
    implementation: subagents.runtime:control_worker
    consumes: [{artifact_type: control_input}]
    produces: [{artifact_type: control_output}]
  - subagent_type: evidence_worker
    capability: control.evidence
    implementation: subagents.runtime:evidence_worker
    consumes: [{artifact_type: control_input}]
    produces: [{artifact_type: extra_evidence}]
planner_policies:
  intent_outputs:
    control_check: [control_output]
report_contracts:
  - contract_id: control.output.v1
    artifact_type: control_output
    required_sections: [value]
    required_source_refs: true
governance:
  max_nodes: 8
  max_depth: 4
  max_concurrency: 2
  max_retries_per_node: 0
""".strip(),
                encoding="utf-8",
            )
            for artifact_type, schema in {
                "control_input": '{"type":"object","required":["value"]}',
                "control_output": '{"type":"object","required":["value"]}',
                "extra_evidence": '{"type":"object","required":["evidence"]}',
            }.items():
                (pack_root / "artifact_schemas" / f"{artifact_type}.json").write_text(
                    schema,
                    encoding="utf-8",
                )
            (pack_root / "subagents" / "runtime.py").write_text(
                """
from research_system.schemas.artifacts import ArtifactDraft

def control_worker(context):
    source = context.require_artifact("control_input")
    return {
        "artifacts": [ArtifactDraft(
            artifact_type="control_output",
            content={"value": source.content["value"]},
            source_refs=list(source.source_refs),
            confidence=0.9,
        )],
        "control_requests": [{
            "request_id": "control-request-1",
            "kind": "request_replan",
            "reason": "The initial result requires corroborating evidence.",
            "requested_artifact_types": ["extra_evidence"],
        }],
    }

def evidence_worker(context):
    source = context.require_artifact("control_input")
    return ArtifactDraft(
        artifact_type="extra_evidence",
        content={"evidence": source.content["value"]},
        source_refs=list(source.source_refs),
        confidence=0.8,
    )
""".strip(),
                encoding="utf-8",
            )

            registry = DomainPackRegistry.load(root / "packs")
            planner = CapabilityPlanner(registry)
            graph = planner.build_lazy_graph(
                CapabilityPlanningRequest(
                    domain_id="control_test",
                    objective="Run a controlled evidence check.",
                    available_artifact_types=["control_input"],
                    required_artifact_types=["control_output"],
                    metadata={"intent": "control_check"},
                )
            )
            events: list[dict] = []
            runner = ExecutionGraphRunner(
                registry=registry,
                run_root=root / "run",
                event_callback=events.append,
                dynamic_planner=CapabilityDynamicPlanner(planner),
            )
            result = runner.run(
                graph=graph,
                run_id="control-run",
                thread_id="control-thread",
                request_id="control-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="control_input",
                        content={"value": "fact"},
                        source_refs=[{"type": "source", "id": "source-1"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "completed")
            self.assertFalse(result.snapshot.pending_control_requests)
            self.assertIn(
                "evidence_worker",
                {node.executor for node in result.snapshot.graph.nodes},
            )
            requests = [
                event
                for event in events
                if event["event_type"] == "subagent_control_request"
            ]
            self.assertEqual(len(requests), 1)
            self.assertEqual(
                requests[0]["request"]["requested_artifact_types"],
                ["extra_evidence"],
            )

    def test_governed_global_cache_rebinds_artifact_lineage_to_current_run(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cache_root = root / "cache"
            seed = ArtifactDraft(
                artifact_type="contract_document",
                content={"text": "Supplier shall deliver. Customer may terminate."},
                source_refs=[{"type": "contract", "id": "contract-1"}],
            )
            first = ExecutionGraphRunner(
                registry=self.registry,
                run_root=root / "run-1",
                cache_root=cache_root,
            )
            first.run(
                graph=self._legal_graph(),
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                seed_artifacts=[seed],
            )
            second_events: list[dict] = []
            second = ExecutionGraphRunner(
                registry=self.registry,
                run_root=root / "run-2",
                cache_root=cache_root,
                event_callback=second_events.append,
            )
            result = second.run(
                graph=self._legal_graph(),
                run_id="run-2",
                thread_id="thread-2",
                request_id="request-2",
                seed_artifacts=[seed],
            )

            cache_hits = [
                event["node_id"]
                for event in second_events
                if event["event_type"] == "graph_node_cache_hit"
            ]
            self.assertEqual(len(cache_hits), 4)
            report = second.board.get(result.final_artifact_ids[0])
            assert report is not None
            self.assertTrue(
                all(artifact.run_id == "run-2" for artifact in second.board.lineage(report.artifact_id))
            )

    def test_side_effecting_node_uses_approval_and_idempotency_receipt(self) -> None:
        manifest = self.registry.get("legal_contract")
        graph = ExecutionGraph(
            domain_id="legal_contract",
            objective="Execute an approved write once.",
            governance=manifest.governance,
            metadata={"required_artifact_types": ["clause_set"]},
            nodes=[
                ExecutionNode(
                    node_id="approved_write",
                    node_kind="subagent",
                    executor="legal_clause_extractor",
                    capability="legal.contract.clause.extract",
                    consumes=[ArtifactPort(artifact_type="contract_document")],
                    produces=[ArtifactPort(artifact_type="clause_set")],
                    side_effect_level="write_external",
                    approval_id="approval-1",
                    idempotency_key="legal-write-1",
                )
            ],
        )
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cache_root = root / "cache"
            seed = ArtifactDraft(
                artifact_type="contract_document",
                content={"text": "Supplier shall deliver."},
                source_refs=[{"type": "contract", "id": "contract-1"}],
            )
            first = ExecutionGraphRunner(
                registry=self.registry,
                run_root=root / "run-1",
                cache_root=cache_root,
            )
            first.approvals.approve(
                approval_id="approval-1",
                run_id="run-1",
                node_id="approved_write",
                capability="legal.contract.clause.extract",
                approved_by="reviewer",
                reason="Approved test write.",
            )
            self.assertEqual(
                first.run(
                    graph=graph.model_copy(deep=True),
                    run_id="run-1",
                    thread_id="thread-1",
                    request_id="request-1",
                    seed_artifacts=[seed],
                ).snapshot.status,
                "completed",
            )
            events: list[dict] = []
            second = ExecutionGraphRunner(
                registry=self.registry,
                run_root=root / "run-2",
                cache_root=cache_root,
                event_callback=events.append,
            )
            second.approvals.approve(
                approval_id="approval-1",
                run_id="run-2",
                node_id="approved_write",
                capability="legal.contract.clause.extract",
                approved_by="reviewer",
                reason="Approved retry lookup.",
            )
            second_result = second.run(
                graph=graph.model_copy(deep=True),
                run_id="run-2",
                thread_id="thread-2",
                request_id="request-2",
                seed_artifacts=[seed],
            )

            self.assertEqual(second_result.snapshot.status, "completed")
            self.assertEqual(
                [event["node_id"] for event in events if event["event_type"] == "graph_node_idempotency_hit"],
                ["approved_write"],
            )

    def test_in_flight_patch_cancels_node_and_replaces_it_at_safe_point(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pack_root = root / "packs" / "control_test"
            for directory in DomainPackRegistry.REQUIRED_DIRECTORIES:
                (pack_root / directory).mkdir(parents=True, exist_ok=True)
            (pack_root / "domain.yaml").write_text(
                """
domain_id: control_test
version: 1.0.0
description: Cooperative cancellation test pack.
artifact_types:
  - artifact_type: control_input
  - artifact_type: control_output
capabilities:
  - subagent_type: slow_worker
    capability: control.slow
    implementation: subagents.runtime:slow
    consumes: [{artifact_type: control_input}]
    produces: [{artifact_type: control_output}]
  - subagent_type: fast_worker
    capability: control.fast
    implementation: subagents.runtime:fast
    consumes: [{artifact_type: control_input}]
    produces: [{artifact_type: control_output}]
quality_gates:
  - gate_id: control.sources
    artifact_type: control_output
    blocking: true
report_contracts:
  - contract_id: control.output.v1
    artifact_type: control_output
    required_sections: [value]
    required_source_refs: true
    required_gate_ids: [control.sources]
governance:
  max_nodes: 8
  max_depth: 4
  max_concurrency: 2
  max_retries_per_node: 0
""".strip(),
                encoding="utf-8",
            )
            (pack_root / "artifact_schemas" / "control_input.json").write_text(
                '{"type":"object","required":["value"]}', encoding="utf-8"
            )
            (pack_root / "artifact_schemas" / "control_output.json").write_text(
                '{"type":"object","required":["value"]}', encoding="utf-8"
            )
            (pack_root / "subagents" / "runtime.py").write_text(
                """
from time import sleep
from research_system.runtime.execution.core.graph_runner import GraphCancelledError
from research_system.schemas.artifacts import ArtifactDraft

def slow(context):
    for _ in range(200):
        if context.cancellation.is_set():
            raise GraphCancelledError("slow worker cancelled")
        sleep(0.005)
    source = context.require_artifact("control_input")
    return ArtifactDraft(
        artifact_type="control_output",
        content={"value": "slow"},
        source_refs=list(source.source_refs),
        confidence=0.9,
    )

def fast(context):
    source = context.require_artifact("control_input")
    return ArtifactDraft(
        artifact_type="control_output",
        content={"value": "replacement"},
        source_refs=list(source.source_refs),
        confidence=0.9,
    )
""".strip(),
                encoding="utf-8",
            )
            registry = DomainPackRegistry.load(root / "packs")
            graph = ExecutionGraph(
                domain_id="control_test",
                objective="Replace a running node.",
                governance=registry.get("control_test").governance.model_copy(
                    update={"required_gate_ids": ["control.sources"]}
                ),
                metadata={"required_artifact_types": ["control_output"]},
                nodes=[
                    ExecutionNode(
                        node_id="slow",
                        node_kind="subagent",
                        executor="slow_worker",
                        capability="control.slow",
                        consumes=[ArtifactPort(artifact_type="control_input")],
                        produces=[ArtifactPort(artifact_type="control_output")],
                    )
                ],
            )
            started = Event()
            events: list[dict] = []

            def observe(event):
                events.append(event)
                if event["event_type"] == "graph_node_started" and event.get("node_id") == "slow":
                    started.set()

            runner = ExecutionGraphRunner(
                registry=registry,
                run_root=root / "run",
                event_callback=observe,
            )
            result_box: dict[str, object] = {}

            def execute():
                result_box["result"] = runner.run(
                    graph=graph,
                    run_id="control-run",
                    thread_id="control-thread",
                    request_id="control-request",
                    seed_artifacts=[
                        ArtifactDraft(
                            artifact_type="control_input",
                            content={"value": "input"},
                            source_refs=[{"type": "test", "id": "input-1"}],
                        )
                    ],
                )

            thread = Thread(target=execute, daemon=True)
            thread.start()
            self.assertTrue(started.wait(timeout=2.0))
            patch = ExecutionGraphPatch(
                base_revision=1,
                cancel_in_flight_node_ids=["slow"],
                operations=[
                    ExecutionGraphPatchOperation(
                        op_type="replace_subgraph",
                        target_node_id="slow",
                        replacement_nodes=[
                            ExecutionNode(
                                node_id="fast",
                                node_kind="subagent",
                                executor="fast_worker",
                                capability="control.fast",
                                consumes=[ArtifactPort(artifact_type="control_input")],
                                produces=[ArtifactPort(artifact_type="control_output")],
                            )
                        ],
                    )
                ],
            )
            runner.submit_patch(patch)
            thread.join(timeout=3.0)
            self.assertFalse(thread.is_alive())
            result = result_box["result"]
            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(result.snapshot.graph.revision, 2)
            self.assertEqual(set(result.snapshot.node_states), {"fast"})
            output = runner.board.get(result.final_artifact_ids[0])
            assert output is not None
            self.assertEqual(output.content["value"], "replacement")
            event_types = [event["event_type"] for event in events]
            self.assertLess(
                event_types.index("graph_patch_requested"),
                event_types.index("graph_patch_applied"),
            )

if __name__ == "__main__":
    unittest.main()
