from __future__ import annotations

import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path

from research_system.agents._subagent_phase_controller import SubagentCapabilityGapError, SubagentPhaseController
from research_system.runtime.orchestration.subagent_discovery_admin_support import (
    OrchestratorSubagentDiscoveryAdminSupport,
)
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentExecutionRecord,
    SubagentGraphPatch,
    SubagentGraphPatchOperation,
    SubagentPlan,
    SubagentToolDiscoveryRequest,
)
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy
from research_system.subagents.registry import SubagentRegistry


class _CheapReviewSubagent:
    subagent_type = "cheap_review"
    input_model = None
    output_model = None
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="cheap review",
        capabilities=("review_strategy",),
        stage="review",
        cost_class="low",
        latency_class="low",
        suitable_intents=("review_thesis",),
    )

    def run(self, **kwargs):
        return kwargs


class _PreferredReviewSubagent:
    subagent_type = "preferred_review"
    input_model = None
    output_model = None
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="preferred review",
        capabilities=("review_strategy",),
        stage="review",
        cost_class="medium",
        latency_class="medium",
        suitable_intents=("review_thesis",),
    )

    def run(self, **kwargs):
        return kwargs


class _HybridShadowProvider:
    available = True

    def __init__(self, *, selected_plan_variant: str) -> None:
        self.selected_plan_variant = selected_plan_variant
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(
            {
                "selected_plan_variant": self.selected_plan_variant,
                "selected_node_ids": ["review_strategy_resolver"],
                "request_replan": False,
                "reason": "keep the base review strategy plan",
                "confidence": 0.93,
                "notes": ["hybrid_keep_current_plan"],
            }
        )


class SubagentPhaseControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SubagentRegistry([_CheapReviewSubagent(), _PreferredReviewSubagent()])
        self.invocations = []

        def _invoke(invocation):
            self.invocations.append(invocation)
            return (
                {"ok": True, "value": invocation.inputs.get("value")},
                SubagentExecutionRecord(
                    subagent_type=invocation.subagent_type,
                    invocation_id=invocation.invocation_id,
                    status="completed",
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["ok", "value"],
                ),
            )

        self.controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=_invoke,
            emit_event=None,
            cache_context_builder=lambda **_: None,
            start_from_node_id_builder=lambda **_: None,
        )

    def test_select_catalog_entry_prefers_explicit_type(self) -> None:
        selected = self.controller.select_catalog_entry(
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type="preferred_review",
        )

        self.assertEqual(selected.subagent_type, "preferred_review")

    def test_select_catalog_entry_records_capability_gap_when_missing(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_capability_gaps=[],
            subagent_tool_discovery_requests=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(emit_event=lambda *_args, **_kwargs: None)
        with tempfile.TemporaryDirectory() as temp_dir:
            discovery_store = SubagentDiscoveryStore(Path(temp_dir))
            controller = SubagentPhaseController(
                registry=self.registry,
                invoke_subagent=lambda invocation: (
                    {"ok": True},
                    SubagentExecutionRecord(
                        subagent_type=invocation.subagent_type,
                        invocation_id=invocation.invocation_id,
                        status="completed",
                        objective=invocation.objective,
                        inputs=invocation.inputs,
                        output_keys=["ok"],
                    ),
                ),
                emit_event=None,
                cache_context_builder=lambda **_: None,
                start_from_node_id_builder=lambda **_: None,
                runtime_binding_getter=lambda: {
                    "context": runtime_context,
                    "support": support,
                    "subagent_discovery_store": discovery_store,
                    "request_id": "req-gap",
                    "intent": "review_thesis",
                },
            )

            with self.assertRaises(SubagentCapabilityGapError) as exc_info:
                controller.select_catalog_entry(
                    stage="review",
                    intent="review_thesis",
                    capability="missing_capability",
                )

            self.assertEqual(exc_info.exception.gap.capability, "missing_capability")
            self.assertEqual(len(runtime_context.subagent_capability_gaps), 1)
            self.assertEqual(len(runtime_context.subagent_tool_discovery_requests), 1)
            self.assertEqual(runtime_context.subagent_capability_gaps[0]["event"], "subagent_capability_gap_detected")
            self.assertEqual(
                runtime_context.subagent_tool_discovery_requests[0]["event"],
                "subagent_tool_discovery_requested",
            )
            self.assertEqual(len(runtime_context.dynamic_trace), 2)
            pending_tickets = discovery_store.list("pending")
            self.assertEqual(len(pending_tickets), 1)
            self.assertEqual(pending_tickets[0].capability_gap.capability, "missing_capability")
            self.assertEqual(
                pending_tickets[0].tool_discovery_request.search_query,
                "review_thesis review missing_capability",
            )

    def test_capability_gap_surfaces_approved_candidate_catalog_entries(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_capability_gaps=[],
            subagent_tool_discovery_requests=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(emit_event=lambda *_args, **_kwargs: None)
        with tempfile.TemporaryDirectory() as temp_dir:
            discovery_store = SubagentDiscoveryStore(Path(temp_dir))
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: discovery_store,
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            ticket = discovery_store.create_request(
                gap=SubagentCapabilityGap(
                    intent="review_thesis",
                    stage="review",
                    capability="missing_capability",
                    reason="missing review capability",
                ),
                request=SubagentToolDiscoveryRequest(
                    intent="review_thesis",
                    stage="review",
                    capability="missing_capability",
                    search_query="review_thesis review missing_capability",
                    reason="missing review capability",
                ),
            )
            admin.review_request(ticket.ticket_id, decision="approve")
            controller = SubagentPhaseController(
                registry=SubagentRegistry(
                    [_CheapReviewSubagent(), _PreferredReviewSubagent()],
                    catalog_overlay_provider=discovery_store.approved_catalog_entries,
                ),
                invoke_subagent=lambda invocation: (
                    {"ok": True},
                    SubagentExecutionRecord(
                        subagent_type=invocation.subagent_type,
                        invocation_id=invocation.invocation_id,
                        status="completed",
                        objective=invocation.objective,
                        inputs=invocation.inputs,
                        output_keys=["ok"],
                    ),
                ),
                emit_event=None,
                cache_context_builder=lambda **_: None,
                start_from_node_id_builder=lambda **_: None,
                runtime_binding_getter=lambda: {
                    "context": runtime_context,
                    "support": support,
                    "subagent_discovery_store": discovery_store,
                    "request_id": "req-gap-approved",
                    "intent": "review_thesis",
                },
            )

            with self.assertRaises(SubagentCapabilityGapError) as exc_info:
                controller.select_catalog_entry(
                    stage="review",
                    intent="review_thesis",
                    capability="missing_capability",
                )

            self.assertIn(
                "candidate_review_missing_capability",
                exc_info.exception.gap.candidate_subagent_types,
            )
            self.assertIn(
                "approved_discovery_candidates_available",
                exc_info.exception.gap.notes,
            )

    def test_execute_plan_injects_plan_metadata_and_trace(self) -> None:
        plan = SubagentPlan(
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                self.controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 7},
                    cache_key="review_strategy_resolver",
                )
            ],
        )
        runtime_nodes = {
            "review_strategy_resolver": {
                "builder": lambda _results: self.controller.build_invocation(
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 7},
                    task_name="review:strategy:resolver",
                    agent_path="reviewer/strategy/resolver",
                    phase="review",
                ),
                "metadata": self.controller.build_metadata(
                    task_name="review:strategy:resolver",
                    agent_path="reviewer/strategy/resolver",
                    objective="Resolve review strategy.",
                    phase="review",
                ),
            }
        }

        results = self.controller.execute_plan(
            plan=plan,
            build_invocation=runtime_nodes,
            dag_id="review_execution",
            max_workers=1,
        )

        self.assertIn("review_strategy_resolver", results)
        self.assertEqual(results["review_strategy_resolver"].outputs["value"], 7)
        trace = self.controller.trace_from_results(results=results)
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["plan_node_id"], "review_strategy_resolver")
        self.assertEqual(trace[0]["subagent_plan_strategy"], "catalog")
        self.assertEqual(trace[0]["task_name"], "review:strategy:resolver")
        self.assertEqual(trace[0]["agent_path"], "reviewer/strategy/resolver")

    def test_execute_patch_cycle_applies_patch_and_reexecutes_plan(self) -> None:
        plan = SubagentPlan(
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                self.controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 1},
                    cache_key="review_strategy_resolver",
                )
            ],
        )

        def _runtime_nodes(current_plan: SubagentPlan):
            return {
                node.node_id: {
                    "builder": (
                        lambda _results, *, current_node=node: self.controller.build_invocation(
                            subagent_type=current_node.subagent_type,
                            objective=current_node.objective,
                            inputs=dict(current_node.inputs or {}),
                            task_name=f"review:{current_node.node_id}",
                            agent_path=f"reviewer/{current_node.node_id}",
                            phase="review",
                        )
                    ),
                    "metadata": self.controller.build_metadata(
                        task_name=f"review:{node.node_id}",
                        agent_path=f"reviewer/{node.node_id}",
                        objective=node.objective,
                        phase="review",
                    ),
                }
                for node in current_plan.nodes
            }

        def _build_patch(_results):
            return SubagentGraphPatch(
                policy_source="rule",
                confidence=1.0,
                notes=["insert_summary"],
                operations=[
                    SubagentGraphPatchOperation(
                        op_type="insert_subagent",
                        node=self.controller.build_catalog_plan_node(
                            node_id="review_summary_builder",
                            subagent_type="cheap_review",
                            objective="Build review summary.",
                            inputs={"value": 2},
                            depends_on=["review_strategy_resolver"],
                            cache_key="review_summary_builder",
                        ),
                        reason="insert summary",
                    )
                ],
            )

        final_plan, base_results, patch_rows, final_results = self.controller.execute_patch_cycle(
            plan=plan,
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            build_invocation_factory=_runtime_nodes,
            patch_builder=_build_patch,
        )

        self.assertIn("review_strategy_resolver", base_results)
        self.assertEqual(len(patch_rows), 1)
        self.assertEqual(
            [node.node_id for node in final_plan.nodes],
            ["review_strategy_resolver", "review_summary_builder"],
        )
        self.assertIn("review_summary_builder", final_results)

    def test_execute_patch_cycle_supports_replan_requests(self) -> None:
        plan = SubagentPlan(
            plan_id="plan_initial",
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                self.controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 1},
                    cache_key="review_strategy_resolver",
                )
            ],
        )

        def _runtime_nodes(current_plan: SubagentPlan):
            return {
                node.node_id: {
                    "builder": (
                        lambda _results, *, current_node=node: self.controller.build_invocation(
                            subagent_type=current_node.subagent_type,
                            objective=current_node.objective,
                            inputs=dict(current_node.inputs or {}),
                            task_name=f"review:{current_node.node_id}",
                            agent_path=f"reviewer/{current_node.node_id}",
                            phase="review",
                        )
                    ),
                    "metadata": self.controller.build_metadata(
                        task_name=f"review:{node.node_id}",
                        agent_path=f"reviewer/{node.node_id}",
                        objective=node.objective,
                        phase="review",
                    ),
                }
                for node in current_plan.nodes
            }

        def _build_patch(_results):
            return SubagentGraphPatch(
                decision_type="replan",
                policy_source="rule",
                confidence=1.0,
                notes=["request_subagent_replan"],
                operations=[
                    SubagentGraphPatchOperation(
                        op_type="request_replan",
                        reason="review summary path should be rebuilt",
                    )
                ],
            )

        def _replan_builder(current_plan: SubagentPlan, patch: SubagentGraphPatch, _results):
            return current_plan.model_copy(
                update={
                    "plan_id": "plan_replanned",
                    "notes": [*list(current_plan.notes or []), *list(patch.notes or []), "replanned"],
                    "nodes": [
                        *list(current_plan.nodes or []),
                        self.controller.build_catalog_plan_node(
                            node_id="review_summary_builder",
                            subagent_type="cheap_review",
                            objective="Build review summary.",
                            inputs={"value": 2},
                            depends_on=["review_strategy_resolver"],
                            cache_key="review_summary_builder",
                        ),
                    ],
                },
                deep=True,
            )

        final_plan, base_results, patch_rows, final_results = self.controller.execute_patch_cycle(
            plan=plan,
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            build_invocation_factory=_runtime_nodes,
            patch_builder=_build_patch,
            replan_builder=_replan_builder,
        )

        self.assertIn("review_strategy_resolver", base_results)
        self.assertEqual(len(patch_rows), 1)
        self.assertEqual(final_plan.plan_id, "plan_replanned")
        self.assertEqual(
            [node.node_id for node in final_plan.nodes],
            ["review_strategy_resolver", "review_summary_builder"],
        )
        self.assertIn("review_summary_builder", final_results)

    def test_execute_patch_cycle_hybrid_can_suppress_rule_replan(self) -> None:
        events: list[dict] = []
        runtime_context = SimpleNamespace(
            subagent_phase_shadow_suggestions=[],
            subagent_phase_hybrid_decisions=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(
            resolve_llm_execution=lambda *_args, **_kwargs: SimpleNamespace(can_use_llm=True, degrade_reason=None),
            record_degradation=lambda *_args, **_kwargs: None,
            emit_event=lambda *_args, **_kwargs: None,
        )
        provider = _HybridShadowProvider(selected_plan_variant="current")
        controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=lambda invocation: (
                {"ok": True, "value": invocation.inputs.get("value")},
                SubagentExecutionRecord(
                    subagent_type=invocation.subagent_type,
                    invocation_id=invocation.invocation_id,
                    status="completed",
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["ok", "value"],
                ),
            ),
            emit_event=lambda **payload: events.append(dict(payload)),
            cache_context_builder=lambda **_: None,
            start_from_node_id_builder=lambda **_: None,
            runtime_binding_getter=lambda: {
                "context": runtime_context,
                "support": support,
                "request_id": "req-test",
                "intent": "review_thesis",
                "phase": "review",
                "agent": "reviewer",
                "execution_policy": SimpleNamespace(max_llm_calls_per_turn=3),
            },
            llm_provider=provider,
            settings=SimpleNamespace(
                subagent_phase_shadow_llm_enabled=True,
                subagent_phase_shadow_llm_reasoning_effort="low",
                subagent_phase_hybrid_llm_enabled=True,
                subagent_phase_hybrid_llm_min_confidence=0.8,
                max_llm_calls_per_turn=3,
            ),
            service_name="reviewer",
        )
        plan = SubagentPlan(
            plan_id="plan_initial",
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 1},
                    cache_key="review_strategy_resolver",
                )
            ],
        )

        def _runtime_nodes(current_plan: SubagentPlan):
            return {
                node.node_id: {
                    "builder": (
                        lambda _results, *, current_node=node: controller.build_invocation(
                            subagent_type=current_node.subagent_type,
                            objective=current_node.objective,
                            inputs=dict(current_node.inputs or {}),
                            task_name=f"review:{current_node.node_id}",
                            agent_path=f"reviewer/{current_node.node_id}",
                            phase="review",
                        )
                    ),
                    "metadata": controller.build_metadata(
                        task_name=f"review:{node.node_id}",
                        agent_path=f"reviewer/{node.node_id}",
                        objective=node.objective,
                        phase="review",
                    ),
                }
                for node in current_plan.nodes
            }

        def _build_patch(_results):
            return SubagentGraphPatch(
                decision_type="replan",
                policy_source="rule",
                confidence=1.0,
                notes=["request_subagent_replan"],
                operations=[
                    SubagentGraphPatchOperation(
                        op_type="request_replan",
                        reason="review summary path should be rebuilt",
                    )
                ],
            )

        def _replan_builder(current_plan: SubagentPlan, patch: SubagentGraphPatch, _results):
            return current_plan.model_copy(
                update={
                    "plan_id": "plan_replanned",
                    "nodes": [
                        *list(current_plan.nodes or []),
                        controller.build_catalog_plan_node(
                            node_id="review_summary_builder",
                            subagent_type="cheap_review",
                            objective="Build review summary.",
                            inputs={"value": 2},
                            depends_on=["review_strategy_resolver"],
                            cache_key="review_summary_builder",
                        ),
                    ],
                    "notes": [*list(current_plan.notes or []), *list(patch.notes or []), "replanned"],
                },
                deep=True,
            )

        final_plan, _base_results, patch_rows, final_results = controller.execute_patch_cycle(
            plan=plan,
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            allow_current_plan_variant=True,
            build_invocation_factory=_runtime_nodes,
            patch_builder=_build_patch,
            replan_builder=_replan_builder,
        )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(final_plan.plan_id, "plan_initial")
        self.assertEqual([node.node_id for node in final_plan.nodes], ["review_strategy_resolver"])
        self.assertEqual(list(patch_rows), [])
        self.assertEqual(list(final_results.keys()), ["review_strategy_resolver"])
        self.assertEqual(len(runtime_context.subagent_phase_shadow_suggestions), 1)
        self.assertEqual(len(runtime_context.subagent_phase_hybrid_decisions), 1)
        self.assertEqual(runtime_context.subagent_phase_hybrid_decisions[0]["applied_plan_variant"], "current")
        self.assertTrue(any(event.get("event_type") == "subagent_phase_hybrid_applied" for event in events))

    def test_execute_patch_cycle_hybrid_can_run_without_shadow_flag(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_phase_shadow_suggestions=[],
            subagent_phase_llm_decisions=[],
            subagent_phase_hybrid_decisions=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(
            resolve_llm_execution=lambda *_args, **_kwargs: SimpleNamespace(can_use_llm=True, degrade_reason=None),
            record_degradation=lambda *_args, **_kwargs: None,
            emit_event=lambda *_args, **_kwargs: None,
        )
        provider = _HybridShadowProvider(selected_plan_variant="current")
        controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=lambda invocation: (
                {"ok": True, "value": invocation.inputs.get("value")},
                SubagentExecutionRecord(
                    subagent_type=invocation.subagent_type,
                    invocation_id=invocation.invocation_id,
                    status="completed",
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["ok", "value"],
                ),
            ),
            emit_event=None,
            cache_context_builder=lambda **_: None,
            start_from_node_id_builder=lambda **_: None,
            runtime_binding_getter=lambda: {
                "context": runtime_context,
                "support": support,
                "request_id": "req-hybrid-only",
                "intent": "review_thesis",
                "phase": "review",
                "agent": "reviewer",
                "execution_policy": SimpleNamespace(max_llm_calls_per_turn=3),
            },
            llm_provider=provider,
            settings=SimpleNamespace(
                subagent_phase_shadow_llm_enabled=False,
                subagent_phase_shadow_llm_reasoning_effort="low",
                subagent_phase_hybrid_llm_enabled=True,
                subagent_phase_hybrid_llm_min_confidence=0.8,
                max_llm_calls_per_turn=3,
            ),
            service_name="reviewer",
        )
        plan = SubagentPlan(
            plan_id="plan_initial",
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 1},
                    cache_key="review_strategy_resolver",
                )
            ],
        )

        def _runtime_nodes(current_plan: SubagentPlan):
            return {
                node.node_id: {
                    "builder": (
                        lambda _results, *, current_node=node: controller.build_invocation(
                            subagent_type=current_node.subagent_type,
                            objective=current_node.objective,
                            inputs=dict(current_node.inputs or {}),
                            task_name=f"review:{current_node.node_id}",
                            agent_path=f"reviewer/{current_node.node_id}",
                            phase="review",
                        )
                    ),
                    "metadata": controller.build_metadata(
                        task_name=f"review:{node.node_id}",
                        agent_path=f"reviewer/{node.node_id}",
                        objective=node.objective,
                        phase="review",
                    ),
                }
                for node in current_plan.nodes
            }

        def _build_patch(_results):
            return SubagentGraphPatch(
                decision_type="replan",
                policy_source="rule",
                confidence=1.0,
                notes=["request_subagent_replan"],
                operations=[
                    SubagentGraphPatchOperation(
                        op_type="request_replan",
                        reason="review summary path should be rebuilt",
                    )
                ],
            )

        def _replan_builder(current_plan: SubagentPlan, patch: SubagentGraphPatch, _results):
            return current_plan.model_copy(
                update={
                    "plan_id": "plan_replanned",
                    "nodes": [
                        *list(current_plan.nodes or []),
                        controller.build_catalog_plan_node(
                            node_id="review_summary_builder",
                            subagent_type="cheap_review",
                            objective="Build review summary.",
                            inputs={"value": 2},
                            depends_on=["review_strategy_resolver"],
                            cache_key="review_summary_builder",
                        ),
                    ],
                    "notes": [*list(current_plan.notes or []), *list(patch.notes or []), "replanned"],
                },
                deep=True,
            )

        final_plan, _base_results, patch_rows, final_results = controller.execute_patch_cycle(
            plan=plan,
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            allow_current_plan_variant=True,
            build_invocation_factory=_runtime_nodes,
            patch_builder=_build_patch,
            replan_builder=_replan_builder,
        )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(final_plan.plan_id, "plan_initial")
        self.assertEqual([node.node_id for node in final_plan.nodes], ["review_strategy_resolver"])
        self.assertEqual(list(patch_rows), [])
        self.assertEqual(list(final_results.keys()), ["review_strategy_resolver"])
        self.assertEqual(len(runtime_context.subagent_phase_shadow_suggestions), 1)
        self.assertEqual(len(runtime_context.subagent_phase_hybrid_decisions), 1)

    def test_execute_patch_cycle_llm_policy_can_suppress_rule_replan(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_phase_shadow_suggestions=[],
            subagent_phase_llm_decisions=[],
            subagent_phase_hybrid_decisions=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(
            resolve_llm_execution=lambda *_args, **_kwargs: SimpleNamespace(can_use_llm=True, degrade_reason=None),
            record_degradation=lambda *_args, **_kwargs: None,
            emit_event=lambda *_args, **_kwargs: None,
        )
        provider = _HybridShadowProvider(selected_plan_variant="current")
        controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=lambda invocation: (
                {"ok": True, "value": invocation.inputs.get("value")},
                SubagentExecutionRecord(
                    subagent_type=invocation.subagent_type,
                    invocation_id=invocation.invocation_id,
                    status="completed",
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["ok", "value"],
                ),
            ),
            emit_event=None,
            cache_context_builder=lambda **_: None,
            start_from_node_id_builder=lambda **_: None,
            runtime_binding_getter=lambda: {
                "context": runtime_context,
                "support": support,
                "request_id": "req-llm-only",
                "intent": "review_thesis",
                "phase": "review",
                "agent": "reviewer",
                "execution_policy": SimpleNamespace(max_llm_calls_per_turn=3),
            },
            llm_provider=provider,
            settings=SimpleNamespace(
                subagent_phase_shadow_llm_enabled=False,
                subagent_phase_shadow_llm_reasoning_effort="low",
                subagent_phase_llm_policy_enabled=True,
                subagent_phase_llm_policy_min_confidence=0.8,
                subagent_phase_hybrid_llm_enabled=False,
                subagent_phase_hybrid_llm_min_confidence=0.8,
                max_llm_calls_per_turn=3,
            ),
            service_name="reviewer",
        )
        plan = SubagentPlan(
            plan_id="plan_initial",
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 1},
                    cache_key="review_strategy_resolver",
                )
            ],
        )

        def _runtime_nodes(current_plan: SubagentPlan):
            return {
                node.node_id: {
                    "builder": (
                        lambda _results, *, current_node=node: controller.build_invocation(
                            subagent_type=current_node.subagent_type,
                            objective=current_node.objective,
                            inputs=dict(current_node.inputs or {}),
                            task_name=f"review:{current_node.node_id}",
                            agent_path=f"reviewer/{current_node.node_id}",
                            phase="review",
                        )
                    ),
                    "metadata": controller.build_metadata(
                        task_name=f"review:{node.node_id}",
                        agent_path=f"reviewer/{node.node_id}",
                        objective=node.objective,
                        phase="review",
                    ),
                }
                for node in current_plan.nodes
            }

        def _build_patch(_results):
            return SubagentGraphPatch(
                decision_type="replan",
                policy_source="rule",
                confidence=1.0,
                notes=["request_subagent_replan"],
                operations=[
                    SubagentGraphPatchOperation(
                        op_type="request_replan",
                        reason="review summary path should be rebuilt",
                    )
                ],
            )

        def _replan_builder(current_plan: SubagentPlan, patch: SubagentGraphPatch, _results):
            return current_plan.model_copy(
                update={
                    "plan_id": "plan_replanned",
                    "nodes": [
                        *list(current_plan.nodes or []),
                        controller.build_catalog_plan_node(
                            node_id="review_summary_builder",
                            subagent_type="cheap_review",
                            objective="Build review summary.",
                            inputs={"value": 2},
                            depends_on=["review_strategy_resolver"],
                            cache_key="review_summary_builder",
                        ),
                    ],
                    "notes": [*list(current_plan.notes or []), *list(patch.notes or []), "replanned"],
                },
                deep=True,
            )

        final_plan, _base_results, patch_rows, final_results = controller.execute_patch_cycle(
            plan=plan,
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            allow_current_plan_variant=True,
            build_invocation_factory=_runtime_nodes,
            patch_builder=_build_patch,
            replan_builder=_replan_builder,
        )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(final_plan.plan_id, "plan_initial")
        self.assertEqual([node.node_id for node in final_plan.nodes], ["review_strategy_resolver"])
        self.assertEqual(list(patch_rows), [])
        self.assertEqual(list(final_results.keys()), ["review_strategy_resolver"])
        self.assertEqual(len(runtime_context.subagent_phase_shadow_suggestions), 1)
        self.assertEqual(len(runtime_context.subagent_phase_llm_decisions), 1)
        self.assertEqual(runtime_context.subagent_phase_llm_decisions[0]["applied_plan_variant"], "current")
        self.assertEqual(len(runtime_context.subagent_phase_hybrid_decisions), 0)

    def test_execute_patch_cycle_respects_runtime_execution_policy_budget(self) -> None:
        runtime_context = SimpleNamespace(
            subagent_phase_shadow_suggestions=[],
            subagent_phase_llm_decisions=[],
            subagent_phase_hybrid_decisions=[],
            dynamic_trace=[],
        )
        support = SimpleNamespace(
            resolve_llm_execution=lambda *_args, **_kwargs: SimpleNamespace(can_use_llm=True, degrade_reason=None),
            record_degradation=lambda *_args, **_kwargs: None,
        )
        provider = _HybridShadowProvider(selected_plan_variant="current")
        controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=lambda invocation: (
                {"ok": True, "value": invocation.inputs.get("value")},
                SubagentExecutionRecord(
                    subagent_type=invocation.subagent_type,
                    invocation_id=invocation.invocation_id,
                    status="completed",
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["ok", "value"],
                ),
            ),
            emit_event=None,
            cache_context_builder=lambda **_: None,
            start_from_node_id_builder=lambda **_: None,
            runtime_binding_getter=lambda: {
                "context": runtime_context,
                "support": support,
                "request_id": "req-runtime-budget",
                "intent": "review_thesis",
                "phase": "review",
                "agent": "reviewer",
                "execution_policy": SimpleNamespace(max_llm_calls_per_turn=1),
            },
            llm_provider=provider,
            settings=SimpleNamespace(
                subagent_phase_shadow_llm_enabled=True,
                subagent_phase_shadow_llm_reasoning_effort="low",
                subagent_phase_hybrid_llm_enabled=True,
                subagent_phase_hybrid_llm_min_confidence=0.8,
                max_llm_calls_per_turn=3,
            ),
            service_name="reviewer",
        )
        plan = SubagentPlan(
            plan_id="plan_initial",
            intent="review_thesis",
            phase="review",
            strategy="catalog",
            nodes=[
                controller.build_catalog_plan_node(
                    node_id="review_strategy_resolver",
                    subagent_type="cheap_review",
                    objective="Resolve review strategy.",
                    inputs={"value": 1},
                    cache_key="review_strategy_resolver",
                )
            ],
        )

        def _runtime_nodes(current_plan: SubagentPlan):
            return {
                node.node_id: {
                    "builder": (
                        lambda _results, *, current_node=node: controller.build_invocation(
                            subagent_type=current_node.subagent_type,
                            objective=current_node.objective,
                            inputs=dict(current_node.inputs or {}),
                            task_name=f"review:{current_node.node_id}",
                            agent_path=f"reviewer/{current_node.node_id}",
                            phase="review",
                        )
                    ),
                    "metadata": controller.build_metadata(
                        task_name=f"review:{node.node_id}",
                        agent_path=f"reviewer/{node.node_id}",
                        objective=node.objective,
                        phase="review",
                    ),
                }
                for node in current_plan.nodes
            }

        def _build_patch(_results):
            return SubagentGraphPatch(
                decision_type="replan",
                policy_source="rule",
                confidence=1.0,
                notes=["request_subagent_replan"],
                operations=[
                    SubagentGraphPatchOperation(
                        op_type="request_replan",
                        reason="review summary path should be rebuilt",
                    )
                ],
            )

        def _replan_builder(current_plan: SubagentPlan, patch: SubagentGraphPatch, _results):
            return current_plan.model_copy(
                update={
                    "plan_id": "plan_replanned",
                    "nodes": [
                        *list(current_plan.nodes or []),
                        controller.build_catalog_plan_node(
                            node_id="review_summary_builder",
                            subagent_type="cheap_review",
                            objective="Build review summary.",
                            inputs={"value": 2},
                            depends_on=["review_strategy_resolver"],
                            cache_key="review_summary_builder",
                        ),
                    ],
                    "notes": [*list(current_plan.notes or []), *list(patch.notes or []), "replanned"],
                },
                deep=True,
            )

        final_plan, _base_results, patch_rows, final_results = controller.execute_patch_cycle(
            plan=plan,
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            allow_current_plan_variant=True,
            build_invocation_factory=_runtime_nodes,
            patch_builder=_build_patch,
            replan_builder=_replan_builder,
        )

        self.assertEqual(provider.calls, 0)
        self.assertEqual(final_plan.plan_id, "plan_replanned")
        self.assertEqual(
            [node.node_id for node in final_plan.nodes],
            ["review_strategy_resolver", "review_summary_builder"],
        )
        self.assertEqual(len(patch_rows), 1)
        self.assertIn("review_summary_builder", final_results)
        self.assertEqual(len(runtime_context.subagent_phase_shadow_suggestions), 0)
        self.assertEqual(len(runtime_context.subagent_phase_hybrid_decisions), 0)


if __name__ == "__main__":
    unittest.main()
