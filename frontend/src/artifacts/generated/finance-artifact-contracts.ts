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
    "fund_code": string;
    "fund_name": string;
    "fund_type"?: string | null;
    "as_of"?: string | null;
    "unit_nav"?: number | null;
    "accumulated_nav"?: number | null;
    "returns_pct": {
      "daily"?: number;
      "week_1"?: number;
      "month_1"?: number;
      "month_3"?: number;
      "month_6"?: number;
      "year_1"?: number;
      "year_2"?: number;
      "year_3"?: number;
      "year_to_date"?: number;
      "since_inception"?: number;
    };
    "fee_pct"?: number | null;
    "ranking_source"?: string | null;
    "nav_history"?: Array<{
      "date": string;
      "nav": number;
    }>;
    "risk"?: {
      "observation_count"?: number;
      "max_drawdown_pct"?: number;
      "annualized_volatility_pct"?: number | null;
      "period_return_pct"?: number;
      "period_start"?: string;
      "period_end"?: string;
    };
    "top_holdings"?: Array<{
      "rank": number;
      "code": string;
      "name": string;
      "weight_pct"?: number | null;
      "shares_10k"?: number | null;
      "market_value_10k"?: number | null;
    }>;
    "holdings_period"?: string | null;
    "top10_concentration_pct"?: number | null;
    "holdings_source"?: string;
    "fund_overview"?: {
      "fund_company"?: unknown;
      "fund_manager"?: unknown;
      "inception_date"?: string | null;
      "latest_scale"?: unknown;
      "custodian"?: unknown;
      "investment_objective"?: unknown;
      "investment_strategy"?: unknown;
      "benchmark_definition"?: unknown;
    };
    "benchmark_definition"?: string | null;
    "benchmark_performance"?: Array<{
      "period": string;
      "period_group"?: string | null;
      "fund_return_pct"?: number | null;
      "benchmark_return_pct"?: number | null;
      "excess_return_pct"?: number | null;
      "fund_max_drawdown_pct"?: number | null;
      "benchmark_max_drawdown_pct"?: number | null;
      "peer_rank"?: unknown;
    }>;
    "manager_tenures"?: Array<{
      "manager": string;
      "managers"?: Array<string>;
      "start_date": string;
      "end_date"?: string | null;
      "current": boolean;
      "tenure_text"?: unknown;
      "tenure_return_pct"?: number | null;
    }>;
    "scale_history"?: Array<{
      "report_date": string;
      "subscriptions_100m_shares"?: number | null;
      "redemptions_100m_shares"?: number | null;
      "net_flow_100m_shares"?: number | null;
      "total_shares_100m"?: number | null;
      "net_assets_100m_cny"?: number | null;
      "reported_share_change_pct"?: number | null;
    }>;
    "scale_change"?: {
      "latest_report_date"?: string | null;
      "previous_report_date"?: string | null;
      "latest_total_shares_100m"?: number | null;
      "latest_net_assets_100m_cny"?: number | null;
      "latest_net_flow_100m_shares"?: number | null;
      "total_share_change_pct"?: number | null;
      "net_asset_change_pct"?: number | null;
      "status"?: string;
    };
    "data_gap": Array<string>;
    "quality_status"?: "available" | "degraded" | "data_gap";
  }>;
  "quality_status": "available" | "degraded" | "data_gap";
  "data_gap": Array<string>;
  "provider_errors"?: Array<{
    "stage": string;
    "error_type": string;
  }>;
  "retrieved_at": string;
}

export type FundAnalysisContent = {
  "methodology_version": string;
  "fund_category"?: string | null;
  "investment_cycle"?: string | null;
  "candidate_count": number;
  "candidates": Array<{
    "fund_code": string;
    "fund_name": string;
    "fund_type"?: string | null;
    "as_of"?: string | null;
    "unit_nav"?: number | null;
    "returns_pct": {
      "daily"?: number;
      "week_1"?: number;
      "month_1"?: number;
      "month_3"?: number;
      "month_6"?: number;
      "year_1"?: number;
      "year_2"?: number;
      "year_3"?: number;
      "year_to_date"?: number;
      "since_inception"?: number;
    };
    "risk": {
      "observation_count"?: number;
      "max_drawdown_pct"?: number;
      "annualized_volatility_pct"?: number | null;
      "period_return_pct"?: number;
      "period_start"?: string;
      "period_end"?: string;
    };
    "fee_pct"?: number | null;
    "metrics": {
      "return_1y"?: number;
      "return_3y_annualized"?: number;
      "benchmark_excess_1y"?: number;
      "max_drawdown"?: number;
      "volatility"?: number;
      "fee"?: number;
      "return_consistency"?: number;
    };
    "top_holdings"?: Array<{
      "rank": number;
      "code": string;
      "name": string;
      "weight_pct"?: number | null;
      "shares_10k"?: number | null;
      "market_value_10k"?: number | null;
    }>;
    "holdings_period"?: string | null;
    "top10_concentration_pct"?: number | null;
    "benchmark_definition"?: string | null;
    "benchmark_performance"?: Array<{
      "period": string;
      "period_group"?: string | null;
      "fund_return_pct"?: number | null;
      "benchmark_return_pct"?: number | null;
      "excess_return_pct"?: number | null;
      "fund_max_drawdown_pct"?: number | null;
      "benchmark_max_drawdown_pct"?: number | null;
      "peer_rank"?: unknown;
    }>;
    "manager_tenures"?: Array<{
      "manager": string;
      "managers"?: Array<string>;
      "start_date": string;
      "end_date"?: string | null;
      "current": boolean;
      "tenure_text"?: unknown;
      "tenure_return_pct"?: number | null;
    }>;
    "scale_history"?: Array<{
      "report_date": string;
      "subscriptions_100m_shares"?: number | null;
      "redemptions_100m_shares"?: number | null;
      "net_flow_100m_shares"?: number | null;
      "total_shares_100m"?: number | null;
      "net_assets_100m_cny"?: number | null;
      "reported_share_change_pct"?: number | null;
    }>;
    "scale_change"?: {
      "latest_report_date"?: string | null;
      "previous_report_date"?: string | null;
      "latest_total_shares_100m"?: number | null;
      "latest_net_assets_100m_cny"?: number | null;
      "latest_net_flow_100m_shares"?: number | null;
      "total_share_change_pct"?: number | null;
      "net_asset_change_pct"?: number | null;
      "status"?: string;
    };
    "comparison_group"?: string;
    "score"?: number | null;
    "score_coverage": number;
    "score_components": Array<{
      "component": string;
      "value": number;
      "weight": number;
      "percentile": number;
    }>;
    "selection_eligible": boolean;
    "rank"?: number | null;
    "data_gap": Array<string>;
    "quality_status": "available" | "degraded" | "data_gap";
  }>;
  "recommendations": Array<{
    "fund_code": string;
    "fund_name": string;
    "fund_type"?: string | null;
    "comparison_group": string;
    "rank": number;
    "score": number;
    "score_coverage": number;
    "reason": string;
    "risk": string;
  }>;
  "selection_policy": {
    "description": string;
    "minimum_score_coverage": number;
    "minimum_comparable_sample": number;
    "components": Array<{
      "id": string;
      "weight": number;
      "direction": "higher" | "lower";
    }>;
    "non_imputation_rule": string;
  };
  "quality_status": "available" | "degraded" | "data_gap";
  "data_gap": Array<string>;
  "provider_errors"?: Array<{
    "stage": string;
    "error_type": string;
  }>;
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
    "funds[].fund_code",
    "funds[].fund_name",
    "funds[].fund_type",
    "funds[].as_of",
    "funds[].unit_nav",
    "funds[].accumulated_nav",
    "funds[].returns_pct",
    "funds[].returns_pct.daily",
    "funds[].returns_pct.week_1",
    "funds[].returns_pct.month_1",
    "funds[].returns_pct.month_3",
    "funds[].returns_pct.month_6",
    "funds[].returns_pct.year_1",
    "funds[].returns_pct.year_2",
    "funds[].returns_pct.year_3",
    "funds[].returns_pct.year_to_date",
    "funds[].returns_pct.since_inception",
    "funds[].fee_pct",
    "funds[].ranking_source",
    "funds[].nav_history",
    "funds[].nav_history[].date",
    "funds[].nav_history[].nav",
    "funds[].risk",
    "funds[].risk.observation_count",
    "funds[].risk.max_drawdown_pct",
    "funds[].risk.annualized_volatility_pct",
    "funds[].risk.period_return_pct",
    "funds[].risk.period_start",
    "funds[].risk.period_end",
    "funds[].top_holdings",
    "funds[].top_holdings[].rank",
    "funds[].top_holdings[].code",
    "funds[].top_holdings[].name",
    "funds[].top_holdings[].weight_pct",
    "funds[].top_holdings[].shares_10k",
    "funds[].top_holdings[].market_value_10k",
    "funds[].holdings_period",
    "funds[].top10_concentration_pct",
    "funds[].holdings_source",
    "funds[].fund_overview",
    "funds[].fund_overview.fund_company",
    "funds[].fund_overview.fund_manager",
    "funds[].fund_overview.inception_date",
    "funds[].fund_overview.latest_scale",
    "funds[].fund_overview.custodian",
    "funds[].fund_overview.investment_objective",
    "funds[].fund_overview.investment_strategy",
    "funds[].fund_overview.benchmark_definition",
    "funds[].benchmark_definition",
    "funds[].benchmark_performance",
    "funds[].benchmark_performance[].period",
    "funds[].benchmark_performance[].period_group",
    "funds[].benchmark_performance[].fund_return_pct",
    "funds[].benchmark_performance[].benchmark_return_pct",
    "funds[].benchmark_performance[].excess_return_pct",
    "funds[].benchmark_performance[].fund_max_drawdown_pct",
    "funds[].benchmark_performance[].benchmark_max_drawdown_pct",
    "funds[].benchmark_performance[].peer_rank",
    "funds[].manager_tenures",
    "funds[].manager_tenures[].manager",
    "funds[].manager_tenures[].managers",
    "funds[].manager_tenures[].start_date",
    "funds[].manager_tenures[].end_date",
    "funds[].manager_tenures[].current",
    "funds[].manager_tenures[].tenure_text",
    "funds[].manager_tenures[].tenure_return_pct",
    "funds[].scale_history",
    "funds[].scale_history[].report_date",
    "funds[].scale_history[].subscriptions_100m_shares",
    "funds[].scale_history[].redemptions_100m_shares",
    "funds[].scale_history[].net_flow_100m_shares",
    "funds[].scale_history[].total_shares_100m",
    "funds[].scale_history[].net_assets_100m_cny",
    "funds[].scale_history[].reported_share_change_pct",
    "funds[].scale_change",
    "funds[].scale_change.latest_report_date",
    "funds[].scale_change.previous_report_date",
    "funds[].scale_change.latest_total_shares_100m",
    "funds[].scale_change.latest_net_assets_100m_cny",
    "funds[].scale_change.latest_net_flow_100m_shares",
    "funds[].scale_change.total_share_change_pct",
    "funds[].scale_change.net_asset_change_pct",
    "funds[].scale_change.status",
    "funds[].data_gap",
    "funds[].quality_status",
    "quality_status",
    "data_gap",
    "provider_errors",
    "provider_errors[].stage",
    "provider_errors[].error_type",
    "retrieved_at",
  ],
  "fund_analysis": [
    "methodology_version",
    "fund_category",
    "investment_cycle",
    "candidate_count",
    "candidates",
    "candidates[].fund_code",
    "candidates[].fund_name",
    "candidates[].fund_type",
    "candidates[].as_of",
    "candidates[].unit_nav",
    "candidates[].returns_pct",
    "candidates[].returns_pct.daily",
    "candidates[].returns_pct.week_1",
    "candidates[].returns_pct.month_1",
    "candidates[].returns_pct.month_3",
    "candidates[].returns_pct.month_6",
    "candidates[].returns_pct.year_1",
    "candidates[].returns_pct.year_2",
    "candidates[].returns_pct.year_3",
    "candidates[].returns_pct.year_to_date",
    "candidates[].returns_pct.since_inception",
    "candidates[].risk",
    "candidates[].risk.observation_count",
    "candidates[].risk.max_drawdown_pct",
    "candidates[].risk.annualized_volatility_pct",
    "candidates[].risk.period_return_pct",
    "candidates[].risk.period_start",
    "candidates[].risk.period_end",
    "candidates[].fee_pct",
    "candidates[].metrics",
    "candidates[].metrics.return_1y",
    "candidates[].metrics.return_3y_annualized",
    "candidates[].metrics.benchmark_excess_1y",
    "candidates[].metrics.max_drawdown",
    "candidates[].metrics.volatility",
    "candidates[].metrics.fee",
    "candidates[].metrics.return_consistency",
    "candidates[].top_holdings",
    "candidates[].top_holdings[].rank",
    "candidates[].top_holdings[].code",
    "candidates[].top_holdings[].name",
    "candidates[].top_holdings[].weight_pct",
    "candidates[].top_holdings[].shares_10k",
    "candidates[].top_holdings[].market_value_10k",
    "candidates[].holdings_period",
    "candidates[].top10_concentration_pct",
    "candidates[].benchmark_definition",
    "candidates[].benchmark_performance",
    "candidates[].benchmark_performance[].period",
    "candidates[].benchmark_performance[].period_group",
    "candidates[].benchmark_performance[].fund_return_pct",
    "candidates[].benchmark_performance[].benchmark_return_pct",
    "candidates[].benchmark_performance[].excess_return_pct",
    "candidates[].benchmark_performance[].fund_max_drawdown_pct",
    "candidates[].benchmark_performance[].benchmark_max_drawdown_pct",
    "candidates[].benchmark_performance[].peer_rank",
    "candidates[].manager_tenures",
    "candidates[].manager_tenures[].manager",
    "candidates[].manager_tenures[].managers",
    "candidates[].manager_tenures[].start_date",
    "candidates[].manager_tenures[].end_date",
    "candidates[].manager_tenures[].current",
    "candidates[].manager_tenures[].tenure_text",
    "candidates[].manager_tenures[].tenure_return_pct",
    "candidates[].scale_history",
    "candidates[].scale_history[].report_date",
    "candidates[].scale_history[].subscriptions_100m_shares",
    "candidates[].scale_history[].redemptions_100m_shares",
    "candidates[].scale_history[].net_flow_100m_shares",
    "candidates[].scale_history[].total_shares_100m",
    "candidates[].scale_history[].net_assets_100m_cny",
    "candidates[].scale_history[].reported_share_change_pct",
    "candidates[].scale_change",
    "candidates[].scale_change.latest_report_date",
    "candidates[].scale_change.previous_report_date",
    "candidates[].scale_change.latest_total_shares_100m",
    "candidates[].scale_change.latest_net_assets_100m_cny",
    "candidates[].scale_change.latest_net_flow_100m_shares",
    "candidates[].scale_change.total_share_change_pct",
    "candidates[].scale_change.net_asset_change_pct",
    "candidates[].scale_change.status",
    "candidates[].comparison_group",
    "candidates[].score",
    "candidates[].score_coverage",
    "candidates[].score_components",
    "candidates[].score_components[].component",
    "candidates[].score_components[].value",
    "candidates[].score_components[].weight",
    "candidates[].score_components[].percentile",
    "candidates[].selection_eligible",
    "candidates[].rank",
    "candidates[].data_gap",
    "candidates[].quality_status",
    "recommendations",
    "recommendations[].fund_code",
    "recommendations[].fund_name",
    "recommendations[].fund_type",
    "recommendations[].comparison_group",
    "recommendations[].rank",
    "recommendations[].score",
    "recommendations[].score_coverage",
    "recommendations[].reason",
    "recommendations[].risk",
    "selection_policy",
    "selection_policy.description",
    "selection_policy.minimum_score_coverage",
    "selection_policy.minimum_comparable_sample",
    "selection_policy.components",
    "selection_policy.components[].id",
    "selection_policy.components[].weight",
    "selection_policy.components[].direction",
    "selection_policy.non_imputation_rule",
    "quality_status",
    "data_gap",
    "provider_errors",
    "provider_errors[].stage",
    "provider_errors[].error_type",
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
