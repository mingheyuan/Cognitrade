import {
  AlertTriangle,
  BrainCircuit,
  ChartNoAxesCombined,
  Database,
  Gauge,
  Scale,
  ShieldCheck,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { BusinessDataView } from './BusinessDataView'
import { financeArtifactTitle, isFinanceArtifactType } from './registry'
import type { FinanceArtifactType } from './generated/finance-artifact-contracts'
import type { ArtifactRecordView } from './types'

const DECISION_TYPES = new Set<FinanceArtifactType>([
  'valuation_model', 'investor_panel_review', 'risk_scan', 'uzi_dimension_coverage',
])

const SYSTEM_METHOD_TYPES = new Set<FinanceArtifactType>([
  'institutional_analysis', 'research_workflow', 'deep_analysis',
])

const SPECIALIST_METHOD_TYPES = new Set<FinanceArtifactType>([
  'ai_readiness', 'earnings_preview', 'model_update', 'returns_attribution',
  'holding_rebalance', 'portfolio_rebalance', 'three_statement_model', 'quick_lbo',
  'accretion_dilution', 'segmental_model', 'initiation_report', 'earnings_review',
  'catalyst_calendar', 'thesis_tracker', 'morning_note', 'ic_memo', 'porter_bcg',
  'dd_checklist', 'unit_economics', 'value_creation_plan', 'uzi_native_method_result',
  'uzi_native_command_result',
])

type MethodDeliveryDossierProps = {
  artifacts: Array<Record<string, unknown>>
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function numberValue(value: unknown): number | null {
  const parsed = Number(value)
  return value == null || value === '' || !Number.isFinite(parsed) ? null : parsed
}

function text(value: unknown, fallback = '未提供'): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback
}

function normalizedArtifacts(artifacts: Array<Record<string, unknown>>): Array<ArtifactRecordView & { artifact_type: FinanceArtifactType }> {
  return artifacts.flatMap((artifact) => {
    const artifactType = String(artifact.artifact_type || '')
    if (!isFinanceArtifactType(artifactType)) return []
    if (['superseded', 'rejected'].includes(String(artifact.status || 'active'))) return []
    const typed = artifact as ArtifactRecordView
    return [{ ...typed, artifact_type: artifactType }]
  })
}

function qualityLabel(value: unknown): string {
  return ({
    available: '数据可用', degraded: '部分可用', data_gap: '数据不足',
    assumption_only: '仅含显式假设', active: '有效',
  } as Record<string, string>)[String(value || '')] || String(value || '质量状态未提供')
}

function MethodArtifactCard({ artifact }: { artifact: ArtifactRecordView & { artifact_type: FinanceArtifactType } }) {
  const content = asRecord(artifact.content)
  const type = artifact.artifact_type
  const quality = content.quality_status || artifact.status || 'active'
  const isPanel = type === 'investor_panel_review'
  const isValuation = type === 'valuation_model'
  const isRisk = type === 'risk_scan'
  const isPortfolio = ['returns_attribution', 'holding_rebalance', 'portfolio_rebalance'].includes(type)
  const count = isPanel
    ? asArray(content.votes).length
    : isValuation
      ? asArray(content.models).length
      : isRisk
        ? asArray(content.signals).length
        : isPortfolio
          ? asArray(content.holdings || content.drift_table || content.asset_classes).length
          : asArray(content.results).length

  return (
    <details className="method-delivery-card" open={DECISION_TYPES.has(type) || isPortfolio} data-method-artifact={type}>
      <summary>
        <span className="method-delivery-card__icon">
          {isValuation ? <ChartNoAxesCombined size={16} /> : isPanel ? <Scale size={16} /> : isRisk ? <AlertTriangle size={16} /> : <BrainCircuit size={16} />}
        </span>
        <span><strong>{financeArtifactTitle(type)}</strong><small>{type} · {text(content.methodology_version, `Schema ${artifact.schema_version || '1'}`)}</small></span>
        <span className={`artifact-quality-tag is-${String(quality)}`}>{qualityLabel(quality)}</span>
        <span className="method-delivery-card__count">{count || '完整'} 项</span>
      </summary>
      <div className="method-delivery-card__body">
        <div className="method-delivery-card__audit">
          <span><small>Artifact 编号</small><strong title={artifact.artifact_id}>{artifact.artifact_id || '—'}</strong></span>
          <span><small>置信度</small><strong>{numberValue(artifact.confidence) == null ? '未提供' : `${(numberValue(artifact.confidence)! * 100).toFixed(1)}%`}</strong></span>
          <span><small>业务项数量</small><strong>{count}</strong></span>
          <span><small>数据质量</small><strong>{qualityLabel(quality)}</strong></span>
        </div>
        <BusinessDataView value={content} fieldKey={type} path={type} />
        {(asArray(artifact.source_refs).length > 0 || asArray(artifact.risk_flags).length > 0) && (
          <div className="method-delivery-card__provenance">
            <section><header><ShieldCheck size={13} /><h4>全部证据引用</h4></header><BusinessDataView value={artifact.source_refs || []} fieldKey="source_refs" path={`${type}.source_refs`} compact /></section>
            <section><header><AlertTriangle size={13} /><h4>全部风险标记</h4></header><BusinessDataView value={artifact.risk_flags || []} fieldKey="risk_flags" path={`${type}.risk_flags`} compact /></section>
          </div>
        )}
      </div>
    </details>
  )
}

function ArtifactGroup({ title, description, icon, artifacts }: { title: string; description: string; icon: ReactNode; artifacts: Array<ArtifactRecordView & { artifact_type: FinanceArtifactType }> }) {
  if (!artifacts.length) return null
  return (
    <section className="method-delivery-group">
      <header><span>{icon}</span><div><h3>{title}</h3><p>{description}</p></div><strong>{artifacts.length} 项 Artifact</strong></header>
      <div>{artifacts.map((artifact, index) => <MethodArtifactCard key={artifact.artifact_id || `${artifact.artifact_type}-${index}`} artifact={artifact} />)}</div>
    </section>
  )
}

export function MethodDeliveryDossier({ artifacts }: MethodDeliveryDossierProps) {
  const normalized = normalizedArtifacts(artifacts)
  const decisions = normalized.filter((artifact) => DECISION_TYPES.has(artifact.artifact_type))
  const systems = normalized.filter((artifact) => SYSTEM_METHOD_TYPES.has(artifact.artifact_type))
  const methods = normalized.filter((artifact) => SPECIALIST_METHOD_TYPES.has(artifact.artifact_type))
  if (!decisions.length && !systems.length && !methods.length) return null
  return (
    <section className="method-delivery-dossier" aria-label="完整方法论交付">
      <header className="method-delivery-dossier__header">
        <div><span className="section-kicker">COMPLETE METHOD OUTPUTS</span><h2>完整方法论交付</h2><p>展示本次运行返回的全部估值、评审、风险、机构方法和组合方法字段；不截断列表，不使用模拟值。</p></div>
        <span><Database size={14} />{decisions.length + systems.length + methods.length} 项方法 Artifact</span>
      </header>
      <ArtifactGroup title="决策辅助与证据覆盖" description="估值、评审团、风险扫描和 UZI 维度覆盖。" icon={<Gauge size={16} />} artifacts={decisions} />
      <ArtifactGroup title="机构研究体系" description="机构模型、研究工作流与 Deep 交叉检验的全部结果。" icon={<ChartNoAxesCombined size={16} />} artifacts={systems} />
      <ArtifactGroup title="专项方法与组合" description="个股专项、收益归因、持仓再平衡与资产配置的全部结果。" icon={<BrainCircuit size={16} />} artifacts={methods} />
    </section>
  )
}
