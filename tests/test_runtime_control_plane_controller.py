from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.execution.core.runtime_control_plane_controller import (
    OrchestratorRuntimeControlPlaneController,
)


class _SupervisorStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls = 0

    def bind_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_runtime(self) -> None:
        self.clear_calls += 1


class _ProtocolControllerStub:
    def __init__(self) -> None:
        self.resolve_calls: list[dict] = []
        self.escalation_calls: list[dict] = []

    def resolve_supervisor_cycle(self, **kwargs):
        self.resolve_calls.append(dict(kwargs))
        return "protocol-resolution"

    def handle_subagent_escalation(self, **kwargs):
        self.escalation_calls.append(dict(kwargs))
        return ["escalated"]


class _ServiceStub:
    def __init__(self) -> None:
        self.bind_calls: list[dict] = []
        self.clear_calls = 0

    def bind_runtime(self, **kwargs) -> None:
        self.bind_calls.append(dict(kwargs))

    def clear_runtime(self) -> None:
        self.clear_calls += 1


class _CatalogControllerStub:
    def __init__(self, resolution) -> None:
        self.resolution = resolution
        self.calls: list[dict] = []
        self.capability_gap_controller = SimpleNamespace(
            build_unresolved_gap_error=lambda resolution: RuntimeError(
                f"unresolved:{getattr(getattr(resolution, 'gap', None), 'capability', 'missing')}"
            )
        )

    def resolve_catalog_entry(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.resolution


class _PhaseExecutionControllerStub:
    def __init__(self, resolution) -> None:
        self.resolution = resolution
        self.calls: list[dict] = []

    def execute_patch_cycle(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.resolution


class _EscalationControllerStub:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    def resolve(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.decision


class RuntimeControlPlaneControllerTests(unittest.TestCase):
    def test_bind_and_clear_runtime_delegate_to_supervisor(self) -> None:
        supervisor = _SupervisorStub()
        controller = OrchestratorRuntimeControlPlaneController(
            host=SimpleNamespace(),
            supervisor=supervisor,
        )

        controller.bind_runtime(
            context="context",
            support="support",
            request_id="req-1",
            intent="stock_screen",
        )
        controller.clear_runtime()

        self.assertEqual(len(supervisor.bind_calls), 1)
        self.assertEqual(supervisor.bind_calls[0]["request_id"], "req-1")
        self.assertEqual(supervisor.bind_calls[0]["intent"], "stock_screen")
        self.assertEqual(supervisor.clear_calls, 1)

    def test_runtime_protocol_service_shares_one_protocol_decision_source(self) -> None:
        controller = OrchestratorRuntimeControlPlaneController(
            host=SimpleNamespace(),
            supervisor=_SupervisorStub(),
        )

        self.assertIs(
            controller.protocol_controller.subagent_protocol_controller.decision_source,
            controller.protocol_decision_source,
        )

        controller.register_execution_callbacks(
            apply_plan_patch=lambda **kwargs: [],
            request_action_plan_replan=lambda **kwargs: [],
        )

        self.assertIsNotNone(controller.protocol_controller.action_protocol_controller)
        self.assertIs(
            controller.protocol_controller.action_protocol_controller.decision_source,
            controller.protocol_decision_source,
        )

    def test_register_execution_callbacks_and_delegate_resolution(self) -> None:
        supervisor = _SupervisorStub()
        controller = OrchestratorRuntimeControlPlaneController(
            host=SimpleNamespace(),
            supervisor=supervisor,
        )
        protocol = _ProtocolControllerStub()
        controller.protocol_controller = protocol

        result = controller.resolve_supervisor_cycle(
            context="context",
            request="request",
            plan="plan",
            ready_actions=["ready"],
            pending_actions=["pending"],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
        )
        escalation = controller.handle_subagent_escalation(
            context="context",
            request="request",
            plan="plan",
            pending_actions=["pending"],
            completed_node_ids=["collect"],
            skipped_node_ids=[],
            action="review",
            escalation_error="error",
        )

        self.assertEqual(result, "protocol-resolution")
        self.assertEqual(escalation, ["escalated"])
        self.assertEqual(len(protocol.resolve_calls), 1)
        self.assertEqual(len(protocol.escalation_calls), 1)

    def test_bind_and_clear_agent_service_runtime_delegate_through_shared_service(self) -> None:
        host = SimpleNamespace(
            subagent_discovery_store="store",
            skill_runtime="skill-runtime",
            require_subagent_discovery_store=lambda: "store",
            require_skill_runtime=lambda: "skill-runtime",
            require_mcp_source_registry=lambda: "mcp-registry",
            require_http_json_source_registry=lambda: "http-json-registry",
            require_openapi_source_registry=lambda: "openapi-registry",
            require_skill_source_registry=lambda: "skill-source-registry",
        )
        controller = OrchestratorRuntimeControlPlaneController(
            host=host,
            supervisor=_SupervisorStub(),
        )
        service = _ServiceStub()
        context = SimpleNamespace(
            agent_control="agent-control",
            execution_policy="execution-policy",
            thread=SimpleNamespace(thread_id="thread-1"),
            turn=SimpleNamespace(turn_id="turn-1"),
        )

        controller.bind_agent_service_runtime(
            service=service,
            context=context,
            support="support",
            request_id="req-1",
            intent="stock_screen",
            agent="collector",
            phase="collect",
        )
        controller.clear_agent_service_runtime(service=service)

        self.assertEqual(len(service.bind_calls), 1)
        bind_call = service.bind_calls[0]
        self.assertIs(bind_call["runtime_control"], controller)
        self.assertEqual(bind_call["thread_id"], "thread-1")
        self.assertEqual(bind_call["turn_id"], "turn-1")
        self.assertEqual(bind_call["request_id"], "req-1")
        self.assertEqual(bind_call["intent"], "stock_screen")
        self.assertEqual(bind_call["agent"], "collector")
        self.assertEqual(bind_call["phase"], "collect")
        self.assertEqual(bind_call["require_mcp_source_registry"](), "mcp-registry")
        self.assertEqual(bind_call["require_http_json_source_registry"](), "http-json-registry")
        self.assertEqual(bind_call["require_openapi_source_registry"](), "openapi-registry")
        self.assertEqual(bind_call["require_skill_source_registry"](), "skill-source-registry")
        self.assertEqual(service.clear_calls, 1)

    def test_resolve_subagent_catalog_entry_records_runtime_control_stage(self) -> None:
        host = SimpleNamespace(events=[])

        def _emit_event(context, **kwargs) -> None:
            host.events.append(dict(kwargs))

        host._emit_event = _emit_event
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])
        resolution = SimpleNamespace(
            selected_entry=SimpleNamespace(subagent_type="cheap_review"),
            capability_gap_resolution=None,
        )
        controller = OrchestratorRuntimeControlPlaneController(
            host=host,
            supervisor=_SupervisorStub(),
        )

        resolved = controller.resolve_subagent_catalog_entry(
            catalog_controller=_CatalogControllerStub(resolution),
            runtime_binding={
                "context": context,
                "request_id": "req-subagent",
                "intent": "review_thesis",
                "agent": "reviewer",
            },
            stage="review",
            intent="review_thesis",
            capability="review_strategy",
            preferred_subagent_type="cheap_review",
            requires_llm=False,
        )

        self.assertEqual(resolved.selected_entry.subagent_type, "cheap_review")
        self.assertEqual(len(context.runtime_control_stage_decisions), 3)
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["event"],
            "runtime_control_subagent_resolved",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["resolution_source"],
            "catalog",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["selected_subagent_type"],
            "cheap_review",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["event"],
            "runtime_control_subagent_transition_decided",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["transition_source"],
            "catalog_selection",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["transition_type"],
            "continue_catalog",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[2]["event"],
            "runtime_control_subagent_protocol_resolved",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[2]["resolution_source"],
            "catalog_selection",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[2]["selected_subagent_type"],
            "cheap_review",
        )
        self.assertEqual(host.events[0]["event_type"], "runtime_control_subagent_resolved")
        self.assertEqual(host.events[1]["event_type"], "runtime_control_subagent_transition_decided")
        self.assertEqual(host.events[2]["event_type"], "runtime_control_subagent_protocol_resolved")

    def test_execute_subagent_phase_patch_cycle_records_runtime_control_stage(self) -> None:
        host = SimpleNamespace(events=[])

        def _emit_event(context, **kwargs) -> None:
            host.events.append(dict(kwargs))

        host._emit_event = _emit_event
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])
        resolution = SimpleNamespace(
            final_plan=SimpleNamespace(nodes=[SimpleNamespace(node_id="review_strategy_resolver")]),
            base_results={"review_strategy_resolver": "base"},
            final_patch_rows=[],
            final_results={"review_strategy_resolver": "final"},
            rule_variant_id="current",
            final_plan_variant="current",
            candidate_plan_variants=["current"],
            final_patch=None,
            shadow_payload=None,
        )
        controller = OrchestratorRuntimeControlPlaneController(
            host=host,
            supervisor=_SupervisorStub(),
        )

        resolved = controller.execute_subagent_phase_patch_cycle(
            phase_execution_controller=_PhaseExecutionControllerStub(resolution),
            runtime_binding={
                "context": context,
                "request_id": "req-phase",
                "intent": "review_thesis",
                "agent": "reviewer",
            },
            plan=SimpleNamespace(
                intent="review_thesis",
                phase="review",
                plan_id="plan-1",
                strategy="catalog",
                nodes=[SimpleNamespace(node_id="review_strategy_resolver")],
            ),
            dag_id="review_execution",
            base_max_workers=1,
            final_max_workers=1,
            build_invocation_factory=lambda _plan: {},
            patch_builder=lambda _results: None,
            replan_builder=None,
            allow_current_plan_variant=True,
            candidate_plan_variants_builder=None,
        )

        self.assertIs(resolved, resolution)
        self.assertEqual(len(context.runtime_control_stage_decisions), 3)
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["event"],
            "runtime_control_subagent_phase_resolved",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["resolution_source"],
            "phase_patch_cycle",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["final_plan_variant"],
            "current",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["event"],
            "runtime_control_subagent_transition_decided",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["transition_source"],
            "phase_patch_cycle",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["transition_type"],
            "keep_current",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[2]["event"],
            "runtime_control_subagent_protocol_resolved",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[2]["resolution_source"],
            "phase_patch_cycle",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[2]["final_plan_variant"],
            "current",
        )
        self.assertEqual(host.events[0]["event_type"], "runtime_control_subagent_phase_resolved")
        self.assertEqual(host.events[1]["event_type"], "runtime_control_subagent_transition_decided")
        self.assertEqual(host.events[2]["event_type"], "runtime_control_subagent_protocol_resolved")

    def test_resolve_subagent_capability_gap_escalation_records_runtime_protocol_stage(self) -> None:
        host = SimpleNamespace(events=[])

        def _emit_event(context, **kwargs) -> None:
            host.events.append(dict(kwargs))

        host._emit_event = _emit_event
        context = SimpleNamespace(runtime_control_stage_decisions=[], dynamic_trace=[])
        controller = OrchestratorRuntimeControlPlaneController(
            host=host,
            supervisor=_SupervisorStub(),
        )
        escalation_controller = _EscalationControllerStub(
            SimpleNamespace(
                strategy="action_replan",
                policy_source="rule",
                notes=("skip_optional_review_phase",),
            )
        )

        decision = controller.resolve_subagent_capability_gap_escalation(
            escalation_controller=escalation_controller,
            runtime_binding={
                "context": context,
                "request_id": "req-gap-escalation",
                "intent": "review_thesis",
                "agent": "reviewer",
            },
            gap=SimpleNamespace(
                intent="review_thesis",
                stage="review",
                capability="missing_review_capability",
            ),
            preferred_subagent_type=None,
            requires_llm=None,
        )

        self.assertEqual(decision.strategy, "action_replan")
        self.assertEqual(len(escalation_controller.calls), 1)
        self.assertEqual(len(context.runtime_control_stage_decisions), 2)
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["event"],
            "runtime_control_subagent_transition_decided",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_source"],
            "capability_gap_escalation",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[0]["transition_type"],
            "request_action_replan",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["event"],
            "runtime_control_subagent_protocol_resolved",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["resolution_source"],
            "capability_gap_escalation",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["decision_type"],
            "request_action_replan",
        )
        self.assertEqual(
            context.runtime_control_stage_decisions[1]["capability"],
            "missing_review_capability",
        )
        self.assertEqual(host.events[0]["event_type"], "runtime_control_subagent_transition_decided")
        self.assertEqual(host.events[1]["event_type"], "runtime_control_subagent_protocol_resolved")

    def test_resolve_before_registering_execution_callbacks_raises(self) -> None:
        controller = OrchestratorRuntimeControlPlaneController(
            host=SimpleNamespace(),
            supervisor=_SupervisorStub(),
        )

        with self.assertRaises(RuntimeError):
            controller.resolve_supervisor_cycle(
                context="context",
                request="request",
                plan="plan",
                ready_actions=[],
                pending_actions=[],
                completed_node_ids=[],
                skipped_node_ids=[],
            )


if __name__ == "__main__":
    unittest.main()
