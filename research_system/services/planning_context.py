from __future__ import annotations

from dataclasses import dataclass


CASHFLOW_TOKENS = (
    "cashflow",
    "resilien",
    "debt",
    "现金流",
    "韧性",
    "债务",
)
SAME_BASIS_TOKENS = (
    "same-basis",
    "same basis",
    "同口径",
    "同一口径",
    "peer",
    "comps",
    "comparison",
    "compare",
    "对比",
    "相对估值",
)
VALUATION_TOKENS = (
    "valuation",
    "估值",
    "dcf",
    "discounted cash flow",
)
POLICY_TOKENS = (
    "policy",
    "regulatory",
    "catalyst",
    "政策",
    "监管",
    "催化",
)
MOMENTUM_TOKENS = (
    "sentiment",
    "momentum",
    "short-term",
    "short term",
    "情绪",
    "动量",
    "短线",
)
LONG_TERM_TOKENS = (
    "long-term",
    "long term",
    "长期",
)
MID_TERM_TOKENS = (
    "mid-term",
    "mid term",
    "中线",
    "中期",
)
NOTICE_TOKENS = (
    "notice",
    "公告",
    "filing",
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


def contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def has_cashflow_focus(text: str) -> bool:
    return contains_any(text, CASHFLOW_TOKENS)


def has_same_basis_focus(text: str) -> bool:
    return contains_any(text, SAME_BASIS_TOKENS)


def has_valuation_focus(text: str) -> bool:
    return contains_any(text, VALUATION_TOKENS)


def has_policy_focus(text: str) -> bool:
    return contains_any(text, POLICY_TOKENS)


def has_momentum_focus(text: str) -> bool:
    return contains_any(text, MOMENTUM_TOKENS)


def has_long_term_bias(text: str) -> bool:
    return contains_any(text, LONG_TERM_TOKENS)


def has_mid_term_bias(text: str) -> bool:
    return contains_any(text, MID_TERM_TOKENS)


def has_notice_focus(text: str) -> bool:
    return contains_any(text, NOTICE_TOKENS)


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
