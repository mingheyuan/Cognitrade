from __future__ import annotations

from dataclasses import dataclass

from research_system.services.planning_context_methodology import (
    methodology_from_benchmark_scope,
    methodology_preference_triplet,
    normalize_planning_context_hints,
)
from research_system.services.planning_context_signals import (
    has_cashflow_focus,
    has_same_basis_focus,
    has_valuation_focus,
)


@dataclass(frozen=True)
class PlanningContextView:
    normalized_hints: dict[str, str]
    query_text: str
    combined_text: str
    recommended_next_step: str
    open_question: str
    previous_summary: str
    thread_topic: str
    methodology_benchmark_scope: str
    methodology_hint_methodology: str


def build_planning_context_view(
    *,
    query: str | None = None,
    planning_context_hints: dict[str, str] | None = None,
    extra_texts: list[str] | None = None,
) -> PlanningContextView:
    normalized_hints = normalize_planning_context_hints(planning_context_hints)
    query_text = str(query or "").strip().lower()
    recommended_next_step = str(normalized_hints.get("recommended_next_step") or "").strip().lower()
    open_question = str(normalized_hints.get("open_question") or "").strip().lower()
    previous_summary = str(normalized_hints.get("previous_summary") or "").strip().lower()
    thread_topic = str(normalized_hints.get("thread_topic") or "").strip().lower()
    methodology_benchmark_scope = str(normalized_hints.get("methodology_benchmark_scope") or "").strip().lower()
    methodology_hint_methodology = str(methodology_from_benchmark_scope(methodology_benchmark_scope) or "").strip().lower()
    methodology_hint_text = " ".join(
        part for part in [methodology_benchmark_scope, methodology_hint_methodology.replace("_", " ")] if part
    )
    combined_text = " ".join(
        part
        for part in [
            query_text,
            *[
                str(text or "").strip().lower()
                for text in list(extra_texts or [])
                if str(text or "").strip()
            ],
            recommended_next_step,
            open_question,
            previous_summary,
            thread_topic,
            methodology_hint_text,
        ]
        if part
    )
    return PlanningContextView(
        normalized_hints=normalized_hints,
        query_text=query_text,
        combined_text=combined_text,
        recommended_next_step=recommended_next_step,
        open_question=open_question,
        previous_summary=previous_summary,
        thread_topic=thread_topic,
        methodology_benchmark_scope=methodology_benchmark_scope,
        methodology_hint_methodology=methodology_hint_methodology,
    )


def methodology_preference_order(
    *,
    query: str | None = None,
    planning_context_hints: dict[str, str] | None = None,
) -> list[str]:
    context = build_planning_context_view(
        query=query,
        planning_context_hints=planning_context_hints,
    )
    preferences: list[str] = []
    if has_cashflow_focus(context.query_text):
        preferences.extend(methodology_preference_triplet("cashflow_resilience"))
    elif has_same_basis_focus(context.combined_text):
        preferences.extend(methodology_preference_triplet("comps"))
    elif has_valuation_focus(context.combined_text):
        preferences.extend(methodology_preference_triplet("dcf"))
    else:
        if has_cashflow_focus(context.recommended_next_step):
            preferences.extend(methodology_preference_triplet("cashflow_resilience"))
        elif has_valuation_focus(context.recommended_next_step):
            preferences.extend(methodology_preference_triplet("dcf"))
        elif context.methodology_hint_methodology:
            preferences.extend(methodology_preference_triplet(context.methodology_hint_methodology))
    deduped: list[str] = []
    for item in preferences:
        if item not in deduped:
            deduped.append(item)
    return deduped

