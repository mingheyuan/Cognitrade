from __future__ import annotations

from datetime import date
from typing import Any

from research_system.schemas.review import ClaimVerdictBreakdown, PosteriorReviewSummary, ReviewFinding
from research_system.services.planning_context import (
    build_planning_context_view,
    combine_focus_text,
    derive_methodology_focus,
    has_cashflow_focus,
    has_momentum_focus,
    has_policy_focus,
    normalize_planning_context_hints,
)


class ReviewerService:
    agent_name = "reviewer"

    def __init__(self, review_policy: dict | None = None) -> None:
        self.review_policy = review_policy or {}

    def run(
        self,
        *,
        source_run_id: str,
        query: str,
        source_entries: list[dict],
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None = None,
        latest_fundamental_snapshot: dict | None = None,
        latest_notice_snapshot: dict | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict:
        normalized_planning_hints = self._normalize_planning_context_hints(planning_context_hints)
        review_strategy = self._derive_review_strategy(
            query=query,
            planning_context_hints=normalized_planning_hints,
        )
        claim_outputs = self._extract_review_claims(source_entries=source_entries)
        candidates = claim_outputs["candidates"]
        claim_type_counts = claim_outputs["claim_type_counts"]
        findings: list[ReviewFinding] = []
        notes: list[str] = list(review_strategy.get("notes") or [])

        if not candidates:
            notes.append("no_reviewable_claims_detected")

        if latest_sector_snapshot is None:
            notes.append("missing_sector_snapshot")
            summary_outputs = self._build_review_summary(
                review_required=True,
                findings=[],
                candidates=candidates,
                notes=notes,
                claim_type_counts=claim_type_counts,
            )
            return {
                "summary": summary_outputs["summary"],
                "findings": [],
            }

        facts = latest_sector_snapshot.get("facts") or {}
        relative_strength = facts.get("relative_strength") or {}
        signal_scorecard = facts.get("signal_scorecard") or {}
        benchmark = facts.get("benchmark") or {}
        latest_hist = facts.get("latest_hist") or {}
        catalyst_digest = facts.get("catalyst_digest") or {}
        policy_signals = facts.get("policy_signals") or []
        regulatory_signals = facts.get("regulatory_signals") or []
        theme_news = facts.get("theme_news") or []

        excess_metric = self._to_float(relative_strength.get("latest_excess_pct_change"))
        benchmark_outputs = self._resolve_benchmark(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_name=str(benchmark.get("benchmark_name") or self.review_policy.get("default_broad_index") or "HS300"),
            default_benchmark_symbol=str(benchmark.get("benchmark_code") or "000300"),
        )
        benchmark_scope = benchmark_outputs["benchmark_scope"]
        benchmark_name = benchmark_outputs["benchmark_name"]
        benchmark_symbol = benchmark_outputs["benchmark_symbol"]
        observed_benchmark_metric = benchmark_outputs["observed_benchmark_metric"]
        anchor_date = self._parse_anchor_date(
            candidates=candidates,
            snapshot_date=str(latest_sector_snapshot.get("snapshot_date") or ""),
        )
        setup_label = str(signal_scorecard.get("setup_label") or "")
        sector_name = str(latest_sector_snapshot.get("sector") or "")

        for candidate in candidates:
            finding_outputs = self._review_candidate(
                candidate=candidate,
                source_run_id=source_run_id,
                anchor_date=anchor_date,
                benchmark_scope=benchmark_scope,
                benchmark_symbol=benchmark_symbol,
                benchmark_name=benchmark_name,
                observed_benchmark_metric=observed_benchmark_metric,
                latest_hist=latest_hist,
                latest_market_snapshot=latest_market_snapshot,
                latest_fundamental_snapshot=latest_fundamental_snapshot,
                latest_notice_snapshot=latest_notice_snapshot,
                excess_metric=excess_metric,
                policy_signals=policy_signals,
                regulatory_signals=regulatory_signals,
                theme_news=theme_news,
                setup_label=setup_label,
                sector_name=sector_name,
                catalyst_digest=catalyst_digest,
            )
            findings.append(ReviewFinding.model_validate(finding_outputs["finding"]))

        focus_validation = self._validate_review_strategy_requirements(
            review_strategy=review_strategy,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
        )
        notes.extend(focus_validation["notes"])
        summary_outputs = self._build_review_summary(
            review_required=bool(focus_validation["review_required"]),
            findings=findings,
            candidates=candidates,
            notes=notes,
            claim_type_counts=claim_type_counts,
        )
        return {
            "summary": summary_outputs["summary"],
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def _derive_review_strategy(
        self,
        *,
        query: str,
        planning_context_hints: dict[str, str],
    ) -> dict[str, Any]:
        strategy: dict[str, Any] = {
            "preferred_methodology": None,
            "focus_area": None,
            "requires_market_snapshot": False,
            "requires_fundamental_snapshot": False,
            "requires_notice_or_policy_signal": False,
            "notes": [],
        }
        if not planning_context_hints:
            return strategy
        context = build_planning_context_view(
            query=query,
            planning_context_hints=planning_context_hints,
        )
        lowered = combine_focus_text(
            context.query_text,
            context.recommended_next_step,
            context.open_question,
            context.previous_summary,
            context.thread_topic,
        )
        preferred_methodology, focus_source = derive_methodology_focus(
            cashflow_text=lowered,
            comparison_text=lowered,
            planning_context_hints=planning_context_hints,
        )
        if preferred_methodology == "cashflow_resilience":
            strategy["preferred_methodology"] = preferred_methodology
            strategy["focus_area"] = "cashflow_resilience"
            strategy["requires_fundamental_snapshot"] = True
        elif preferred_methodology == "dcf":
            strategy["preferred_methodology"] = preferred_methodology
            strategy["focus_area"] = "valuation"
            strategy["requires_fundamental_snapshot"] = True
        elif preferred_methodology == "comps":
            strategy["preferred_methodology"] = preferred_methodology
            strategy["focus_area"] = "same_basis_compare"
        if has_policy_focus(lowered):
            strategy["requires_notice_or_policy_signal"] = True
            if strategy["focus_area"] is None:
                strategy["focus_area"] = "catalyst_validation"
        if has_momentum_focus(lowered):
            strategy["requires_market_snapshot"] = True
            if strategy["focus_area"] is None:
                strategy["focus_area"] = "momentum_validation"
        focus_area = str(strategy.get("focus_area") or "").strip()
        preferred_methodology = str(strategy.get("preferred_methodology") or "").strip()
        if focus_area:
            strategy["notes"].append(f"review_focus={focus_area}")
        if preferred_methodology:
            strategy["notes"].append(f"review_preferred_methodology={preferred_methodology}")
        if focus_source:
            strategy["notes"].append(f"review_focus_source={focus_source}")
        return strategy

    def _validate_review_strategy_requirements(
        self,
        *,
        review_strategy: dict[str, Any],
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
    ) -> dict[str, Any]:
        notes: list[str] = []
        review_required = False
        if review_strategy.get("requires_fundamental_snapshot") and latest_fundamental_snapshot is None:
            review_required = True
            notes.append("missing_fundamental_snapshot_for_review_focus")
        if review_strategy.get("requires_market_snapshot") and latest_market_snapshot is None:
            review_required = True
            notes.append("missing_market_snapshot_for_review_focus")
        if review_strategy.get("requires_notice_or_policy_signal"):
            has_notice = latest_notice_snapshot is not None
            has_policy_signal = bool(policy_signals or regulatory_signals)
            if not has_notice and not has_policy_signal:
                review_required = True
                notes.append("missing_notice_or_policy_signal_for_review_focus")
        return {"review_required": review_required, "notes": notes}

    def _extract_review_claims(self, *, source_entries: list[dict]) -> dict:
        candidates = self._extract_candidates(source_entries=source_entries)
        return {
            "candidates": candidates,
            "claim_type_counts": self._claim_type_counts(candidates),
        }

    def _resolve_benchmark(
        self,
        *,
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None,
        default_benchmark_name: str,
        default_benchmark_symbol: str,
    ) -> dict:
        facts = (latest_sector_snapshot or {}).get("facts") or {}
        benchmark = facts.get("benchmark") or {}
        scope = self._resolve_benchmark_scope(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
        )
        sector_name = str((latest_sector_snapshot or {}).get("sector") or "")
        benchmark_name = self._resolve_benchmark_name(
            benchmark_scope=scope,
            sector_name=sector_name,
            default_benchmark_name=default_benchmark_name,
        )
        benchmark_symbol = self._resolve_benchmark_symbol(
            benchmark_scope=scope,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_symbol=default_benchmark_symbol,
        )
        return {
            "benchmark_scope": scope,
            "benchmark_name": benchmark_name,
            "benchmark_symbol": benchmark_symbol,
            "observed_benchmark_metric": self._benchmark_pct_change(benchmark=benchmark),
        }

    def _review_candidate(self, **kwargs) -> dict:
        candidate = kwargs["candidate"]
        finding = ReviewFinding(
            claim_id=candidate["claim_id"],
            claim_type=str(candidate.get("claim_type") or "direction"),
            source_run_id=kwargs["source_run_id"],
            target_entry_id=candidate["entry_id"],
            window_type=str(candidate.get("window_type") or "trading_days"),
            window_size=int(candidate.get("window_size") or 5),
            anchor_date=kwargs["anchor_date"],
            evaluation_start_rule=str(candidate.get("evaluation_start_rule") or "next_trading_day_open"),
            early_invalidation_rule=candidate.get("early_invalidation_rule"),
            benchmark_scope=kwargs["benchmark_scope"],
            benchmark_symbol=kwargs["benchmark_symbol"],
            benchmark_name=kwargs["benchmark_name"],
            expected_direction=candidate.get("expected_direction"),
            observed_metric=self._observed_metric_for_candidate(
                candidate=candidate,
                latest_hist=kwargs["latest_hist"],
                latest_market_snapshot=kwargs["latest_market_snapshot"],
                latest_notice_snapshot=kwargs["latest_notice_snapshot"],
                excess_metric=kwargs["excess_metric"],
                policy_signals=kwargs["policy_signals"],
                regulatory_signals=kwargs["regulatory_signals"],
                theme_news=kwargs["theme_news"],
            ),
            observed_benchmark_metric=kwargs["observed_benchmark_metric"],
            excess_metric=kwargs["excess_metric"],
            verdict=self._candidate_verdict(
                candidate=candidate,
                latest_market_snapshot=kwargs["latest_market_snapshot"],
                latest_notice_snapshot=kwargs["latest_notice_snapshot"],
                excess_metric=kwargs["excess_metric"],
                policy_signals=kwargs["policy_signals"],
                regulatory_signals=kwargs["regulatory_signals"],
                theme_news=kwargs["theme_news"],
            ),
            notes=self._candidate_notes(
                candidate=candidate,
                sector_name=kwargs["sector_name"],
                latest_market_snapshot=kwargs["latest_market_snapshot"],
                latest_fundamental_snapshot=kwargs["latest_fundamental_snapshot"],
                latest_notice_snapshot=kwargs["latest_notice_snapshot"],
                setup_label=kwargs["setup_label"],
                catalyst_digest=kwargs["catalyst_digest"],
                policy_signals=kwargs["policy_signals"],
                regulatory_signals=kwargs["regulatory_signals"],
                theme_news=kwargs["theme_news"],
            ),
        )
        return {"finding": finding.model_dump(mode="json")}

    def _build_review_summary(
        self,
        *,
        review_required: bool,
        findings: list[ReviewFinding],
        candidates: list[dict],
        notes: list[str],
        claim_type_counts: dict[str, int],
    ) -> dict:
        verdict_by_claim_type = self._verdict_by_claim_type(findings)
        benchmark_scope_counts = self._benchmark_scope_counts(findings)
        confirmed, rejected, inconclusive = self._verdict_totals(verdict_by_claim_type)
        overall_verdict = self._overall_verdict(
            review_required=review_required,
            finding_count=len(findings),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
        )
        summary = PosteriorReviewSummary(
            review_required=review_required,
            finding_count=len(findings),
            reviewable_claim_count=len(candidates),
            claim_type_counts=claim_type_counts,
            benchmark_scope_counts=benchmark_scope_counts,
            benchmark_scope_explanations=self._benchmark_scope_explanations(benchmark_scope_counts),
            verdict_by_claim_type=verdict_by_claim_type,
            overall_verdict=overall_verdict,
            summary_text=self._summary_text(
                overall_verdict=overall_verdict,
                finding_count=len(findings),
                confirmed=confirmed,
                rejected=rejected,
                inconclusive=inconclusive,
                benchmark_scope_counts=benchmark_scope_counts,
            ),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            notes=notes,
        )
        return {"summary": summary.model_dump(mode="json")}

    def _extract_candidates(self, *, source_entries: list[dict]) -> list[dict]:
        candidates: list[dict] = []
        for entry in source_entries:
            kind = str(entry.get("kind") or "")
            payload = entry.get("payload") or {}
            entry_id = str(entry.get("entry_id") or kind or "unknown_entry")

            if kind == "report_fragment":
                candidates.extend(self._extract_report_fragment_candidates(entry_id=entry_id, payload=payload))
            elif kind == "source_record" and str(payload.get("source_type") or "") == "distillation_scorecard":
                candidates.extend(self._extract_scorecard_candidates(entry_id=entry_id, payload=payload))
            elif kind == "logic_block":
                candidates.extend(self._extract_logic_block_candidates(entry_id=entry_id, payload=payload))
        return self._dedupe_candidates(candidates)

    def _extract_report_fragment_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        candidates: list[dict] = []
        risk_text = str(payload.get("risk") or "")
        conclusion_text = str(payload.get("conclusion") or "")
        follow_up_text = str(payload.get("follow_up") or "")
        combined = f"{conclusion_text}\n{risk_text}\n{follow_up_text}"

        expected_direction = self._infer_expected_direction(combined)
        if expected_direction is not None:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": entry_id,
                    "claim_type": "direction",
                    "expected_direction": expected_direction,
                    "claim_source": "report_fragment",
                    "claim_text": combined,
                    "window_type": "trading_days",
                    "window_size": 5,
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )

        if self._contains_catalyst_claim(combined):
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": f"{entry_id}:catalyst",
                    "claim_type": "catalyst",
                    "claim_source": "report_fragment",
                    "claim_text": combined,
                    "window_type": "trading_days",
                    "window_size": 10,
                    "evaluation_start_rule": "next_trading_day_open",
                    "early_invalidation_rule": "catalyst_signal_disappears",
                }
            )

        if self._contains_time_window_claim(combined):
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": f"{entry_id}:time_window",
                    "claim_type": "time_window",
                    "claim_source": "report_fragment",
                    "claim_text": combined,
                    "window_type": "trading_days",
                    "window_size": self._infer_window_size(combined),
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )
        return candidates

    def _extract_scorecard_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        candidates: list[dict] = []
        scorecard = payload.get("scorecard") or {}
        setup_label = str(scorecard.get("setup_label") or "")
        expected_direction = self._expected_direction_from_setup(setup_label)
        if expected_direction is not None:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": entry_id,
                    "claim_type": "direction",
                    "expected_direction": expected_direction,
                    "claim_source": "distillation_scorecard",
                    "claim_text": str(scorecard),
                    "window_type": "trading_days",
                    "window_size": 5,
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )

        review_strength = str(scorecard.get("review_strength") or "")
        attribution_risk = str(scorecard.get("attribution_risk") or "")
        if review_strength in {"medium", "high"} or attribution_risk in {"mixed", "high"}:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": f"{entry_id}:time_window",
                    "claim_type": "time_window",
                    "claim_source": "distillation_scorecard",
                    "claim_text": str(scorecard),
                    "window_type": "trading_days",
                    "window_size": 5,
                    "evaluation_start_rule": "next_trading_day_open",
                }
            )
        return candidates

    def _extract_logic_block_candidates(self, *, entry_id: str, payload: dict) -> list[dict]:
        candidates: list[dict] = []
        block_type = str(payload.get("block_type") or "")
        text = str(payload.get("text") or "")
        direction = str(payload.get("direction") or "")
        time_horizon = str(payload.get("time_horizon") or "")

        if block_type == "catalyst" and text:
            candidates.append(
                {
                    "entry_id": entry_id,
                    "claim_id": entry_id,
                    "claim_type": "catalyst",
                    "claim_source": "logic_block",
                    "claim_text": text,
                    "window_type": "trading_days",
                    "window_size": 10,
                    "evaluation_start_rule": "next_trading_day_open",
                    "early_invalidation_rule": "catalyst_signal_disappears",
                }
            )

        if block_type == "claim" and text:
            expected_direction = self._expected_direction_from_logic_block(
                text=text,
                direction=direction,
            )
            if expected_direction is not None:
                candidates.append(
                    {
                        "entry_id": entry_id,
                        "claim_id": f"{entry_id}:direction",
                        "claim_type": "direction",
                        "expected_direction": expected_direction,
                        "claim_source": "logic_block",
                        "claim_text": text,
                        "window_type": "trading_days",
                        "window_size": self._infer_window_size_from_horizon(time_horizon, text),
                        "evaluation_start_rule": "next_trading_day_open",
                    }
                )

            if self._contains_time_window_claim(text) or time_horizon:
                candidates.append(
                    {
                        "entry_id": entry_id,
                        "claim_id": f"{entry_id}:time_window",
                        "claim_type": "time_window",
                        "claim_source": "logic_block",
                        "claim_text": text,
                        "window_type": "trading_days",
                        "window_size": self._infer_window_size_from_horizon(time_horizon, text),
                        "evaluation_start_rule": "next_trading_day_open",
                    }
                )

            if self._contains_catalyst_claim(text):
                candidates.append(
                    {
                        "entry_id": entry_id,
                        "claim_id": f"{entry_id}:catalyst",
                        "claim_type": "catalyst",
                        "claim_source": "logic_block",
                        "claim_text": text,
                        "window_type": "trading_days",
                        "window_size": 10,
                        "evaluation_start_rule": "next_trading_day_open",
                        "early_invalidation_rule": "catalyst_signal_disappears",
                    }
                )
        return candidates

    def _dedupe_candidates(self, candidates: list[dict]) -> list[dict]:
        deduped: dict[tuple[str, str], dict] = {}
        for candidate in candidates:
            key = (
                str(candidate.get("entry_id") or ""),
                str(candidate.get("claim_type") or ""),
            )
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = candidate
                continue
            existing_text = str(existing.get("claim_text") or "")
            current_text = str(candidate.get("claim_text") or "")
            if len(current_text) > len(existing_text):
                deduped[key] = candidate
        return list(deduped.values())

    def _claim_type_counts(self, candidates: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for candidate in candidates:
            claim_type = str(candidate.get("claim_type") or "unknown")
            counts[claim_type] = counts.get(claim_type, 0) + 1
        return counts

    def _verdict_by_claim_type(self, findings: list[ReviewFinding]) -> dict[str, ClaimVerdictBreakdown]:
        buckets: dict[str, ClaimVerdictBreakdown] = {}
        for finding in findings:
            claim_type = str(finding.claim_type or "unknown")
            verdict = str(finding.verdict)
            if claim_type not in buckets:
                buckets[claim_type] = ClaimVerdictBreakdown()
            current = buckets[claim_type]
            if verdict == "confirmed":
                current.confirmed += 1
            elif verdict == "rejected":
                current.rejected += 1
            else:
                current.inconclusive += 1
        return buckets

    def _verdict_totals(self, verdict_by_claim_type: dict[str, ClaimVerdictBreakdown]) -> tuple[int, int, int]:
        confirmed = sum(item.confirmed for item in verdict_by_claim_type.values())
        rejected = sum(item.rejected for item in verdict_by_claim_type.values())
        inconclusive = sum(item.inconclusive for item in verdict_by_claim_type.values())
        return confirmed, rejected, inconclusive

    def _benchmark_scope_counts(self, findings: list[ReviewFinding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in findings:
            scope = str(finding.benchmark_scope or "unknown")
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def _benchmark_scope_explanations(self, benchmark_scope_counts: dict[str, int]) -> dict[str, str]:
        explanations: dict[str, str] = {}
        for scope in benchmark_scope_counts:
            explanations[scope] = self._benchmark_scope_explanation(scope)
        return explanations

    def _benchmark_scope_explanation(self, scope: str) -> str:
        if scope == "stock_vs_sector":
            return "个股方向主张按个股相对所属板块表现复核。"
        if scope == "sector_vs_hs300":
            return "板块方向主张按板块相对 HS300 表现复核。"
        if scope == "stock_absolute":
            return "缺少板块基准时，个股方向主张按个股自身涨跌做最小复核。"
        return "按当前 review policy 的默认 benchmark 口径复核。"

    def _overall_verdict(
        self,
        *,
        review_required: bool,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
    ) -> str:
        if review_required:
            return "needs_review"
        if finding_count == 0:
            return "no_findings"
        if rejected > 0 and confirmed == 0:
            return "rejected"
        if rejected > 0 and confirmed > 0:
            return "mixed"
        if inconclusive > 0 and confirmed == 0:
            return "inconclusive"
        if confirmed > 0 and inconclusive > 0:
            return "confirmed_with_caveats"
        if confirmed > 0:
            return "confirmed"
        return "inconclusive"

    def _summary_text(
        self,
        *,
        overall_verdict: str,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
        benchmark_scope_counts: dict[str, int],
    ) -> str:
        scope_labels = ",".join(sorted(benchmark_scope_counts.keys())) if benchmark_scope_counts else "no_benchmark"
        return (
            f"overall_verdict={overall_verdict}; findings={finding_count}; "
            f"confirmed={confirmed}; rejected={rejected}; inconclusive={inconclusive}; "
            f"benchmark_scopes={scope_labels}"
        )

    def _candidate_verdict(
        self,
        *,
        candidate: dict,
        excess_metric: float | None,
        latest_market_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        claim_type = str(candidate.get("claim_type") or "direction")
        if claim_type == "direction":
            return self._direction_verdict(
                expected_direction=str(candidate.get("expected_direction") or ""),
                market_change=self._market_pct_change(latest_market_snapshot),
                excess_metric=excess_metric,
            )
        if claim_type == "catalyst":
            return self._catalyst_verdict(
                latest_notice_snapshot=latest_notice_snapshot,
                policy_signals=policy_signals,
                regulatory_signals=regulatory_signals,
                theme_news=theme_news,
            )
        if claim_type == "time_window":
            return self._time_window_verdict(
                policy_signals=policy_signals,
                regulatory_signals=regulatory_signals,
                theme_news=theme_news,
            )
        return "inconclusive"

    def _observed_metric_for_candidate(
        self,
        *,
        candidate: dict,
        latest_hist: dict,
        latest_market_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        excess_metric: float | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> float | None:
        claim_type = str(candidate.get("claim_type") or "direction")
        if claim_type == "direction":
            market_pct_change = self._market_pct_change(latest_market_snapshot)
            if market_pct_change is not None:
                return market_pct_change
            return self._to_float(latest_hist.get("pct_change"))
        if claim_type == "catalyst":
            notice_count = self._notice_signal_count(latest_notice_snapshot)
            if notice_count is not None:
                return float(notice_count)
            return float(len(policy_signals) + len(regulatory_signals))
        if claim_type == "time_window":
            if excess_metric is not None:
                return excess_metric
            return float(len(theme_news))
        return None

    def _candidate_notes(
        self,
        *,
        candidate: dict,
        sector_name: str,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        setup_label: str,
        catalyst_digest: dict,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        claim_type = str(candidate.get("claim_type") or "direction")
        claim_source = str(candidate.get("claim_source") or "unknown")
        parts = [
            f"sector={sector_name or 'unknown'}",
            f"setup_label={setup_label or 'unknown'}",
            f"review_source={claim_source}",
            f"claim_type={claim_type}",
        ]
        market_symbol = str((latest_market_snapshot or {}).get("symbol") or "")
        if market_symbol:
            parts.append(f"market_symbol={market_symbol}")
        claim_text = str(candidate.get("claim_text") or "")
        if claim_text:
            parts.append(f"claim_excerpt={claim_text[:80]}")
        if claim_type == "catalyst":
            top_policy = (catalyst_digest.get("top_policy_titles") or [None])[0]
            top_regulatory = (catalyst_digest.get("top_regulatory_titles") or [None])[0]
            parts.append(f"policy_signal_count={len(policy_signals)}")
            parts.append(f"regulatory_signal_count={len(regulatory_signals)}")
            notice_count = self._notice_signal_count(latest_notice_snapshot)
            if notice_count is not None:
                parts.append(f"notice_signal_count={notice_count}")
            if top_policy:
                parts.append(f"top_policy_title={top_policy}")
            if top_regulatory:
                parts.append(f"top_regulatory_title={top_regulatory}")
        elif claim_type == "time_window":
            parts.append(f"window_size={candidate.get('window_size')}")
            parts.append(f"theme_news_count={len(theme_news)}")
            parts.append(f"policy_signal_count={len(policy_signals)}")
        if latest_fundamental_snapshot is not None:
            parts.append("fundamental_snapshot_present=true")
        return "; ".join(parts)

    def _infer_expected_direction(self, text: str) -> str | None:
        lowered = text.lower()
        if any(token in lowered for token in ("跑赢", "outperform", "偏强", "强于", "relative strength")):
            return "outperform"
        if any(token in lowered for token in ("跑输", "underperform", "偏弱", "弱于")):
            return "underperform"
        return None

    def _expected_direction_from_setup(self, setup_label: str) -> str | None:
        if setup_label in {"momentum_expansion", "breadth_confirmed_strength", "index_weak_but_breadth_resilient"}:
            return "outperform"
        if setup_label in {"weak_without_support", "weak_tape_but_catalysts_alive"}:
            return "underperform"
        return None

    def _expected_direction_from_logic_block(self, *, text: str, direction: str) -> str | None:
        normalized_direction = direction.strip().lower()
        if normalized_direction == "bullish":
            return "outperform"
        if normalized_direction == "bearish":
            return "underperform"
        inferred = self._infer_expected_direction(text)
        if inferred is not None:
            return inferred
        if any(token in text for token in ("受益", "改善", "增强", "强化")):
            return "outperform"
        if any(token in text for token in ("承压", "走弱", "回落", "减弱")):
            return "underperform"
        return None

    def _contains_catalyst_claim(self, text: str) -> bool:
        lowered = text.lower()
        markers = (
            "催化", "催化剂", "政策", "监管", "审批", "注册", "临床", "出海", "医保",
            "catalyst", "policy", "regulatory", "approval", "trial", "licensing",
        )
        return any(marker in lowered for marker in markers)

    def _contains_time_window_claim(self, text: str) -> bool:
        lowered = text.lower()
        markers = (
            "短期", "近期", "未来", "几日", "几周", "下周", "本周", "窗口", "trade window",
            "near term", "short term", "next week", "coming days", "window",
        )
        return any(marker in lowered for marker in markers)

    def _infer_window_size(self, text: str) -> int:
        lowered = text.lower()
        if any(marker in lowered for marker in ("几周", "下周", "next week")):
            return 10
        return 5

    def _infer_window_size_from_horizon(self, time_horizon: str, text: str) -> int:
        if time_horizon in {"中期", "长期"}:
            return 10
        return self._infer_window_size(text)

    def _direction_verdict(self, *, expected_direction: str, market_change: float | None, excess_metric: float | None) -> str:
        if excess_metric is None:
            if market_change is None:
                return "inconclusive"
            if expected_direction == "outperform":
                return "confirmed" if market_change > 0 else "rejected"
            if expected_direction == "underperform":
                return "confirmed" if market_change < 0 else "rejected"
            return "inconclusive"
        if expected_direction == "outperform":
            return "confirmed" if excess_metric > 0 else "rejected"
        if expected_direction == "underperform":
            return "confirmed" if excess_metric < 0 else "rejected"
        return "inconclusive"

    def _catalyst_verdict(
        self,
        *,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        notice_count = self._notice_signal_count(latest_notice_snapshot)
        if notice_count is not None and notice_count > 0:
            return "confirmed"
        if policy_signals or regulatory_signals:
            return "confirmed"
        if theme_news:
            return "inconclusive"
        return "rejected"

    def _time_window_verdict(
        self,
        *,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
        theme_news: list[dict],
    ) -> str:
        if policy_signals or regulatory_signals:
            return "confirmed"
        if theme_news:
            return "inconclusive"
        return "rejected"

    def _parse_anchor_date(self, *, candidates: list[dict], snapshot_date: str) -> str:
        if snapshot_date:
            return snapshot_date
        return date.today().isoformat()

    def _resolve_benchmark_scope(self, *, latest_sector_snapshot: dict | None, latest_market_snapshot: dict | None) -> str:
        if latest_market_snapshot is not None and latest_sector_snapshot is not None:
            return "stock_vs_sector"
        if latest_sector_snapshot is not None:
            return "sector_vs_hs300"
        if latest_market_snapshot is not None:
            return "stock_absolute"
        return str(self.review_policy.get("benchmark_resolution") or "sector")

    def _resolve_benchmark_name(
        self,
        *,
        benchmark_scope: str,
        sector_name: str,
        default_benchmark_name: str,
    ) -> str:
        if benchmark_scope == "stock_vs_sector":
            return sector_name or "sector"
        return default_benchmark_name

    def _resolve_benchmark_symbol(
        self,
        *,
        benchmark_scope: str,
        latest_market_snapshot: dict | None,
        default_benchmark_symbol: str,
    ) -> str:
        if benchmark_scope == "stock_absolute":
            return str((latest_market_snapshot or {}).get("symbol") or default_benchmark_symbol)
        return default_benchmark_symbol

    def _benchmark_pct_change(self, *, benchmark: dict) -> float | None:
        latest_hist = benchmark.get("latest_hist") or {}
        return self._to_float(latest_hist.get("pct_change"))

    def _market_pct_change(self, snapshot: dict | None) -> float | None:
        if snapshot is None:
            return None
        facts = snapshot.get("facts") or {}
        latest_bar = facts.get("latest_bar") or {}
        for key in ("pct_change", "change_percent", "涨跌幅"):
            value = self._to_float(latest_bar.get(key))
            if value is not None:
                return value
        return None

    def _notice_signal_count(self, snapshot: dict | None) -> int | None:
        if snapshot is None:
            return None
        facts = snapshot.get("facts") or {}
        notices = facts.get("recent_notices") or facts.get("notices") or []
        if isinstance(notices, list):
            return len(notices)
        return None

    def _to_float(self, value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
