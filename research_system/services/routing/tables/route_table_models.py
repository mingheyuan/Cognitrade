from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RouteIntent = Literal[
    "stock_check",
    "stock_compare",
    "stock_screen",
    "sector_research",
    "distill_kol_logic",
    "review_thesis",
    "methodology_evolution",
]
RouteMatchType = Literal["contains_any", "regex", "semantic"]
RouteTarget = Literal["query", "lowered"]


class IntentRouteRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str | None = None
    intent: RouteIntent
    match_type: RouteMatchType
    priority: int = 100
    target: RouteTarget = "query"
    enabled: bool = True
    pattern: str | None = None
    keywords: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    semantic_threshold: float | None = None

    def haystack(self, *, query: str, lowered: str) -> str:
        return query if self.target == "query" else lowered

    def blocked_by_negative_terms(self, *, query: str, lowered: str) -> bool:
        haystack = self.haystack(query=query, lowered=lowered)
        if self.target == "lowered":
            return any(term.lower() in haystack for term in self.negative_terms if term)
        return any(term in haystack for term in self.negative_terms if term)

    def matches(self, *, query: str, lowered: str) -> bool:
        if not self.enabled:
            return False

        if self.blocked_by_negative_terms(query=query, lowered=lowered):
            return False

        haystack = self.haystack(query=query, lowered=lowered)
        if self.match_type == "regex":
            if not self.pattern:
                return False
            try:
                return re.search(self.pattern, haystack) is not None
            except re.error:
                return False

        if self.match_type == "semantic":
            return False

        if not self.keywords:
            return False
        if self.target == "lowered":
            return any(keyword.lower() in haystack for keyword in self.keywords if keyword)
        return any(keyword in haystack for keyword in self.keywords if keyword)


class IntentRouteTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "default-v1"
    rules: list[IntentRouteRule] = Field(default_factory=list)

    def ordered_rules(self) -> list[IntentRouteRule]:
        return sorted((rule for rule in self.rules if rule.enabled), key=lambda rule: rule.priority)
