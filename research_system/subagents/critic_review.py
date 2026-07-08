from __future__ import annotations

from collections import Counter, defaultdict

from pydantic import BaseModel, ConfigDict, Field

from research_system.schemas.review import CriticReviewFinding, CriticReviewSummary
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy


class CriticDistillationSummaryInputModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content_type: str | None = None
    overall_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = False
    major_risks: list[str] = Field(default_factory=list)
    processing_notes: list[str] = Field(default_factory=list)


class SummaryRiskCriticInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distillation_summary: CriticDistillationSummaryInputModel | None = None


class CriticFindingsOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[CriticReviewFinding] = Field(default_factory=list)


class CriticLogicBlockInputItemModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    block_id: str | None = None
    speaker_type: str = "author"
    attribution_label: str | None = None
    normalized_claim: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LogicBlockCriticInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logic_blocks: list[CriticLogicBlockInputItemModel] = Field(default_factory=list)


class CriticDimensionInputItemModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimension_id: str | None = None
    dimension_name: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supported_claim_block_ids: list[str] = Field(default_factory=list)
    evidence_block_ids: list[str] = Field(default_factory=list)


class DimensionCriticInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distilled_dimensions: list[CriticDimensionInputItemModel] = Field(default_factory=list)
    logic_blocks: list[CriticLogicBlockInputItemModel] = Field(default_factory=list)


class CriticSummaryBuilderInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[CriticReviewFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CriticStrategyResolverInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distillation_summary: CriticDistillationSummaryInputModel | None = None
    logic_blocks: list[CriticLogicBlockInputItemModel] = Field(default_factory=list)
    distilled_dimensions: list[CriticDimensionInputItemModel] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CriticStrategyResolverOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_strategy: dict[str, object] = Field(default_factory=dict)


class CriticSummaryBuilderOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: CriticReviewSummary
    findings: list[CriticReviewFinding] = Field(default_factory=list)


class SummaryRiskCriticSubagent:
    subagent_type = "summary_risk_critic"
    input_model = SummaryRiskCriticInputModel
    output_model = CriticFindingsOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Review run-level distillation summary risks and emit critic findings.",
        capabilities=("summary_risk_review", "critic_review"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic", "critic_review"),
        tags=("deterministic", "parallel_candidate"),
    )

    def run(self, *, distillation_summary: CriticDistillationSummaryInputModel | None) -> dict:
        findings: list[CriticReviewFinding] = []
        summary_risks = list((distillation_summary.major_risks if distillation_summary else []) or [])
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
        return {
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }


class LogicBlockCriticSubagent:
    subagent_type = "logic_block_critic"
    input_model = LogicBlockCriticInputModel
    output_model = CriticFindingsOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Review logic-block attribution, confidence, and duplicate-claim risks.",
        capabilities=("logic_block_review", "critic_review"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic", "critic_review"),
        tags=("deterministic", "parallel_candidate"),
    )

    def __init__(self, *, attribution_review_payload) -> None:
        self.attribution_review_payload = attribution_review_payload

    def run(self, *, logic_blocks: list[CriticLogicBlockInputItemModel]) -> dict:
        findings: list[CriticReviewFinding] = []
        claim_clusters: dict[str, list[str]] = defaultdict(list)
        for block in logic_blocks:
            entry_id = str(block.block_id or "")
            confidence = float(block.confidence or 0.0)
            risk_flags = list(block.risk_flags or [])
            normalized_claim = str(block.normalized_claim or "").strip().lower()
            if normalized_claim:
                claim_clusters[normalized_claim].append(entry_id)

            if confidence < 0.6:
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=entry_id or "distillation_summary",
                        target_kind="logic_block",
                        finding_type="low_confidence",
                        severity="medium" if confidence >= 0.45 else "high",
                        reason=f"Logic block confidence is low ({confidence:.2f}).",
                        suggested_action="Review whether the claim is actually supported by source text.",
                    )
                )
            if "quoted_content_present" in risk_flags:
                severity, reason, action = self.attribution_review_payload(
                    block=block.model_dump(mode="python"),
                    risk_flags=risk_flags,
                )
                findings.append(
                    CriticReviewFinding(
                        target_entry_id=entry_id or "distillation_summary",
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
                        target_entry_id=entry_id or "distillation_summary",
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
        return {
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }


class DimensionCriticSubagent:
    subagent_type = "dimension_critic"
    input_model = DimensionCriticInputModel
    output_model = CriticFindingsOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Review dimension coverage and confidence quality for distilled themes.",
        capabilities=("dimension_review", "critic_review"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic", "critic_review"),
        tags=("deterministic", "parallel_candidate"),
    )

    def run(
        self,
        *,
        distilled_dimensions: list[CriticDimensionInputItemModel],
        logic_blocks: list[CriticLogicBlockInputItemModel],
    ) -> dict:
        findings: list[CriticReviewFinding] = []
        if len(distilled_dimensions) < 2 and logic_blocks:
            findings.append(
                CriticReviewFinding(
                    target_entry_id="distillation_summary",
                    target_kind="distillation_summary",
                    finding_type="sparse_dimension_coverage",
                    severity="medium",
                    reason="Dimension coverage is sparse relative to extracted logic blocks.",
                    suggested_action="Check whether the source text really supports only one theme or whether extraction missed dimensions.",
                )
            )
        for dimension in distilled_dimensions:
            entry_id = str(dimension.dimension_id or dimension.dimension_name or "distillation_summary")
            confidence = float(dimension.confidence or 0.0)
            support_count = len(dimension.supported_claim_block_ids or [])
            evidence_count = len(dimension.evidence_block_ids or [])
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
        return {
            "findings": [finding.model_dump(mode="json") for finding in findings],
        }


class CriticStrategyResolverSubagent:
    subagent_type = "critic_strategy_resolver"
    input_model = CriticStrategyResolverInputModel
    output_model = CriticStrategyResolverOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Resolve critic-review execution strategy from distillation artifacts.",
        capabilities=("critic_strategy", "critic_review"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic", "critic_review"),
        tags=("deterministic", "preprocess"),
    )

    def run(
        self,
        *,
        distillation_summary: CriticDistillationSummaryInputModel | None,
        logic_blocks: list[CriticLogicBlockInputItemModel],
        distilled_dimensions: list[CriticDimensionInputItemModel],
        notes: list[str],
    ) -> dict:
        summary_risks = list((distillation_summary.major_risks if distillation_summary else []) or [])
        include_logic_block_review = bool(logic_blocks)
        include_dimension_review = bool(distilled_dimensions or logic_blocks)
        review_mode = "full_parallel" if include_logic_block_review or include_dimension_review else "summary_only"
        strategy_notes: list[str] = []
        if summary_risks:
            strategy_notes.append(f"summary_risk_count={len(summary_risks)}")
        if not include_logic_block_review:
            strategy_notes.append("skip_logic_block_review")
        if not include_dimension_review:
            strategy_notes.append("skip_dimension_review")
        if notes:
            strategy_notes.extend(str(item) for item in notes[:3] if str(item).strip())
        return {
            "review_strategy": {
                "review_mode": review_mode,
                "include_summary_risk_review": True,
                "include_logic_block_review": include_logic_block_review,
                "include_dimension_review": include_dimension_review,
                "notes": strategy_notes,
            }
        }


class CriticSummaryBuilderSubagent:
    subagent_type = "critic_summary_builder"
    input_model = CriticSummaryBuilderInputModel
    output_model = CriticSummaryBuilderOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Build critic review summary and normalize findings into one scoreable bundle.",
        capabilities=("critic_summary", "critic_review"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("distill_kol_logic", "critic_review"),
        tags=("deterministic", "postprocess"),
    )

    def run(self, *, findings: list[CriticReviewFinding], notes: list[str]) -> dict:
        typed_findings = [CriticReviewFinding.model_validate(item) for item in findings]
        severity_counter = Counter(finding.severity for finding in typed_findings)
        type_counter = Counter(finding.finding_type for finding in typed_findings)
        summary = CriticReviewSummary(
            review_required=bool(typed_findings),
            finding_count=len(typed_findings),
            high_severity_count=severity_counter.get("high", 0),
            medium_severity_count=severity_counter.get("medium", 0),
            low_severity_count=severity_counter.get("low", 0),
            finding_types=sorted(type_counter.keys()),
            notes=notes,
        )
        return {
            "summary": summary.model_dump(mode="json"),
            "findings": [finding.model_dump(mode="json") for finding in typed_findings],
        }
