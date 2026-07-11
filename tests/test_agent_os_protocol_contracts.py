from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.schemas.core_runtime import PlanDecision
from research_system.schemas.events import EventEnvelope
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.execution_graph import ExecutionGraphPatch, ExecutionGraphPatchOperation


class AgentOSProtocolContractTests(unittest.TestCase):
    def test_plan_decision_requires_structured_payload_for_replan_and_clarify(self) -> None:
        with self.assertRaises(ValueError):
            PlanDecision(action="replan", reason="missing patch")
        with self.assertRaises(ValueError):
            PlanDecision(action="clarify", reason="missing question")

        patch = ExecutionGraphPatch(
            base_revision=1,
            actor="main_agent.plan_agent",
            operations=[
                ExecutionGraphPatchOperation(
                    op_type="request_replan",
                    reason="Need a governed frontier update.",
                )
            ],
        )
        decision = PlanDecision(
            action="replan",
            reason="The active Board is missing a required output.",
            graph_patch=patch,
        )
        self.assertEqual(decision.actor, "main_agent.plan_agent")
        self.assertEqual(decision.graph_patch.patch_id, patch.patch_id)

    def test_event_envelope_normalizes_tool_and_artifact_aliases(self) -> None:
        event = EventEnvelope(
            event_type="tool_call_completed",
            run_id="run-1",
            thread_id="thread-1",
            request_id="request-1",
            call_id="call-1",
            artifact_id="artifact-1",
        )
        self.assertEqual(event.tool_call_id, "call-1")
        self.assertEqual(event.artifact_ids, ["artifact-1"])

    def test_artifact_source_refs_expose_canonical_and_legacy_fields(self) -> None:
        artifact = ArtifactDraft(
            artifact_type="evidence",
            content={"value": "verified"},
            source_refs=[{"type": "provider", "id": "provider-1"}],
        )
        self.assertEqual(artifact.source_refs[0]["source_type"], "provider")
        self.assertEqual(artifact.source_refs[0]["source_id"], "provider-1")
        self.assertEqual(artifact.source_refs[0]["type"], "provider")
        self.assertEqual(artifact.source_refs[0]["id"], "provider-1")

    def test_approval_binding_rejects_revision_and_scope_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            store = HumanApprovalStore(Path(directory) / "approvals.jsonl")
            approval = store.approve(
                approval_id="approval-1",
                run_id="run-1",
                node_id="node-1",
                capability="pack.capability",
                approved_by="reviewer",
                reason="approved",
                graph_revision=2,
                domain_id="pack",
                domain_version="1.0.0",
                user_id="user-1",
                tenant_id="tenant-1",
                policy_fingerprint="policy-1",
            )
            self.assertTrue(
                store.validate(
                    approval_id=approval.approval_id,
                    run_id="run-1",
                    node_id="node-1",
                    capability="pack.capability",
                    graph_revision=2,
                    domain_id="pack",
                    domain_version="1.0.0",
                    user_id="user-1",
                    tenant_id="tenant-1",
                    policy_fingerprint="policy-1",
                    require_binding=True,
                )
            )
            self.assertFalse(
                store.validate(
                    approval_id=approval.approval_id,
                    run_id="run-1",
                    node_id="node-1",
                    capability="pack.capability",
                    graph_revision=3,
                    domain_id="pack",
                    domain_version="1.0.0",
                    user_id="user-1",
                    tenant_id="tenant-1",
                    policy_fingerprint="policy-1",
                    require_binding=True,
                )
            )
            self.assertFalse(
                store.validate(
                    approval_id=approval.approval_id,
                    run_id="run-1",
                    node_id="node-1",
                    capability="pack.capability",
                    graph_revision=2,
                    domain_id="pack",
                    domain_version="1.0.0",
                    user_id="other-user",
                    tenant_id="tenant-1",
                    policy_fingerprint="policy-1",
                    require_binding=True,
                )
            )


if __name__ == "__main__":
    unittest.main()
