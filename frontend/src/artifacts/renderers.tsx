import { BusinessDataView } from './BusinessDataView'
import type { ArtifactRendererProps } from './types'

function orderedContent(content: Record<string, unknown>, priority: readonly string[]) {
  const ordered: Record<string, unknown> = {}
  for (const key of priority) {
    if (Object.hasOwn(content, key)) ordered[key] = content[key]
  }
  for (const [key, value] of Object.entries(content)) {
    if (!Object.hasOwn(ordered, key)) ordered[key] = value
  }
  return ordered
}

function ArtifactData({ content, priority }: { content: Record<string, unknown>; priority: readonly string[] }) {
  return <BusinessDataView value={orderedContent(content, priority)} />
}

const REQUEST_PRIORITY = ['query', 'symbols', 'fund_codes', 'fund_category', 'sector', 'portfolio_holdings', 'requested_methods'] as const
const SNAPSHOT_PRIORITY = ['snapshot_type', 'scope', 'symbol', 'sector', 'snapshot_date', 'data_period', 'quality_status', 'facts', 'missing_fields', 'degrade_reasons'] as const
const REPORT_PRIORITY = ['conclusion', 'assessment', 'risk', 'follow_up', 'evidence', 'quality_status'] as const
const METHOD_PRIORITY = ['method', 'quality_status', 'results', 'data_gap', 'assumption_flags', 'methodology_version', 'generated_at'] as const
const QUALITY_PRIORITY = ['quality_status', 'confidence', 'data_gap', 'risk_flags'] as const

export function UserRequestArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={REQUEST_PRIORITY} />
}

export function NormalizedQueryArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={REQUEST_PRIORITY} />
}

export function StockSnapshotBundleArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['query', 'symbols', 'sector', 'collection', 'candidate_universe', 'snapshots', 'snapshot_keys']} />
}

export function SnapshotArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={SNAPSHOT_PRIORITY} />
}

export function EvidenceSetArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['query', 'symbols', 'items']} />
}

export function FinancialAnalysisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['summary', 'symbols', 'scorecards', 'methodologies']} />
}

export function ScreeningResultArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['scorecard', 'screened_candidates', 'watchlist_candidates', 'enrichment_targets', 'screening_notes', 'methodology']} />
}

export function SectorAnalysisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['sector', 'theme', 'signal_scorecard', 'breadth', 'relative_strength', 'sector_heatmap', 'constituents', 'catalysts', 'policy_snapshots', 'findings', 'risks']} />
}

export function ThesisReviewArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['overall_verdict', 'verdict', 'review_target_run_id', 'review_target_found', 'target_thesis', 'evidence_comparison', 'findings', 'strengths', 'weaknesses', 'follow_up']} />
}

export function SourceRecordSetArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['query', 'records']} />
}

export function DistillationResultArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['summary', 'dimensions']} />
}

export function DistillationReviewArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['verdict', 'findings', 'distillation']} />
}

export function ResearchReportArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={REPORT_PRIORITY} />
}

export function MainAgentSynthesisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['conclusion', 'assistant_message', 'model_generated', 'source_artifact_id', 'follow_up']} />
}

export function ValuationModelArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['primary_symbol', 'profile', 'models', 'methodologies', 'methodology_version']} />
}

export function InvestorPanelReviewArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['average_score', 'distribution', 'disagreement', 'votes', 'confidence', 'risk_flags', 'profile', 'scope', 'panel_version']} />
}

export function RiskScanArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['symbol', 'risk_level', 'recommendation', 'signals_hit_count', 'signals', 'confidence', 'risk_flags']} />
}

export function ReportPresentationArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['subject', 'hero', 'core_conclusion', 'dashboard', 'great_divide', 'panel', 'radar', 'deep_scan', 'panel_insights', 'risk_list', 'buy_zones', 'institutional_modeling', 'advanced_methods']} />
}

export function InstitutionalAnalysisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['summary', 'profile', 'methods', 'results', ...QUALITY_PRIORITY]} />
}

export function ResearchWorkflowArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['summary', 'profile', 'results', ...QUALITY_PRIORITY]} />
}

export function DeepAnalysisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['summary', 'profile', 'results', ...QUALITY_PRIORITY]} />
}

export function UziDimensionCoverageArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['profile', 'available_count', 'missing_count', 'dimension_count', 'coverage', 'confidence', 'data_gap', 'risk_flags']} />
}

export function FundSnapshotArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['query', 'fund_category', 'investment_cycle', 'candidate_pool_size', 'fund_codes', 'funds', 'quality_status', 'data_gap', 'provider_errors', 'retrieved_at']} />
}

export function FundAnalysisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['fund_category', 'investment_cycle', 'candidate_count', 'recommendations', 'candidates', 'selection_policy', 'quality_status', 'data_gap', 'provider_errors', 'methodology_version', 'generated_at']} />
}

export function AiReadinessArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['companies', ...METHOD_PRIORITY]} />
}

export function EarningsPreviewArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['previews', ...METHOD_PRIORITY]} />
}

export function ModelUpdateArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['updates', 'impacts', ...METHOD_PRIORITY]} />
}

export function ReturnsAttributionArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['total_return_pct', 'observed_contribution_pct', 'observed_weight_coverage', 'benchmark', 'groups', 'holdings', ...METHOD_PRIORITY]} />
}

export function HoldingRebalanceArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['target_mode', 'drift_threshold_pp', 'concentration', 'drift_table', 'trades', 'estimated_cost', ...METHOD_PRIORITY]} />
}

export function PortfolioRebalanceArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['asset_classes', ...METHOD_PRIORITY]} />
}

export function ThreeStatementModelArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function QuickLboArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function AccretionDilutionArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function SegmentalModelArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function InitiationReportArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function EarningsReviewArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function CatalystCalendarArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function ThesisTrackerArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function MorningNoteArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function IcMemoArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function PorterBcgArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function DdChecklistArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function UnitEconomicsArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

export function ValueCreationPlanArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
}

