from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunStartupSupport:
    def __init__(self, host: Any) -> None:
        self.host = host

    def build_clarification_response(self, *, request: RequestEnvelope, decision) -> ResponseEnvelope:
        progress = self.host.progress_service.build(
            current_phase="clarify",
            active_agent=None,
            latest_event="clarification_requested",
            status_message="Need clarification before execution",
            percent=0,
        )
        return ResponseEnvelope(
            request_id=request.request_id,
            intent=request.intent,
            status="needs_clarification",
            message="Clarification required",
            clarification=decision.clarification,
            progress=progress,
            result={"routing_trace": decision.trace} if decision.trace else None,
        )

    def create_context(self, *, request: RequestEnvelope, intent: str):
        run_id, paths, state = self.host.run_store.create_run(request, intent)
        context = build_runtime_context(
            run_id=run_id,
            paths=paths,
            state=state,
            run_timeout_seconds=self.host.settings.run_timeout_seconds,
        )
        return run_id, paths, context

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
        plan = self.host.planner.build_initial_plan(intent=intent, request=request)
        self.write_task_plan(context=context, plan=plan)
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="plan_created",
            message="Execution plan created",
            payload={
                "strategy": plan.strategy,
                "entry_strategy": plan.entry_strategy,
                "entry_reason": plan.entry_reason,
                "subagent_required": plan.subagent_required,
                "dispatch_trace": plan.dispatch_trace,
                "actions": [
                    {"node_id": action.node_id, "phase": action.phase, "agent": action.agent}
                    for action in plan.actions
                ],
                "notes": plan.notes,
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
        return plan

    def write_task_plan(self, *, context, plan) -> None:
        write_json(
            context.paths.task_plan,
            {
                "intent": plan.intent,
                "strategy": plan.strategy,
                "entry_strategy": plan.entry_strategy,
                "entry_reason": plan.entry_reason,
                "subagent_required": plan.subagent_required,
                "dispatch_trace": plan.dispatch_trace,
                "notes": plan.notes,
                "actions": [action.model_dump(mode="json") for action in plan.actions],
            },
        )
