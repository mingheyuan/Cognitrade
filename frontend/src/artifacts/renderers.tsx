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
  return <ArtifactData content={content} priority={METHOD_PRIORITY} />
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
  return <ArtifactData content={content} priority={['roleplay', 'average_score', 'distribution', 'disagreement', 'votes', 'per_investor_override', 'confidence', 'risk_flags', 'profile', 'scope', 'panel_version']} />
}

export function RiskScanArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['symbol', 'risk_level', 'recommendation', 'signals_hit_count', 'signals', 'confidence', 'risk_flags']} />
}

export function QuickScanArtifactRenderer({ content }: ArtifactRendererProps) {
  const votes = Array.isArray(content.votes)
    ? content.votes.filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value))
    : []
  const risks = Array.isArray(content.risks) ? content.risks.map(String) : []
  const score = typeof content.aggregate_score === 'number' ? content.aggregate_score : null
  const scoreLabel = score == null
    ? content.aggregate_score == null ? '未评分' : String(content.aggregate_score)
    : `${score.toFixed(1)} / 100`
  const summaryFields = new Set(['verdict', 'aggregate_score', 'votes', 'trap_level', 'risks', 'markdown'])
  const auditContent = Object.fromEntries(
    Object.entries(content).filter(([field]) => !summaryFields.has(field)),
  )
  return (
    <section className="quick-scan-artifact" aria-label="UZI 股票速判结果">
      <header className="quick-scan-artifact__summary">
        <span><small>一句话定调</small><strong>{String(content.verdict || '证据不足，暂不能形成结论')}</strong></span>
        <span><small>综合评分</small><strong>{scoreLabel}</strong></span>
        <span><small>杀猪盘安全等级</small><strong>{String(content.trap_level || '数据缺口，无法判断')}</strong></span>
      </header>
      <div className="quick-scan-artifact__votes">
        {votes.map((vote) => (
          <article key={String(vote.investor_id)} data-signal={String(vote.signal || 'unknown')}>
            <span>{String(vote.perspective_label || vote.investor_id || '方法论席位')}</span>
            <strong>{String(vote.signal || '未知')}</strong>
            <b>{Number(vote.score) >= 0 ? `${Number(vote.score).toFixed(1)} 分` : '跳过'}</b>
            <p>{String(vote.headline || vote.reasoning || '无有效规则命中')}</p>
            <details><summary>查看完整席位审计</summary><BusinessDataView value={vote} compact /></details>
          </article>
        ))}
        {!Array.isArray(content.votes) && content.votes != null && <span>{String(content.votes)}</span>}
      </div>
      <section className="quick-scan-artifact__risks">
        <h4>关键风险</h4>
        {risks.map((risk) => <p key={risk}>{risk}</p>)}
        {!Array.isArray(content.risks) && content.risks != null && <p>{String(content.risks)}</p>}
      </section>
      <details className="quick-scan-artifact__details">
        <summary>完整命令产物与审计字段</summary>
        <pre>{String(content.markdown || '')}</pre>
        <ArtifactData content={auditContent} priority={['required_dimensions', 'dimension_status', 'investor_ids', 'quality_status', 'confidence', 'data_gap', 'risk_flags', 'methodology_version', 'source_commit']} />
      </details>
    </section>
  )
}

export function UziNativeAnalysisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['report_allowed', 'quality_status', 'critical_count', 'warning_count', 'profile', 'results', 'recovery_history', 'methodology_version', 'source_commit']} />
}

export function UziNativeMethodResultArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['method_id', 'quality_status', 'results', 'data_gap', 'assumptions', 'source_commit', 'implementation']} />
}

export function UziNativeCommandResultArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['command_id', 'phase', 'quality_status', 'results', 'data_gap', 'source_commit', 'implementation']} />
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
  return <ArtifactData content={content} priority={['query', 'analysis_mode', 'fund_category', 'investment_cycle', 'candidate_pool_size', 'fund_codes', 'funds', 'quality_status', 'data_gap', 'provider_errors', 'retrieved_at']} />
}

export function FundAnalysisArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['analysis_mode', 'fund_category', 'investment_cycle', 'candidate_count', 'recommendations', 'candidates', 'selection_policy', 'quality_status', 'data_gap', 'provider_errors', 'methodology_version', 'generated_at']} />
}

export function FundHoldingReportBatchArtifactRenderer({ content }: ArtifactRendererProps) {
  return <ArtifactData content={content} priority={['fund_name', 'fund_code', 'profile', 'status', 'counts', 'disclosure_date', 'stale_level', 'holding_source', 'holdings', 'data_gap', 'source_run_id', 'source_artifact_id']} />
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
