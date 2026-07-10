from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.orchestration.orchestrator import ResearchOrchestrator
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.planning.planner import RuntimePlanner
from research_system.runtime.platform.settings import Settings
from research_system.schemas.protocol import RequestEnvelope


class StandardAgentOSEntryTests(unittest.TestCase):
    def test_domain_request_builds_capability_graph_without_legacy_plan_conversion(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        settings = Settings.from_env(repository_root)
        planner = RuntimePlanner(settings=settings)

        graph = planner.build_initial_execution_graph(
            intent="domain:finance_ashare",
            request=RequestEnvelope(
                intent="domain:finance_ashare",
                user_query="Review supplied A-share evidence.",
                slots={"symbols": ["600000"]},
            ),
        )

        self.assertEqual(graph.domain_id, "finance_ashare")
        self.assertEqual(graph.metadata["planner"], "capability_contract")
        self.assertIn("domain_task", {node.node_id for node in graph.nodes})
        self.assertTrue(any(node.node_kind == "compound" for node in graph.nodes))
        self.assertTrue(
            all(
                not str(node.capability or "").startswith("legacy.action.")
                for node in graph.nodes
            )
        )

    def test_legacy_finance_intent_can_opt_into_domain_graph_protocol(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = RuntimePlanner(settings=Settings.from_env(repository_root))

        graph = planner.build_initial_execution_graph(
            intent="stock_check",
            request=RequestEnvelope(
                intent="stock_check",
                user_query="Check 600000 using the Agent OS protocol.",
                slots={"symbols": ["600000"]},
                constraints={"execution_protocol": "agent_os"},
            ),
        )

        self.assertEqual(graph.domain_id, "finance_ashare")
        self.assertEqual(graph.metadata["planner"], "capability_contract")

    def test_stock_check_defaults_to_agent_os_when_finance_pack_is_installed(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = RuntimePlanner(settings=Settings.from_env(repository_root))

        graph = planner.build_initial_execution_graph(
            intent="stock_check",
            request=RequestEnvelope(
                intent="stock_check",
                user_query="Check 600000.",
                slots={"symbols": ["600000"]},
            ),
        )

        self.assertEqual(graph.domain_id, "finance_ashare")
        self.assertEqual(graph.metadata["planner"], "capability_contract")

    def test_stock_compare_defaults_to_agent_os_when_finance_pack_is_installed(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = RuntimePlanner(settings=Settings.from_env(repository_root))

        graph = planner.build_initial_execution_graph(
            intent="stock_compare",
            request=RequestEnvelope(
                intent="stock_compare",
                user_query="Compare 600000 and 000001.",
                slots={"symbols": ["600000", "000001"]},
            ),
        )

        self.assertEqual(graph.domain_id, "finance_ashare")
        self.assertEqual(graph.metadata["planner"], "capability_contract")

    def test_stock_screen_defaults_to_agent_os_when_finance_pack_is_installed(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = RuntimePlanner(settings=Settings.from_env(repository_root))

        graph = planner.build_initial_execution_graph(
            intent="stock_screen",
            request=RequestEnvelope(
                intent="stock_screen",
                user_query="Screen growth stocks.",
                slots={"symbols": []},
            ),
        )

        self.assertEqual(graph.domain_id, "finance_ashare")
        self.assertEqual(graph.metadata["planner"], "capability_contract")

    def test_sector_research_defaults_to_agent_os_when_finance_pack_is_installed(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = RuntimePlanner(settings=Settings.from_env(repository_root))

        graph = planner.build_initial_execution_graph(
            intent="sector_research",
            request=RequestEnvelope(
                intent="sector_research",
                user_query="Research the semiconductor sector.",
                slots={"sector": "semiconductor"},
            ),
        )

        self.assertEqual(graph.domain_id, "finance_ashare")
        self.assertEqual(graph.metadata["planner"], "capability_contract")

    def test_review_thesis_defaults_to_agent_os_when_finance_pack_is_installed(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = RuntimePlanner(settings=Settings.from_env(repository_root))

        graph = planner.build_initial_execution_graph(
            intent="review_thesis",
            request=RequestEnvelope(
                intent="review_thesis",
                user_query="Review the thesis for 600000.",
                slots={"symbols": ["600000"]},
            ),
        )

        self.assertEqual(graph.domain_id, "finance_ashare")
        self.assertEqual(graph.metadata["planner"], "capability_contract")

    def test_research_orchestrator_executes_domain_graph_without_legacy_gate_projection(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with ResearchOrchestrator(settings) as orchestrator:
                self.assertIsNone(orchestrator.supervisor)
                self.assertIsNone(orchestrator.action_runner)
                self.assertFalse(orchestrator.legacy_runtime_used)
                self.assertFalse(orchestrator.collector.initialized)
                self.assertFalse(orchestrator.retriever.initialized)
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="domain:finance_ashare",
                        user_query="Review supplied evidence.",
                        slots={"symbols": ["600000"]},
                    )
                )
                self.assertFalse(orchestrator.legacy_runtime_used)

            self.assertEqual(response.status, "completed")
            self.assertEqual(response.result["report"]["protocol"], "agent_os")
            run_root = settings.runs_root / str(response.run_id)
            self.assertTrue((run_root / "artifact_board.jsonl").is_file())
            self.assertFalse((run_root / "blackboard.jsonl").is_file())

    def test_default_stock_check_execution_does_not_create_legacy_plan_or_blackboard(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with ResearchOrchestrator(settings) as orchestrator:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="stock_check",
                        user_query="Check 600000.",
                        slots={"symbols": ["600000"]},
                    )
                )

            self.assertEqual(response.status, "completed")
            run_root = settings.runs_root / str(response.run_id)
            self.assertTrue((run_root / "execution_snapshot.json").is_file())
            self.assertTrue((run_root / "artifact_board.jsonl").is_file())
            self.assertFalse((run_root / "task_plan.json").is_file())
            self.assertFalse((run_root / "blackboard.jsonl").is_file())

    def test_intent_specific_finance_artifacts_are_executed_and_written_to_board(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        cases = {
            "stock_screen": "screening_result",
            "sector_research": "sector_analysis",
            "review_thesis": "thesis_review",
        }
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with ResearchOrchestrator(settings) as orchestrator:
                for intent, artifact_type in cases.items():
                    with self.subTest(intent=intent):
                        response = orchestrator.run(
                            RequestEnvelope(
                                intent=intent,
                                user_query=f"Run {intent}.",
                                slots={"symbols": ["600000"], "sector": "semiconductor"},
                            )
                        )
                        self.assertEqual(response.status, "completed")
                        board = ArtifactBoard(
                            settings.runs_root / str(response.run_id) / "artifact_board.jsonl"
                        )
                        self.assertTrue(
                            board.list_artifacts(artifact_type=artifact_type),
                            f"missing {artifact_type} for {intent}",
                        )

    def test_all_default_finance_intents_avoid_legacy_runtime(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        intents = ["stock_check", "stock_compare", "stock_screen", "sector_research", "review_thesis"]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with ResearchOrchestrator(settings) as orchestrator:
                for intent in intents:
                    with self.subTest(intent=intent):
                        response = orchestrator.run(
                            RequestEnvelope(
                                intent=intent,
                                user_query=f"Run {intent}.",
                                slots={"symbols": ["600000"], "sector": "semiconductor"},
                            )
                        )
                        self.assertEqual(response.status, "completed")
                        self.assertFalse(orchestrator.legacy_runtime_used)

    def test_explicit_legacy_runtime_is_the_only_compatibility_switch(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with ResearchOrchestrator(settings) as orchestrator:
                self.assertFalse(orchestrator.legacy_runtime_used)
                orchestrator.ensure_legacy_runtime()
                self.assertTrue(orchestrator.legacy_runtime_used)


if __name__ == "__main__":
    unittest.main()
