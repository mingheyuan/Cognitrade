from __future__ import annotations

from research_system.services.planning_context import normalize_planning_context_hints
from research_system.services.review_focus_support import build_review_focus_notes, resolve_review_focus


class ScreenReviewerStrategySupport:
    def normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def derive_review_strategy(
        self,
        *,
        candidates: list[dict],
        scorecard: dict | None,
        planning_context_hints: dict[str, str],
    ) -> dict[str, object]:
        strategy: dict[str, object] = {
            "focus_area": None,
            "preferred_methodology": None,
            "notes": [],
        }
        candidate_text = " ".join(
            str(candidate.get("main_risk") or "").strip()
            for candidate in candidates
            if str(candidate.get("main_risk") or "").strip()
        )
        scorecard_summary = str((scorecard or {}).get("summary") or "").strip()
        focus = resolve_review_focus(
            query=scorecard_summary,
            planning_context_hints=planning_context_hints,
            extra_texts=[candidate_text],
            include_policy_focus=False,
            include_momentum_focus=True,
        )
        strategy.update(
            focus_area=focus["focus_area"],
            preferred_methodology=focus["preferred_methodology"],
        )
        strategy["notes"] = build_review_focus_notes(
            note_prefix="screen_review",
            focus_area=strategy.get("focus_area"),
            preferred_methodology=strategy.get("preferred_methodology"),
            focus_source=focus.get("focus_source"),
        )
        return strategy
