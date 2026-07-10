from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from research_system.runtime.execution.core.execution_graph import (
    ExecutionGraphScheduler,
    apply_execution_graph_patch,
)
from research_system.runtime.execution.core.execution_graph_compat import (
    action_plan_to_execution_graph,
    execution_graph_to_action_plan,
    subagent_plan_to_execution_graph,
)
from research_system.runtime.execution.core.execution_graph_store import persist_execution_plan
from research_system.runtime.storage.jsonl import read_json
from research_system.schemas.execution_graph import (
    ArtifactPort,
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionGraphPatchOperation,
    ExecutionGovernance,
    ExecutionNode,
)
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.subagents import SubagentPlan, SubagentPlanNode


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

    def test_legacy_plans_convert_to_the_canonical_protocol(self) -> None:
        action_plan = ActionPlan(
            intent="stock_check",
            actions=[
                ActionSpec(node_id="collect", phase="collect", agent="collector"),
                ActionSpec(
                    node_id="report",
                    phase="report",
                    agent="reporter",
                    depends_on=["collect"],
                ),
            ],
        )
        subagent_plan = SubagentPlan(
            intent="stock_check",
            phase="collect",
            nodes=[
                SubagentPlanNode(
                    node_id="normalize",
                    subagent_type="query_normalizer",
                )
            ],
        )

        action_graph = action_plan_to_execution_graph(action_plan)
        subagent_graph = subagent_plan_to_execution_graph(subagent_plan)

        self.assertEqual(action_graph.graph_id, action_plan.plan_id)
        self.assertEqual(action_graph.nodes[0].capability, "legacy.action.collector")
        self.assertEqual(subagent_graph.graph_id, subagent_plan.plan_id)
        self.assertEqual(subagent_graph.nodes[0].executor, "query_normalizer")
        round_trip_plan = execution_graph_to_action_plan(action_graph)
        self.assertEqual(round_trip_plan.plan_id, action_plan.plan_id)
        self.assertEqual(round_trip_plan.actions[1].depends_on, ["collect"])

    def test_persisted_execution_graph_tracks_revisions_and_patch_history(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            context = SimpleNamespace(
                paths=SimpleNamespace(
                    task_plan=root / "task_plan.json",
                    execution_graph=root / "execution_graph.json",
                ),
                execution_graph=None,
                action_plan_patches=[],
            )
            plan = ActionPlan(
                intent="stock_check",
                actions=[ActionSpec(node_id="collect", phase="collect", agent="collector")],
            )

            first = persist_execution_plan(context=context, plan=plan)
            context.action_plan_patches.append({"patch_id": "patch-1"})
            second = persist_execution_plan(context=context, plan=plan)

            self.assertEqual(first.revision, 1)
            self.assertEqual(second.revision, 2)
            self.assertEqual(second.metadata["legacy_patch_history"], [{"patch_id": "patch-1"}])
            self.assertEqual(read_json(root / "execution_graph.json")["revision"], 2)
            self.assertEqual(read_json(root / "task_plan.json")["plan_id"], plan.plan_id)


if __name__ == "__main__":
    unittest.main()
