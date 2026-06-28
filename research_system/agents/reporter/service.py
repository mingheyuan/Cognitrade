from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.schemas.review import ClaimVerdictBreakdown, PosteriorReviewSummary


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

    def run(self, *, intent: str, query: str, evidences: list[dict], retrieval_summary: str | None = None) -> dict:
        review_summary_payload = self._review_summary_payload(evidences) if intent == "review_thesis" else None
        if not self.llm_provider.available:
            if intent == "distill_kol_logic":
                review_count = len([
                    item for item in evidences
                    if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
                ])
                scorecard_digest = self._distillation_scorecard_digest(evidences)
                scorecard_payload = self._distillation_scorecard_payload(evidences)
                return {
                    "conclusion": (
                        f"LLM unavailable. Returning degraded distillation report with {len(evidences)} evidence item(s) "
                        f"for query: {query}. {scorecard_digest}"
                    ),
                    "evidence": evidences[:8],
                    "scorecard": scorecard_payload,
                    "risk": (
                        "Distillation output is running in degraded mode; "
                        f"{review_count} critic review finding(s) indicate manual review is still required."
                    ),
                    "follow_up": (
                        "Review the critic findings and confirm which claims are author-original versus "
                        "reply/repost/reported content before reusing this distillation."
                    ),
                    "degraded_note": "llm_unavailable",
                }
            if intent == "review_thesis":
                review_findings = [
                    item for item in evidences
                    if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
                ]
                confirmed = sum(1 for item in review_findings if "verdict=confirmed" in str(item.get("summary") or ""))
                rejected = sum(1 for item in review_findings if "verdict=rejected" in str(item.get("summary") or ""))
                inconclusive = sum(1 for item in review_findings if "verdict=inconclusive" in str(item.get("summary") or ""))
                verdict_by_claim_type = self._review_verdict_by_claim_type_payload(review_findings)
                overall_verdict = (review_summary_payload or {}).get("overall_verdict") or "inconclusive"
                return {
                    "conclusion": (
                        f"LLM unavailable. Returning degraded review report with {len(review_findings)} review finding(s) "
                        f"for query: {query}. overall_verdict={overall_verdict}. "
                        f"confirmed={confirmed}, rejected={rejected}, inconclusive={inconclusive}. "
                        f"claim_type_distribution={verdict_by_claim_type}."
                    ),
                    "evidence": evidences[:8],
                    "review_summary": review_summary_payload,
                    "risk": "Posterior review is running in degraded mode; use the structured review findings as the primary verdict source.",
                    "follow_up": "Inspect confirmed versus rejected review findings and keep benchmark-relative interpretation explicit.",
                    "degraded_note": "llm_unavailable",
                }
            return {
                "conclusion": (
                    f"LLM unavailable. Returning degraded Phase 1 report for {intent}. "
                    f"The system collected {len(evidences)} evidence item(s) for query: {query}"
                ),
                "evidence": evidences[:5],
                "risk": "LLM unavailable; report quality is degraded. Exact claims remain constrained by verified snapshots.",
                "follow_up": "Configure OPENAI_API_KEY to enable LLM-powered reporting.",
                "degraded_note": "llm_unavailable",
            }

        evidence_block = [
            {
                "evidence_id": item["evidence_id"],
                "title": item["title"],
                "summary": item["summary"],
                "metadata": item["metadata"],
                "source_refs": item["source_refs"],
            }
            for item in evidences
        ]

        try:
            report = self.llm_provider.parse(
                system_prompt=(
                    "You are the reporter agent in an A-share research system.\n"
                    "Produce a concise research output using only the provided evidence.\n"
                    "Do not invent exact prices, dates, financial metrics, or notices.\n"
                    "If evidence is insufficient for exact claims, explicitly say the data is unavailable or constrained.\n"
                    "For distill_kol_logic, summarize the distilled logic, dimensions, major risks, and review need. "
                    "Call out whether key claims are author-original, replies, reposts, or explicit quotes when evidence supports it. "
                    "Do not pretend the distilled output has been verified by market data.\n"
                    "For review_thesis, summarize the posterior review verdicts, benchmark-relative direction validation, and any inconclusive cases. "
                    "If a structured review summary is provided, anchor the conclusion to its `overall_verdict` first. "
                    "Treat review findings as the primary source of truth.\n"
                    "For sector research, prefer stating the sector's recent direction, turnover, constituent breadth, relative strength versus any provided benchmark, "
                    "recent theme news, any policy signals or regulatory disclosure signals, any provided signal scorecard, "
                    "and any concrete catalyst samples or catalyst digest from representative constituents."
                ),
                user_prompt=(
                    f"Intent: {intent}\n"
                    f"User query: {query}\n"
                    f"Retriever summary: {retrieval_summary or 'N/A'}\n"
                    f"Structured review summary: {review_summary_payload or 'N/A'}\n"
                    f"Evidence candidates: {evidence_block}\n\n"
                    "Return a JSON object with keys "
                    "`conclusion`, `evidence_ids`, `risk`, `follow_up`, and optional `degraded_note`.\n"
                    "Use `risk` for the risk statement and `follow_up` for the optional next step."
                ),
                schema=ReportPayload,
                temperature=0.2,
                reasoning_effort="medium",
            )
        except LLMProviderError as exc:
            scorecard_digest = self._distillation_scorecard_digest(evidences) if intent == "distill_kol_logic" else None
            scorecard_payload = self._distillation_scorecard_payload(evidences) if intent == "distill_kol_logic" else None
            review_conclusion = (
                f"LLM request failed during reporting for {intent}. "
                f"Returning degraded Phase 1 report with {len(evidences)} evidence item(s) for query: {query}"
                + (f". {scorecard_digest}" if scorecard_digest else "")
            )
            if intent == "review_thesis":
                overall_verdict = (review_summary_payload or {}).get("overall_verdict") or "inconclusive"
                review_conclusion = (
                    f"LLM request failed during reporting for {intent}. "
                    f"Returning degraded review report for query: {query}. "
                    f"overall_verdict={overall_verdict}."
                )
            return {
                "conclusion": review_conclusion,
                "evidence": evidences[:5],
                "scorecard": scorecard_payload,
                "review_summary": review_summary_payload,
                "risk": "LLM reporting failed; report quality is degraded. Exact claims remain constrained by verified snapshots.",
                "follow_up": "Check LLM gateway availability or provider compatibility.",
                "degraded_note": f"llm_error:{exc.__class__.__name__}",
            }
        selected_ids = set(report.evidence_ids)
        selected_evidence = [item for item in evidences if item["evidence_id"] in selected_ids] or evidences[:5]
        conclusion = report.conclusion
        if intent == "review_thesis":
            conclusion = self._normalize_review_conclusion(
                conclusion=conclusion,
                review_summary_payload=review_summary_payload,
            )
        return {
            "conclusion": conclusion,
            "evidence": selected_evidence[:5],
            "scorecard": self._distillation_scorecard_payload(selected_evidence) if intent == "distill_kol_logic" else None,
            "review_summary": review_summary_payload,
            "risk": report.risk,
            "follow_up": report.follow_up,
            "degraded_note": report.degraded_note,
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
