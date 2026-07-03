from __future__ import annotations

from research_system.services.planning_context_signals import (
    has_cashflow_focus,
    has_same_basis_focus,
    has_valuation_focus,
)


def normalize_planning_context_hints(raw_hints) -> dict[str, str]:
    if not isinstance(raw_hints, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw_hints.items():
        key_text = str(key or "").strip()
        value_text = str(value or "").strip()
        if key_text and value_text:
            normalized[key_text] = value_text
    return normalized


def methodology_from_benchmark_scope(benchmark_scope: str | None) -> str | None:
    normalized = str(benchmark_scope or "").strip().lower()
    if not normalized:
        return None
    if any(
        token in normalized
        for token in ("cashflow", "free_cash_flow", "free cash flow", "defensive", "compounder", "leverage", "resilience")
    ):
        return "cashflow_resilience"
    if any(token in normalized for token in ("dcf", "intrinsic", "valuation")):
        return "dcf"
    if any(token in normalized for token in ("peer", "same_basis", "same-basis", "comps", "relative", "event_driven", "event-driven")):
        return "comps"
    return None


def methodology_preference_from_hints(planning_context_hints: dict[str, str] | None) -> str | None:
    normalized_hints = normalize_planning_context_hints(planning_context_hints)
    return methodology_from_benchmark_scope(normalized_hints.get("methodology_benchmark_scope"))


def methodology_preference_triplet(methodology_type: str | None) -> list[str]:
    normalized = str(methodology_type or "").strip()
    if normalized == "cashflow_resilience":
        return ["cashflow_resilience", "dcf", "comps"]
    if normalized == "dcf":
        return ["dcf", "comps", "cashflow_resilience"]
    if normalized == "comps":
        return ["comps", "dcf", "cashflow_resilience"]
    return []


def normalize_methodology_preference(methodology_type: str | None) -> str | None:
    normalized = str(methodology_type or "").strip().lower()
    if normalized in {"cashflow_resilience", "dcf", "comps"}:
        return normalized
    return methodology_from_benchmark_scope(normalized)


def normalize_methodology_preferences(values) -> list[str]:
    normalized: list[str] = []
    for value in list(values or []):
        candidate = normalize_methodology_preference(value)
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def combine_focus_text(*parts: str | None) -> str:
    return " ".join(
        part
        for part in [
            str(value or "").strip().lower()
            for value in parts
            if str(value or "").strip()
        ]
        if part
    )


def derive_methodology_focus(
    *,
    cashflow_text: str | None = None,
    comparison_text: str | None = None,
    planning_context_hints: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    normalized_cashflow_text = str(cashflow_text or "").strip().lower()
    normalized_comparison_text = str(comparison_text or "").strip().lower()
    methodology_hint_preference = methodology_preference_from_hints(planning_context_hints)
    if has_cashflow_focus(normalized_cashflow_text):
        return "cashflow_resilience", "planning_context"
    if has_same_basis_focus(normalized_comparison_text):
        return "comps", "planning_context"
    if has_valuation_focus(normalized_comparison_text):
        return "dcf", "planning_context"
    if methodology_hint_preference:
        return methodology_hint_preference, "methodology_memory"
    return None, None

