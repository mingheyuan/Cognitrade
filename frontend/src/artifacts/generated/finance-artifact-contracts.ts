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
  "screened_candidates": Array<{
    "symbol": string;
    "name": string;
    "latest_price": number | null;
    "latest_pct_change": number | null;
    "latest_amount": number | null;
    "notice_count": number;
    "has_financial_abstract": boolean;
    "sector": string;
    "score": number;
    "score_breakdown": {
      "liquidity": number;
      "momentum": number;
      "fundamental": number;
      "notice": number;
      "enrichment_coverage": number;
      "evidence_penalty": number;
    };
    "missing_evidence": Array<string>;
    "degraded_sources": Array<string>;
    "deep_checked": boolean;
    "enrichment_requested": boolean;
    "evidence_snapshot_types": Array<string>;
    "why_it_made_the_list": string;
    "main_risk": string;
    "methodology": {
      "screen_style": string;
      "criteria": Array<{
        "id": string;
        "label": string;
        "status"?: string;
        "source_field"?: string | null;
      }>;
      "evaluated_criterion_count": number;
      "passed_criterion_count": number;
      "pass_rate": number | null;
      "data_gaps": Array<string>;
    };
    "rank": number;
    "candidate_tier": "core" | "watchlist";
  }>;
  "watchlist_candidates": Array<{
    "symbol": string;
    "name": string;
    "latest_price": number | null;
    "latest_pct_change": number | null;
    "latest_amount": number | null;
    "notice_count": number;
    "has_financial_abstract": boolean;
    "sector": string;
    "score": number;
    "score_breakdown": {
      "liquidity": number;
      "momentum": number;
      "fundamental": number;
      "notice": number;
      "enrichment_coverage": number;
      "evidence_penalty": number;
    };
    "missing_evidence": Array<string>;
    "degraded_sources": Array<string>;
    "deep_checked": boolean;
    "enrichment_requested": boolean;
    "evidence_snapshot_types": Array<string>;
    "why_it_made_the_list": string;
    "main_risk": string;
    "methodology": {
      "screen_style": string;
      "criteria": Array<{
        "id": string;
        "label": string;
        "status"?: string;
        "source_field"?: string | null;
      }>;
      "evaluated_criterion_count": number;
      "passed_criterion_count": number;
      "pass_rate": number | null;
      "data_gaps": Array<string>;
    };
    "rank": number;
    "candidate_tier": "core" | "watchlist";
  }>;
  "enrichment_targets": Array<string>;
  "scorecard": {
    "investment_cycle": string;
    "investment_cycle_assumption": string;
    "candidate_count": number;
    "eligible_candidate_count": number;
    "top_candidate_count": number;
    "watchlist_candidate_count": number;
    "deep_checked_count": number;
    "watchlist_deep_checked_count": number;
    "enrichment_target_count": number;
    "candidate_universe_source": string | null;
    "requested_snapshot_types": Array<string>;
    "collected_snapshot_types": Array<string>;
    "preferred_methodology": string;
    "screening_strategy": {
      "methodology_source": string;
      "investment_cycle": string;
      "screen_style": string;
    };
    "summary": string;
  };
  "screening_notes": Array<string>;
  "methodology": {
    "screen_style": string;
    "requested_screen_style": string;
    "criteria": Array<{
      "id": string;
      "label": string;
      "status"?: string;
      "source_field"?: string | null;
    }>;
  };
}

export type SectorAnalysisContent = {
  "sector": string | null;
  "theme": string | null;
  "findings": Array<{
    "snapshot_type": string;
    "scope"?: string;
    "symbol"?: string | null;
    "sector"?: string | null;
    "theme"?: string | null;
    "query"?: string | null;
    "snapshot_date"?: string | null;
    "data_period"?: string | null;
    "retrieved_at"?: string | null;
    "created_at"?: string | null;
    "quality_status"?: string;
    "facts": Record<string, unknown>;
    "source"?: {
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    };
    "primary_source"?: unknown;
    "fallback_sources"?: Array<{
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    }>;
    "field_sources"?: Record<string, unknown>;
    "fallback_applied"?: boolean;
    "missing_fields"?: Array<string>;
    "source_confidence"?: string;
    "source_trace"?: Array<Record<string, unknown>>;
    "source_refs"?: Array<{
      "type"?: string;
      "provider"?: string;
      "id"?: string;
    }>;
    "degraded"?: boolean;
    "degrade_reason"?: string | null;
    "degrade_reasons"?: Array<string>;
  }>;
  "related_snapshots": Array<{
    "snapshot_type": string;
    "scope"?: string;
    "symbol"?: string | null;
    "sector"?: string | null;
    "theme"?: string | null;
    "query"?: string | null;
    "snapshot_date"?: string | null;
    "data_period"?: string | null;
    "retrieved_at"?: string | null;
    "created_at"?: string | null;
    "quality_status"?: string;
    "facts": Record<string, unknown>;
    "source"?: {
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    };
    "primary_source"?: unknown;
    "fallback_sources"?: Array<{
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    }>;
    "field_sources"?: Record<string, unknown>;
    "fallback_applied"?: boolean;
    "missing_fields"?: Array<string>;
    "source_confidence"?: string;
    "source_trace"?: Array<Record<string, unknown>>;
    "source_refs"?: Array<{
      "type"?: string;
      "provider"?: string;
      "id"?: string;
    }>;
    "degraded"?: boolean;
    "degrade_reason"?: string | null;
    "degrade_reasons"?: Array<string>;
  }>;
  "risks": Array<string>;
  "source_count": number;
  "analysis_notes": Array<string>;
  "sector_heatmap": Array<{
    "name": string;
    "symbol"?: string;
    "score": number;
    "raw_value"?: number | null;
    "score_basis": string;
    "source_count": number;
    "related_snapshot_count"?: number;
    "theme": string | null;
  }>;
  "constituents": Array<Record<string, unknown>>;
  "breadth": {
    "sample_size"?: number;
    "up_count"?: number;
    "down_count"?: number;
    "flat_count"?: number;
    "up_ratio"?: number | null;
    "down_ratio"?: number | null;
    "median_pct_change"?: number | null;
    "mean_pct_change"?: number | null;
    "average_turnover_rate"?: number | null;
    "top3_average_pct_change"?: number | null;
    "bottom3_average_pct_change"?: number | null;
    "best_constituents"?: Array<{
      "code"?: string | null;
      "name"?: string | null;
      "pct_change"?: number | null;
      "turnover_rate"?: number | null;
    }>;
    "worst_constituents"?: Array<{
      "code"?: string | null;
      "name"?: string | null;
      "pct_change"?: number | null;
      "turnover_rate"?: number | null;
    }>;
  };
  "relative_strength": {
    "latest_excess_pct_change"?: number | null;
    "recent_relative_strength"?: Array<{
      "date": string;
      "sector_pct_change": number;
      "benchmark_pct_change": number;
      "excess_pct_change": number;
    }>;
  };
  "signal_scorecard": {
    "theme"?: string;
    "market_tone"?: string;
    "benchmark_view"?: string;
    "turnover_state"?: string;
    "breadth_state"?: string;
    "policy_signal_strength"?: string;
    "regulatory_signal_strength"?: string;
    "sample_signal_strength"?: string;
    "setup_label"?: string;
    "headline_counts"?: {
      "theme_news"?: number;
      "policy_signals"?: number;
      "regulatory_signals"?: number;
      "catalyst_samples"?: number;
    };
    "breadth"?: {
      "sample_size"?: number;
      "up_count"?: number;
      "down_count"?: number;
      "flat_count"?: number;
      "up_ratio"?: number | null;
      "down_ratio"?: number | null;
      "median_pct_change"?: number | null;
      "mean_pct_change"?: number | null;
      "average_turnover_rate"?: number | null;
      "top3_average_pct_change"?: number | null;
      "bottom3_average_pct_change"?: number | null;
      "best_constituents"?: Array<{
        "code"?: string | null;
        "name"?: string | null;
        "pct_change"?: number | null;
        "turnover_rate"?: number | null;
      }>;
      "worst_constituents"?: Array<{
        "code"?: string | null;
        "name"?: string | null;
        "pct_change"?: number | null;
        "turnover_rate"?: number | null;
      }>;
    };
    "summary"?: string;
  };
  "catalysts": {
    "theme_news": Array<Record<string, unknown>>;
    "policy_signals": Array<Record<string, unknown>>;
    "regulatory_signals": Array<Record<string, unknown>>;
    "samples": Array<Record<string, unknown>>;
  };
  "policy_snapshots": Array<{
    "snapshot_type": string;
    "scope"?: string;
    "symbol"?: string | null;
    "sector"?: string | null;
    "theme"?: string | null;
    "query"?: string | null;
    "snapshot_date"?: string | null;
    "data_period"?: string | null;
    "retrieved_at"?: string | null;
    "created_at"?: string | null;
    "quality_status"?: string;
    "facts": Record<string, unknown>;
    "source"?: {
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    };
    "primary_source"?: unknown;
    "fallback_sources"?: Array<{
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    }>;
    "field_sources"?: Record<string, unknown>;
    "fallback_applied"?: boolean;
    "missing_fields"?: Array<string>;
    "source_confidence"?: string;
    "source_trace"?: Array<Record<string, unknown>>;
    "source_refs"?: Array<{
      "type"?: string;
      "provider"?: string;
      "id"?: string;
    }>;
    "degraded"?: boolean;
    "degrade_reason"?: string | null;
    "degrade_reasons"?: Array<string>;
  }>;
}

export type ThesisReviewContent = {
  "verdict": string;
  "overall_verdict": string;
  "findings": Array<{
    "claim"?: string;
    "artifact_id"?: string;
    "artifact_type"?: string;
    "summary"?: string;
    "verdict"?: string;
    "evidence_index"?: number;
    "snapshot_type"?: string;
    "scope"?: string;
    "symbol"?: string | null;
    "sector"?: string | null;
    "theme"?: string | null;
    "query"?: string | null;
    "snapshot_date"?: string | null;
    "data_period"?: string | null;
    "retrieved_at"?: string | null;
    "created_at"?: string | null;
    "quality_status"?: string;
    "facts"?: Record<string, unknown>;
    "source"?: {
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    };
    "primary_source"?: unknown;
    "fallback_sources"?: Array<{
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    }>;
    "field_sources"?: Record<string, unknown>;
    "fallback_applied"?: boolean;
    "missing_fields"?: Array<string>;
    "source_confidence"?: string;
    "source_trace"?: Array<Record<string, unknown>>;
    "source_refs"?: Array<{
      "type"?: string;
      "provider"?: string;
      "id"?: string;
    }>;
    "degraded"?: boolean;
    "degrade_reason"?: string | null;
    "degrade_reasons"?: Array<string>;
  }>;
  "strengths": Array<{
    "claim"?: string;
    "artifact_id"?: string;
    "artifact_type"?: string;
    "summary"?: string;
    "verdict"?: string;
    "evidence_index"?: number;
    "snapshot_type"?: string;
    "scope"?: string;
    "symbol"?: string | null;
    "sector"?: string | null;
    "theme"?: string | null;
    "query"?: string | null;
    "snapshot_date"?: string | null;
    "data_period"?: string | null;
    "retrieved_at"?: string | null;
    "created_at"?: string | null;
    "quality_status"?: string;
    "facts"?: Record<string, unknown>;
    "source"?: {
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    };
    "primary_source"?: unknown;
    "fallback_sources"?: Array<{
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    }>;
    "field_sources"?: Record<string, unknown>;
    "fallback_applied"?: boolean;
    "missing_fields"?: Array<string>;
    "source_confidence"?: string;
    "source_trace"?: Array<Record<string, unknown>>;
    "source_refs"?: Array<{
      "type"?: string;
      "provider"?: string;
      "id"?: string;
    }>;
    "degraded"?: boolean;
    "degrade_reason"?: string | null;
    "degrade_reasons"?: Array<string>;
  }>;
  "weaknesses": Array<unknown>;
  "follow_up": Array<string>;
  "review_target_run_id": string | null;
  "review_target_artifact_ids": Array<string>;
  "review_target_found": boolean;
  "target_thesis": Array<{
    "claim"?: string;
    "artifact_id"?: string;
    "artifact_type"?: string;
    "summary"?: string;
    "verdict"?: string;
    "evidence_index"?: number;
    "snapshot_type"?: string;
    "scope"?: string;
    "symbol"?: string | null;
    "sector"?: string | null;
    "theme"?: string | null;
    "query"?: string | null;
    "snapshot_date"?: string | null;
    "data_period"?: string | null;
    "retrieved_at"?: string | null;
    "created_at"?: string | null;
    "quality_status"?: string;
    "facts"?: Record<string, unknown>;
    "source"?: {
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    };
    "primary_source"?: unknown;
    "fallback_sources"?: Array<{
      "skill"?: string;
      "vendor"?: string;
      "from_cache"?: boolean;
      "provider_id"?: string | null;
      "source_id"?: string | null;
      "chain_position"?: number | null;
      "dim"?: string | null;
    }>;
    "field_sources"?: Record<string, unknown>;
    "fallback_applied"?: boolean;
    "missing_fields"?: Array<string>;
    "source_confidence"?: string;
    "source_trace"?: Array<Record<string, unknown>>;
    "source_refs"?: Array<{
      "type"?: string;
      "provider"?: string;
      "id"?: string;
    }>;
    "degraded"?: boolean;
    "degrade_reason"?: string | null;
    "degrade_reasons"?: Array<string>;
  }>;
  "evidence_comparison": {
    "target_artifact_count": number;
    "fresh_evidence_count": number;
    "fresh_snapshot_types": Array<string>;
    "fresh_symbols": Array<string>;
  };
}

export type SourceRecordSetContent = {
  "query": string;
  "records": Array<{
    "record_id": string;
    "text": string;
    "author": string | null;
    "published_at": string | null;
    "tags": Array<string>;
    "dimensions": Array<string>;
  }>;
}

export type DistillationResultContent = {
  "dimensions": Array<{
    "dimension_name": string;
    "category": string;
    "claim": string;
    "source_record_ids": Array<string>;
    "confidence": number;
    "attribution": string | null;
  }>;
  "summary": {
    "source_record_count": number;
    "dimension_count": number;
    "major_risks": Array<string>;
    "method": string;
  };
}

export type DistillationReviewContent = {
  "verdict": string;
  "findings": Array<{
    "finding_type": string;
    "severity": string;
    "dimension_name": string | null;
    "reason": string;
  }>;
  "distillation": {
    "dimensions": Array<{
      "dimension_name": string;
      "category": string;
      "claim": string;
      "source_record_ids": Array<string>;
      "confidence": number;
      "attribution": string | null;
    }>;
    "summary": {
      "source_record_count": number;
      "dimension_count": number;
      "major_risks": Array<string>;
      "method": string;
    };
  };
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
  "models": Array<{
    "model": string;
    "methodology_version": string;
    "symbol": string;
    "status": string;
    "fair_value": number | null;
    "market_price": number | null;
    "margin_of_safety": number | null;
    "assumptions": {
      "wacc"?: number;
      "terminal_growth"?: number;
      "stage_one_growth"?: number;
      "stage_two_growth"?: number;
      "forecast_years"?: number;
      "risk_free_rate"?: number;
      "equity_risk_premium"?: number;
      "beta"?: number;
      "tax_rate"?: number;
      "target_debt_ratio"?: number;
      "cost_of_equity"?: number;
      "after_tax_cost_of_debt"?: number;
      "total_debt_yi"?: number | null;
      "cash_yi"?: number | null;
    };
    "sensitivity": Array<{
      "wacc": number;
      "terminal_growth": number;
      "fair_value": number | null;
    }>;
    "verdict": string;
    "confidence": number;
    "warnings": Array<string>;
    "data_gap": Array<string>;
    "assumption_flags": Array<string>;
    "field_sources": Record<string, unknown>;
    "methodology_log": Array<string>;
    "comps": unknown;
  }>;
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
  "votes": Array<{
    "symbol": string;
    "investor_id": string;
    "perspective_id": string;
    "perspective_label": string;
    "school": string;
    "required_dimensions": Array<string>;
    "signal": string;
    "score": number;
    "confidence": number;
    "reasoning": string;
    "headline": string;
    "pass_rules": Array<{
      "rule_id": string;
      "name": string;
      "weight": number;
      "msg": string;
      "missing_fields"?: Array<string>;
    }>;
    "fail_rules": Array<{
      "rule_id": string;
      "name": string;
      "weight": number;
      "msg": string;
      "missing_fields"?: Array<string>;
    }>;
    "skipped_rules": Array<{
      "rule_id": string;
      "name": string;
      "weight": number;
      "msg": string;
      "missing_fields"?: Array<string>;
    }>;
    "data_gap_rules": Array<string>;
    "pass_count": number;
    "fail_count": number;
    "skipped_count": number;
    "weight_pass": number;
    "weight_total": number;
    "rule_engine_version": string;
    "checks": Array<{
      "dimension": string;
      "status": string;
      "feature_names": Array<string>;
    }>;
    "is_methodology_perspective": boolean;
    "is_real_person_advice": boolean;
    "rule_mode": string;
  }>;
  "distribution": {
    "bullish"?: number;
    "bearish"?: number;
    "neutral"?: number;
    "insufficient_evidence"?: number;
  };
  "average_score"?: number | null;
  "evaluated_rule_count"?: number;
  "skipped_rule_count"?: number;
  "quality_status"?: string;
  "data_gap"?: Array<string>;
  "disagreement": {
    "exists": boolean;
    "summary": string;
  };
  "confidence": number;
  "risk_flags": Array<string>;
}

export type RiskScanContent = {
  "methodology_version": string;
  "profile": string;
  "symbol": string | null;
  "risk_level": string;
  "signals": Array<{
    "signal_id": string;
    "status": string;
    "severity": string;
    "finding": string;
    "evidence_refs": Array<{
      "type": string;
      "id": string;
      "provider"?: string | null;
    }>;
  }>;
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
  "data_gap"?: Array<string>;
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
  "holdings"?: Array<{
    "ticker"?: string;
    "symbol"?: string;
    "name"?: string;
    "weight"?: number;
    "normalized_weight"?: number;
    "return_pct"?: number | null;
    "contribution_pct"?: number | null;
    "status"?: string;
    "industry"?: string;
    "market"?: string;
    "asset_class"?: string;
    "value"?: number;
    "market_value"?: number;
    "quantity"?: number;
    "price"?: number;
  }>;
  "observed_weight_coverage"?: number;
  "observed_contribution_pct"?: number;
  "total_return_pct"?: number | null;
  "groups"?: {
    "industry"?: Array<{
      "industry"?: string;
      "market"?: string;
      "weight"?: number;
      "observed_weight"?: number;
      "contribution_pct"?: number;
      "missing_tickers"?: Array<string>;
      "status"?: string;
    }>;
    "market"?: Array<{
      "industry"?: string;
      "market"?: string;
      "weight"?: number;
      "observed_weight"?: number;
      "contribution_pct"?: number;
      "missing_tickers"?: Array<string>;
      "status"?: string;
    }>;
  };
  "benchmark"?: {
    "benchmark_return_pct": number | null;
    "excess_return_pct": number | null;
    "status": string;
  };
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
  "drift_table"?: Array<{
    "ticker"?: string;
    "symbol"?: string;
    "name"?: string;
    "weight"?: number;
    "normalized_weight"?: number;
    "target_weight"?: number;
    "drift_pp"?: number;
    "breached"?: boolean;
    "action"?: string;
    "trade_amount"?: number | null;
    "estimated_shares"?: number | null;
    "estimated_cost"?: number | null;
    "return_pct"?: number;
    "industry"?: string;
    "market"?: string;
    "asset_class"?: string;
    "value"?: number;
    "market_value"?: number;
    "quantity"?: number;
    "price"?: number;
  }>;
  "trades"?: Array<{
    "ticker": string;
    "action": string;
    "trade_amount": number | null;
    "estimated_shares": number | null;
    "estimated_cost": number | null;
    "drift_pp": number;
  }>;
  "concentration"?: {
    "before_hhi": number;
    "after_hhi": number;
    "before_top3_weight": number;
    "after_top3_weight": number;
  };
  "estimated_cost"?: number | null;
  "quality_status": "available" | "degraded" | "data_gap" | "assumption_only";
  "data_gap": Array<string>;
  "assumption_flags": Array<string>;
  "generated_at": string;
}

export type PortfolioRebalanceContent = {
  "methodology_version": string;
  "method": "portfolio_rebalance";
  "target_mode"?: string;
  "drift_threshold_pp"?: number;
  "asset_classes"?: Array<{
    "asset_class": string;
    "current_weight": number;
    "target_weight": number;
    "drift_pp": number;
    "breached": boolean;
    "action": string;
    "suggested_value_change": number | null;
  }>;
  "portfolio_value"?: number | null;
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
    "screened_candidates[].symbol",
    "screened_candidates[].name",
    "screened_candidates[].latest_price",
    "screened_candidates[].latest_pct_change",
    "screened_candidates[].latest_amount",
    "screened_candidates[].notice_count",
    "screened_candidates[].has_financial_abstract",
    "screened_candidates[].sector",
    "screened_candidates[].score",
    "screened_candidates[].score_breakdown",
    "screened_candidates[].score_breakdown.liquidity",
    "screened_candidates[].score_breakdown.momentum",
    "screened_candidates[].score_breakdown.fundamental",
    "screened_candidates[].score_breakdown.notice",
    "screened_candidates[].score_breakdown.enrichment_coverage",
    "screened_candidates[].score_breakdown.evidence_penalty",
    "screened_candidates[].missing_evidence",
    "screened_candidates[].degraded_sources",
    "screened_candidates[].deep_checked",
    "screened_candidates[].enrichment_requested",
    "screened_candidates[].evidence_snapshot_types",
    "screened_candidates[].why_it_made_the_list",
    "screened_candidates[].main_risk",
    "screened_candidates[].methodology",
    "screened_candidates[].methodology.screen_style",
    "screened_candidates[].methodology.criteria",
    "screened_candidates[].methodology.criteria[].id",
    "screened_candidates[].methodology.criteria[].label",
    "screened_candidates[].methodology.criteria[].status",
    "screened_candidates[].methodology.criteria[].source_field",
    "screened_candidates[].methodology.evaluated_criterion_count",
    "screened_candidates[].methodology.passed_criterion_count",
    "screened_candidates[].methodology.pass_rate",
    "screened_candidates[].methodology.data_gaps",
    "screened_candidates[].rank",
    "screened_candidates[].candidate_tier",
    "watchlist_candidates",
    "watchlist_candidates[].symbol",
    "watchlist_candidates[].name",
    "watchlist_candidates[].latest_price",
    "watchlist_candidates[].latest_pct_change",
    "watchlist_candidates[].latest_amount",
    "watchlist_candidates[].notice_count",
    "watchlist_candidates[].has_financial_abstract",
    "watchlist_candidates[].sector",
    "watchlist_candidates[].score",
    "watchlist_candidates[].score_breakdown",
    "watchlist_candidates[].score_breakdown.liquidity",
    "watchlist_candidates[].score_breakdown.momentum",
    "watchlist_candidates[].score_breakdown.fundamental",
    "watchlist_candidates[].score_breakdown.notice",
    "watchlist_candidates[].score_breakdown.enrichment_coverage",
    "watchlist_candidates[].score_breakdown.evidence_penalty",
    "watchlist_candidates[].missing_evidence",
    "watchlist_candidates[].degraded_sources",
    "watchlist_candidates[].deep_checked",
    "watchlist_candidates[].enrichment_requested",
    "watchlist_candidates[].evidence_snapshot_types",
    "watchlist_candidates[].why_it_made_the_list",
    "watchlist_candidates[].main_risk",
    "watchlist_candidates[].methodology",
    "watchlist_candidates[].methodology.screen_style",
    "watchlist_candidates[].methodology.criteria",
    "watchlist_candidates[].methodology.criteria[].id",
    "watchlist_candidates[].methodology.criteria[].label",
    "watchlist_candidates[].methodology.criteria[].status",
    "watchlist_candidates[].methodology.criteria[].source_field",
    "watchlist_candidates[].methodology.evaluated_criterion_count",
    "watchlist_candidates[].methodology.passed_criterion_count",
    "watchlist_candidates[].methodology.pass_rate",
    "watchlist_candidates[].methodology.data_gaps",
    "watchlist_candidates[].rank",
    "watchlist_candidates[].candidate_tier",
    "enrichment_targets",
    "scorecard",
    "scorecard.investment_cycle",
    "scorecard.investment_cycle_assumption",
    "scorecard.candidate_count",
    "scorecard.eligible_candidate_count",
    "scorecard.top_candidate_count",
    "scorecard.watchlist_candidate_count",
    "scorecard.deep_checked_count",
    "scorecard.watchlist_deep_checked_count",
    "scorecard.enrichment_target_count",
    "scorecard.candidate_universe_source",
    "scorecard.requested_snapshot_types",
    "scorecard.collected_snapshot_types",
    "scorecard.preferred_methodology",
    "scorecard.screening_strategy",
    "scorecard.screening_strategy.methodology_source",
    "scorecard.screening_strategy.investment_cycle",
    "scorecard.screening_strategy.screen_style",
    "scorecard.summary",
    "screening_notes",
    "methodology",
    "methodology.screen_style",
    "methodology.requested_screen_style",
    "methodology.criteria",
    "methodology.criteria[].id",
    "methodology.criteria[].label",
    "methodology.criteria[].status",
    "methodology.criteria[].source_field",
  ],
  "sector_analysis": [
    "sector",
    "theme",
    "findings",
    "findings[].snapshot_type",
    "findings[].scope",
    "findings[].symbol",
    "findings[].sector",
    "findings[].theme",
    "findings[].query",
    "findings[].snapshot_date",
    "findings[].data_period",
    "findings[].retrieved_at",
    "findings[].created_at",
    "findings[].quality_status",
    "findings[].facts",
    "findings[].source",
    "findings[].source.skill",
    "findings[].source.vendor",
    "findings[].source.from_cache",
    "findings[].source.provider_id",
    "findings[].source.source_id",
    "findings[].source.chain_position",
    "findings[].source.dim",
    "findings[].primary_source",
    "findings[].fallback_sources",
    "findings[].fallback_sources[].skill",
    "findings[].fallback_sources[].vendor",
    "findings[].fallback_sources[].from_cache",
    "findings[].fallback_sources[].provider_id",
    "findings[].fallback_sources[].source_id",
    "findings[].fallback_sources[].chain_position",
    "findings[].fallback_sources[].dim",
    "findings[].field_sources",
    "findings[].fallback_applied",
    "findings[].missing_fields",
    "findings[].source_confidence",
    "findings[].source_trace",
    "findings[].source_refs",
    "findings[].source_refs[].type",
    "findings[].source_refs[].provider",
    "findings[].source_refs[].id",
    "findings[].degraded",
    "findings[].degrade_reason",
    "findings[].degrade_reasons",
    "related_snapshots",
    "related_snapshots[].snapshot_type",
    "related_snapshots[].scope",
    "related_snapshots[].symbol",
    "related_snapshots[].sector",
    "related_snapshots[].theme",
    "related_snapshots[].query",
    "related_snapshots[].snapshot_date",
    "related_snapshots[].data_period",
    "related_snapshots[].retrieved_at",
    "related_snapshots[].created_at",
    "related_snapshots[].quality_status",
    "related_snapshots[].facts",
    "related_snapshots[].source",
    "related_snapshots[].source.skill",
    "related_snapshots[].source.vendor",
    "related_snapshots[].source.from_cache",
    "related_snapshots[].source.provider_id",
    "related_snapshots[].source.source_id",
    "related_snapshots[].source.chain_position",
    "related_snapshots[].source.dim",
    "related_snapshots[].primary_source",
    "related_snapshots[].fallback_sources",
    "related_snapshots[].fallback_sources[].skill",
    "related_snapshots[].fallback_sources[].vendor",
    "related_snapshots[].fallback_sources[].from_cache",
    "related_snapshots[].fallback_sources[].provider_id",
    "related_snapshots[].fallback_sources[].source_id",
    "related_snapshots[].fallback_sources[].chain_position",
    "related_snapshots[].fallback_sources[].dim",
    "related_snapshots[].field_sources",
    "related_snapshots[].fallback_applied",
    "related_snapshots[].missing_fields",
    "related_snapshots[].source_confidence",
    "related_snapshots[].source_trace",
    "related_snapshots[].source_refs",
    "related_snapshots[].source_refs[].type",
    "related_snapshots[].source_refs[].provider",
    "related_snapshots[].source_refs[].id",
    "related_snapshots[].degraded",
    "related_snapshots[].degrade_reason",
    "related_snapshots[].degrade_reasons",
    "risks",
    "source_count",
    "analysis_notes",
    "sector_heatmap",
    "sector_heatmap[].name",
    "sector_heatmap[].symbol",
    "sector_heatmap[].score",
    "sector_heatmap[].raw_value",
    "sector_heatmap[].score_basis",
    "sector_heatmap[].source_count",
    "sector_heatmap[].related_snapshot_count",
    "sector_heatmap[].theme",
    "constituents",
    "breadth",
    "breadth.sample_size",
    "breadth.up_count",
    "breadth.down_count",
    "breadth.flat_count",
    "breadth.up_ratio",
    "breadth.down_ratio",
    "breadth.median_pct_change",
    "breadth.mean_pct_change",
    "breadth.average_turnover_rate",
    "breadth.top3_average_pct_change",
    "breadth.bottom3_average_pct_change",
    "breadth.best_constituents",
    "breadth.best_constituents[].code",
    "breadth.best_constituents[].name",
    "breadth.best_constituents[].pct_change",
    "breadth.best_constituents[].turnover_rate",
    "breadth.worst_constituents",
    "breadth.worst_constituents[].code",
    "breadth.worst_constituents[].name",
    "breadth.worst_constituents[].pct_change",
    "breadth.worst_constituents[].turnover_rate",
    "relative_strength",
    "relative_strength.latest_excess_pct_change",
    "relative_strength.recent_relative_strength",
    "relative_strength.recent_relative_strength[].date",
    "relative_strength.recent_relative_strength[].sector_pct_change",
    "relative_strength.recent_relative_strength[].benchmark_pct_change",
    "relative_strength.recent_relative_strength[].excess_pct_change",
    "signal_scorecard",
    "signal_scorecard.theme",
    "signal_scorecard.market_tone",
    "signal_scorecard.benchmark_view",
    "signal_scorecard.turnover_state",
    "signal_scorecard.breadth_state",
    "signal_scorecard.policy_signal_strength",
    "signal_scorecard.regulatory_signal_strength",
    "signal_scorecard.sample_signal_strength",
    "signal_scorecard.setup_label",
    "signal_scorecard.headline_counts",
    "signal_scorecard.headline_counts.theme_news",
    "signal_scorecard.headline_counts.policy_signals",
    "signal_scorecard.headline_counts.regulatory_signals",
    "signal_scorecard.headline_counts.catalyst_samples",
    "signal_scorecard.breadth",
    "signal_scorecard.breadth.sample_size",
    "signal_scorecard.breadth.up_count",
    "signal_scorecard.breadth.down_count",
    "signal_scorecard.breadth.flat_count",
    "signal_scorecard.breadth.up_ratio",
    "signal_scorecard.breadth.down_ratio",
    "signal_scorecard.breadth.median_pct_change",
    "signal_scorecard.breadth.mean_pct_change",
    "signal_scorecard.breadth.average_turnover_rate",
    "signal_scorecard.breadth.top3_average_pct_change",
    "signal_scorecard.breadth.bottom3_average_pct_change",
    "signal_scorecard.breadth.best_constituents",
    "signal_scorecard.breadth.best_constituents[].code",
    "signal_scorecard.breadth.best_constituents[].name",
    "signal_scorecard.breadth.best_constituents[].pct_change",
    "signal_scorecard.breadth.best_constituents[].turnover_rate",
    "signal_scorecard.breadth.worst_constituents",
    "signal_scorecard.breadth.worst_constituents[].code",
    "signal_scorecard.breadth.worst_constituents[].name",
    "signal_scorecard.breadth.worst_constituents[].pct_change",
    "signal_scorecard.breadth.worst_constituents[].turnover_rate",
    "signal_scorecard.summary",
    "catalysts",
    "catalysts.theme_news",
    "catalysts.policy_signals",
    "catalysts.regulatory_signals",
    "catalysts.samples",
    "policy_snapshots",
    "policy_snapshots[].snapshot_type",
    "policy_snapshots[].scope",
    "policy_snapshots[].symbol",
    "policy_snapshots[].sector",
    "policy_snapshots[].theme",
    "policy_snapshots[].query",
    "policy_snapshots[].snapshot_date",
    "policy_snapshots[].data_period",
    "policy_snapshots[].retrieved_at",
    "policy_snapshots[].created_at",
    "policy_snapshots[].quality_status",
    "policy_snapshots[].facts",
    "policy_snapshots[].source",
    "policy_snapshots[].source.skill",
    "policy_snapshots[].source.vendor",
    "policy_snapshots[].source.from_cache",
    "policy_snapshots[].source.provider_id",
    "policy_snapshots[].source.source_id",
    "policy_snapshots[].source.chain_position",
    "policy_snapshots[].source.dim",
    "policy_snapshots[].primary_source",
    "policy_snapshots[].fallback_sources",
    "policy_snapshots[].fallback_sources[].skill",
    "policy_snapshots[].fallback_sources[].vendor",
    "policy_snapshots[].fallback_sources[].from_cache",
    "policy_snapshots[].fallback_sources[].provider_id",
    "policy_snapshots[].fallback_sources[].source_id",
    "policy_snapshots[].fallback_sources[].chain_position",
    "policy_snapshots[].fallback_sources[].dim",
    "policy_snapshots[].field_sources",
    "policy_snapshots[].fallback_applied",
    "policy_snapshots[].missing_fields",
    "policy_snapshots[].source_confidence",
    "policy_snapshots[].source_trace",
    "policy_snapshots[].source_refs",
    "policy_snapshots[].source_refs[].type",
    "policy_snapshots[].source_refs[].provider",
    "policy_snapshots[].source_refs[].id",
    "policy_snapshots[].degraded",
    "policy_snapshots[].degrade_reason",
    "policy_snapshots[].degrade_reasons",
  ],
  "thesis_review": [
    "verdict",
    "overall_verdict",
    "findings",
    "findings[].claim",
    "findings[].artifact_id",
    "findings[].artifact_type",
    "findings[].summary",
    "findings[].verdict",
    "findings[].evidence_index",
    "findings[].snapshot_type",
    "findings[].scope",
    "findings[].symbol",
    "findings[].sector",
    "findings[].theme",
    "findings[].query",
    "findings[].snapshot_date",
    "findings[].data_period",
    "findings[].retrieved_at",
    "findings[].created_at",
    "findings[].quality_status",
    "findings[].facts",
    "findings[].source",
    "findings[].source.skill",
    "findings[].source.vendor",
    "findings[].source.from_cache",
    "findings[].source.provider_id",
    "findings[].source.source_id",
    "findings[].source.chain_position",
    "findings[].source.dim",
    "findings[].primary_source",
    "findings[].fallback_sources",
    "findings[].fallback_sources[].skill",
    "findings[].fallback_sources[].vendor",
    "findings[].fallback_sources[].from_cache",
    "findings[].fallback_sources[].provider_id",
    "findings[].fallback_sources[].source_id",
    "findings[].fallback_sources[].chain_position",
    "findings[].fallback_sources[].dim",
    "findings[].field_sources",
    "findings[].fallback_applied",
    "findings[].missing_fields",
    "findings[].source_confidence",
    "findings[].source_trace",
    "findings[].source_refs",
    "findings[].source_refs[].type",
    "findings[].source_refs[].provider",
    "findings[].source_refs[].id",
    "findings[].degraded",
    "findings[].degrade_reason",
    "findings[].degrade_reasons",
    "strengths",
    "strengths[].claim",
    "strengths[].artifact_id",
    "strengths[].artifact_type",
    "strengths[].summary",
    "strengths[].verdict",
    "strengths[].evidence_index",
    "strengths[].snapshot_type",
    "strengths[].scope",
    "strengths[].symbol",
    "strengths[].sector",
    "strengths[].theme",
    "strengths[].query",
    "strengths[].snapshot_date",
    "strengths[].data_period",
    "strengths[].retrieved_at",
    "strengths[].created_at",
    "strengths[].quality_status",
    "strengths[].facts",
    "strengths[].source",
    "strengths[].source.skill",
    "strengths[].source.vendor",
    "strengths[].source.from_cache",
    "strengths[].source.provider_id",
    "strengths[].source.source_id",
    "strengths[].source.chain_position",
    "strengths[].source.dim",
    "strengths[].primary_source",
    "strengths[].fallback_sources",
    "strengths[].fallback_sources[].skill",
    "strengths[].fallback_sources[].vendor",
    "strengths[].fallback_sources[].from_cache",
    "strengths[].fallback_sources[].provider_id",
    "strengths[].fallback_sources[].source_id",
    "strengths[].fallback_sources[].chain_position",
    "strengths[].fallback_sources[].dim",
    "strengths[].field_sources",
    "strengths[].fallback_applied",
    "strengths[].missing_fields",
    "strengths[].source_confidence",
    "strengths[].source_trace",
    "strengths[].source_refs",
    "strengths[].source_refs[].type",
    "strengths[].source_refs[].provider",
    "strengths[].source_refs[].id",
    "strengths[].degraded",
    "strengths[].degrade_reason",
    "strengths[].degrade_reasons",
    "weaknesses",
    "follow_up",
    "review_target_run_id",
    "review_target_artifact_ids",
    "review_target_found",
    "target_thesis",
    "target_thesis[].claim",
    "target_thesis[].artifact_id",
    "target_thesis[].artifact_type",
    "target_thesis[].summary",
    "target_thesis[].verdict",
    "target_thesis[].evidence_index",
    "target_thesis[].snapshot_type",
    "target_thesis[].scope",
    "target_thesis[].symbol",
    "target_thesis[].sector",
    "target_thesis[].theme",
    "target_thesis[].query",
    "target_thesis[].snapshot_date",
    "target_thesis[].data_period",
    "target_thesis[].retrieved_at",
    "target_thesis[].created_at",
    "target_thesis[].quality_status",
    "target_thesis[].facts",
    "target_thesis[].source",
    "target_thesis[].source.skill",
    "target_thesis[].source.vendor",
    "target_thesis[].source.from_cache",
    "target_thesis[].source.provider_id",
    "target_thesis[].source.source_id",
    "target_thesis[].source.chain_position",
    "target_thesis[].source.dim",
    "target_thesis[].primary_source",
    "target_thesis[].fallback_sources",
    "target_thesis[].fallback_sources[].skill",
    "target_thesis[].fallback_sources[].vendor",
    "target_thesis[].fallback_sources[].from_cache",
    "target_thesis[].fallback_sources[].provider_id",
    "target_thesis[].fallback_sources[].source_id",
    "target_thesis[].fallback_sources[].chain_position",
    "target_thesis[].fallback_sources[].dim",
    "target_thesis[].field_sources",
    "target_thesis[].fallback_applied",
    "target_thesis[].missing_fields",
    "target_thesis[].source_confidence",
    "target_thesis[].source_trace",
    "target_thesis[].source_refs",
    "target_thesis[].source_refs[].type",
    "target_thesis[].source_refs[].provider",
    "target_thesis[].source_refs[].id",
    "target_thesis[].degraded",
    "target_thesis[].degrade_reason",
    "target_thesis[].degrade_reasons",
    "evidence_comparison",
    "evidence_comparison.target_artifact_count",
    "evidence_comparison.fresh_evidence_count",
    "evidence_comparison.fresh_snapshot_types",
    "evidence_comparison.fresh_symbols",
  ],
  "source_record_set": [
    "query",
    "records",
    "records[].record_id",
    "records[].text",
    "records[].author",
    "records[].published_at",
    "records[].tags",
    "records[].dimensions",
  ],
  "distillation_result": [
    "dimensions",
    "dimensions[].dimension_name",
    "dimensions[].category",
    "dimensions[].claim",
    "dimensions[].source_record_ids",
    "dimensions[].confidence",
    "dimensions[].attribution",
    "summary",
    "summary.source_record_count",
    "summary.dimension_count",
    "summary.major_risks",
    "summary.method",
  ],
  "distillation_review": [
    "verdict",
    "findings",
    "findings[].finding_type",
    "findings[].severity",
    "findings[].dimension_name",
    "findings[].reason",
    "distillation",
    "distillation.dimensions",
    "distillation.dimensions[].dimension_name",
    "distillation.dimensions[].category",
    "distillation.dimensions[].claim",
    "distillation.dimensions[].source_record_ids",
    "distillation.dimensions[].confidence",
    "distillation.dimensions[].attribution",
    "distillation.summary",
    "distillation.summary.source_record_count",
    "distillation.summary.dimension_count",
    "distillation.summary.major_risks",
    "distillation.summary.method",
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
    "models[].model",
    "models[].methodology_version",
    "models[].symbol",
    "models[].status",
    "models[].fair_value",
    "models[].market_price",
    "models[].margin_of_safety",
    "models[].assumptions",
    "models[].assumptions.wacc",
    "models[].assumptions.terminal_growth",
    "models[].assumptions.stage_one_growth",
    "models[].assumptions.stage_two_growth",
    "models[].assumptions.forecast_years",
    "models[].assumptions.risk_free_rate",
    "models[].assumptions.equity_risk_premium",
    "models[].assumptions.beta",
    "models[].assumptions.tax_rate",
    "models[].assumptions.target_debt_ratio",
    "models[].assumptions.cost_of_equity",
    "models[].assumptions.after_tax_cost_of_debt",
    "models[].assumptions.total_debt_yi",
    "models[].assumptions.cash_yi",
    "models[].sensitivity",
    "models[].sensitivity[].wacc",
    "models[].sensitivity[].terminal_growth",
    "models[].sensitivity[].fair_value",
    "models[].verdict",
    "models[].confidence",
    "models[].warnings",
    "models[].data_gap",
    "models[].assumption_flags",
    "models[].field_sources",
    "models[].methodology_log",
    "models[].comps",
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
    "votes[].symbol",
    "votes[].investor_id",
    "votes[].perspective_id",
    "votes[].perspective_label",
    "votes[].school",
    "votes[].required_dimensions",
    "votes[].signal",
    "votes[].score",
    "votes[].confidence",
    "votes[].reasoning",
    "votes[].headline",
    "votes[].pass_rules",
    "votes[].pass_rules[].rule_id",
    "votes[].pass_rules[].name",
    "votes[].pass_rules[].weight",
    "votes[].pass_rules[].msg",
    "votes[].pass_rules[].missing_fields",
    "votes[].fail_rules",
    "votes[].fail_rules[].rule_id",
    "votes[].fail_rules[].name",
    "votes[].fail_rules[].weight",
    "votes[].fail_rules[].msg",
    "votes[].fail_rules[].missing_fields",
    "votes[].skipped_rules",
    "votes[].skipped_rules[].rule_id",
    "votes[].skipped_rules[].name",
    "votes[].skipped_rules[].weight",
    "votes[].skipped_rules[].msg",
    "votes[].skipped_rules[].missing_fields",
    "votes[].data_gap_rules",
    "votes[].pass_count",
    "votes[].fail_count",
    "votes[].skipped_count",
    "votes[].weight_pass",
    "votes[].weight_total",
    "votes[].rule_engine_version",
    "votes[].checks",
    "votes[].checks[].dimension",
    "votes[].checks[].status",
    "votes[].checks[].feature_names",
    "votes[].is_methodology_perspective",
    "votes[].is_real_person_advice",
    "votes[].rule_mode",
    "distribution",
    "distribution.bullish",
    "distribution.bearish",
    "distribution.neutral",
    "distribution.insufficient_evidence",
    "average_score",
    "evaluated_rule_count",
    "skipped_rule_count",
    "quality_status",
    "data_gap",
    "disagreement",
    "disagreement.exists",
    "disagreement.summary",
    "confidence",
    "risk_flags",
  ],
  "risk_scan": [
    "methodology_version",
    "profile",
    "symbol",
    "risk_level",
    "signals",
    "signals[].signal_id",
    "signals[].status",
    "signals[].severity",
    "signals[].finding",
    "signals[].evidence_refs",
    "signals[].evidence_refs[].type",
    "signals[].evidence_refs[].id",
    "signals[].evidence_refs[].provider",
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
    "holdings[].ticker",
    "holdings[].symbol",
    "holdings[].name",
    "holdings[].weight",
    "holdings[].normalized_weight",
    "holdings[].return_pct",
    "holdings[].contribution_pct",
    "holdings[].status",
    "holdings[].industry",
    "holdings[].market",
    "holdings[].asset_class",
    "holdings[].value",
    "holdings[].market_value",
    "holdings[].quantity",
    "holdings[].price",
    "observed_weight_coverage",
    "observed_contribution_pct",
    "total_return_pct",
    "groups",
    "groups.industry",
    "groups.industry[].industry",
    "groups.industry[].market",
    "groups.industry[].weight",
    "groups.industry[].observed_weight",
    "groups.industry[].contribution_pct",
    "groups.industry[].missing_tickers",
    "groups.industry[].status",
    "groups.market",
    "groups.market[].industry",
    "groups.market[].market",
    "groups.market[].weight",
    "groups.market[].observed_weight",
    "groups.market[].contribution_pct",
    "groups.market[].missing_tickers",
    "groups.market[].status",
    "benchmark",
    "benchmark.benchmark_return_pct",
    "benchmark.excess_return_pct",
    "benchmark.status",
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
    "drift_table[].ticker",
    "drift_table[].symbol",
    "drift_table[].name",
    "drift_table[].weight",
    "drift_table[].normalized_weight",
    "drift_table[].target_weight",
    "drift_table[].drift_pp",
    "drift_table[].breached",
    "drift_table[].action",
    "drift_table[].trade_amount",
    "drift_table[].estimated_shares",
    "drift_table[].estimated_cost",
    "drift_table[].return_pct",
    "drift_table[].industry",
    "drift_table[].market",
    "drift_table[].asset_class",
    "drift_table[].value",
    "drift_table[].market_value",
    "drift_table[].quantity",
    "drift_table[].price",
    "trades",
    "trades[].ticker",
    "trades[].action",
    "trades[].trade_amount",
    "trades[].estimated_shares",
    "trades[].estimated_cost",
    "trades[].drift_pp",
    "concentration",
    "concentration.before_hhi",
    "concentration.after_hhi",
    "concentration.before_top3_weight",
    "concentration.after_top3_weight",
    "estimated_cost",
    "quality_status",
    "data_gap",
    "assumption_flags",
    "generated_at",
  ],
  "portfolio_rebalance": [
    "methodology_version",
    "method",
    "target_mode",
    "drift_threshold_pp",
    "asset_classes",
    "asset_classes[].asset_class",
    "asset_classes[].current_weight",
    "asset_classes[].target_weight",
    "asset_classes[].drift_pp",
    "asset_classes[].breached",
    "asset_classes[].action",
    "asset_classes[].suggested_value_change",
    "portfolio_value",
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
