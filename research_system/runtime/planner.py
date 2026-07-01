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
        context_hints = self._extract_context_hints(request)
        if intent in {"stock_check", "stock_compare", "stock_screen", "sector_research"}:
            decision = self._decide_research_entry(intent=intent, request=request)
            actions = self._build_entry_actions(
                intent=intent,
                action_ids=decision.initial_actions,
                context_hints=context_hints,
            )
            return ActionPlan(
                intent=intent,
                strategy="dynamic",
                actions=actions,
                notes=[f"{intent} uses dynamic supervisor"],
                entry_strategy=decision.source,
                entry_reason=self._merge_entry_reason(decision.reason, context_hints),
                subagent_required=decision.use_subagents,
                dispatch_trace={
                    "mode": "research",
                    "intent": intent,
                    "source": decision.source,
                    "reason": decision.reason,
                    "context_hints": context_hints,
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
            planning_context_hints = {
                str(key): str(value)
                for key, value in context_hints.items()
                if str(key).strip() and str(value).strip()
            }
            actions = [
                ActionSpec(
                    node_id="collect",
                    phase="collect",
                    agent="collector",
                    objective="Collect posterior review inputs.",
                    inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                ),
                ActionSpec(
                    node_id="retrieve",
                    phase="retrieve",
                    agent="retriever",
                    objective="Retrieve review evidence.",
                    inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                ),
                ActionSpec(
                    node_id="report",
                    phase="report",
                    agent="reporter",
                    objective="Compose final review report.",
                    inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                ),
            ]
            return ActionPlan(
                intent=intent,
                strategy="dynamic",
                actions=actions,
                notes=["review_thesis uses dynamic supervisor"],
                entry_strategy="explicit_intent",
                entry_reason=self._merge_entry_reason("review_thesis always uses research subagents", context_hints),
                subagent_required=True,
                dispatch_trace={
                    "mode": "research",
                    "intent": intent,
                    "source": "explicit_intent",
                    "reason": "review_thesis always uses research subagents",
                    "context_hints": context_hints,
                    "subagent_required": True,
                    "initial_actions": ["collect", "retrieve", "report"],
                },
            )
        if intent == "methodology_evolution":
            actions = [
                ActionSpec(node_id="collect", phase="collect", agent="collector", objective="Load methodology evolution inputs."),
                ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", objective="Retrieve methodology evolution evidence."),
                ActionSpec(node_id="report", phase="report", agent="reporter", objective="Compose methodology evolution report."),
            ]
            return ActionPlan(
                intent=intent,
                strategy="static",
                actions=actions,
                entry_strategy="explicit_intent" if request.intent else "rule",
                entry_reason=self._merge_entry_reason("methodology_evolution uses internal evolution services", context_hints),
                subagent_required=True,
                dispatch_trace={
                    "mode": "research",
                    "intent": intent,
                    "source": "explicit_intent" if request.intent else "rule",
                    "reason": "methodology_evolution uses internal evolution services",
                    "context_hints": context_hints,
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
                        "Use stock_compare -> collect,retrieve,report for multi-stock comparison requests.\n"
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

    def _extract_context_hints(self, request: RequestEnvelope) -> dict[str, str]:
        hints: dict[str, str] = {}
        for item in request.conversation_context:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            if item_type == "chat_thread_profile":
                topic_title = str(item.get("topic_title") or "").strip()
                topic_intent = str(item.get("topic_intent") or "").strip()
                last_result_summary = str(item.get("last_result_summary") or "").strip()
                recommended_next_step = str(item.get("recommended_next_step") or "").strip()
                open_question = str(item.get("open_question") or "").strip()
                thread_status = str(item.get("thread_status") or "").strip()
                if topic_title:
                    hints["thread_topic"] = topic_title
                if topic_intent:
                    hints["thread_intent"] = topic_intent
                if last_result_summary:
                    hints["previous_summary"] = last_result_summary
                if recommended_next_step:
                    hints["recommended_next_step"] = recommended_next_step
                if open_question:
                    hints["open_question"] = open_question
                if thread_status:
                    hints["thread_status"] = thread_status
                continue
            if item_type == "relevant_thread_profile":
                topic_title = str(item.get("topic_title") or "").strip()
                topic_intent = str(item.get("topic_intent") or "").strip()
                previous_summary = str(item.get("last_result_summary") or "").strip()
                recommended_next_step = str(item.get("recommended_next_step") or "").strip()
                open_question = str(item.get("open_question") or "").strip()
                thread_status = str(item.get("thread_status") or "").strip()
                if topic_title and "thread_topic" not in hints:
                    hints["thread_topic"] = topic_title
                if topic_intent and "thread_intent" not in hints:
                    hints["thread_intent"] = topic_intent
                if previous_summary and "previous_summary" not in hints:
                    hints["previous_summary"] = previous_summary
                if recommended_next_step and "recommended_next_step" not in hints:
                    hints["recommended_next_step"] = recommended_next_step
                if open_question and "open_question" not in hints:
                    hints["open_question"] = open_question
                if thread_status and "thread_status" not in hints:
                    hints["thread_status"] = thread_status
                continue
            if item_type == "relevant_methodology_memory":
                benchmark_scope = str(item.get("benchmark_scope") or "").strip()
                memory_type = str(item.get("memory_type") or "").strip()
                match_score = str(item.get("match_score") or "").strip()
                if benchmark_scope and "methodology_benchmark_scope" not in hints:
                    hints["methodology_benchmark_scope"] = benchmark_scope
                if memory_type and "methodology_memory_type" not in hints:
                    hints["methodology_memory_type"] = memory_type
                if match_score and "methodology_memory_match_score" not in hints:
                    hints["methodology_memory_match_score"] = match_score
        return hints

    def _merge_entry_reason(self, base_reason: str | None, context_hints: dict[str, str]) -> str | None:
        base = str(base_reason or "").strip()
        if not context_hints:
            return base or None
        fragments: list[str] = [base] if base else []
        if context_hints.get("recommended_next_step"):
            fragments.append(f"thread_next_step={context_hints['recommended_next_step']}")
        if context_hints.get("open_question"):
            fragments.append(f"thread_open_question={context_hints['open_question']}")
        if context_hints.get("thread_topic"):
            fragments.append(f"thread_topic={context_hints['thread_topic']}")
        return " | ".join(fragments) if fragments else None

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

    def _build_entry_actions(
        self,
        *,
        intent: str,
        action_ids: list[str],
        context_hints: dict[str, str] | None = None,
    ) -> list[ActionSpec]:
        action_specs: list[ActionSpec] = []
        planning_context_hints = {
            str(key): str(value)
            for key, value in (context_hints or {}).items()
            if str(key).strip() and str(value).strip()
        }
        for action_id in action_ids:
            if action_id == "collect":
                objective = {
                    "stock_check": "Collect stock snapshots.",
                    "stock_compare": "Collect comparison stock snapshots.",
                    "stock_screen": "Build candidate universe and snapshots.",
                    "sector_research": "Collect sector snapshots.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="collect",
                        phase="collect",
                        agent="collector",
                        objective=objective,
                        inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                    )
                )
            elif action_id == "screen" and intent == "stock_screen":
                action_specs.append(
                    ActionSpec(
                        node_id="screen",
                        phase="screen",
                        agent="screener",
                        objective="Screen and rank stock candidates.",
                        inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                    )
                )
            elif action_id == "retrieve":
                objective = {
                    "stock_check": "Retrieve relevant evidence.",
                    "stock_compare": "Retrieve comparison evidence.",
                    "stock_screen": "Retrieve screening evidence.",
                    "sector_research": "Retrieve relevant evidence.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="retrieve",
                        phase="retrieve",
                        agent="retriever",
                        objective=objective,
                        inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                    )
                )
            elif action_id == "report":
                objective = {
                    "stock_check": "Compose final report.",
                    "stock_compare": "Compose comparison report.",
                    "stock_screen": "Compose watchlist-style screening report.",
                    "sector_research": "Compose final report.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="report",
                        phase="report",
                        agent="reporter",
                        objective=objective,
                        inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                    )
                )
        return action_specs
