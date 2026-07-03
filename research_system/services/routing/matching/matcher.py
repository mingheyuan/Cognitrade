from __future__ import annotations

from research_system.services.routing.matching.route_matcher_result_support import RouteMatchResult, RouteMatcherResultSupport
from research_system.services.routing.matching.route_matcher_semantic_support import RouteMatcherSemanticSupport
from research_system.services.routing.matching.route_matcher_text_support import RouteMatcherTextSupport
from research_system.services.routing.route_table import IntentRouteRule, IntentRouteTableStore


class RouteMatcher:
    def __init__(self, route_table_store: IntentRouteTableStore) -> None:
        self.route_table_store = route_table_store
        self.text_support = RouteMatcherTextSupport()
        self.semantic_support = RouteMatcherSemanticSupport(text_support=self.text_support)
        self.result_support = RouteMatcherResultSupport()

    def match(self, query: str) -> RouteMatchResult | None:
        lowered = query.lower()
        table = self.route_table_store.load_table()
        runtime = self.route_table_store.describe_runtime_state()
        semantic_candidates: list[tuple[float, IntentRouteRule]] = []
        query_tokens = self.text_support.tokenize_for_overlap(query)
        query_vector = self.text_support.token_vector(query_tokens)
        for rule in table.ordered_rules():
            if rule.match_type == "semantic":
                score = self.semantic_support.semantic_score(
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
                return self.result_support.route_table_result(
                    rule=rule,
                    table_version=table.version,
                    runtime=runtime,
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
            return self.result_support.semantic_result(
                rule=rule,
                table_version=table.version,
                runtime=runtime,
                score=score,
                threshold=rule.semantic_threshold if rule.semantic_threshold is not None else 0.55,
            )
        return None
