from __future__ import annotations

from typing import Callable

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.services.reporting_utils import strategy_follow_up, summarize_proposal_drafts


class FallbackReportComposerSubagent:
    subagent_type = "fallback_report_composer"

    def __init__(
        self,
        *,
        review_verdict_by_claim_type_payload: Callable[[list[dict]], dict[str, dict[str, int]]],
        screening_digest: Callable[[dict | None, list[dict]], str],
        compare_targets_payload: Callable[[list[dict]], list[dict]],
        compare_digest: Callable[..., str],
    ) -> None:
        self.review_verdict_by_claim_type_payload = review_verdict_by_claim_type_payload
        self.screening_digest = screening_digest
        self.compare_targets_payload = compare_targets_payload
        self.compare_digest = compare_digest

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        review_summary_payload: dict | None,
        scorecard_digest: str | None,
        scorecard_payload: dict | None,
        screening_scorecard_payload: dict | None,
        screening_candidates_payload: list[dict],
        methodology_payloads: list[dict],
        report_strategy: dict | None,
        degraded_note: str,
    ) -> dict:
        strategy_follow_up_text = strategy_follow_up(report_strategy or {})
        if intent == "distill_kol_logic":
            review_count = len(
                [
                    item for item in evidences
                    if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
                ]
            )
            return {
                "conclusion": (
                    f"LLM unavailable. Returning degraded distillation report with {len(evidences)} evidence item(s) "
                    f"for query: {query}. {scorecard_digest or ''}".strip()
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
                "degraded_note": degraded_note,
            }
        if intent == "review_thesis":
            review_findings = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
            ]
            confirmed = sum(1 for item in review_findings if "verdict=confirmed" in str(item.get("summary") or ""))
            rejected = sum(1 for item in review_findings if "verdict=rejected" in str(item.get("summary") or ""))
            inconclusive = sum(1 for item in review_findings if "verdict=inconclusive" in str(item.get("summary") or ""))
            verdict_by_claim_type = self.review_verdict_by_claim_type_payload(review_findings)
            overall_verdict = (review_summary_payload or {}).get("overall_verdict") or "inconclusive"
            methodology_notes = ", ".join(
                f"{item.get('methodology_type')}={item.get('valuation_band_label') or 'no_band'}"
                for item in methodology_payloads[:3]
            )
            return {
                "conclusion": (
                    f"LLM unavailable. Returning degraded review report with {len(review_findings)} review finding(s) "
                    f"for query: {query}. overall_verdict={overall_verdict}. "
                    f"confirmed={confirmed}, rejected={rejected}, inconclusive={inconclusive}. "
                    f"claim_type_distribution={verdict_by_claim_type}."
                    + (f" methodology_views={methodology_notes}." if methodology_notes else "")
                ),
                "evidence": evidences[:8],
                "methodologies": methodology_payloads or None,
                "review_summary": review_summary_payload,
                "risk": "Posterior review is running in degraded mode; use the structured review findings as the primary verdict source.",
                "follow_up": strategy_follow_up_text or "Inspect confirmed versus rejected review findings and keep benchmark-relative interpretation explicit.",
                "degraded_note": degraded_note,
            }
        if intent == "stock_screen":
            methodology_notes = ", ".join(
                f"{item.get('methodology_type')}={item.get('valuation_band_label') or 'no_band'}"
                for item in methodology_payloads[:3]
            )
            return {
                "conclusion": (
                    self.screening_digest(screening_scorecard_payload, screening_candidates_payload)
                    + (f" Relative valuation views: {methodology_notes}." if methodology_notes else "")
                ),
                "evidence": evidences[:8],
                "screening_scorecard": screening_scorecard_payload,
                "candidates": screening_candidates_payload,
                "methodologies": methodology_payloads or None,
                "review_summary": review_summary_payload,
                "risk": "Stock screening is running in degraded mode; treat the output as a watchlist rather than a direct recommendation.",
                "follow_up": strategy_follow_up_text or "Review the top candidates manually and confirm price, financial, and notice snapshots before acting.",
                "degraded_note": degraded_note,
            }
        if intent == "stock_check":
            methodology_notes = ", ".join(
                f"{item.get('methodology_type')}={item.get('valuation_band_label') or 'no_band'}"
                for item in methodology_payloads[:3]
            )
            return {
                "conclusion": (
                    f"LLM unavailable. Returning degraded stock check report for query: {query}. "
                    f"methodology_views={methodology_notes or 'unavailable'}."
                ),
                "evidence": evidences[:8],
                "methodologies": methodology_payloads or None,
                "risk": "Stock check is running in degraded mode; validate valuation assumptions and snapshot freshness before using the result.",
                "follow_up": strategy_follow_up_text or "Review the methodology warnings and confirm the latest market, fundamental, and notice snapshots.",
                "degraded_note": degraded_note,
            }
        if intent == "stock_compare":
            compare_digest = self.compare_digest(
                compare_targets=self.compare_targets_payload(evidences),
                methodology_payloads=methodology_payloads,
            )
            return {
                "conclusion": compare_digest,
                "evidence": evidences[:8],
                "methodologies": methodology_payloads or None,
                "risk": "多标的比较正在降级模式下生成，结论主要依赖快照和轻量方法论模块，不能替代完整财务建模。",
                "follow_up": "补充同口径行业景气、盈利预期和更完整估值假设后再做最终排序。",
                "degraded_note": degraded_note,
            }
        if intent == "methodology_evolution":
            proposal_summary = summarize_proposal_drafts(evidences)
            return {
                "conclusion": (
                    f"LLM unavailable. Returning degraded methodology evolution report for query: {query}. "
                    f"proposal_count={proposal_summary['proposal_count']}. "
                    f"approved={proposal_summary['approved_count']}; pending={proposal_summary['pending_count']}."
                    + (
                        f" pending_reasons={proposal_summary['pending_reasons'][:3]}."
                        if proposal_summary["pending_reasons"] else ""
                    )
                ),
                "evidence": evidences[:8],
                "risk": "Methodology evolution ran in degraded mode; review proposal drafts before broad rollout.",
                "follow_up": "Inspect approved proposals, methodology manifest updates, and memory exports.",
                "degraded_note": degraded_note,
            }
        return {
            "conclusion": (
                f"LLM unavailable. Returning degraded Phase 1 report for {intent}. "
                f"The system collected {len(evidences)} evidence item(s) for query: {query}"
            ),
            "evidence": evidences[:5],
            "risk": "LLM unavailable; report quality is degraded. Exact claims remain constrained by verified snapshots.",
            "follow_up": "Configure OPENAI_API_KEY to enable LLM-powered reporting.",
            "degraded_note": degraded_note,
        }

class LLMReportComposerSubagent:
    subagent_type = "llm_report_composer"

    def __init__(self, *, llm_provider: OpenAIProvider, schema: type, normalize_review_conclusion: Callable[..., str]) -> None:
        self.llm_provider = llm_provider
        self.schema = schema
        self.normalize_review_conclusion = normalize_review_conclusion

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None,
        review_summary_payload: dict | None,
        report_strategy: dict | None,
    ) -> dict:
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
                "For stock_screen, return a watchlist-style answer rather than a trade instruction. "
                "Rank the top candidates, explain why each candidate made the list, call out missing evidence or degraded sources, "
                "and explicitly frame the result as research candidates or a follow-up list.\n"
                "For sector research, prefer stating the sector's recent direction, turnover, constituent breadth, relative strength versus any provided benchmark, "
                "recent theme news, any policy signals or regulatory disclosure signals, any provided signal scorecard, "
                "and any concrete catalyst samples or catalyst digest from representative constituents."
            ),
            user_prompt=(
                f"Intent: {intent}\n"
                f"User query: {query}\n"
                f"Retriever summary: {retrieval_summary or 'N/A'}\n"
                f"Structured review summary: {review_summary_payload or 'N/A'}\n"
                f"Report strategy: {report_strategy or 'N/A'}\n"
                f"Evidence candidates: {evidence_block}\n\n"
                "Return a JSON object with keys "
                "`conclusion`, `evidence_ids`, `risk`, `follow_up`, and optional `degraded_note`.\n"
                "Use `risk` for the risk statement and `follow_up` for the optional next step."
            ),
            schema=self.schema,
            temperature=0.2,
            reasoning_effort="medium",
        )
        selected_ids = set(report.evidence_ids)
        selected_evidence = [item for item in evidences if item["evidence_id"] in selected_ids] or evidences[:5]
        conclusion = report.conclusion
        if intent == "review_thesis":
            conclusion = self.normalize_review_conclusion(
                conclusion=conclusion,
                review_summary_payload=review_summary_payload,
            )
        return {
            "conclusion": conclusion,
            "selected_evidence": selected_evidence,
            "risk": report.risk,
            "follow_up": report.follow_up,
            "degraded_note": report.degraded_note,
        }


class LLMErrorReportComposerSubagent:
    subagent_type = "llm_error_report_composer"

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        review_summary_payload: dict | None,
        scorecard_digest: str | None,
        scorecard_payload: dict | None,
        screening_scorecard_payload: dict | None,
        screening_candidates_payload: list[dict],
        screening_digest: str | None,
        methodology_payloads: list[dict],
        report_strategy: dict | None,
        error_name: str,
    ) -> dict:
        strategy_follow_up_text = strategy_follow_up(report_strategy or {})
        review_conclusion = (
            f"LLM request failed during reporting for {intent}. "
            f"Returning degraded Phase 1 report with {len(evidences)} evidence item(s) for query: {query}"
            + (f". {scorecard_digest}" if scorecard_digest else "")
        )
        if intent == "review_thesis":
            overall_verdict = (review_summary_payload or {}).get("overall_verdict") or "inconclusive"
            methodology_notes = ", ".join(
                f"{item.get('methodology_type')}={item.get('valuation_band_label') or 'no_band'}"
                for item in methodology_payloads[:3]
            )
            review_conclusion = (
                f"LLM request failed during reporting for {intent}. "
                f"Returning degraded review report for query: {query}. "
                f"overall_verdict={overall_verdict}."
                + (f" methodology_views={methodology_notes}." if methodology_notes else "")
            )
        elif intent == "stock_screen":
            methodology_notes = ", ".join(
                f"{item.get('methodology_type')}={item.get('valuation_band_label') or 'no_band'}"
                for item in methodology_payloads[:3]
            )
            review_conclusion = (screening_digest or (
                f"LLM request failed during reporting for {intent}. "
                f"Returning degraded stock screening report for query: {query}."
            )) + (f" Relative valuation views: {methodology_notes}." if methodology_notes else "")
        elif intent == "stock_check":
            methodology_notes = ", ".join(
                f"{item.get('methodology_type')}={item.get('valuation_band_label') or 'no_band'}"
                for item in methodology_payloads[:3]
            )
            review_conclusion = (
                f"LLM request failed during reporting for {intent}. "
                f"Returning degraded stock check report for query: {query}. "
                f"methodology_views={methodology_notes or 'unavailable'}."
            )
        elif intent == "stock_compare":
            methodology_notes = ", ".join(
                f"{item.get('title')}={item.get('methodology_type')}"
                for item in methodology_payloads[:6]
            )
            review_conclusion = (
                f"LLM request failed during reporting for {intent}. "
                f"Returning degraded stock comparison report for query: {query}. "
                f"methodology_views={methodology_notes or 'unavailable'}."
            )
        elif intent == "methodology_evolution":
            review_conclusion = (
                f"LLM request failed during reporting for {intent}. "
                f"Returning degraded methodology evolution report for query: {query}."
            )
        return {
            "conclusion": review_conclusion,
            "evidence": evidences[:5],
            "scorecard": scorecard_payload,
            "screening_scorecard": screening_scorecard_payload,
            "candidates": screening_candidates_payload,
            "methodologies": methodology_payloads or None,
            "review_summary": review_summary_payload,
            "risk": "LLM reporting failed; report quality is degraded. Exact claims remain constrained by verified snapshots.",
            "follow_up": strategy_follow_up_text or "Check LLM gateway availability or provider compatibility.",
            "degraded_note": f"llm_error:{error_name}",
        }
