import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Database,
  FileSearch,
  Search,
  ShieldCheck,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { BusinessDataView } from './BusinessDataView'
import type { FinanceArtifactType } from './generated/finance-artifact-contracts'
import {
  FINANCE_ARTIFACT_RENDERERS,
  financeArtifactRendererName,
  financeArtifactTitle,
  isFinanceArtifactType,
} from './registry'
import type { ArtifactRecordView } from './types'

type ArtifactArchiveProps = {
  artifacts: Array<Record<string, unknown>>
}

const GROUPS: Array<{
  id: string
  title: string
  types: readonly FinanceArtifactType[]
}> = [
  {
    id: 'delivery',
    title: '结论与交付',
    types: ['main_agent_synthesis', 'research_report', 'quick_scan'],
  },
  {
    id: 'analysis',
    title: '核心分析',
    types: [
      'financial_analysis', 'valuation_model', 'investor_panel_review', 'risk_scan',
      'institutional_analysis', 'research_workflow', 'deep_analysis', 'uzi_dimension_coverage',
    ],
  },
  {
    id: 'intent',
    title: '专属业务结果',
    types: [
      'fund_snapshot', 'fund_analysis', 'screening_result', 'sector_analysis',
      'thesis_review', 'source_record_set', 'distillation_result', 'distillation_review',
    ],
  },
  {
    id: 'methods',
    title: '专项方法与组合',
    types: [
      'uzi_native_method_result', 'uzi_native_command_result', 'ai_readiness', 'earnings_preview', 'model_update', 'returns_attribution',
      'holding_rebalance', 'portfolio_rebalance', 'three_statement_model', 'quick_lbo',
      'accretion_dilution', 'segmental_model', 'initiation_report', 'earnings_review',
      'catalyst_calendar', 'thesis_tracker', 'morning_note', 'ic_memo', 'porter_bcg',
      'dd_checklist', 'unit_economics', 'value_creation_plan',
    ],
  },
  {
    id: 'evidence',
    title: '请求、快照与证据',
    types: ['user_request', 'normalized_query', 'stock_snapshot_bundle', 'snapshot', 'evidence_set'],
  },
]

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function asArtifact(value: Record<string, unknown>): ArtifactRecordView {
  return value as ArtifactRecordView
}

function numberValue(value: unknown): number | null {
  const parsed = Number(value)
  return value == null || value === '' || !Number.isFinite(parsed) ? null : parsed
}

function qualityStatus(artifact: ArtifactRecordView): string {
  return String(asRecord(artifact.content).quality_status || asRecord(artifact.metadata).quality_status || artifact.status || 'active')
}

function qualityLabel(value: string): string {
  return ({
    available: '数据可用',
    degraded: '部分可用',
    data_gap: '数据不足',
    assumption_only: '仅含显式假设',
    active: '有效',
    needs_review: '需要审核',
    superseded: '已被替代',
    rejected: '已拒绝',
  } as Record<string, string>)[value] || value
}

function formatDate(value: unknown): string {
  const date = new Date(String(value || ''))
  return Number.isNaN(date.getTime())
    ? String(value || '—')
    : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function ArtifactCard({ artifact, artifactType, index }: { artifact: ArtifactRecordView; artifactType: FinanceArtifactType; index: number }) {
  const Renderer = FINANCE_ARTIFACT_RENDERERS[artifactType]
  const content = asRecord(artifact.content)
  const sourceRefs = Array.isArray(artifact.source_refs) ? artifact.source_refs : []
  const riskFlags = Array.isArray(artifact.risk_flags) ? artifact.risk_flags : []
  const confidence = numberValue(artifact.confidence)
  const quality = qualityStatus(artifact)
  const defaultOpen = ['research_report', 'main_agent_synthesis', 'fund_analysis', 'quick_scan'].includes(artifactType) && index === 0
  return (
    <details className="artifact-archive-card" open={defaultOpen} data-artifact-type={artifactType}>
      <summary>
        <span className={`artifact-archive-icon is-${quality}`}><Database size={15} /></span>
        <span className="artifact-archive-title">
          <strong>{financeArtifactTitle(artifactType)}</strong>
          <small>{artifactType} · Schema {artifact.schema_version || '1'} · 第 {artifact.artifact_version || 1} 版</small>
        </span>
        <span className={`artifact-quality-tag is-${quality}`}>{qualityLabel(quality)}</span>
        <ChevronDown className="artifact-summary-chevron" size={16} />
      </summary>
      <div className="artifact-archive-body">
        <div className="artifact-audit-strip">
          <span><small>Artifact 编号</small><strong title={artifact.artifact_id}>{artifact.artifact_id || '—'}</strong></span>
          <span><small>生成时间</small><strong>{formatDate(artifact.created_at)}</strong></span>
          <span><small>置信度</small><strong>{confidence == null ? '未提供' : `${(confidence * 100).toFixed(1)}%`}</strong></span>
          <span><small>专属渲染器</small><strong>{financeArtifactRendererName(artifactType)}</strong></span>
        </div>
        <Renderer artifact={artifact} content={content} artifactType={artifactType} />
        {(sourceRefs.length > 0 || riskFlags.length > 0) && (
          <div className="artifact-provenance-grid">
            <section>
              <header><ShieldCheck size={14} /><h4>证据来源</h4><span>{sourceRefs.length} 项</span></header>
              {sourceRefs.length ? <BusinessDataView value={sourceRefs} fieldKey="source_refs" path={`${artifactType}.source_refs`} compact /> : <p>后端未返回来源引用。</p>}
            </section>
            <section>
              <header><AlertCircle size={14} /><h4>质量与风险标记</h4><span>{riskFlags.length} 项</span></header>
              {riskFlags.length ? <BusinessDataView value={riskFlags} fieldKey="risk_flags" path={`${artifactType}.risk_flags`} compact /> : <p>后端未返回风险标记。</p>}
            </section>
          </div>
        )}
      </div>
    </details>
  )
}

export function ArtifactArchive({ artifacts }: ArtifactArchiveProps) {
  const [query, setQuery] = useState('')
  const normalized = useMemo(() => artifacts.map(asArtifact).filter((artifact) => isFinanceArtifactType(String(artifact.artifact_type || ''))), [artifacts])
  const active = normalized.filter((artifact) => !['superseded', 'rejected'].includes(String(artifact.status || 'active')))
  const historical = normalized.filter((artifact) => ['superseded', 'rejected'].includes(String(artifact.status || 'active')))
  const matchesQuery = (artifact: ArtifactRecordView) => {
    const artifactType = String(artifact.artifact_type || '') as FinanceArtifactType
    const haystack = `${artifactType} ${financeArtifactTitle(artifactType)}`.toLowerCase()
    return !query.trim() || haystack.includes(query.trim().toLowerCase())
  }
  const filtered = active.filter(matchesQuery)
  const filteredHistorical = historical.filter(matchesQuery)
  const availableCount = active.filter((artifact) => ['available', 'active'].includes(qualityStatus(artifact))).length
  const degradedCount = active.filter((artifact) => !['available', 'active'].includes(qualityStatus(artifact))).length

  if (!normalized.length) return null
  return (
    <section className="artifact-archive" aria-label="完整分析档案">
      <header className="artifact-archive-header">
        <div>
          <span className="section-kicker">COMPLETE ANALYSIS DOSSIER</span>
          <h2>完整分析档案</h2>
          <p>逐项呈现本次运行的全部业务 Artifact；不渲染原始 JSON，不显示敏感运行信息。</p>
        </div>
        <div className="artifact-archive-stats">
          <span><CheckCircle2 size={13} />{availableCount} 项可用</span>
          <span className={degradedCount ? 'is-warning' : ''}><AlertCircle size={13} />{degradedCount} 项需关注</span>
          <span><Database size={13} />{historical.length} 项历史版本</span>
        </div>
      </header>
      <div className="artifact-archive-toolbar">
        <label><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索分析产物" /></label>
        <span>{filtered.length + filteredHistorical.length} / {normalized.length} 项业务 Artifact</span>
      </div>
      <div className="artifact-archive-groups">
        {GROUPS.map((group) => {
          const rows = filtered.filter((artifact) => group.types.includes(String(artifact.artifact_type) as FinanceArtifactType))
          if (!rows.length) return null
          return (
            <section className="artifact-archive-group" key={group.id}>
              <header><h3>{group.title}</h3><span>{rows.length} 项</span></header>
              <div>
                {rows.map((artifact, index) => {
                  const artifactType = String(artifact.artifact_type) as FinanceArtifactType
                  return <ArtifactCard key={artifact.artifact_id || `${artifactType}-${index}`} artifact={artifact} artifactType={artifactType} index={index} />
                })}
              </div>
            </section>
          )
        })}
        {filteredHistorical.length > 0 && (
          <section className="artifact-archive-group artifact-archive-group--historical">
            <header><h3>历史与已拒绝版本</h3><span>{filteredHistorical.length} 项</span></header>
            <div>{filteredHistorical.map((artifact, index) => {
              const artifactType = String(artifact.artifact_type) as FinanceArtifactType
              return <ArtifactCard key={artifact.artifact_id || `${artifactType}-history-${index}`} artifact={artifact} artifactType={artifactType} index={index} />
            })}</div>
          </section>
        )}
        {!filtered.length && !filteredHistorical.length && <div className="artifact-archive-empty"><FileSearch size={18} />没有匹配的业务 Artifact。</div>}
      </div>
    </section>
  )
}
