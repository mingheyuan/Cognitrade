from __future__ import annotations

from research_system.services.routing.tables import IntentRouteRule


class RouteMatcherSemanticSupport:
    def __init__(self, *, text_support) -> None:
        self.text_support = text_support

    def semantic_score(
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
            keyword_tokens = self.text_support.tokenize_for_overlap(" ".join(keyword.lower() for keyword in keywords))
            haystack = lowered
        else:
            keyword_tokens = self.text_support.tokenize_for_overlap(" ".join(keywords))
            haystack = query
        keyword_vector = self.text_support.token_vector(keyword_tokens)
        direct_hits = sum(1 for keyword in keywords if keyword.lower() in haystack.lower())
        overlap = len(query_tokens & keyword_tokens)
        denominator = max(1, min(max(1, len(query_tokens)), max(1, len(keyword_tokens))))
        overlap_score = overlap / denominator
        direct_bonus = min(1.0, direct_hits / max(1, len(keywords)))
        semantic_similarity = self.text_support.cosine_similarity(query_vector, keyword_vector)
        return max(overlap_score, direct_bonus, semantic_similarity)


__all__ = ["RouteMatcherSemanticSupport"]
