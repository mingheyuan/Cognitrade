from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from research_system.schemas.review import PosteriorReviewSummary, ReviewFinding
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy


class ReviewStrategyResolverInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    planning_context_hints: dict[str, str] = Field(default_factory=dict)


class ReviewStrategyResolverOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_planning_context_hints: dict[str, str] = Field(default_factory=dict)
    review_strategy: dict[str, Any] = Field(default_factory=dict)


class ReviewClaimExtractorInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_entries: list[dict[str, Any]] = Field(default_factory=list)


class ReviewClaimExtractorOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[dict[str, Any]] = Field(default_factory=list)
    claim_type_counts: dict[str, int] = Field(default_factory=dict)


class ReviewContextResolverInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_sector_snapshot: dict[str, Any] | None = None
    latest_market_snapshot: dict[str, Any] | None = None
    latest_fundamental_snapshot: dict[str, Any] | None = None
    latest_notice_snapshot: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    default_benchmark_name: str = "HS300"
    default_benchmark_symbol: str = "000300"


class ReviewContextResolverOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector_snapshot_available: bool = False
    review_required: bool = False
    notes: list[str] = Field(default_factory=list)
    review_context: dict[str, Any] = Field(default_factory=dict)


class ReviewFocusValidatorInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_strategy: dict[str, Any] = Field(default_factory=dict)
    latest_market_snapshot: dict[str, Any] | None = None
    latest_fundamental_snapshot: dict[str, Any] | None = None
    latest_notice_snapshot: dict[str, Any] | None = None
    policy_signals: list[dict[str, Any]] = Field(default_factory=list)
    regulatory_signals: list[dict[str, Any]] = Field(default_factory=list)


class ReviewFocusValidatorOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_required: bool = False
    notes: list[str] = Field(default_factory=list)


class ReviewCandidateVerdictInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_run_id: str
    candidate: dict[str, Any] = Field(default_factory=dict)
    latest_market_snapshot: dict[str, Any] | None = None
    latest_fundamental_snapshot: dict[str, Any] | None = None
    latest_notice_snapshot: dict[str, Any] | None = None
    review_context: dict[str, Any] = Field(default_factory=dict)


class ReviewCandidateVerdictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: ReviewFinding


class ReviewSummaryBuilderInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_required: bool = False
    findings: list[ReviewFinding] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    claim_type_counts: dict[str, int] = Field(default_factory=dict)


class ReviewSummaryBuilderOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: PosteriorReviewSummary


class ReviewStrategyResolverSubagent:
    subagent_type = "review_strategy_resolver"
    input_model = ReviewStrategyResolverInputModel
    output_model = ReviewStrategyResolverOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Resolve posterior review focus and required snapshot inputs from query and planning hints.",
        capabilities=("review_strategy", "review_planning"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("review_thesis",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(
        self,
        *,
        normalize_planning_context_hints: Callable[[Any], dict[str, str]],
        derive_review_strategy: Callable[..., dict[str, Any]],
    ) -> None:
        self.normalize_planning_context_hints = normalize_planning_context_hints
        self.derive_review_strategy = derive_review_strategy

    def run(self, *, query: str, planning_context_hints: dict[str, str] | None = None) -> dict[str, Any]:
        normalized = self.normalize_planning_context_hints(planning_context_hints)
        review_strategy = self.derive_review_strategy(
            query=query,
            planning_context_hints=normalized,
        )
        return {
            "normalized_planning_context_hints": normalized,
            "review_strategy": review_strategy,
        }


class ReviewClaimExtractorSubagent:
    subagent_type = "review_claim_extractor"
    input_model = ReviewClaimExtractorInputModel
    output_model = ReviewClaimExtractorOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Extract reviewable claims from prior run artifacts and compute claim-type counts.",
        capabilities=("review_claim_extraction", "candidate_building"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("review_thesis",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(self, *, extract_review_claims: Callable[..., dict[str, Any]]) -> None:
        self.extract_review_claims = extract_review_claims

    def run(self, *, source_entries: list[dict[str, Any]]) -> dict[str, Any]:
        return self.extract_review_claims(source_entries=source_entries)


class ReviewContextResolverSubagent:
    subagent_type = "review_context_resolver"
    input_model = ReviewContextResolverInputModel
    output_model = ReviewContextResolverOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Resolve benchmark, anchor date, and sector evidence context needed for posterior review verdicts.",
        capabilities=("review_context", "benchmark_resolution"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("review_thesis",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(
        self,
        *,
        resolve_benchmark: Callable[..., dict[str, Any]],
        parse_anchor_date: Callable[..., str],
        to_float: Callable[[Any], float | None],
    ) -> None:
        self.resolve_benchmark = resolve_benchmark
        self.parse_anchor_date = parse_anchor_date
        self.to_float = to_float

    def run(
        self,
        *,
        latest_sector_snapshot: dict[str, Any] | None,
        latest_market_snapshot: dict[str, Any] | None = None,
        latest_fundamental_snapshot: dict[str, Any] | None = None,
        latest_notice_snapshot: dict[str, Any] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        default_benchmark_name: str = "HS300",
        default_benchmark_symbol: str = "000300",
    ) -> dict[str, Any]:
        _ = latest_fundamental_snapshot
        notes: list[str] = []
        review_context: dict[str, Any] = {
            "latest_hist": {},
            "excess_metric": None,
            "policy_signals": [],
            "regulatory_signals": [],
            "theme_news": [],
            "catalyst_digest": {},
            "setup_label": "",
            "sector_name": "",
            "anchor_date": "",
            "benchmark_scope": "",
            "benchmark_name": default_benchmark_name,
            "benchmark_symbol": default_benchmark_symbol,
            "observed_benchmark_metric": None,
        }
        if latest_sector_snapshot is None:
            notes.append("missing_sector_snapshot")
            return {
                "sector_snapshot_available": False,
                "review_required": True,
                "notes": notes,
                "review_context": review_context,
            }

        facts = latest_sector_snapshot.get("facts") or {}
        relative_strength = facts.get("relative_strength") or {}
        signal_scorecard = facts.get("signal_scorecard") or {}
        benchmark = facts.get("benchmark") or {}
        latest_hist = facts.get("latest_hist") or {}
        catalyst_digest = facts.get("catalyst_digest") or {}
        policy_signals = list(facts.get("policy_signals") or [])
        regulatory_signals = list(facts.get("regulatory_signals") or [])
        theme_news = list(facts.get("theme_news") or [])
        benchmark_outputs = self.resolve_benchmark(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_name=str(benchmark.get("benchmark_name") or default_benchmark_name),
            default_benchmark_symbol=str(benchmark.get("benchmark_code") or default_benchmark_symbol),
        )
        review_context.update(
            {
                "latest_hist": latest_hist,
                "excess_metric": self.to_float(relative_strength.get("latest_excess_pct_change")),
                "policy_signals": policy_signals,
                "regulatory_signals": regulatory_signals,
                "theme_news": theme_news,
                "catalyst_digest": catalyst_digest,
                "setup_label": str(signal_scorecard.get("setup_label") or ""),
                "sector_name": str(latest_sector_snapshot.get("sector") or ""),
                "anchor_date": self.parse_anchor_date(
                    candidates=list(candidates or []),
                    snapshot_date=str(latest_sector_snapshot.get("snapshot_date") or ""),
                ),
                "benchmark_scope": benchmark_outputs["benchmark_scope"],
                "benchmark_name": benchmark_outputs["benchmark_name"],
                "benchmark_symbol": benchmark_outputs["benchmark_symbol"],
                "observed_benchmark_metric": benchmark_outputs["observed_benchmark_metric"],
            }
        )
        return {
            "sector_snapshot_available": True,
            "review_required": False,
            "notes": notes,
            "review_context": review_context,
        }


class ReviewFocusValidatorSubagent:
    subagent_type = "review_focus_validator"
    input_model = ReviewFocusValidatorInputModel
    output_model = ReviewFocusValidatorOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Validate whether the currently available review snapshots satisfy the requested review focus.",
        capabilities=("review_focus_validation", "review_requirements"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("review_thesis",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(self, *, validate_review_strategy_requirements: Callable[..., dict[str, Any]]) -> None:
        self.validate_review_strategy_requirements = validate_review_strategy_requirements

    def run(
        self,
        *,
        review_strategy: dict[str, Any],
        latest_market_snapshot: dict[str, Any] | None = None,
        latest_fundamental_snapshot: dict[str, Any] | None = None,
        latest_notice_snapshot: dict[str, Any] | None = None,
        policy_signals: list[dict[str, Any]] | None = None,
        regulatory_signals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.validate_review_strategy_requirements(
            review_strategy=review_strategy,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            policy_signals=list(policy_signals or []),
            regulatory_signals=list(regulatory_signals or []),
        )


class ReviewCandidateVerdictSubagent:
    subagent_type = "review_candidate_verdict"
    input_model = ReviewCandidateVerdictInputModel
    output_model = ReviewCandidateVerdictOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=4)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Review one extracted claim candidate against benchmark-relative evidence and snapshot context.",
        capabilities=("review_candidate_verdict", "posterior_verdict"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("review_thesis",),
        tags=("deterministic", "parallel_fanout"),
    )

    def __init__(self, *, review_candidate: Callable[..., dict[str, Any]]) -> None:
        self.review_candidate = review_candidate

    def run(
        self,
        *,
        source_run_id: str,
        candidate: dict[str, Any],
        latest_market_snapshot: dict[str, Any] | None = None,
        latest_fundamental_snapshot: dict[str, Any] | None = None,
        latest_notice_snapshot: dict[str, Any] | None = None,
        review_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(review_context or {})
        return self.review_candidate(
            candidate=candidate,
            source_run_id=source_run_id,
            anchor_date=str(context.get("anchor_date") or ""),
            benchmark_scope=str(context.get("benchmark_scope") or ""),
            benchmark_symbol=str(context.get("benchmark_symbol") or ""),
            benchmark_name=str(context.get("benchmark_name") or ""),
            observed_benchmark_metric=context.get("observed_benchmark_metric"),
            latest_hist=dict(context.get("latest_hist") or {}),
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
            latest_notice_snapshot=latest_notice_snapshot,
            excess_metric=context.get("excess_metric"),
            policy_signals=list(context.get("policy_signals") or []),
            regulatory_signals=list(context.get("regulatory_signals") or []),
            theme_news=list(context.get("theme_news") or []),
            setup_label=str(context.get("setup_label") or ""),
            sector_name=str(context.get("sector_name") or ""),
            catalyst_digest=dict(context.get("catalyst_digest") or {}),
        )


class ReviewSummaryBuilderSubagent:
    subagent_type = "review_summary_builder"
    input_model = ReviewSummaryBuilderInputModel
    output_model = ReviewSummaryBuilderOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Aggregate posterior review findings into one summary payload and overall verdict.",
        capabilities=("review_summary", "posterior_review"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("review_thesis",),
        tags=("deterministic", "postprocess"),
    )

    def __init__(self, *, build_review_summary: Callable[..., dict[str, Any]]) -> None:
        self.build_review_summary = build_review_summary

    def run(
        self,
        *,
        review_required: bool,
        findings: list[ReviewFinding],
        candidates: list[dict[str, Any]],
        notes: list[str],
        claim_type_counts: dict[str, int],
    ) -> dict[str, Any]:
        return self.build_review_summary(
            review_required=review_required,
            findings=findings,
            candidates=candidates,
            notes=notes,
            claim_type_counts=claim_type_counts,
        )


class ScreenReviewStrategyResolverInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[dict[str, Any]] = Field(default_factory=list)
    scorecard: dict[str, Any] | None = None
    planning_context_hints: dict[str, str] = Field(default_factory=dict)


class ScreenReviewStrategyResolverOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_planning_context_hints: dict[str, str] = Field(default_factory=dict)
    review_strategy: dict[str, Any] = Field(default_factory=dict)


class ScreenReviewCandidateInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_run_id: str
    candidate: dict[str, Any] = Field(default_factory=dict)


class ScreenReviewCandidateOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: ReviewFinding


class ScreenReviewSummaryBuilderInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    scorecard: dict[str, Any] | None = None


class ScreenReviewSummaryBuilderOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: PosteriorReviewSummary


class ScreenReviewStrategyResolverSubagent:
    subagent_type = "screen_review_strategy_resolver"
    input_model = ScreenReviewStrategyResolverInputModel
    output_model = ScreenReviewStrategyResolverOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Resolve lightweight screen-review focus from candidates, scorecard, and planning hints.",
        capabilities=("screen_review_strategy", "review_planning"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_screen_review",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(
        self,
        *,
        normalize_planning_context_hints: Callable[[Any], dict[str, str]],
        derive_review_strategy: Callable[..., dict[str, object]],
    ) -> None:
        self.normalize_planning_context_hints = normalize_planning_context_hints
        self.derive_review_strategy = derive_review_strategy

    def run(
        self,
        *,
        candidates: list[dict[str, Any]],
        scorecard: dict[str, Any] | None = None,
        planning_context_hints: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized = self.normalize_planning_context_hints(planning_context_hints)
        review_strategy = self.derive_review_strategy(
            candidates=candidates,
            scorecard=scorecard,
            planning_context_hints=normalized,
        )
        return {
            "normalized_planning_context_hints": normalized,
            "review_strategy": review_strategy,
        }


class ScreenReviewCandidateSubagent:
    subagent_type = "screen_review_candidate"
    input_model = ScreenReviewCandidateInputModel
    output_model = ScreenReviewCandidateOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=6)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Review one screened candidate for evidence completeness and caveats.",
        capabilities=("screen_review_candidate", "posterior_verdict"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_screen_review",),
        tags=("deterministic", "parallel_fanout"),
    )

    def __init__(self, *, build_finding: Callable[..., dict[str, Any]]) -> None:
        self.build_finding = build_finding

    def run(self, *, source_run_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "finding": self.build_finding(
                source_run_id=source_run_id,
                candidate=candidate,
            )
        }


class ScreenReviewSummaryBuilderSubagent:
    subagent_type = "screen_review_summary_builder"
    input_model = ScreenReviewSummaryBuilderInputModel
    output_model = ScreenReviewSummaryBuilderOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Aggregate screen-review findings into a lightweight posterior review summary.",
        capabilities=("screen_review_summary", "posterior_review"),
        stage="review",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_screen_review",),
        tags=("deterministic", "postprocess"),
    )

    def __init__(self, *, build_review_summary: Callable[..., dict[str, Any]]) -> None:
        self.build_review_summary = build_review_summary

    def run(
        self,
        *,
        candidates: list[dict[str, Any]],
        findings: list[ReviewFinding],
        notes: list[str],
        scorecard: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "summary": self.build_review_summary(
                candidates=candidates,
                findings=[finding.model_dump(mode="python") for finding in findings],
                notes=notes,
                scorecard=scorecard,
            )
        }
