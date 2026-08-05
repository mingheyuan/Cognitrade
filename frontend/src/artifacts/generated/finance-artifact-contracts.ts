// Run: python scripts/generate_finance_artifact_contracts.py
// Renderer ownership and field coverage are intentionally not generated.

export const FINANCE_ARTIFACT_TYPES = [
  "user_request",
  "normalized_query",
  "stock_snapshot_bundle",
  "snapshot",
  "evidence_set",
  "financial_analysis",
  "screening_result",
  "sector_analysis",
  "thesis_review",
  "source_record_set",
  "distillation_result",
  "distillation_review",
  "research_report",
  "main_agent_synthesis",
  "valuation_model",
  "investor_panel_review",
  "risk_scan",
  "report_presentation",
  "institutional_analysis",
  "research_workflow",
  "deep_analysis",
  "uzi_dimension_coverage",
  "fund_snapshot",
  "fund_analysis",
  "ai_readiness",
  "earnings_preview",
  "model_update",
  "returns_attribution",
  "holding_rebalance",
  "portfolio_rebalance",
  "three_statement_model",
  "quick_lbo",
  "accretion_dilution",
  "segmental_model",
  "initiation_report",
  "earnings_review",
  "catalyst_calendar",
  "thesis_tracker",
  "morning_note",
  "ic_memo",
  "porter_bcg",
  "dd_checklist",
  "unit_economics",
  "value_creation_plan",
] as const

export type FinanceArtifactType = typeof FINANCE_ARTIFACT_TYPES[number]

export type UserRequestContent = {
  "query": string;
  "symbols"?: Array<string>;
  "fund_codes"?: Array<string>;
  "fund_category"?: string | null;
  "portfolio_holdings"?: Array<Record<string, unknown>>;
  "benchmark_return"?: number | null;
  "rebalance_targets"?: Record<string, unknown>;
  "asset_class_targets"?: Record<string, unknown>;
  "drift_threshold"?: number | null;
  "fee_model"?: Record<string, unknown>;
  "requested_methods"?: Array<string>;
  "model_updates"?: Record<string, unknown>;
  "method_assumptions"?: Record<string, unknown>;
  "sector"?: string | null;
  "source_records"?: Array<Record<string, unknown>>;
  "snapshots"?: Array<Record<string, unknown>>;
  "source_refs"?: Array<Record<string, unknown>>;
}

export type NormalizedQueryContent = {
  "query": string;
  "symbols": Array<string>;
  "fund_codes"?: Array<string>;
  "fund_category"?: string | null;
  "portfolio_holdings"?: Array<Record<string, unknown>>;
  "benchmark_return"?: number | null;
  "rebalance_targets"?: Record<string, unknown> | null;
  "drift_threshold"?: number | null;
  "fee_model"?: Record<string, unknown> | null;
  "asset_class_targets"?: Record<string, unknown> | null;
  "requested_methods"?: Array<string>;
  "method_assumptions"?: Record<string, unknown> | null;
}

export type StockSnapshotBundleContent = {
  "query": string | null;
  "symbols": Array<string>;
  "sector"?: string | null;
  "snapshots": Array<Record<string, unknown>>;
  "candidate_universe": Array<Record<string, unknown>>;
  "collection": {
    "universe_source": string;
    "candidate_count": number;
    "requested_snapshot_types": Array<string>;
    "collected_snapshot_types": Array<string>;
    "enrichment_snapshot_types"?: Array<string>;
    "enrichment_targets": Array<string>;
    "degraded"?: boolean;
  };
  "review_target_run_id"?: string | null;
  "review_target_artifact_ids"?: Array<string>;
  "snapshot_keys"?: Array<string>;
}

export type SnapshotContent = {
  "snapshot_type": string;
  "scope": "symbol" | "sector" | "universe";
  "symbol"?: string | null;
  "sector"?: string | null;
  "query"?: string | null;
  "snapshot_date"?: string | null;
  "data_period"?: string | null;
  "retrieved_at"?: string | null;
  "quality_status": "available" | "degraded" | "data_gap" | "assumption_only";
  "missing_fields"?: Array<string>;
  "degrade_reasons"?: Array<string>;
  "facts": Record<string, unknown>;
  "source"?: Record<string, unknown>;
  "source_refs"?: Array<Record<string, unknown>>;
}

export type EvidenceSetContent = {
  "items": Array<unknown>;
  "symbols"?: Array<string>;
  "query": string | null;
}

export type FinancialAnalysisContent = {
  "symbols": Array<unknown>;
  "methodologies": Array<unknown>;
  "scorecards": Array<unknown>;
  "summary": string;
}

export type ScreeningResultContent = {
  "screened_candidates": Array<unknown>;
  "watchlist_candidates": Array<unknown>;
  "enrichment_targets": Array<unknown>;
  "scorecard": Record<string, unknown>;
  "screening_notes": Array<unknown>;
  "methodology"?: Record<string, unknown>;
}

export type SectorAnalysisContent = {
  "sector": string | null;
  "theme": string | null;
  "findings": Array<unknown>;
  "related_snapshots": Array<unknown>;
  "risks": Array<unknown>;
  "source_count": number;
  "analysis_notes": Array<unknown>;
  "sector_heatmap"?: Array<unknown>;
  "constituents"?: Array<unknown>;
  "breadth"?: Record<string, unknown>;
  "relative_strength"?: Record<string, unknown>;
  "signal_scorecard"?: Record<string, unknown>;
  "catalysts"?: Record<string, unknown>;
  "policy_snapshots"?: Array<unknown>;
}

export type ThesisReviewContent = {
  "verdict": string;
  "overall_verdict": string;
  "findings": Array<unknown>;
  "strengths": Array<unknown>;
  "weaknesses": Array<unknown>;
  "follow_up": Array<unknown>;
  "review_target_run_id"?: string | null;
  "review_target_artifact_ids"?: Array<unknown>;
  "review_target_found"?: boolean;
  "target_thesis"?: Array<unknown>;
  "evidence_comparison"?: Record<string, unknown>;
}

export type SourceRecordSetContent = {
  "query"?: string;
  "records": Array<Record<string, unknown>>;
}

export type DistillationResultContent = {
  "dimensions": Array<Record<string, unknown>>;
  "summary": Record<string, unknown>;
}

export type DistillationReviewContent = {
  "verdict": string;
  "findings": Array<unknown>;
  "distillation": Record<string, unknown>;
}

export type ResearchReportContent = {
  "conclusion": string;
  "assessment"?: Record<string, unknown> | null;
  "evidence": Array<unknown>;
  "risk": Array<unknown>;
  "follow_up": Array<unknown>;
  "methodologies"?: Array<Record<string, unknown>>;
  "scorecard"?: Record<string, unknown> | null;
  "screening_scorecard"?: Record<string, unknown> | null;
  "uzi_profile"?: Record<string, unknown>;
  "valuation_model"?: Record<string, unknown>;
  "investor_panel_review"?: Record<string, unknown>;
  "risk_scan"?: Record<string, unknown>;
  "institutional_analysis"?: Record<string, unknown> | null;
  "research_workflow"?: Record<string, unknown> | null;
  "deep_analysis"?: Record<string, unknown> | null;
  "uzi_dimension_coverage"?: Record<string, unknown> | null;
  "presentation_artifact_type"?: string;
  "fund_analysis"?: Record<string, unknown>;
  "quality_status"?: string | null;
  "delivery_type"?: string;
  "candidates"?: Array<Record<string, unknown>>;
  "screening"?: Record<string, unknown>;
  "sector_analysis"?: Record<string, unknown>;
  "sector_heatmap"?: Array<Record<string, unknown>>;
  "thesis_review"?: Record<string, unknown>;
  "review_summary"?: Record<string, unknown>;
}

export type MainAgentSynthesisContent = {
  "conclusion": string;
  "assistant_message": string;
  "follow_up"?: Array<string>;
  "source_artifact_id": string;
  "model_generated"?: boolean;
}

export type ValuationModelContent = {
  "methodology_version": string;
  "profile": string;
  "models": Array<Record<string, unknown>>;
  "primary_symbol": string | null;
  "methodologies": Array<string>;
  "dimensions"?: Record<string, unknown>;
  "quality_status"?: string;
  "method_input_contract"?: Record<string, unknown>;
}

export type InvestorPanelReviewContent = {
  "methodology_version": string;
  "profile": string;
  "scope": string;
  "panel_version": string;
  "source_commit"?: string;
  "registered_seat_count"?: number;
  "rule_mode"?: string;
  "votes": Array<Record<string, unknown>>;
  "distribution": Record<string, unknown>;
  "average_score"?: number | null;
  "evaluated_rule_count"?: number;
  "skipped_rule_count"?: number;
  "quality_status"?: string;
  "data_gap"?: Array<string>;
  "disagreement": Record<string, unknown>;
  "confidence": number;
  "risk_flags": Array<string>;
}

export type RiskScanContent = {
  "methodology_version": string;
  "profile": string;
  "symbol": string | null;
  "risk_level": string;
  "signals": Array<Record<string, unknown>>;
  "signals_hit_count": number;
  "recommendation": string;
  "method_input_contract"?: Record<string, unknown>;
  "quality_status"?: string;
  "confidence": number;
  "risk_flags": Array<string>;
}

export type ReportPresentationContent = {
  "version": string;
  "theme": string;
  "profile": string;
  "subject": Record<string, unknown>;
  "hero": Record<string, unknown>;
  "core_conclusion": string;
  "dashboard": Record<string, unknown>;
  "great_divide": Record<string, unknown>;
  "panel": Record<string, unknown>;
  "radar": Array<unknown>;
  "deep_scan": Array<Record<string, unknown>>;
  "panel_insights": Array<Record<string, unknown>>;
  "risk_list": Array<unknown>;
  "buy_zones": Array<unknown>;
  "institutional_modeling": Array<Record<string, unknown>>;
  "advanced_methods"?: {
    "institutional_analysis"?: Record<string, unknown> | null;
    "research_workflow"?: Record<string, unknown> | null;
    "deep_analysis"?: Record<string, unknown> | null;
    "uzi_dimension_coverage"?: Record<string, unknown> | null;
  };
  "source_artifact_ids": Array<string>;
  "generated_at": string;
  "data_gap": Array<string>;
}

export type InstitutionalAnalysisContent = {
  "methodology_version": string;
  "profile": string;
  "methods"?: Array<unknown>;
  "results": Array<unknown>;
  "data_gap"?: Array<unknown>;
  "confidence": number;
  "risk_flags": Array<string>;
}

export type ResearchWorkflowContent = {
  "methodology_version": string;
  "profile": string;
  "results": Array<unknown>;
  "summary"?: string;
  "confidence": number;
  "risk_flags": Array<string>;
}

export type DeepAnalysisContent = {
  "methodology_version": string;
  "profile": string;
  "results": Array<unknown>;
  "summary"?: string;
  "confidence": number;
  "risk_flags": Array<string>;
}

export type UziDimensionCoverageContent = {
  "methodology_version": string;
  "profile": string;
  "dimension_count": number;
  "raw_dimension_count"?: number;
  "derived_dimension_count"?: number;
  "total_dimension_count"?: number;
  "coverage": Record<string, unknown>;
  "available_count"?: number;
  "missing_count"?: number;
  "derived_available_count"?: number;
  "derived_missing_count"?: number;
  "data_gap"?: Array<unknown>;
  "confidence": number;
  "risk_flags": Array<string>;
  "quality_status"?: string;
  "method_input_contract"?: Record<string, unknown>;
}

export type FundSnapshotContent = {
  "query": string;
  "fund_codes": Array<string>;
  "fund_category": string;
  "investment_cycle"?: string;
  "candidate_pool_size"?: number;
  "funds": Array<{
    "fund_overview"?: Record<string, unknown>;
    "benchmark_definition"?: string | null;
    "benchmark_performance"?: Array<Record<string, unknown>>;
    "manager_tenures"?: Array<Record<string, unknown>>;
    "scale_history"?: Array<Record<string, unknown>>;
    "scale_change"?: Record<string, unknown>;
  }>;
  "quality_status": "available" | "degraded" | "data_gap";
  "data_gap": Array<string>;
  "provider_errors"?: Array<Record<string, unknown>>;
  "retrieved_at": string;
}

export type FundAnalysisContent = {
  "methodology_version": string;
  "fund_category"?: string | null;
  "investment_cycle"?: string | null;
  "candidate_count": number;
  "candidates": Array<{
    "benchmark_definition"?: string | null;
    "benchmark_performance"?: Array<Record<string, unknown>>;
    "manager_tenures"?: Array<Record<string, unknown>>;
    "scale_history"?: Array<Record<string, unknown>>;
    "scale_change"?: Record<string, unknown>;
  }>;
  "recommendations": Array<Record<string, unknown>>;
  "selection_policy": Record<string, unknown>;
  "quality_status": "available" | "degraded" | "data_gap";
  "data_gap": Array<string>;
  "provider_errors"?: Array<Record<string, unknown>>;
  "generated_at": string;
}

export type AiReadinessContent = {
  "methodology_version": string;
  "method": "ai_readiness";
  "companies": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type EarningsPreviewContent = {
  "methodology_version": string;
  "method": "earnings_preview";
  "previews": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "data_gap" | "assumption_only";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type ModelUpdateContent = {
  "methodology_version": string;
  "method": "model_update";
  "updates": Array<Record<string, unknown>>;
  "impacts": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "data_gap" | "assumption_only";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type ReturnsAttributionContent = {
  "methodology_version": string;
  "method": "returns_attribution";
  "holdings"?: Array<Record<string, unknown>>;
  "observed_weight_coverage"?: number;
  "observed_contribution_pct"?: number;
  "total_return_pct"?: number | null;
  "groups"?: Record<string, unknown>;
  "benchmark"?: Record<string, unknown>;
  "quality_status": "available" | "degraded" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type HoldingRebalanceContent = {
  "methodology_version": string;
  "method": "holding_rebalance";
  "target_mode"?: string;
  "drift_threshold_pp"?: number;
  "drift_table"?: Array<Record<string, unknown>>;
  "trades"?: Array<Record<string, unknown>>;
  "concentration"?: Record<string, unknown>;
  "estimated_cost"?: number | null;
  "quality_status": "available" | "degraded" | "data_gap" | "assumption_only";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type PortfolioRebalanceContent = {
  "methodology_version": string;
  "method": "portfolio_rebalance";
  "asset_classes"?: Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type ThreeStatementModelContent = {
  "methodology_version": string;
  "method": "three_statement";
  "artifact_type": "three_statement_model";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type QuickLboContent = {
  "methodology_version": string;
  "method": "quick_lbo";
  "artifact_type": "quick_lbo";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type AccretionDilutionContent = {
  "methodology_version": string;
  "method": "accretion_dilution";
  "artifact_type": "accretion_dilution";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type SegmentalModelContent = {
  "methodology_version": string;
  "method": "segmental";
  "artifact_type": "segmental_model";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type InitiationReportContent = {
  "methodology_version": string;
  "method": "initiation_report";
  "artifact_type": "initiation_report";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type EarningsReviewContent = {
  "methodology_version": string;
  "method": "earnings_review";
  "artifact_type": "earnings_review";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type CatalystCalendarContent = {
  "methodology_version": string;
  "method": "catalyst_calendar";
  "artifact_type": "catalyst_calendar";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type ThesisTrackerContent = {
  "methodology_version": string;
  "method": "thesis_tracker";
  "artifact_type": "thesis_tracker";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type MorningNoteContent = {
  "methodology_version": string;
  "method": "morning_note";
  "artifact_type": "morning_note";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type IcMemoContent = {
  "methodology_version": string;
  "method": "ic_memo";
  "artifact_type": "ic_memo";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type PorterBcgContent = {
  "methodology_version": string;
  "method": "porter_bcg";
  "artifact_type": "porter_bcg";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type DdChecklistContent = {
  "methodology_version": string;
  "method": "dd_checklist";
  "artifact_type": "dd_checklist";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type UnitEconomicsContent = {
  "methodology_version": string;
  "method": "unit_economics";
  "artifact_type": "unit_economics";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type ValueCreationPlanContent = {
  "methodology_version": string;
  "method": "value_creation_plan";
  "artifact_type": "value_creation_plan";
  "results": Array<Record<string, unknown>>;
  "quality_status": "available" | "degraded" | "assumption_only" | "data_gap";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type FinanceArtifactContentMap = {
  "user_request": UserRequestContent
  "normalized_query": NormalizedQueryContent
  "stock_snapshot_bundle": StockSnapshotBundleContent
  "snapshot": SnapshotContent
  "evidence_set": EvidenceSetContent
  "financial_analysis": FinancialAnalysisContent
  "screening_result": ScreeningResultContent
  "sector_analysis": SectorAnalysisContent
  "thesis_review": ThesisReviewContent
  "source_record_set": SourceRecordSetContent
  "distillation_result": DistillationResultContent
  "distillation_review": DistillationReviewContent
  "research_report": ResearchReportContent
  "main_agent_synthesis": MainAgentSynthesisContent
  "valuation_model": ValuationModelContent
  "investor_panel_review": InvestorPanelReviewContent
  "risk_scan": RiskScanContent
  "report_presentation": ReportPresentationContent
  "institutional_analysis": InstitutionalAnalysisContent
  "research_workflow": ResearchWorkflowContent
  "deep_analysis": DeepAnalysisContent
  "uzi_dimension_coverage": UziDimensionCoverageContent
  "fund_snapshot": FundSnapshotContent
  "fund_analysis": FundAnalysisContent
  "ai_readiness": AiReadinessContent
  "earnings_preview": EarningsPreviewContent
  "model_update": ModelUpdateContent
  "returns_attribution": ReturnsAttributionContent
  "holding_rebalance": HoldingRebalanceContent
  "portfolio_rebalance": PortfolioRebalanceContent
  "three_statement_model": ThreeStatementModelContent
  "quick_lbo": QuickLboContent
  "accretion_dilution": AccretionDilutionContent
  "segmental_model": SegmentalModelContent
  "initiation_report": InitiationReportContent
  "earnings_review": EarningsReviewContent
  "catalyst_calendar": CatalystCalendarContent
  "thesis_tracker": ThesisTrackerContent
  "morning_note": MorningNoteContent
  "ic_memo": IcMemoContent
  "porter_bcg": PorterBcgContent
  "dd_checklist": DdChecklistContent
  "unit_economics": UnitEconomicsContent
  "value_creation_plan": ValueCreationPlanContent
}

export const FINANCE_ARTIFACT_SCHEMA_FIELDS = {
  "user_request": [
    "query",
    "symbols",
    "fund_codes",
    "fund_category",
    "portfolio_holdings",
    "benchmark_return",
    "rebalance_targets",
    "asset_class_targets",
    "drift_threshold",
    "fee_model",
    "requested_methods",
    "model_updates",
    "method_assumptions",
    "sector",
    "source_records",
    "snapshots",
    "source_refs",
  ],
  "normalized_query": [
    "query",
    "symbols",
    "fund_codes",
    "fund_category",
    "portfolio_holdings",
    "benchmark_return",
    "rebalance_targets",
    "drift_threshold",
    "fee_model",
    "asset_class_targets",
    "requested_methods",
    "method_assumptions",
  ],
  "stock_snapshot_bundle": [
    "query",
    "symbols",
    "sector",
    "snapshots",
    "candidate_universe",
    "collection",
    "collection.universe_source",
    "collection.candidate_count",
    "collection.requested_snapshot_types",
    "collection.collected_snapshot_types",
    "collection.enrichment_snapshot_types",
    "collection.enrichment_targets",
    "collection.degraded",
    "review_target_run_id",
    "review_target_artifact_ids",
    "snapshot_keys",
  ],
  "snapshot": [
    "snapshot_type",
    "scope",
    "symbol",
    "sector",
    "query",
    "snapshot_date",
    "data_period",
    "retrieved_at",
    "quality_status",
    "missing_fields",
    "degrade_reasons",
    "facts",
    "source",
    "source_refs",
  ],
  "evidence_set": [
    "items",
    "symbols",
    "query",
  ],
  "financial_analysis": [
    "symbols",
    "methodologies",
    "scorecards",
    "summary",
  ],
  "screening_result": [
    "screened_candidates",
    "watchlist_candidates",
    "enrichment_targets",
    "scorecard",
    "screening_notes",
    "methodology",
  ],
  "sector_analysis": [
    "sector",
    "theme",
    "findings",
    "related_snapshots",
    "risks",
    "source_count",
    "analysis_notes",
    "sector_heatmap",
    "constituents",
    "breadth",
    "relative_strength",
    "signal_scorecard",
    "catalysts",
    "policy_snapshots",
  ],
  "thesis_review": [
    "verdict",
    "overall_verdict",
    "findings",
    "strengths",
    "weaknesses",
    "follow_up",
    "review_target_run_id",
    "review_target_artifact_ids",
    "review_target_found",
    "target_thesis",
    "evidence_comparison",
  ],
  "source_record_set": [
    "query",
    "records",
  ],
  "distillation_result": [
    "dimensions",
    "summary",
  ],
  "distillation_review": [
    "verdict",
    "findings",
    "distillation",
  ],
  "research_report": [
    "conclusion",
    "assessment",
    "evidence",
    "risk",
    "follow_up",
    "methodologies",
    "scorecard",
    "screening_scorecard",
    "uzi_profile",
    "valuation_model",
    "investor_panel_review",
    "risk_scan",
    "institutional_analysis",
    "research_workflow",
    "deep_analysis",
    "uzi_dimension_coverage",
    "presentation_artifact_type",
    "fund_analysis",
    "quality_status",
    "delivery_type",
    "candidates",
    "screening",
    "sector_analysis",
    "sector_heatmap",
    "thesis_review",
    "review_summary",
  ],
  "main_agent_synthesis": [
    "conclusion",
    "assistant_message",
    "follow_up",
    "source_artifact_id",
    "model_generated",
  ],
  "valuation_model": [
    "methodology_version",
    "profile",
    "models",
    "primary_symbol",
    "methodologies",
    "dimensions",
    "quality_status",
    "method_input_contract",
  ],
  "investor_panel_review": [
    "methodology_version",
    "profile",
    "scope",
    "panel_version",
    "source_commit",
    "registered_seat_count",
    "rule_mode",
    "votes",
    "distribution",
    "average_score",
    "evaluated_rule_count",
    "skipped_rule_count",
    "quality_status",
    "data_gap",
    "disagreement",
    "confidence",
    "risk_flags",
  ],
  "risk_scan": [
    "methodology_version",
    "profile",
    "symbol",
    "risk_level",
    "signals",
    "signals_hit_count",
    "recommendation",
    "method_input_contract",
    "quality_status",
    "confidence",
    "risk_flags",
  ],
  "report_presentation": [
    "version",
    "theme",
    "profile",
    "subject",
    "hero",
    "core_conclusion",
    "dashboard",
    "great_divide",
    "panel",
    "radar",
    "deep_scan",
    "panel_insights",
    "risk_list",
    "buy_zones",
    "institutional_modeling",
    "advanced_methods",
    "advanced_methods.institutional_analysis",
    "advanced_methods.research_workflow",
    "advanced_methods.deep_analysis",
    "advanced_methods.uzi_dimension_coverage",
    "source_artifact_ids",
    "generated_at",
    "data_gap",
  ],
  "institutional_analysis": [
    "methodology_version",
    "profile",
    "methods",
    "results",
    "data_gap",
    "confidence",
    "risk_flags",
  ],
  "research_workflow": [
    "methodology_version",
    "profile",
    "results",
    "summary",
    "confidence",
    "risk_flags",
  ],
  "deep_analysis": [
    "methodology_version",
    "profile",
    "results",
    "summary",
    "confidence",
    "risk_flags",
  ],
  "uzi_dimension_coverage": [
    "methodology_version",
    "profile",
    "dimension_count",
    "raw_dimension_count",
    "derived_dimension_count",
    "total_dimension_count",
    "coverage",
    "available_count",
    "missing_count",
    "derived_available_count",
    "derived_missing_count",
    "data_gap",
    "confidence",
    "risk_flags",
    "quality_status",
    "method_input_contract",
  ],
  "fund_snapshot": [
    "query",
    "fund_codes",
    "fund_category",
    "investment_cycle",
    "candidate_pool_size",
    "funds",
    "funds[].fund_overview",
    "funds[].benchmark_definition",
    "funds[].benchmark_performance",
    "funds[].manager_tenures",
    "funds[].scale_history",
    "funds[].scale_change",
    "quality_status",
    "data_gap",
    "provider_errors",
    "retrieved_at",
  ],
  "fund_analysis": [
    "methodology_version",
    "fund_category",
    "investment_cycle",
    "candidate_count",
    "candidates",
    "candidates[].benchmark_definition",
    "candidates[].benchmark_performance",
    "candidates[].manager_tenures",
    "candidates[].scale_history",
    "candidates[].scale_change",
    "recommendations",
    "selection_policy",
    "quality_status",
    "data_gap",
    "provider_errors",
    "generated_at",
  ],
  "ai_readiness": [
    "methodology_version",
    "method",
    "companies",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "earnings_preview": [
    "methodology_version",
    "method",
    "previews",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "model_update": [
    "methodology_version",
    "method",
    "updates",
    "impacts",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "returns_attribution": [
    "methodology_version",
    "method",
    "holdings",
    "observed_weight_coverage",
    "observed_contribution_pct",
    "total_return_pct",
    "groups",
    "benchmark",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "holding_rebalance": [
    "methodology_version",
    "method",
    "target_mode",
    "drift_threshold_pp",
    "drift_table",
    "trades",
    "concentration",
    "estimated_cost",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "portfolio_rebalance": [
    "methodology_version",
    "method",
    "asset_classes",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "three_statement_model": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "quick_lbo": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "accretion_dilution": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "segmental_model": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "initiation_report": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "earnings_review": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "catalyst_calendar": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "thesis_tracker": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "morning_note": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "ic_memo": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "porter_bcg": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "dd_checklist": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "unit_economics": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "value_creation_plan": [
    "methodology_version",
    "method",
    "artifact_type",
    "results",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
} as const satisfies Record<FinanceArtifactType, readonly string[]>
