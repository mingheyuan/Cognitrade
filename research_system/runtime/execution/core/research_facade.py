from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.runtime.orchestration.orchestrator_research_forwarding_support import (
    OrchestratorResearchForwardingSupport,
)
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorExecutionResearchFacade:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.forwarding = OrchestratorResearchForwardingSupport(host)

    def _research_support(self):
        return self.forwarding._research_support()

    def run_methodology_evolution_collect(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        return self.forwarding.run_methodology_evolution_collect(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
        )

    def build_candidate_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        candidate: dict,
        source_type: str,
    ) -> list[BlackboardEntry]:
        return self.forwarding.build_candidate_methodology_entries(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
            candidate=candidate,
            source_type=source_type,
        )

    def build_stock_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        return self.forwarding.build_stock_methodology_entries(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
        )

    def build_review_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        review_request: dict,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
    ) -> list[BlackboardEntry]:
        return self.forwarding.build_review_methodology_entries(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
            review_request=review_request,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
        )

    def build_debug_payload(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan,
        plan_intent: str,
        retrieval_summary: dict | None,
    ) -> dict:
        return self.forwarding.build_debug_payload(
            context=context,
            request=request,
            plan=plan,
            plan_intent=plan_intent,
            retrieval_summary=retrieval_summary,
        )
