from __future__ import annotations

import unittest

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.schemas.domain_pack import (
    ArtifactTypeDefinition,
    CapabilityDefinition,
    CapabilityPlanningRequest,
    DomainPackManifest,
    IntentDefinition,
    PackIntakeDefinition,
)
from research_system.schemas.execution_graph import ArtifactPort


class PackPlannerContractTests(unittest.TestCase):
    def test_non_finance_pack_uses_its_own_profile_and_method_contract(self) -> None:
        manifest = DomainPackManifest(
            domain_id="audit_demo",
            version="1.0.0",
            description="A minimal non-finance planner fixture.",
            artifact_types=[
                ArtifactTypeDefinition(artifact_type="user_request"),
                ArtifactTypeDefinition(artifact_type="audit_evidence"),
                ArtifactTypeDefinition(artifact_type="audit_report"),
            ],
            capabilities=[
                CapabilityDefinition(
                    subagent_type="audit_collector",
                    capability="audit.collect",
                    consumes=[ArtifactPort(artifact_type="user_request")],
                    produces=[ArtifactPort(artifact_type="audit_evidence")],
                    parallel_safe=True,
                ),
                CapabilityDefinition(
                    subagent_type="audit_reader",
                    capability="audit.read",
                    consumes=[ArtifactPort(artifact_type="audit_evidence")],
                    produces=[ArtifactPort(artifact_type="audit_report")],
                    parallel_safe=True,
                )
            ],
            intake=PackIntakeDefinition(
                implementation="audit_demo.intake:resolve",
                intents=[
                    IntentDefinition(
                        intent_id="audit.review",
                        candidate_capabilities=["audit.read"],
                        output_artifact_types=["audit_report"],
                    )
                ],
            ),
            planner_policies={
                "default_profile": "focused",
                "profile_intents": ["audit.review"],
                "research_profiles": {"focused": {"allowed_capability_tags": []}},
                "profile_outputs": {"focused": ["audit_report"]},
                "method_catalog": {
                    "audit_summary": {
                        "capability": "audit.read",
                        "output_artifact_types": ["audit_report"],
                        "required_inputs": ["audit_evidence"],
                        "allowed_quality_statuses": ["available"],
                    }
                },
            },
        )
        planner = CapabilityPlanner(DomainPackRegistry([manifest]))

        graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="audit_demo",
                objective="Review the audit evidence.",
                available_artifact_types=["user_request"],
                required_artifact_types=["audit_report"],
                metadata={
                    "intent": "audit.review",
                    "intent_inputs": {
                        "intent": "audit.review",
                        "requested_methods": ["audit_summary"],
                    },
                },
            )
        )

        leaf = next(node for node in graph.nodes if node.capability == "audit.read")
        self.assertEqual(leaf.capability, "audit.read")
        collector = next(node for node in graph.nodes if node.capability == "audit.collect")
        self.assertEqual(
            leaf.consumes[0].selector["producer_node_id"],
            collector.node_id,
        )
        self.assertIn(collector.node_id, leaf.depends_on)
        self.assertEqual(graph.metadata["selected_research_profile"], "focused")
        self.assertEqual(graph.metadata["requested_methods"], ["audit_summary"])
        self.assertNotIn("stock_check", graph.metadata["profile_intents"])


if __name__ == "__main__":
    unittest.main()
