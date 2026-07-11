from __future__ import annotations

from research_system.cli.flow.chat_event_adapter import build_chat_presenter, chat_event_listener
from research_system.cli.commands.research_request_payloads import build_research_request
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings
from research_system.schemas.runtime import RunEvent
from research_system.services.routing.router.task_router import ChatDispatchDecision


def run_chat_research(
    settings: Settings,
    *,
    query: str,
    intent: str | None,
    run_id: str | None,
    thinking_enabled: bool,
    events_mode: str,
    dispatch: ChatDispatchDecision | None = None,
    conversation_context: list[dict] | None = None,
    output_writer=None,
    orchestrator_cls=None,
) -> object:
    presenter = build_chat_presenter(thinking_enabled=thinking_enabled, events_mode=events_mode)

    def _listener(event) -> None:
        if isinstance(event, dict):
            event = RunEvent(
                event_id=str(event.get("event_id") or ""),
                request_id=str(event.get("request_id") or request.request_id),
                run_id=str(event.get("run_id") or run_id or ""),
                intent=str(event.get("intent") or intent or ""),
                event_type=str(event.get("event_type") or "agent_progress"),
                phase=event.get("phase"),
                agent=event.get("agent") or event.get("actor"),
                message=str(event.get("message") or event.get("event_type") or ""),
                payload=dict(event),
            )
        chat_event_listener(event, presenter=presenter, output_writer=output_writer)

    request = build_research_request(
        intent=intent,
        query=query,
        review_target_run_id=run_id,
        dispatch=dispatch,
        conversation_context=conversation_context or [],
    )
    if orchestrator_cls is None:
        response = AgentOS(settings).handle_request(request, event_callback=_listener)
        return response.model_dump(mode="json")
    with orchestrator_cls(settings, event_listener=_listener) as orchestrator:
        response = orchestrator.run(request)
        return response.model_dump(mode="json")
