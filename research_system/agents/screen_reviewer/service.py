from __future__ import annotations

from research_system.agents.screen_reviewer.screen_reviewer_finding_support import ScreenReviewerFindingSupport
from research_system.agents.screen_reviewer.screen_reviewer_strategy_support import ScreenReviewerStrategySupport
from research_system.agents.screen_reviewer.screen_reviewer_summary_support import ScreenReviewerSummarySupport


class ScreenReviewerService:
    agent_name = "screen_reviewer"

    def __init__(self) -> None:
        self.strategy_support = ScreenReviewerStrategySupport()
        self.finding_support = ScreenReviewerFindingSupport()
        self.summary_support = ScreenReviewerSummarySupport()

    def run(
        self,
        *,
        source_run_id: str,
        candidates: list[dict],
        scorecard: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        findings: list[dict] = []
        notes: list[str] = []
        normalized_planning_hints = self._normalize_planning_context_hints(planning_context_hints)
        review_strategy = self._derive_review_strategy(
            candidates=candidates,
            scorecard=scorecard,
            planning_context_hints=normalized_planning_hints,
        )
        notes.extend(list(review_strategy.get("notes") or []))

        for candidate in candidates:
            findings.append(
                self.finding_support.build_finding(
                    source_run_id=source_run_id,
                    candidate=candidate,
                )
            )

        return {
            "summary": self.summary_support.build_review_summary(
                candidates=candidates,
                findings=findings,
                notes=notes,
                scorecard=scorecard,
            ),
            "findings": findings,
        }

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return self.strategy_support.normalize_planning_context_hints(raw_hints)

    def _derive_review_strategy(
        self,
        *,
        candidates: list[dict],
        scorecard: dict | None,
        planning_context_hints: dict[str, str],
    ) -> dict[str, object]:
        return self.strategy_support.derive_review_strategy(
            candidates=candidates,
            scorecard=scorecard,
            planning_context_hints=planning_context_hints,
        )

    def _to_float(self, value):
        return self.finding_support.to_float(value)
