from __future__ import annotations

from dataclasses import dataclass
import math

from research_system.services.route_table import IntentRouteRule, IntentRouteTableStore


def _tokenize_for_overlap(text: str) -> set[str]:
    lowered = text.lower()
    ascii_tokens = {
        token
        for token in "".join(char if char.isalnum() else " " for char in lowered).split()
        if len(token) >= 2
    }
    cjk_bigrams = {
        lowered[index : index + 2]
        for index in range(len(lowered) - 1)
        if any("\u4e00" <= char <= "\u9fff" for char in lowered[index : index + 2])
    }
    return ascii_tokens | cjk_bigrams


def _token_vector(tokens: set[str]) -> dict[str, float]:
    if not tokens:
        return {}
    weight = 1.0 / math.sqrt(len(tokens))
    return {token: weight for token in tokens}


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    overlap = set(left) & set(right)
    if not overlap:
        return 0.0
    numerator = sum(left[token] * right[token] for token in overlap)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


@dataclass(frozen=True)
class RouteMatchResult:
    intent: str
    source: str
    trace: dict


class RouteMatcher:
    def __init__(self, route_table_store: IntentRouteTableStore) -> None:
        self.route_table_store = route_table_store

    def match(self, query: str) -> RouteMatchResult | None:
        lowered = query.lower()
        table = self.route_table_store.load_table()
        runtime = self.route_table_store.describe_runtime_state()
        semantic_candidates: list[tuple[float, IntentRouteRule]] = []
        query_tokens = _tokenize_for_overlap(query)
        query_vector = _token_vector(query_tokens)
        for rule in table.ordered_rules():
            if rule.match_type == "semantic":
                score = self._semantic_score(
                    rule=rule,
                    query=query,
                    lowered=lowered,
                    query_tokens=query_tokens,
                    query_vector=query_vector,
                )
                threshold = rule.semantic_threshold if rule.semantic_threshold is not None else 0.55
                if score >= threshold:
                    semantic_candidates.append((score, rule))
                continue
            if rule.matches(query=query, lowered=lowered):
                return RouteMatchResult(
                    intent=rule.intent,
                    source="route_table",
                    trace={
                        "source": "route_table",
                        "intent": rule.intent,
                        "route_table_version": table.version,
                        "rule_id": rule.rule_id,
                        "match_type": rule.match_type,
                        "target": rule.target,
                        "priority": rule.priority,
                        "runtime": runtime,
                    },
                )

        if semantic_candidates:
            score, rule = min(
                semantic_candidates,
                key=lambda item: (
                    item[1].priority,
                    -item[0],
                    item[1].rule_id or "",
                ),
            )
            return RouteMatchResult(
                intent=rule.intent,
                source="route_table_semantic",
                trace={
                    "source": "route_table_semantic",
                    "intent": rule.intent,
                    "route_table_version": table.version,
                    "rule_id": rule.rule_id,
                    "match_type": rule.match_type,
                    "target": rule.target,
                    "priority": rule.priority,
                    "semantic_score": round(score, 4),
                    "semantic_threshold": rule.semantic_threshold if rule.semantic_threshold is not None else 0.55,
                    "runtime": runtime,
                },
            )
        return None

    def _semantic_score(
        self,
        *,
        rule: IntentRouteRule,
        query: str,
        lowered: str,
        query_tokens: set[str],
        query_vector: dict[str, float],
    ) -> float:
        if not rule.enabled or rule.blocked_by_negative_terms(query=query, lowered=lowered):
            return 0.0
        keywords = [keyword for keyword in rule.keywords if keyword]
        if not keywords:
            return 0.0
        if rule.target == "lowered":
            keyword_tokens = _tokenize_for_overlap(" ".join(keyword.lower() for keyword in keywords))
            haystack = lowered
        else:
            keyword_tokens = _tokenize_for_overlap(" ".join(keywords))
            haystack = query
        keyword_vector = _token_vector(keyword_tokens)
        direct_hits = sum(1 for keyword in keywords if keyword.lower() in haystack.lower())
        overlap = len(query_tokens & keyword_tokens)
        denominator = max(1, min(max(1, len(query_tokens)), max(1, len(keyword_tokens))))
        overlap_score = overlap / denominator
        direct_bonus = min(1.0, direct_hits / max(1, len(keywords)))
        semantic_similarity = _cosine_similarity(query_vector, keyword_vector)
        return max(overlap_score, direct_bonus, semantic_similarity)
