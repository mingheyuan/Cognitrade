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
) -> object:
    presenter = build_chat_presenter(thinking_enabled=thinking_enabled, events_mode=events_mode)

    def _listener(event: RunEvent) -> None:
        chat_event_listener(event, presenter=presenter, output_writer=output_writer)

    request = build_research_request(
        intent=intent,
        query=query,
        review_target_run_id=run_id,
        dispatch=dispatch,
        conversation_context=conversation_context or [],
    )
    response = AgentOS(settings).handle_request(request, event_callback=_listener)
    return response.model_dump(mode="json")
