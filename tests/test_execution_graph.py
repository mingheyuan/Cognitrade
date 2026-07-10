from __future__ import annotations

import unittest
from pydantic import ValidationError

from research_system.runtime.execution.core.execution_graph import (
    ExecutionGraphScheduler,
    apply_execution_graph_patch,
)
from research_system.schemas.execution_graph import (
    ArtifactPort,
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionGraphPatchOperation,
    ExecutionGovernance,
    ExecutionNode,
)


def _leaf(
    node_id: str,
    *,
    parent_node_id: str | None = None,
    depends_on: list[str] | None = None,
    allow_partial_dependencies: bool = False,
) -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id,
        node_kind="subagent",
        executor=f"test.{node_id}",
        capability=f"test.{node_id}",
        parent_node_id=parent_node_id,
        depends_on=list(depends_on or []),
        allow_partial_dependencies=allow_partial_dependencies,
        parallel_safe=True,
        consumes=[ArtifactPort(artifact_type="input", required=False)],
        produces=[ArtifactPort(artifact_type=f"output.{node_id}")],
    )


class ExecutionGraphTests(unittest.TestCase):
    def test_validates_hierarchy_dependencies_and_governance_limits(self) -> None:
        graph = ExecutionGraph(
            domain_id="test_domain",
            objective="Test a hierarchical graph.",
            governance=ExecutionGovernance(max_nodes=4, max_depth=3),
            nodes=[
                ExecutionNode(node_id="collect", node_kind="compound"),
                _leaf("market", parent_node_id="collect"),
                _leaf("notice", parent_node_id="collect"),
                _leaf("report", depends_on=["collect"]),
            ],
        )

        self.assertEqual(graph.domain_id, "test_domain")
        with self.assertRaisesRegex(ValidationError, "parent must be compound"):
            ExecutionGraph(
                domain_id="test_domain",
                objective="Invalid hierarchy.",
                nodes=[_leaf("parent"), _leaf("child", parent_node_id="parent")],
            )
        with self.assertRaisesRegex(ValidationError, "dependency cycle"):
            ExecutionGraph(
                domain_id="test_domain",
                objective="Invalid dependencies.",
                nodes=[_leaf("one", depends_on=["two"]), _leaf("two", depends_on=["one"])],
            )

    def test_scheduler_runs_ready_leaves_and_resolves_compound_dependencies(self) -> None:
        graph = ExecutionGraph(
            domain_id="test_domain",
            objective="Schedule a hierarchical graph.",
            nodes=[
                ExecutionNode(node_id="collect", node_kind="compound"),
                _leaf("market", parent_node_id="collect"),
                _leaf("notice", parent_node_id="collect"),
                _leaf("report", depends_on=["collect"]),
            ],
        )
        scheduler = ExecutionGraphScheduler()

        self.assertEqual(
            [node.node_id for node in scheduler.ready_leaf_nodes(graph)],
            ["market", "notice"],
        )
        self.assertEqual(
            [
                node.node_id
                for node in scheduler.ready_leaf_nodes(
                    graph,
                    completed_node_ids=["market", "notice"],
                )
            ],
            ["report"],
        )

    def test_scheduler_inherits_compound_dependencies_and_supports_partial_inputs(self) -> None:
        graph = ExecutionGraph(
            domain_id="test_domain",
            objective="Schedule dependent compound nodes.",
            nodes=[
                ExecutionNode(node_id="collect", node_kind="compound"),
                _leaf("source", parent_node_id="collect"),
                ExecutionNode(
                    node_id="review",
                    node_kind="compound",
                    depends_on=["collect"],
                ),
                _leaf("strict", parent_node_id="review", depends_on=["source"]),
                _leaf(
                    "partial",
                    parent_node_id="review",
                    depends_on=["source"],
                    allow_partial_dependencies=True,
                ),
            ],
        )
        scheduler = ExecutionGraphScheduler()

        self.assertEqual(
            [
                node.node_id
                for node in scheduler.ready_leaf_nodes(
                    graph,
                    failed_node_ids=["source"],
                )
            ],
            ["partial"],
        )
        self.assertEqual(
            [
                node.node_id
                for node in scheduler.blocked_leaf_nodes(
                    graph,
                    failed_node_ids=["source"],
                )
            ],
            ["strict"],
        )

    def test_scheduler_identifies_unexpanded_compounds(self) -> None:
        graph = ExecutionGraph(
            domain_id="test_domain",
            objective="Expand a lazy professional phase.",
            nodes=[
                ExecutionNode(
                    node_id="professional_review",
                    node_kind="compound",
                    capability="professional.review.plan",
                    expansion_policy="lazy",
                )
            ],
        )

        ready = ExecutionGraphScheduler().expandable_compounds(graph)

        self.assertEqual([node.node_id for node in ready], ["professional_review"])

    def test_patch_is_revision_guarded_versioned_and_validated(self) -> None:
        graph = ExecutionGraph(
            domain_id="test_domain",
            objective="Patch a graph safely.",
            nodes=[_leaf("collect"), _leaf("report", depends_on=["collect"])],
        )
        patch = ExecutionGraphPatch(
            base_revision=1,
            operations=[
                ExecutionGraphPatchOperation(
                    op_type="insert_node",
                    node=_leaf("review", depends_on=["collect"]),
                ),
                ExecutionGraphPatchOperation(
                    op_type="rewire_dependency",
                    target_node_id="report",
                    remove_depends_on=["collect"],
                    new_depends_on=["review"],
                ),
            ],
        )

        updated = apply_execution_graph_patch(graph, patch)

        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.nodes[-1].node_id, "review")
        self.assertEqual(next(node for node in updated.nodes if node.node_id == "report").depends_on, ["review"])
        self.assertEqual(updated.metadata["patch_history"][0]["patch_id"], patch.patch_id)
        with self.assertRaisesRegex(ValueError, "stale execution graph patch"):
            apply_execution_graph_patch(updated, patch)

    def test_patch_rerun_and_terminal_operations_change_canonical_node_state(self) -> None:
        graph = ExecutionGraph(
            domain_id="test_domain",
            objective="Patch runtime state.",
            nodes=[
                _leaf("collect").model_copy(update={"status": "completed", "attempt_count": 1}),
                _leaf("review", depends_on=["collect"]).model_copy(
                    update={"status": "completed", "attempt_count": 1}
                ),
                _leaf("report", depends_on=["review"]).model_copy(
                    update={"status": "completed", "attempt_count": 1}
                ),
            ],
        )

        rerun = apply_execution_graph_patch(
            graph,
            ExecutionGraphPatch(
                base_revision=1,
                operations=[
                    ExecutionGraphPatchOperation(
                        op_type="rerun_from",
                        target_node_id="review",
                    )
                ],
            ),
        )
        node_map = {node.node_id: node for node in rerun.nodes}
        self.assertEqual(node_map["collect"].status, "completed")
        self.assertEqual(node_map["review"].status, "pending")
        self.assertEqual(node_map["report"].status, "pending")

        terminal = apply_execution_graph_patch(
            rerun,
            ExecutionGraphPatch(
                base_revision=2,
                operations=[
                    ExecutionGraphPatchOperation(
                        op_type="mark_terminal",
                        target_node_id="review",
                        metadata={"status": "skipped"},
                    )
                ],
            ),
        )
        self.assertEqual(
            next(node for node in terminal.nodes if node.node_id == "review").status,
            "skipped",
        )

if __name__ == "__main__":
    unittest.main()
