import rendererContract from './finance-artifact-renderers.json'
import {
  FINANCE_ARTIFACT_TYPES,
  type FinanceArtifactType,
} from './generated/finance-artifact-contracts'
import * as Renderers from './renderers'
import type { ArtifactRendererComponent } from './types'

export const FINANCE_ARTIFACT_RENDERERS = {
  user_request: Renderers.UserRequestArtifactRenderer,
  normalized_query: Renderers.NormalizedQueryArtifactRenderer,
  stock_snapshot_bundle: Renderers.StockSnapshotBundleArtifactRenderer,
  snapshot: Renderers.SnapshotArtifactRenderer,
  evidence_set: Renderers.EvidenceSetArtifactRenderer,
  financial_analysis: Renderers.FinancialAnalysisArtifactRenderer,
  screening_result: Renderers.ScreeningResultArtifactRenderer,
  sector_analysis: Renderers.SectorAnalysisArtifactRenderer,
  thesis_review: Renderers.ThesisReviewArtifactRenderer,
  source_record_set: Renderers.SourceRecordSetArtifactRenderer,
  distillation_result: Renderers.DistillationResultArtifactRenderer,
  distillation_review: Renderers.DistillationReviewArtifactRenderer,
  research_report: Renderers.ResearchReportArtifactRenderer,
  main_agent_synthesis: Renderers.MainAgentSynthesisArtifactRenderer,
  valuation_model: Renderers.ValuationModelArtifactRenderer,
  investor_panel_review: Renderers.InvestorPanelReviewArtifactRenderer,
  risk_scan: Renderers.RiskScanArtifactRenderer,
  quick_scan: Renderers.QuickScanArtifactRenderer,
  uzi_native_method_result: Renderers.UziNativeMethodResultArtifactRenderer,
  uzi_native_command_result: Renderers.UziNativeCommandResultArtifactRenderer,
  uzi_native_analysis: Renderers.UziNativeAnalysisArtifactRenderer,
  institutional_analysis: Renderers.InstitutionalAnalysisArtifactRenderer,
  research_workflow: Renderers.ResearchWorkflowArtifactRenderer,
  deep_analysis: Renderers.DeepAnalysisArtifactRenderer,
  uzi_dimension_coverage: Renderers.UziDimensionCoverageArtifactRenderer,
  fund_snapshot: Renderers.FundSnapshotArtifactRenderer,
  fund_analysis: Renderers.FundAnalysisArtifactRenderer,
  fund_holding_report_batch: Renderers.FundHoldingReportBatchArtifactRenderer,
  ai_readiness: Renderers.AiReadinessArtifactRenderer,
  earnings_preview: Renderers.EarningsPreviewArtifactRenderer,
  model_update: Renderers.ModelUpdateArtifactRenderer,
  returns_attribution: Renderers.ReturnsAttributionArtifactRenderer,
  holding_rebalance: Renderers.HoldingRebalanceArtifactRenderer,
  portfolio_rebalance: Renderers.PortfolioRebalanceArtifactRenderer,
  three_statement_model: Renderers.ThreeStatementModelArtifactRenderer,
  quick_lbo: Renderers.QuickLboArtifactRenderer,
  accretion_dilution: Renderers.AccretionDilutionArtifactRenderer,
  segmental_model: Renderers.SegmentalModelArtifactRenderer,
  initiation_report: Renderers.InitiationReportArtifactRenderer,
  earnings_review: Renderers.EarningsReviewArtifactRenderer,
  catalyst_calendar: Renderers.CatalystCalendarArtifactRenderer,
  thesis_tracker: Renderers.ThesisTrackerArtifactRenderer,
  morning_note: Renderers.MorningNoteArtifactRenderer,
  ic_memo: Renderers.IcMemoArtifactRenderer,
  porter_bcg: Renderers.PorterBcgArtifactRenderer,
  dd_checklist: Renderers.DdChecklistArtifactRenderer,
  unit_economics: Renderers.UnitEconomicsArtifactRenderer,
  value_creation_plan: Renderers.ValueCreationPlanArtifactRenderer,
} satisfies Record<FinanceArtifactType, ArtifactRendererComponent>

const artifactTypeSet = new Set<string>(FINANCE_ARTIFACT_TYPES)
const contractRows = rendererContract.artifacts as Record<string, { title: string; renderer: string; schema_field_digest: string }>

export function isFinanceArtifactType(value: string): value is FinanceArtifactType {
  return artifactTypeSet.has(value)
}

export function financeArtifactTitle(artifactType: FinanceArtifactType): string {
  return contractRows[artifactType].title
}

export function financeArtifactRendererName(artifactType: FinanceArtifactType): string {
  return contractRows[artifactType].renderer
}
