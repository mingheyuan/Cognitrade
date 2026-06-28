from __future__ import annotations

from collections import Counter, defaultdict

from research_system.schemas.review import CriticReviewFinding, CriticReviewSummary


class CriticService:
    agent_name = "critic"

    def run(
        self,
        *,
        distillation_summary: dict | None,
        logic_blocks: list[dict],
        distilled_dimensions: list[dict],
    ) -> dict:
        findings: list[CriticReviewFinding] = []
        notes: list[str] = []

        summary_risks = list((distillation_summary or {}).get("major_risks") or [])
        summary_notes = list((distillation_summary or {}).get("processing_notes") or [])
        review_target_id = "distillation_summary"

        if "fallback_distillation" in summary_risks:
            findings.append(
                CriticReviewFinding(
                    target_entry_id=review_target_id,
                    target_kind="distillation_summary",
                    finding_type="fallback_distillation",
                    severity="high",
                    reason="Current distillation used fallback extraction instead of the full LLM path.",
                    suggested_action="Manually review the main claims and quote attribution before downstream reuse.",
                )
            )

        if "llm_timeout" in summary_risks:
            findings.append(
                CriticReviewFinding(
                    target_entry_id=review_target_id,
                    target_kind="distillation_summary",
                    finding_type="llm_timeout",
                    severity="high",
                    reason="At least one LLM distillation attempt timed out and the run fell back to heuristic extraction.",
                    suggested_action="Treat the output as partial distillation and retry only when the gateway is stable.",
                )
            )

        if len(distilled_dimensions) < 2 and logic_blocks:
            findings.append(
                CriticReviewFinding(
                    target_entry_id=review_target_id,
                    target_kind="distillation_summary",
                    finding_type="sparse_dimension_coverage",
                    severity="medium",
                    reason="Dimension coverage is sparse relative to extracted logic blocks.",
                    suggested_action="Check whether the source text really supports only one theme or whether extraction missed dimensions.",
                )
            )

        claim_clusters: dict[str, list[str]] = defaultdict(list)
        for block in logic_blocks:
            entry_id = str(block.get("block_id") or "")
            confidence = float(block.get("confidence") or 0.0)
            risk_flags = list(block.get("risk_flags") or [])
            normalized_claim = str(block.get("normalized_claim") or "").strip().lower()
            if normalized_claim:
                claim_clusters[normalized_claim].append(entry_id)

            if confidence < 0.6:
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=entry_id or review_target_id,
                        target_kind="logic_block",
                        finding_type="low_confidence",
                        severity="medium" if confidence >= 0.45 else "high",
                        reason=f"Logic block confidence is low ({confidence:.2f}).",
                        suggested_action="Review whether the claim is actually supported by source text.",
                    )
                )

            if "quoted_content_present" in risk_flags:
                severity, reason, action = self._attribution_review_payload(block=block, risk_flags=risk_flags)
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=entry_id or review_target_id,
                        target_kind="logic_block",
                        finding_type="quoted_attribution",
                        severity=severity,
                        reason=reason,
                        suggested_action=action,
                    )
                )

            if "low_information_density" in risk_flags:
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=entry_id or review_target_id,
                        target_kind="logic_block",
                        finding_type="low_information_density",
                        severity="low",
                        reason="Logic block text is too short or too thin to be relied on directly.",
                        suggested_action="Use this block as supporting color only, not as a core thesis anchor.",
                    )
                )

        for normalized_claim, block_ids in claim_clusters.items():
            if normalized_claim and len(block_ids) >= 2:
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=block_ids[0],
                        target_kind="logic_block",
                        finding_type="duplicate_claim_cluster",
                        severity="low",
                        reason=f"Detected duplicated normalized claim across {len(block_ids)} logic blocks.",
                        suggested_action="Deduplicate overlapping claim blocks before persistence to long-term reviewer stats.",
                    )
                )

        for dimension in distilled_dimensions:
            entry_id = str(dimension.get("dimension_id") or dimension.get("dimension_name") or review_target_id)
            confidence = float(dimension.get("confidence") or 0.0)
            support_count = len(dimension.get("supported_claim_block_ids") or [])
            evidence_count = len(dimension.get("evidence_block_ids") or [])

            if confidence < 0.6:
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=entry_id,
                        target_kind="distilled_dimension",
                        finding_type="low_confidence",
                        severity="medium",
                        reason=f"Distilled dimension confidence is low ({confidence:.2f}).",
                        suggested_action="Review whether this dimension should remain a candidate or be dropped.",
                    )
                )
            if support_count == 0 and evidence_count <= 1:
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=entry_id,
                        target_kind="distilled_dimension",
                        finding_type="sparse_dimension_coverage",
                        severity="low",
                        reason="Dimension has weak support from both claim and evidence blocks.",
                        suggested_action="Keep this dimension as tentative until stronger evidence appears.",
                    )
                )

        severity_counter = Counter(finding.severity for finding in findings)
        type_counter = Counter(finding.finding_type for finding in findings)
        if summary_notes:
            notes.extend(summary_notes[:3])

        summary = CriticReviewSummary(
            review_required=bool(findings),
            finding_count=len(findings),
            high_severity_count=severity_counter.get("high", 0),
            medium_severity_count=severity_counter.get("medium", 0),
            low_severity_count=severity_counter.get("low", 0),
            finding_types=sorted(type_counter.keys()),
            notes=notes,
        )
        scorecard = self._build_scorecard(logic_blocks=logic_blocks, findings=findings, summary=summary.model_dump(mode="json"))
        return {
            "summary": summary.model_dump(mode="json"),
            "scorecard": scorecard,
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }

    def _build_scorecard(self, *, logic_blocks: list[dict], findings: list[CriticReviewFinding], summary: dict) -> dict:
        speaker_counts: dict[str, int] = {
            "author": 0,
            "reply": 0,
            "repost": 0,
            "reported": 0,
            "quoted": 0,
            "uncertain": 0,
        }
        for block in logic_blocks:
            speaker_type = str(block.get("speaker_type") or "")
            if speaker_type in speaker_counts:
                speaker_counts[speaker_type] += 1

        total_logic_blocks = max(len(logic_blocks), 1)
        quoted_like = (
            speaker_counts["reply"]
            + speaker_counts["repost"]
            + speaker_counts["reported"]
            + speaker_counts["quoted"]
            + speaker_counts["uncertain"]
        )
        review_strength = "high" if summary["high_severity_count"] > 0 else "medium" if summary["medium_severity_count"] > 0 else "low"
        attribution_risk = "high" if speaker_counts["repost"] > 0 else "medium" if quoted_like > 0 else "low"
        author_ratio = round(speaker_counts["author"] / total_logic_blocks, 4)
        quoted_ratio = round(quoted_like / total_logic_blocks, 4)
        setup_label = "author_led" if author_ratio >= 0.7 else "mixed_attribution" if author_ratio >= 0.4 else "quote_dominated"

        return {
            "total_logic_blocks": len(logic_blocks),
            "speaker_type_counts": speaker_counts,
            "author_ratio": author_ratio,
            "quoted_like_ratio": quoted_ratio,
            "review_strength": review_strength,
            "attribution_risk": attribution_risk,
            "setup_label": setup_label,
            "summary": (
                f"attribution={setup_label}; author_ratio={author_ratio}; "
                f"quoted_like_ratio={quoted_ratio}; review_strength={review_strength}"
            ),
            "finding_types": summary.get("finding_types", []),
        }

    def _attribution_review_payload(self, *, block: dict, risk_flags: list[str]) -> tuple[str, str, str]:
        speaker_type = str(block.get("speaker_type") or "")
        attribution_label = str(block.get("attribution_label") or "")

        if speaker_type == "reply" or "reply_attribution" in risk_flags:
            return (
                "medium",
                "Logic block appears to be a reply to another user, so the stance may depend on prior context.",
                "Review the upstream conversation before treating this as a standalone KOL thesis.",
            )
        if speaker_type == "repost" or "repost_attribution" in risk_flags:
            return (
                "high",
                "Logic block appears to be reposted or forwarded content rather than the author's original statement.",
                "Trace the original source before counting this block as first-hand viewpoint evidence.",
            )
        if speaker_type == "reported" or "reported_attribution" in risk_flags:
            return (
                "medium",
                "Logic block is reported speech or paraphrase, so wording may not reflect the source precisely.",
                "Check the original referenced statement and avoid over-interpreting paraphrased claims.",
            )
        if speaker_type == "quoted" or attribution_label == "explicit_quote" or "explicit_quote_marker" in risk_flags:
            return (
                "medium",
                "Logic block contains explicit quoted content and may not represent the author's own viewpoint.",
                "Keep the quote/source distinction explicit in downstream summaries and review the original wording.",
            )
        return (
            "medium",
            "Logic block may contain quoted or forwarded content rather than the author's own view.",
            "Confirm speaker attribution before counting it as a primary KOL viewpoint.",
        )
