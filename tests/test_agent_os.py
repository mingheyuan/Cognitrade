from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_jsonl
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.core_runtime import RequestIntakeDecision
from research_system.schemas.protocol import RequestEnvelope


class AgentOSTests(unittest.TestCase):
    def _settings(self, repository_root: Path, root: Path) -> Settings:
        return Settings.from_env(repository_root).model_copy(
            update={
                "runs_root": root / "runs",
                "memory_root": root / "memory",
                "domain_packs_root": repository_root / "domain_packs",
                "openai_api_key": None,
                "openai_base_url": None,
            }
        )

    def test_standard_request_entry_routes_through_request_intake(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture_bundle = {
                "symbols": ["600000"],
                "snapshots": [
                    {
                        "snapshot_type": "market",
                        "symbol": "600000",
                        "facts": {"availability": "available", "latest_price": 10.0},
                    }
                ],
                "candidate_universe": [
                    {"symbol": "600000", "name": "Fixture", "source": "test_fixture"}
                ],
                "collection": {
                    "universe_source": "test_fixture",
                    "candidate_count": 1,
                    "requested_snapshot_types": ["market", "fundamental", "notice"],
                    "collected_snapshot_types": ["market"],
                    "enrichment_targets": [],
                    "degraded": True,
                },
                "source_refs": [{"type": "test_fixture", "id": "stock-screen-routing"}],
            }
            agent_os = AgentOS(self._settings(repository_root, root))
            provider_tool = agent_os.tool_registry._tools["financial_data_provider"]
            agent_os.tool_registry._tools["financial_data_provider"] = replace(
                provider_tool,
                handler=lambda _arguments, _context: fixture_bundle,
            )
            response = agent_os.handle_request(
                RequestEnvelope(
                    intent="stock_screen",
                    user_query="Screen the supplied candidate.",
                    slots={"symbols": ["600000"]},
                    constraints={"research_profile": "lite", "investment_cycle": "short_term"},
                )
            )

            self.assertEqual(response.status, "completed")
            self.assertEqual(response.mode, "graph")
            events = read_jsonl(
                self._settings(repository_root, root).runs_root
                / str(response.run_id)
                / "execution_events.jsonl"
            )
            event_types = [event["event_type"] for event in events]
            self.assertLess(
                event_types.index("main_agent_started"),
                event_types.index("graph_run_started"),
            )
            self.assertIn("main_agent_route_decided", event_types)
            self.assertIn("main_agent_synthesis_fallback", event_types)

    def test_public_api_runs_and_inspects_an_installed_non_finance_pack(self) -> None:
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
            agent_os = AgentOS(settings)

            packs = agent_os.list_domain_packs()
            self.assertEqual(
                [pack["domain_id"] for pack in packs],
                ["finance_ashare", "legal_contract"],
            )
            result = agent_os.run_domain(
                domain_id="legal_contract",
                objective="Review the contract.",
                input_artifacts=[
                    ArtifactDraft(
                        artifact_type="contract_document",
                        content={"text": "Supplier shall deliver. Customer may terminate."},
                        source_refs=[{"type": "contract", "id": "contract-1"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "completed")
            inspected = agent_os.inspect_run(result.snapshot.run_id)
            self.assertEqual(inspected["snapshot"]["graph"]["domain_id"], "legal_contract")
            self.assertEqual(agent_os.replay(result.snapshot.run_id).status, "completed")
            episodes = agent_os.memory.list(layer="episodic", domain_id="legal_contract")
            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0].source_artifact_ids, result.final_artifact_ids)

    def test_handle_request_releases_prepared_run_after_turn_runner_failure(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(repository_root, root))
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="domain_task",
                recommended_mode="graph",
                intent="legal_contract_review",
                domain_id="legal_contract",
                confidence=1.0,
            )
            request = RequestEnvelope(
                intent="legal_contract_review",
                user_query="Review this contract.",
            )
            with (
                patch.object(agent_os.main_agent, "route", return_value=decision),
                patch.object(
                    agent_os.turn_runner,
                    "run",
                    side_effect=RuntimeError("simulated turn failure"),
                ),
                self.assertRaisesRegex(RuntimeError, "simulated turn failure"),
            ):
                agent_os.handle_request(request)

            self.assertEqual(agent_os._prepared_run_ids, set())
            with (
                patch.object(
                    agent_os.main_agent,
                    "route",
                    side_effect=RuntimeError("simulated route failure"),
                ),
                self.assertRaisesRegex(RuntimeError, "simulated route failure"),
            ):
                agent_os.handle_request(request)
            self.assertEqual(agent_os._prepared_run_ids, set())
            self.assertIsInstance(agent_os.reload_domain_packs(), list)


if __name__ == "__main__":
    unittest.main()
