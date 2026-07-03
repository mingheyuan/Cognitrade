from __future__ import annotations

from typing import Any

from research_system.services.methodologies.planning_context import (
    build_planning_context_view,
    combine_focus_text,
    derive_methodology_focus,
    has_momentum_focus,
    has_policy_focus,
)


def resolve_review_focus(
    *,
    query: str,
    planning_context_hints: dict[str, str],
    extra_texts: list[str] | None = None,
    include_policy_focus: bool,
    include_momentum_focus: bool,
) -> dict[str, Any]:
    focus: dict[str, Any] = {
        "focus_area": None,
        "preferred_methodology": None,
        "focus_source": None,
        "requires_market_snapshot": False,
        "requires_fundamental_snapshot": False,
        "requires_notice_or_policy_signal": False,
    }
    if not planning_context_hints:
        return focus

    normalized_extra_texts = [
        str(item).strip()
        for item in extra_texts or []
        if str(item).strip()
    ]
    context = build_planning_context_view(
        query=query,
        planning_context_hints=planning_context_hints,
        extra_texts=normalized_extra_texts,
    )
    lowered = combine_focus_text(
        context.query_text,
        *normalized_extra_texts,
        context.recommended_next_step,
        context.open_question,
        context.previous_summary,
        context.thread_topic,
    )
    preferred_methodology, focus_source = derive_methodology_focus(
        cashflow_text=lowered,
        comparison_text=lowered,
        planning_context_hints=planning_context_hints,
    )
    if preferred_methodology == "cashflow_resilience":
        focus["preferred_methodology"] = preferred_methodology
        focus["focus_area"] = "cashflow_resilience"
        focus["requires_fundamental_snapshot"] = True
    elif preferred_methodology == "dcf":
        focus["preferred_methodology"] = preferred_methodology
        focus["focus_area"] = "valuation"
        focus["requires_fundamental_snapshot"] = True
    elif preferred_methodology == "comps":
        focus["preferred_methodology"] = preferred_methodology
        focus["focus_area"] = "same_basis_compare"

    if include_policy_focus and has_policy_focus(lowered):
        focus["requires_notice_or_policy_signal"] = True
        if focus["focus_area"] is None:
            focus["focus_area"] = "catalyst_validation"
    if include_momentum_focus and has_momentum_focus(lowered):
        focus["requires_market_snapshot"] = True
        if focus["focus_area"] is None:
            focus["focus_area"] = "momentum_validation"

    focus["focus_source"] = focus_source
    return focus


def build_review_focus_notes(
    *,
    note_prefix: str,
    focus_area: str | None,
    preferred_methodology: str | None,
    focus_source: str | None,
) -> list[str]:
    notes: list[str] = []
    normalized_focus_area = str(focus_area or "").strip()
    normalized_methodology = str(preferred_methodology or "").strip()
    normalized_source = str(focus_source or "").strip()
    if normalized_focus_area:
        notes.append(f"{note_prefix}_focus={normalized_focus_area}")
    if normalized_methodology:
        notes.append(f"{note_prefix}_preferred_methodology={normalized_methodology}")
    if normalized_source:
        notes.append(f"{note_prefix}_focus_source={normalized_source}")
    return notes
