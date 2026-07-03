from __future__ import annotations

from research_system.services.routing.router.task_router_models import RoutingDecision


class TaskRouterRuleSupport:
    def rule_based_identify(self, *, query: str, route_matcher) -> RoutingDecision | None:
        match = route_matcher.match(query)
        if match is None:
            return None
        return RoutingDecision(
            intent=match.intent,
            missing_fields=[],
            clarification=None,
            trace=match.trace,
        )
