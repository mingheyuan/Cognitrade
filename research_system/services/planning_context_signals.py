from __future__ import annotations


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

