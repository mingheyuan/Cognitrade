from __future__ import annotations

from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.runtime import BlackboardEntry


class RuntimeSupervisor:
    def decide(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        if plan.intent == "stock_check":
            return self._decide_stock_check(
                plan=plan,
                completed_actions=completed_actions,
                pending_actions=pending_actions,
                blackboard_entries=blackboard_entries,
            )
        if plan.intent == "sector_research":
            return self._decide_sector_research(
                plan=plan,
                completed_actions=completed_actions,
                pending_actions=pending_actions,
                blackboard_entries=blackboard_entries,
            )
        if plan.intent == "review_thesis":
            return self._decide_review_thesis(
                plan=plan,
                completed_actions=completed_actions,
                pending_actions=pending_actions,
                blackboard_entries=blackboard_entries,
            )
        if plan.intent != "stock_screen":
            return SupervisorDecision(
                decision_type="continue" if pending_actions else "stop",
                next_actions=pending_actions[:1],
                completed_action_ids=completed_actions,
            )

        pending_by_id = {action.node_id: action for action in pending_actions}
        source_records = [entry for entry in blackboard_entries if entry.kind == "source_record"]
        review_summary_present = any(
            str((entry.payload or {}).get("source_type") or "") == "review_summary"
            for entry in source_records
        )
        scorecard_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") == "screening_scorecard"
            ),
            None,
        )
        scorecard = ((scorecard_entry.payload or {}).get("scorecard") if scorecard_entry else None) or {}
        top_candidate_count = int(scorecard.get("top_candidate_count") or 0)
        watchlist_candidate_count = int(scorecard.get("watchlist_candidate_count") or 0)

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["collect"]],
                completed_action_ids=completed_actions,
                notes=["start with candidate collection"],
            )

        if "screen" not in completed_actions and "screen" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["screen"]],
                completed_action_ids=completed_actions,
                notes=["screen collected candidates before deciding follow-ups"],
            )

        if (
            "screen" in completed_actions
            and not review_summary_present
            and ("screen_review" not in completed_actions)
            and (top_candidate_count > 0 or watchlist_candidate_count > 0)
        ):
            review_action = pending_by_id.get("screen_review")
            if review_action is None:
                review_action = ActionSpec(
                    node_id="screen_review",
                    phase="review",
                    agent="screen_reviewer",
                    objective="Review screened candidates before retrieval/reporting.",
                    tags=["dynamic_inserted"],
                )
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[review_action],
                completed_action_ids=completed_actions,
                notes=["insert screen reviewer dynamically after screening"],
            )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["retrieve"]],
                completed_action_ids=completed_actions,
                notes=["retrieve evidence after screening/review"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["report"]],
                completed_action_ids=completed_actions,
                notes=["compose final report after retrieval"],
            )

        return SupervisorDecision(
            decision_type="stop",
            completed_action_ids=completed_actions,
            notes=["no further stock_screen actions required"],
        )

    def _decide_stock_check(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = {action.node_id: action for action in pending_actions}
        source_records = [entry for entry in blackboard_entries if entry.kind == "source_record"]
        request_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") == "user_query"
            ),
            None,
        )
        symbol = str(((request_entry.payload if request_entry else {}) or {}).get("symbol") or "")
        snapshot_types = {
            str((entry.payload or {}).get("snapshot_type") or "")
            for entry in blackboard_entries
            if entry.kind == "verified_snapshot"
        }
        missing_snapshot_types = [
            snapshot_type
            for snapshot_type in ("market", "fundamental", "notice")
            if snapshot_type not in snapshot_types
        ]
        request_query = str(((request_entry.payload if request_entry else {}) or {}).get("content") or symbol or "")

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["collect"]],
                completed_action_ids=completed_actions,
                notes=["collect baseline stock snapshots first"],
            )

        if "collect" in completed_actions and missing_snapshot_types:
            replenish_action = ActionSpec(
                node_id=f"collect_replenish_{'_'.join(missing_snapshot_types)}",
                phase="collect",
                agent="collector",
                objective="Replenish missing stock snapshots before retrieval.",
                inputs={
                    "query": request_query,
                    "symbol": symbol,
                    "snapshot_types": missing_snapshot_types,
                    "source_type": "stock_check_replenish",
                },
                tags=["dynamic_inserted", "replenish"],
            )
            if replenish_action.node_id not in completed_actions and replenish_action.node_id not in pending_by_id:
                return SupervisorDecision(
                    decision_type="continue",
                    next_actions=[replenish_action],
                    completed_action_ids=completed_actions,
                    notes=[f"replenish missing stock snapshots: {','.join(missing_snapshot_types)}"],
                )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["retrieve"]],
                completed_action_ids=completed_actions,
                notes=["retrieve stock evidence after collection is sufficient"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["report"]],
                completed_action_ids=completed_actions,
                notes=["compose final stock report after retrieval"],
            )

        return SupervisorDecision(
            decision_type="stop",
            completed_action_ids=completed_actions,
            notes=["no further stock_check actions required"],
        )

    def _decide_sector_research(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = {action.node_id: action for action in pending_actions}
        source_records = [entry for entry in blackboard_entries if entry.kind == "source_record"]
        request_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") in {"user_query", "sector_research_replenish"}
            ),
            None,
        )
        sector_snapshot_entry = next(
            (
                entry for entry in reversed(blackboard_entries)
                if entry.kind == "verified_snapshot" and str((entry.payload or {}).get("snapshot_type") or "") == "sector"
            ),
            None,
        )
        sector_snapshot = (sector_snapshot_entry.payload if sector_snapshot_entry else None) or {}
        facts = sector_snapshot.get("facts") or {}
        missing_sector_evidence = [
            evidence_name
            for evidence_name in ("policy_signals", "regulatory_signals", "benchmark", "relative_strength")
            if not facts.get(evidence_name)
        ]
        request_query = str(((request_entry.payload if request_entry else {}) or {}).get("content") or sector_snapshot.get("query") or "")

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["collect"]],
                completed_action_ids=completed_actions,
                notes=["collect sector snapshot first"],
            )

        if "collect" in completed_actions and missing_sector_evidence and request_query:
            replenish_action = ActionSpec(
                node_id=f"collect_replenish_sector_{'_'.join(missing_sector_evidence)}",
                phase="collect",
                agent="collector",
                objective="Replenish missing sector evidence before retrieval.",
                inputs={
                    "query": request_query,
                    "source_type": "sector_research_replenish",
                    "replenish_sector_snapshot": True,
                    "missing_evidence": missing_sector_evidence,
                },
                tags=["dynamic_inserted", "replenish"],
            )
            if replenish_action.node_id not in completed_actions and replenish_action.node_id not in pending_by_id:
                return SupervisorDecision(
                    decision_type="continue",
                    next_actions=[replenish_action],
                    completed_action_ids=completed_actions,
                    notes=[f"replenish missing sector evidence: {','.join(missing_sector_evidence)}"],
                )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["retrieve"]],
                completed_action_ids=completed_actions,
                notes=["retrieve sector evidence after collection"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["report"]],
                completed_action_ids=completed_actions,
                notes=["compose final sector report after retrieval"],
            )

        return SupervisorDecision(
            decision_type="stop",
            completed_action_ids=completed_actions,
            notes=["no further sector_research actions required"],
        )

    def _decide_review_thesis(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> SupervisorDecision:
        pending_by_id = {action.node_id: action for action in pending_actions}
        source_records = [entry for entry in blackboard_entries if entry.kind == "source_record"]
        review_request_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") == "review_request"
            ),
            None,
        )
        review_request = (review_request_entry.payload if review_request_entry else None) or {}
        review_summary_present = any(
            str((entry.payload or {}).get("source_type") or "") == "review_summary"
            for entry in source_records
        )
        review_target_run_id = str(review_request.get("review_target_run_id") or "")
        review_source_entries = list(review_request.get("review_source_entries") or [])
        has_symbol_snapshot = any(
            entry.kind == "verified_snapshot" and str((entry.payload or {}).get("snapshot_type") or "") in {"market", "fundamental", "notice"}
            for entry in blackboard_entries
        )
        has_sector_snapshot = any(
            entry.kind == "verified_snapshot" and str((entry.payload or {}).get("snapshot_type") or "") == "sector"
            for entry in blackboard_entries
        )
        needs_review = bool(review_target_run_id and review_source_entries and (has_symbol_snapshot or has_sector_snapshot))

        if "collect" not in completed_actions and "collect" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["collect"]],
                completed_action_ids=completed_actions,
                notes=["load prior run context and any available benchmark snapshots first"],
            )

        if (
            "collect" in completed_actions
            and not review_summary_present
            and ("review" not in completed_actions)
            and needs_review
        ):
            review_action = pending_by_id.get("review")
            if review_action is None:
                review_action = ActionSpec(
                    node_id="review",
                    phase="review",
                    agent="reviewer",
                    objective="Run posterior review after verifying source context and snapshots.",
                    tags=["dynamic_inserted"],
                )
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[review_action],
                completed_action_ids=completed_actions,
                notes=["insert posterior review dynamically once review target and snapshots are ready"],
            )

        if "retrieve" not in completed_actions and "retrieve" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["retrieve"]],
                completed_action_ids=completed_actions,
                notes=["retrieve review evidence after posterior review or minimal collection fallback"],
            )

        if "report" not in completed_actions and "report" in pending_by_id:
            return SupervisorDecision(
                decision_type="continue",
                next_actions=[pending_by_id["report"]],
                completed_action_ids=completed_actions,
                notes=["compose final posterior review report after retrieval"],
            )

        return SupervisorDecision(
            decision_type="stop",
            completed_action_ids=completed_actions,
            notes=["no further review_thesis actions required"],
        )
