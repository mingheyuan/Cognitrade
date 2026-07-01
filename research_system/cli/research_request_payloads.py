from __future__ import annotations

from research_system.schemas.protocol import RequestEnvelope
from research_system.services.task_router import ChatDispatchDecision


def build_research_request(
    *,
    query: str,
    intent: str | None = None,
    review_target_run_id: str | None = None,
    dispatch: ChatDispatchDecision | None = None,
    conversation_context: list[dict] | None = None,
) -> RequestEnvelope:
    constraints: dict[str, object] = {}
    if review_target_run_id:
        constraints["review_target_run_id"] = str(review_target_run_id)
    if dispatch is not None:
        constraints["chat_dispatch"] = {
            "mode": dispatch.mode,
            "intent": dispatch.intent,
            "reason": dispatch.reason,
            "source": "chat_dispatch",
        }
    return RequestEnvelope(
        intent=intent,
        user_query=query,
        conversation_context=list(conversation_context or []),
        constraints=constraints,
    )
