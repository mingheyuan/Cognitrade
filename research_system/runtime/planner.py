from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


class EntryPlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_subagents: bool
    initial_actions: list[Literal["collect", "screen", "retrieve", "report"]]
    reason: str | None = None


@dataclass(frozen=True)
class EntryPlanDecision:
    source: Literal["explicit_intent", "llm", "rule"]
    use_subagents: bool
    initial_actions: list[str]
    reason: str | None = None


class RuntimePlanner:
    def __init__(self, llm_provider: OpenAIProvider | None = None) -> None:
        self.llm_provider = llm_provider

    def build_initial_plan(self, *, intent: str, request: RequestEnvelope) -> ActionPlan:
        if intent in {"stock_check", "stock_screen", "sector_research"}:
            decision = self._decide_research_entry(intent=intent, request=request)
            actions = self._build_entry_actions(intent=intent, action_ids=decision.initial_actions)
            return ActionPlan(
                intent=intent,
                strategy="dynamic",
                actions=actions,
                notes=[f"{intent} uses dynamic supervisor"],
                entry_strategy=decision.source,
                entry_reason=decision.reason,
                subagent_required=decision.use_subagents,
                dispatch_trace={
                    "mode": "research",
                    "intent": intent,
                    "source": decision.source,
                    "reason": decision.reason,
                    "subagent_required": decision.use_subagents,
                    "initial_actions": decision.initial_actions,
                },
            )
        if intent == "distill_kol_logic":
            actions = [
                ActionSpec(node_id="collect", phase="collect", agent="collector", objective="Load source records."),
                ActionSpec(node_id="distill", phase="distill", agent="distiller", objective="Distill source content."),
                ActionSpec(node_id="critic", phase="review", agent="critic", objective="Critique distilled output."),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", objective="Retrieve distilled evidence."),
                ActionSpec(node_id="report", phase="report", agent="reporter", objective="Compose final distillation report."),
            ]
            return ActionPlan(intent=intent, strategy="static", actions=actions, entry_strategy="explicit_intent")
        if intent == "review_thesis":
            actions = [
                ActionSpec(node_id="collect", phase="collect", agent="collector", objective="Collect posterior review inputs."),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", objective="Retrieve review evidence."),
                ActionSpec(node_id="report", phase="report", agent="reporter", objective="Compose final review report."),
            ]
            return ActionPlan(
                intent=intent,
                strategy="dynamic",
                actions=actions,
                notes=["review_thesis uses dynamic supervisor"],
                entry_strategy="explicit_intent",
                subagent_required=True,
                dispatch_trace={
                    "mode": "research",
                    "intent": intent,
                    "source": "explicit_intent",
                    "reason": "review_thesis always uses research subagents",
                    "subagent_required": True,
                    "initial_actions": ["collect", "retrieve", "report"],
                },
            )
        raise ValueError(f"unsupported intent for planner: {intent}")

    def _decide_research_entry(self, *, intent: str, request: RequestEnvelope) -> EntryPlanDecision:
        if request.intent:
            return EntryPlanDecision(
                source="explicit_intent",
                use_subagents=True,
                initial_actions=self._default_initial_actions(intent),
                reason="explicit intent provided by caller",
            )

        query = request.user_query.strip()
        if self.llm_provider and self.llm_provider.available:
            try:
                payload = self.llm_provider.parse(
                    system_prompt=(
                        "You are the research entry planner for an A-share research terminal.\n"
                        "The broad intent is already fixed. Decide whether research subagents are still needed, and if they are, which initial actions should run first.\n"
                        "Allowed initial actions are collect, screen, retrieve, and report.\n"
                        "Use stock_screen -> collect,screen,... when the task is recommendation or watchlist building.\n"
                        "Use stock_check or sector_research -> collect,retrieve,report for normal evidence-backed research.\n"
                        "Only choose use_subagents=false when the answer could safely be produced directly without research collection."
                    ),
                    user_prompt=(
                        f"Intent: {intent}\n"
                        f"User query: {query}\n"
                        f"Current constraints: {request.constraints}\n"
                        "Return a compact plan decision."
                    ),
                    schema=EntryPlanPayload,
                    temperature=0.0,
                    reasoning_effort="low",
                )
                initial_actions = self._normalize_initial_actions(intent=intent, actions=payload.initial_actions)
                return EntryPlanDecision(
                    source="llm",
                    use_subagents=payload.use_subagents,
                    initial_actions=initial_actions,
                    reason=payload.reason or "llm_entry_plan",
                )
            except LLMProviderError:
                pass

        return EntryPlanDecision(
            source="rule",
            use_subagents=True,
            initial_actions=self._default_initial_actions(intent),
            reason="rule_fallback_entry_plan",
        )

    def _default_initial_actions(self, intent: str) -> list[str]:
        if intent == "stock_screen":
            return ["collect", "screen", "retrieve", "report"]
        return ["collect", "retrieve", "report"]

    def _normalize_initial_actions(self, *, intent: str, actions: list[str]) -> list[str]:
        allowed = {"collect", "screen", "retrieve", "report"}
        normalized = [action for action in actions if action in allowed]
        if not normalized:
            return self._default_initial_actions(intent)
        if intent != "stock_screen":
            normalized = [action for action in normalized if action != "screen"]
        if "report" not in normalized:
            normalized.append("report")
        return normalized

    def _build_entry_actions(self, *, intent: str, action_ids: list[str]) -> list[ActionSpec]:
        action_specs: list[ActionSpec] = []
        for action_id in action_ids:
            if action_id == "collect":
                objective = {
                    "stock_check": "Collect stock snapshots.",
                    "stock_screen": "Build candidate universe and snapshots.",
                    "sector_research": "Collect sector snapshots.",
                }[intent]
                action_specs.append(ActionSpec(node_id="collect", phase="collect", agent="collector", objective=objective))
            elif action_id == "screen" and intent == "stock_screen":
                action_specs.append(ActionSpec(node_id="screen", phase="screen", agent="screener", objective="Screen and rank stock candidates."))
            elif action_id == "retrieve":
                objective = {
                    "stock_check": "Retrieve relevant evidence.",
                    "stock_screen": "Retrieve screening evidence.",
                    "sector_research": "Retrieve relevant evidence.",
                }[intent]
                action_specs.append(ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", objective=objective))
            elif action_id == "report":
                objective = {
                    "stock_check": "Compose final report.",
                    "stock_screen": "Compose watchlist-style screening report.",
                    "sector_research": "Compose final report.",
                }[intent]
                action_specs.append(ActionSpec(node_id="report", phase="report", agent="reporter", objective=objective))
        return action_specs
