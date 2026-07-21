from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentDiscoveryTicket,
    SubagentRuntimeRegistration,
    SubagentToolDiscoveryRequest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeRuntime:
    timeout_seconds = 1.5

    def run(self, **kwargs):
        del kwargs
        return {
            "findings": [
                {
                    "risk": "The termination clause needs a notice period.",
                    "severity": "medium",
                }
            ],
            "confidence": 0.9,
        }


class _FakeRuntimePackaging:
    def materialize_runtime_subagent(self, *, activation_entry, skill_runtime):
        del activation_entry, skill_runtime
        return _FakeRuntime()


class RuntimeRoleOverlayTests(unittest.TestCase):
    def test_active_runtime_alias_is_planned_and_executed_by_graph_runner(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(REPO_ROOT).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "subagent_discovery_root": root / "discovery",
                }
            )
            agent_os = AgentOS(settings)
            store = agent_os.subagent_discovery_store
            alias = "approved_legal_risk_runtime"
            catalog = SubagentCatalogEntry(
                subagent_type=alias,
                summary="Approved legal risk runtime.",
                capabilities=["legal.contract.risk.review"],
                cost_class="medium",
                latency_class="low",
                allow_parallel=True,
                max_concurrency=3,
                input_json_schema={"type": "object"},
                output_json_schema={"type": "object", "required": ["findings"]},
            )
            activation = SubagentActivationEntry(
                ticket_id="ticket-legal-risk",
                subagent_type=alias,
                catalog_entry=catalog,
                runtime_registration=SubagentRuntimeRegistration(
                    registration_type="http_json_runner",
                    source_id="eastmoney",
                    endpoint_path="/fake-legal-risk",
                    http_method="GET",
                    timeout_seconds=1.5,
                ),
            )
            ticket = SubagentDiscoveryTicket(
                ticket_id="ticket-legal-risk",
                status="approved",
                capability_gap=SubagentCapabilityGap(
                    capability="legal.contract.risk.review",
                    reason="approved runtime extension",
                ),
                tool_discovery_request=SubagentToolDiscoveryRequest(
                    capability="legal.contract.risk.review",
                    search_query="legal risk review",
                    reason="approved runtime extension",
                ),
                candidate_catalog_entries=[catalog],
                activated_entries=[activation],
            )
            store.save(ticket)

            agent_os.runtime_role_overlay.runtime_packaging = _FakeRuntimePackaging()
            agent_os.runtime_role_overlay.skill_runtime = object()
            result = agent_os.run_domain(
                domain_id="legal_contract",
                objective="Review the contract risk clauses.",
                input_artifacts=[
                    ArtifactDraft(
                        artifact_type="clause_set",
                        content={
                            "clauses": [
                                {"clause_id": "c-1", "text": "Customer may terminate."}
                            ]
                        },
                        source_refs=[{"type": "contract", "id": "contract-1"}],
                    )
                ],
                required_artifact_types=["risk_finding_set"],
                runtime_role_aliases=[alias],
            )

            nodes = [
                node
                for node in result.snapshot.graph.nodes
                if node.node_kind == "subagent"
            ]
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].executor, alias)
            self.assertTrue(nodes[0].parallel_safe)
            self.assertEqual(nodes[0].max_concurrency, 3)
            self.assertEqual(
                agent_os.runtime_role_overlay.role_contracts()[0].timeout_seconds,
                2,
            )
            with self.assertRaises(KeyError):
                agent_os.runtime_role_overlay.resolve_executor(
                    domain_id="legal_contract",
                    capability="legal.contract.risk.review",
                    subagent_type="deactivated_runtime_alias",
                )

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(result.snapshot.node_states[nodes[0].node_id].status, "completed")
            output = [
                artifact
                for artifact in agent_os.inspect_run(result.snapshot.run_id)["artifacts"]
                if artifact["artifact_type"] == "risk_finding_set"
            ]
            self.assertEqual(output[0]["content"]["findings"][0]["severity"], "medium")


if __name__ == "__main__":
    unittest.main()
