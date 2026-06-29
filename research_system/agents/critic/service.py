from __future__ import annotations

from collections import Counter, defaultdict

from research_system.schemas.review import CriticReviewFinding, CriticReviewSummary
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.critic_review import (
    CriticScorecardBuilderSubagent,
    CriticSummaryBuilderSubagent,
    DimensionCriticSubagent,
    LogicBlockCriticSubagent,
    SummaryRiskCriticSubagent,
)
from research_system.subagents.registry import SubagentRegistry


class CriticService:
    agent_name = "critic"

    def __init__(self) -> None:
        self.registry = SubagentRegistry(
            [
                SummaryRiskCriticSubagent(),
                LogicBlockCriticSubagent(attribution_review_payload=self._attribution_review_payload),
                DimensionCriticSubagent(),
                CriticSummaryBuilderSubagent(),
                CriticScorecardBuilderSubagent(),
            ]
        )

    def run(
        self,
        *,
        distillation_summary: dict | None,
        logic_blocks: list[dict],
        distilled_dimensions: list[dict],
    ) -> dict:
        findings: list[dict] = []
        notes: list[str] = []

        summary_notes = list((distillation_summary or {}).get("processing_notes") or [])
        if summary_notes:
            notes.extend(summary_notes[:3])

        risk_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="summary_risk_critic",
                objective="Check summary-level distillation risks.",
                inputs={"distillation_summary": distillation_summary},
            )
        )
        findings.extend(risk_outputs["findings"])
        logic_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="logic_block_critic",
                objective="Check logic-block quality and attribution risks.",
                inputs={"logic_blocks": logic_blocks},
            )
        )
        findings.extend(logic_outputs["findings"])
        dimension_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="dimension_critic",
                objective="Check dimension coverage and confidence.",
                inputs={"distilled_dimensions": distilled_dimensions, "logic_blocks": logic_blocks},
            )
        )
        findings.extend(dimension_outputs["findings"])
        summary_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="critic_summary_builder",
                objective="Build critic summary from findings.",
                inputs={"findings": findings, "notes": notes},
            )
        )
        scorecard_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="critic_scorecard_builder",
                objective="Build critic scorecard.",
                inputs={"logic_blocks": logic_blocks, "summary": summary_outputs["summary"]},
            )
        )
        return {
            "summary": summary_outputs["summary"],
            "scorecard": scorecard_outputs["scorecard"],
            "findings": summary_outputs["findings"],
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
