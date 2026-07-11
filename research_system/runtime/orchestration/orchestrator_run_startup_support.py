from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.execution_graph_store import persist_execution_graph
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.runtime import RuntimeTurnState
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunStartupSupport:
    def __init__(self, host: Any) -> None:
        self.host = host

    def build_clarification_response(self, *, request: RequestEnvelope, decision, run_id: str | None = None) -> ResponseEnvelope:
        progress = self.host.progress_service.build(
            current_phase="clarify",
            active_agent=None,
            latest_event="clarification_requested",
            status_message="Need clarification before execution",
            percent=0,
        )
        return ResponseEnvelope(
            request_id=request.request_id,
            run_id=run_id,
            intent=request.intent,
            status="needs_clarification",
            message="Clarification required",
            clarification=decision.clarification,
            progress=progress,
            result={"routing_trace": decision.trace} if decision.trace else None,
        )

    def create_preflight_context(self, *, request: RequestEnvelope):
        return self.create_context(request=request, intent="routing")

    def create_context(self, *, request: RequestEnvelope, intent: str):
        run_id, paths, state = self.host.run_store.create_run(request, intent)
        context = build_runtime_context(
            run_id=run_id,
            paths=paths,
            state=state,
            run_timeout_seconds=self.host.settings.run_timeout_seconds,
            max_llm_calls_per_turn=self.host.settings.max_llm_calls_per_turn,
            max_subagent_calls_per_turn=self.host.settings.max_subagent_calls_per_turn,
            max_child_threads_per_turn=self.host.settings.max_child_threads_per_turn,
        )
        return run_id, paths, context

    def promote_context_intent(self, *, context, request: RequestEnvelope, intent: str) -> None:
        previous_turn_id = context.turn.turn_id
        context.execution_policy.release_turn(turn_id=previous_turn_id)
        context.turn.status = "completed"
        main_turn = RuntimeTurnState(
            thread_id=context.thread.thread_id,
            run_id=context.run_id,
            request_id=request.request_id,
            turn_index=max(int(context.thread.total_turns or 1) + 1, 2),
            status="ready",
            intent=intent,
            objective=f"{intent}:{request.request_id}",
        )
        context.thread.intent = intent
        context.thread.active_turn_id = main_turn.turn_id
        context.thread.total_turns = main_turn.turn_index
        context.state.intent = intent
        context.state.thread_id = context.thread.thread_id
        context.state.active_turn_id = main_turn.turn_id
        context.turn = main_turn
        write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
        write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
        write_json(context.paths.run_state, context.state.model_dump(mode="json"))
        context.dynamic_trace.append(
            {
                "event": "routing_turn_completed",
                "previous_turn_id": previous_turn_id,
                "next_turn_id": main_turn.turn_id,
                "intent": intent,
            }
        )
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="routing_turn_completed",
            phase="routing",
            agent="router",
            message=f"Routing completed: {intent}",
            payload={
                "previous_turn_id": previous_turn_id,
                "next_turn_id": main_turn.turn_id,
                "intent": intent,
            },
        )

    def initialize_run(self, *, context, request: RequestEnvelope, intent: str):
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="request_received",
            message="Request received",
        )
        self.host._transition(
            context,
            "identifying_intent",
            request_id=request.request_id,
            intent=intent,
            message="Identifying intent",
        )
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="intent_identified",
            message=f"Intent identified: {intent}",
        )
        execution_graph = self.host.planner.build_initial_execution_graph(
            intent=intent,
            request=request,
            lazy=True,
        )
        persist_execution_graph(context=context, graph=execution_graph)
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="plan_created",
            message="Execution plan created",
            payload={
                "strategy": execution_graph.strategy,
                "graph_id": execution_graph.graph_id,
                "revision": execution_graph.revision,
                "domain_id": execution_graph.domain_id,
                "nodes": [
                    {
                        "node_id": node.node_id,
                        "node_kind": node.node_kind,
                        "capability": node.capability,
                        "executor": node.executor,
                    }
                    for node in execution_graph.nodes
                ],
                "notes": execution_graph.notes,
            },
        )
        self.host._transition(
            context,
            "ready",
            request_id=request.request_id,
            intent=intent,
            message="Execution plan created",
        )
        self.host._transition(
            context,
            "running",
            request_id=request.request_id,
            intent=intent,
            message="Run accepted",
        )
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="run_accepted",
            message="Run accepted",
        )
        return execution_graph
