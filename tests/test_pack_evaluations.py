from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.evaluation.pack_runner import PackEvaluationRunner


class PackEvaluationTests(unittest.TestCase):
    def test_installed_packs_load_declared_contract_evaluations(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = DomainPackRegistry.load(root / "domain_packs")

        finance = registry.evaluations(domain_id="finance_ashare")
        legal = registry.evaluations(domain_id="legal_contract")

        self.assertEqual(
            [item.evaluation_id for item in finance],
            ["finance.research.smoke.v1", "finance.research.failure.v1"],
        )
        self.assertEqual(
            [item.evaluation_id for item in legal],
            ["legal.contract.smoke.v1", "legal.contract.failure.v1"],
        )
        self.assertEqual(finance[0].mode, "execution")

        with TemporaryDirectory() as temporary_directory:
            runner = PackEvaluationRunner(
                registry=registry,
                root=Path(temporary_directory),
            )
            results = runner.release_check(domain_id="legal_contract")
            finance_results = runner.release_check(domain_id="finance_ashare")

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.passed for result in results))
        self.assertTrue(all(result.executed for result in results))
        self.assertEqual(len(finance_results), 2)
        self.assertTrue(all(result.passed for result in finance_results))

    def test_execution_and_failure_injection_evaluations_use_graph_governance(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pack = root / "packs" / "evaluation_pack"
            for directory in (
                "ontology",
                "artifact_schemas",
                "subagents",
                "tools",
                "planner_policies",
                "quality_gates",
                "report_contracts",
                "evaluations",
            ):
                (pack / directory).mkdir(parents=True)
            (pack / "artifact_schemas" / "eval_input.json").write_text(
                '{"type":"object","required":["value"]}', encoding="utf-8"
            )
            (pack / "artifact_schemas" / "eval_output.json").write_text(
                '{"type":"object","required":["value"]}', encoding="utf-8"
            )
            (pack / "subagents" / "runtime.py").write_text(
                """
from research_system.schemas.artifacts import ArtifactDraft

def execute(context):
    source = context.require_artifact("eval_input")
    return ArtifactDraft(
        artifact_type="eval_output",
        content={"value": source.content["value"]},
        source_refs=list(source.source_refs),
        parent_artifact_ids=[source.artifact_id],
        confidence=0.95,
    )
""".strip(),
                encoding="utf-8",
            )
            (pack / "domain.yaml").write_text(
                """
domain_id: evaluation_pack
version: 1.0.0
description: Evaluation runner test pack.
artifact_types:
  - artifact_type: eval_input
  - artifact_type: eval_output
capabilities:
  - subagent_type: eval_worker
    capability: eval.worker
    implementation: subagents.runtime:execute
    consumes: [{artifact_type: eval_input}]
    produces: [{artifact_type: eval_output}]
quality_gates:
  - gate_id: eval.sources
    artifact_type: eval_output
    blocking: true
report_contracts:
  - contract_id: eval.output.v1
    artifact_type: eval_output
    required_sections: [value]
    required_source_refs: true
    required_gate_ids: [eval.sources]
evaluations:
  - evaluations/success.yaml
  - evaluations/failure.yaml
governance:
  max_nodes: 8
  max_depth: 4
  max_concurrency: 2
  max_retries_per_node: 0
""".strip(),
                encoding="utf-8",
            )
            common_seed = """
mode: execution
available_artifact_types: [eval_input]
required_artifact_types: [eval_output]
seed_artifacts:
  - artifact_type: eval_input
    content: {value: expected}
    source_refs: [{type: test, id: eval-input}]
"""
            (pack / "evaluations" / "success.yaml").write_text(
                "evaluation_id: eval.success\n"
                "objective: Execute the evaluation worker.\n"
                + common_seed
                + "expected_status: completed\n"
                + "required_gate_id: eval.sources\n"
                + "expected_gate_verdicts: {eval.sources: pass}\n"
                + "required_event_types: [graph_node_completed]\n",
                encoding="utf-8",
            )
            (pack / "evaluations" / "failure.yaml").write_text(
                "evaluation_id: eval.failure\n"
                "objective: Inject a capability failure.\n"
                + common_seed
                + "expected_status: failed\n"
                + "failure_injections:\n"
                + "  - kind: capability\n"
                + "    target: eval.worker\n"
                + "    reason: deterministic test failure\n"
                + "required_event_types: [evaluation_failure_injected]\n",
                encoding="utf-8",
            )

            registry = DomainPackRegistry.load(root / "packs")
            results = {
                result.evaluation_id: result
                for result in PackEvaluationRunner(
                    registry=registry,
                    root=root / "evaluation_runs",
                ).run_all(domain_id="evaluation_pack")
            }

        self.assertTrue(results["eval.success"].passed)
        self.assertTrue(results["eval.success"].executed)
        self.assertEqual(results["eval.success"].status, "completed")
        self.assertTrue(results["eval.failure"].passed, results["eval.failure"].model_dump())
        self.assertEqual(results["eval.failure"].status, "failed")


if __name__ == "__main__":
    unittest.main()
