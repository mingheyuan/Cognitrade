from __future__ import annotations

from dataclasses import dataclass

from research_system.services.routing.route_table import IntentRouteRule


@dataclass(frozen=True)
class RouteMatchResult:
    intent: str
    source: str
    trace: dict


class RouteMatcherResultSupport:
    def route_table_result(
        self,
        *,
        rule: IntentRouteRule,
        table_version: str,
        runtime: dict,
    ) -> RouteMatchResult:
        return RouteMatchResult(
            intent=rule.intent,
            source="route_table",
            trace={
                "source": "route_table",
                "intent": rule.intent,
                "route_table_version": table_version,
                "rule_id": rule.rule_id,
                "match_type": rule.match_type,
                "target": rule.target,
                "priority": rule.priority,
                "runtime": runtime,
            },
        )

    def semantic_result(
        self,
        *,
        rule: IntentRouteRule,
        table_version: str,
        runtime: dict,
        score: float,
        threshold: float,
    ) -> RouteMatchResult:
        return RouteMatchResult(
            intent=rule.intent,
            source="route_table_semantic",
            trace={
                "source": "route_table_semantic",
                "intent": rule.intent,
                "route_table_version": table_version,
                "rule_id": rule.rule_id,
                "match_type": rule.match_type,
                "target": rule.target,
                "priority": rule.priority,
                "semantic_score": round(score, 4),
                "semantic_threshold": threshold,
                "runtime": runtime,
            },
        )


__all__ = ["RouteMatchResult", "RouteMatcherResultSupport"]
