from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.schemas.review import ClaimVerdictBreakdown, PosteriorReviewSummary
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.reporting import (
    DistillationScorecardComposerSubagent,
    FallbackReportComposerSubagent,
    LLMErrorReportComposerSubagent,
    LLMReportComposerSubagent,
    ReviewSummaryComposerSubagent,
)


class ReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str
    evidence_ids: list[str] = Field(default_factory=list)
    risk: str = Field(validation_alias=AliasChoices("risk", "risk_statement"))
    follow_up: str | None = Field(
        default=None,
        validation_alias=AliasChoices("follow_up", "follow_up_suggestion"),
    )
    degraded_note: str | None = None


class ReporterService:
    agent_name = "reporter"

    def __init__(self, llm_provider: OpenAIProvider) -> None:
        self.llm_provider = llm_provider
        self.registry = SubagentRegistry(
            [
                ReviewSummaryComposerSubagent(build_review_summary=self._review_summary_payload),
                DistillationScorecardComposerSubagent(
                    build_scorecard_digest=self._distillation_scorecard_digest,
                    build_scorecard_payload=self._distillation_scorecard_payload,
                ),
                FallbackReportComposerSubagent(
                    review_verdict_by_claim_type_payload=self._review_verdict_by_claim_type_payload,
                    screening_digest=self._screening_digest,
                ),
                LLMReportComposerSubagent(
                    llm_provider=self.llm_provider,
                    schema=ReportPayload,
                    normalize_review_conclusion=self._normalize_review_conclusion,
                ),
                LLMErrorReportComposerSubagent(),
            ]
        )

    def run(self, *, intent: str, query: str, evidences: list[dict], retrieval_summary: str | None = None) -> dict:
        review_summary_payload = None
        screening_scorecard_payload = self._screening_scorecard_payload(evidences) if intent == "stock_screen" else None
        screening_candidates_payload = self._screening_candidates_payload(evidences) if intent == "stock_screen" else []
        if intent in {"review_thesis", "stock_screen"}:
            review_outputs, _ = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="review_summary_composer",
                    objective="Build structured review summary for reporter.",
                    inputs={"evidences": evidences},
                )
            )
            review_summary_payload = review_outputs["review_summary"]
        if not self.llm_provider.available:
            scorecard_digest = None
            scorecard_payload = None
            if intent == "distill_kol_logic":
                scorecard_outputs, _ = self.registry.invoke(
                    SubagentInvocation(
                        subagent_type="distillation_scorecard_composer",
                        objective="Build distillation scorecard digest and payload for fallback report.",
                        inputs={"evidences": evidences},
                    )
                )
                scorecard_digest = scorecard_outputs["scorecard_digest"]
                scorecard_payload = scorecard_outputs["scorecard_payload"]
            fallback_outputs, _ = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="fallback_report_composer",
                    objective="Compose degraded fallback report.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": review_summary_payload,
                        "scorecard_digest": scorecard_digest,
                        "scorecard_payload": scorecard_payload,
                        "screening_scorecard_payload": screening_scorecard_payload,
                        "screening_candidates_payload": screening_candidates_payload,
                        "degraded_note": "llm_unavailable",
                    },
                )
            )
            return fallback_outputs

        try:
            llm_outputs, _ = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="llm_report_composer",
                    objective="Compose final report via LLM.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "retrieval_summary": retrieval_summary,
                        "review_summary_payload": review_summary_payload,
                    },
                )
            )
        except LLMProviderError as exc:
            scorecard_digest = None
            scorecard_payload = None
            if intent == "distill_kol_logic":
                scorecard_outputs, _ = self.registry.invoke(
                    SubagentInvocation(
                        subagent_type="distillation_scorecard_composer",
                        objective="Build distillation scorecard digest and payload for LLM error fallback.",
                        inputs={"evidences": evidences},
                    )
                )
                scorecard_digest = scorecard_outputs["scorecard_digest"]
                scorecard_payload = scorecard_outputs["scorecard_payload"]
            error_outputs, _ = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="llm_error_report_composer",
                    objective="Compose degraded report after LLM error.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": review_summary_payload,
                        "scorecard_digest": scorecard_digest,
                        "scorecard_payload": scorecard_payload,
                        "screening_scorecard_payload": screening_scorecard_payload,
                        "screening_candidates_payload": screening_candidates_payload,
                        "screening_digest": self._screening_digest(screening_scorecard_payload, screening_candidates_payload) if intent == "stock_screen" else None,
                        "error_name": exc.__class__.__name__,
                    },
                )
            )
            return error_outputs
        screening_scorecard = self._screening_scorecard_payload(llm_outputs["selected_evidence"]) if intent == "stock_screen" else None
        screening_candidates = self._screening_candidates_payload(llm_outputs["selected_evidence"]) if intent == "stock_screen" else None
        return {
            "conclusion": llm_outputs["conclusion"],
            "evidence": llm_outputs["selected_evidence"][:5],
            "scorecard": self._distillation_scorecard_payload(llm_outputs["selected_evidence"]) if intent == "distill_kol_logic" else None,
            "screening_scorecard": screening_scorecard,
            "candidates": screening_candidates,
            "review_summary": review_summary_payload,
            "risk": llm_outputs["risk"],
            "follow_up": llm_outputs["follow_up"],
            "degraded_note": llm_outputs["degraded_note"],
        }

    def _distillation_attribution_digest(self, evidences: list[dict]) -> str:
        counts = {
            "author": 0,
            "reply": 0,
            "repost": 0,
            "reported": 0,
            "quoted": 0,
            "uncertain": 0,
        }
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "logic_block":
                continue
            summary = str(item.get("summary") or "")
            marker = "speaker_type="
            if marker not in summary:
                continue
            speaker_type = summary.split(marker, 1)[1].split()[0].strip()
            if speaker_type in counts:
                counts[speaker_type] += 1

        non_zero = [f"{name}={count}" for name, count in counts.items() if count > 0]
        if not non_zero:
            return "Attribution mix unavailable from selected evidence."
        return "Attribution mix: " + ", ".join(non_zero) + "."

    def _distillation_scorecard_digest(self, evidences: list[dict]) -> str:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "distillation_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" in summary:
                _, tail = summary.split("\n", 1)
                return f"Scorecard: {tail}"
            return f"Scorecard: {summary}"
        return self._distillation_attribution_digest(evidences)

    def _distillation_scorecard_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "distillation_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                return None
            _, tail = summary.split("\n", 1)
            import ast

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _review_verdict_by_claim_type(self, review_findings: list[dict]) -> dict[str, ClaimVerdictBreakdown]:
        buckets: dict[str, ClaimVerdictBreakdown] = {}
        for item in review_findings:
            summary = str(item.get("summary") or "")
            if "claim_type=" not in summary or "verdict=" not in summary:
                continue
            claim_type = summary.split("claim_type=", 1)[1].split()[0].strip()
            verdict = summary.split("verdict=", 1)[1].split()[0].strip()
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

    def _review_verdict_by_claim_type_payload(self, review_findings: list[dict]) -> dict[str, dict[str, int]]:
        return {
            claim_type: breakdown.model_dump(mode="json")
            for claim_type, breakdown in self._review_verdict_by_claim_type(review_findings).items()
        }

    def _review_benchmark_scope_counts(self, review_findings: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in review_findings:
            summary = str(item.get("summary") or "")
            if "scope=" not in summary:
                continue
            scope = summary.split("scope=", 1)[1].split()[0].strip()
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def _review_benchmark_scope_explanations(self, benchmark_scope_counts: dict[str, int]) -> dict[str, str]:
        explanations: dict[str, str] = {}
        for scope in benchmark_scope_counts:
            if scope == "stock_vs_sector":
                explanations[scope] = "个股方向主张按个股相对所属板块表现复核。"
            elif scope == "sector_vs_hs300":
                explanations[scope] = "板块方向主张按板块相对 HS300 表现复核。"
            elif scope == "stock_absolute":
                explanations[scope] = "缺少板块基准时，个股方向主张按个股自身涨跌做最小复核。"
            else:
                explanations[scope] = "按当前 review policy 的默认 benchmark 口径复核。"
        return explanations

    def _review_overall_verdict(
        self,
        *,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
    ) -> str:
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

    def _review_summary_text(
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

    def _normalize_review_conclusion(self, *, conclusion: str, review_summary_payload: dict | None) -> str:
        if not review_summary_payload:
            return conclusion
        overall_verdict = str(review_summary_payload.get("overall_verdict") or "").strip()
        if not overall_verdict:
            return conclusion
        if f"overall_verdict={overall_verdict}" in conclusion:
            return conclusion
        return f"overall_verdict={overall_verdict}. {conclusion}"

    def _review_summary_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "review_summary":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                continue
            _, tail = summary.split("\n", 1)
            import ast
            import json

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                try:
                    parsed = json.loads(tail)
                except Exception:
                    normalized_tail = (
                        tail.replace(": true", ": True")
                        .replace(": false", ": False")
                        .replace(": null", ": None")
                    )
                    try:
                        parsed = ast.literal_eval(normalized_tail)
                    except Exception:
                        parsed = None
            if isinstance(parsed, dict):
                return parsed
        review_findings = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
        ]
        if not review_findings:
            return None
        confirmed = sum(1 for item in review_findings if "verdict=confirmed" in str(item.get("summary") or ""))
        rejected = sum(1 for item in review_findings if "verdict=rejected" in str(item.get("summary") or ""))
        inconclusive = sum(1 for item in review_findings if "verdict=inconclusive" in str(item.get("summary") or ""))
        claim_type_counts: dict[str, int] = {}
        verdict_by_claim_type = self._review_verdict_by_claim_type(review_findings)
        benchmark_scope_counts = self._review_benchmark_scope_counts(review_findings)
        for claim_type, breakdown in verdict_by_claim_type.items():
            claim_type_counts[claim_type] = breakdown.confirmed + breakdown.rejected + breakdown.inconclusive
        overall_verdict = self._review_overall_verdict(
            finding_count=len(review_findings),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
        )
        summary = PosteriorReviewSummary(
            review_required=False,
            finding_count=len(review_findings),
            reviewable_claim_count=len(review_findings),
            claim_type_counts=claim_type_counts,
            benchmark_scope_counts=benchmark_scope_counts,
            benchmark_scope_explanations=self._review_benchmark_scope_explanations(benchmark_scope_counts),
            verdict_by_claim_type=verdict_by_claim_type,
            overall_verdict=overall_verdict,
            summary_text=self._review_summary_text(
                overall_verdict=overall_verdict,
                finding_count=len(review_findings),
                confirmed=confirmed,
                rejected=rejected,
                inconclusive=inconclusive,
                benchmark_scope_counts=benchmark_scope_counts,
            ),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            notes=[],
        )
        return summary.model_dump(mode="json")

    def _screening_scorecard_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "screening_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                return None
            _, tail = summary.split("\n", 1)
            import ast

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _screening_candidates_payload(self, evidences: list[dict]) -> list[dict]:
        candidates: list[dict] = []
        for item in evidences:
            metadata = item.get("metadata") or {}
            source_type = str(metadata.get("source_type") or "")
            if source_type not in {"screen_candidate", "watchlist_candidate"}:
                continue
            summary = str(item.get("summary") or "")
            title = str(item.get("title") or "")
            symbol = title.split(":", 1)[1].strip() if ":" in title else title
            parsed_candidate = self._parse_screen_candidate_summary(summary)
            name = str(parsed_candidate.get("name") or symbol)
            rationale = str(parsed_candidate.get("why_it_made_the_list") or "")
            risk = str(parsed_candidate.get("main_risk") or "")
            score = parsed_candidate.get("score")
            missing_evidence = parsed_candidate.get("missing_evidence") or ""
            deep_checked = bool(parsed_candidate.get("deep_checked"))
            candidate = {
                "symbol": symbol,
                "name": name,
                "title": title,
                "summary": summary,
                "score": score,
                "why_it_made_the_list": rationale,
                "main_risk": risk,
                "missing_evidence": missing_evidence,
                "deep_checked": deep_checked,
                "candidate_tier": "watchlist" if source_type == "watchlist_candidate" else "core",
            }
            candidates.append(candidate)
        return candidates

    def _parse_screen_candidate_summary(self, summary: str) -> dict:
        import ast

        lines = summary.splitlines()
        if len(lines) >= 2:
            tail = lines[-1].strip()
            if tail.startswith("{") and tail.endswith("}"):
                try:
                    parsed = ast.literal_eval(tail)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    return parsed

        parsed: dict = {}
        for line in lines:
            if line.startswith("screen_candidate symbol=") or line.startswith("watchlist_candidate symbol="):
                parts = line.split()
                for part in parts:
                    if "=" not in part:
                        continue
                    key, value = part.split("=", 1)
                    if key == "symbol":
                        parsed["symbol"] = value.strip()
                    elif key == "name":
                        parsed["name"] = value.strip()
            elif line.startswith("why="):
                parsed["why_it_made_the_list"] = line.split("=", 1)[1].strip()
            elif line.startswith("risk="):
                parsed["main_risk"] = line.split("=", 1)[1].strip()
            elif line.startswith("score="):
                parsed["score"] = line.split("=", 1)[1].split()[0].strip()
            elif line.startswith("deep_checked="):
                parsed["deep_checked"] = line.split("=", 1)[1].strip().lower() == "true"
            elif line.startswith("missing_evidence="):
                parsed["missing_evidence"] = line.split("=", 1)[1].strip()
        return parsed

    def _screening_digest(self, screening_scorecard_payload: dict | None, screening_candidates_payload: list[dict]) -> str:
        if not screening_candidates_payload:
            return "未筛出满足最小条件的候选，当前更适合继续观察而不是直接给出标的。"
        core_candidates = [item for item in screening_candidates_payload if str(item.get("candidate_tier") or "core") == "core"]
        watchlist_candidates = [item for item in screening_candidates_payload if str(item.get("candidate_tier") or "") == "watchlist"]
        top_names = [str(item.get("name") or item.get("symbol") or "") for item in core_candidates[:3] if str(item.get("name") or item.get("symbol") or "").strip()]
        watch_names = [str(item.get("name") or item.get("symbol") or "") for item in watchlist_candidates[:3] if str(item.get("name") or item.get("symbol") or "").strip()]
        cycle = str((screening_scorecard_payload or {}).get("investment_cycle") or "mid_term")
        summary = str((screening_scorecard_payload or {}).get("summary") or "").strip()
        deep_checked_count = int((screening_scorecard_payload or {}).get("deep_checked_count") or 0)
        first = (core_candidates or watchlist_candidates or screening_candidates_payload)[0]
        leader = str(first.get("name") or first.get("symbol") or "").strip()
        leader_reason = str(first.get("why_it_made_the_list") or "").strip()
        leader_risk = str(first.get("main_risk") or "").strip()
        lines = [f"当前更适合作为 {cycle} 观察名单，而不是直接交易指令。"]
        if top_names:
            lines.append(f"优先关注：{', '.join(top_names)}。")
        if watch_names:
            lines.append(f"次级观察：{', '.join(watch_names)}。")
        if summary:
            lines.append(summary)
        if deep_checked_count > 0:
            lines.append(f"其中有 {deep_checked_count} 只候选已经完成市场、财务和公告的深度核查。")
        else:
            lines.append("当前候选仍以轻量筛选为主，尚未完成系统性的深度核查。")
        if leader and leader_reason:
            lines.append(f"其中 {leader} 的入选理由是：{leader_reason}。")
        if leader_risk:
            lines.append(f"主要风险是：{leader_risk}。")
        return " ".join(lines)
