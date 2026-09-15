import {
  AlertTriangle,
  BookOpenCheck,
  CheckCircle2,
  Database,
  FileSearch,
  Layers3,
  ListFilter,
  Scale,
  ShieldQuestion,
  Sparkles,
} from 'lucide-react'
import { asArray, asOptionalNumber, asRecord } from '../utils/value'
import { BusinessDataView } from './BusinessDataView'
import { financeArtifactTitle, isFinanceArtifactType } from './registry'
import type { ArtifactRecordView } from './types'

const INTENT_TYPES = [
  'screening_result',
  'sector_analysis',
  'thesis_review',
  'source_record_set',
  'distillation_result',
  'distillation_review',
] as const

type IntentArtifactType = typeof INTENT_TYPES[number]

type IntentDeliveryDossierProps = {
  artifacts: Array<Record<string, unknown>>
  includeTypes?: readonly IntentArtifactType[]
}

function text(value: unknown, fallback = '未提供'): string {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}

function activeArtifacts(artifacts: Array<Record<string, unknown>>): ArtifactRecordView[] {
  return artifacts
    .filter((artifact) => !['superseded', 'rejected'].includes(String(artifact.status || 'active')))
    .filter((artifact) => isFinanceArtifactType(String(artifact.artifact_type || '')))
    .map((artifact) => artifact as ArtifactRecordView)
}

function latestByType(artifacts: ArtifactRecordView[], type: IntentArtifactType): ArtifactRecordView | null {
  return [...artifacts].reverse().find((artifact) => artifact.artifact_type === type) || null
}

function DossierHeader({ type, artifact, countLabel }: { type: IntentArtifactType; artifact: ArtifactRecordView; countLabel: string }) {
  return (
    <header className="intent-dossier-header">
      <div>
        <span className="section-kicker">DEDICATED DELIVERY · AGENT OS</span>
        <h3>{financeArtifactTitle(type)}</h3>
        <p>{countLabel}；以下明细直接来自 ArtifactBoard，不进行数量截断或模拟补值。</p>
      </div>
      <div className="intent-dossier-audit">
        <span><Database size={13} />Schema {artifact.schema_version || '1'}</span>
        <strong title={artifact.artifact_id}>{artifact.artifact_id || 'Artifact 编号未提供'}</strong>
      </div>
    </header>
  )
}

function ScreeningDossier({ artifact }: { artifact: ArtifactRecordView }) {
  const content = asRecord(artifact.content)
  const screened = asArray(content.screened_candidates)
  const watchlist = asArray(content.watchlist_candidates)
  const scorecard = asRecord(content.scorecard)
  const deepChecked = [...screened, ...watchlist].filter((item) => Boolean(asRecord(item).deep_checked)).length
  return (
    <article className="intent-dossier intent-dossier--screening" data-intent-artifact="screening_result">
      <DossierHeader type="screening_result" artifact={artifact} countLabel={`${screened.length} 个核心候选、${watchlist.length} 个观察候选`} />
      <div className="intent-dossier-kpis">
        <span><small>全部候选</small><strong>{asOptionalNumber(scorecard.candidate_count) ?? screened.length + watchlist.length}</strong></span>
        <span><small>通过筛选</small><strong>{asOptionalNumber(scorecard.eligible_candidate_count) ?? screened.length}</strong></span>
        <span><small>完成深度核验</small><strong>{asOptionalNumber(scorecard.deep_checked_count) ?? deepChecked}</strong></span>
        <span><small>深度补全标的</small><strong>{asArray(content.enrichment_targets).length}</strong></span>
      </div>
      <section className="intent-dossier-section">
        <header><ListFilter size={15} /><h4>完整评分卡与筛选方法</h4></header>
        <BusinessDataView value={{ scorecard: content.scorecard, methodology: content.methodology }} path="screening_result.contract" />
      </section>
      <section className="intent-dossier-section">
        <header><CheckCircle2 size={15} /><h4>核心候选</h4><span>{screened.length} 项</span></header>
        <BusinessDataView value={screened} fieldKey="screened_candidates" path="screening_result.screened_candidates" />
      </section>
      <section className="intent-dossier-section">
        <header><ShieldQuestion size={15} /><h4>观察候选</h4><span>{watchlist.length} 项</span></header>
        <BusinessDataView value={watchlist} fieldKey="watchlist_candidates" path="screening_result.watchlist_candidates" />
      </section>
      <section className="intent-dossier-section intent-dossier-section--audit">
        <header><FileSearch size={15} /><h4>补全目标与全部筛选说明</h4></header>
        <BusinessDataView value={{ enrichment_targets: content.enrichment_targets, screening_notes: content.screening_notes }} path="screening_result.audit" compact />
      </section>
    </article>
  )
}

function SectorDossier({ artifact }: { artifact: ArtifactRecordView }) {
  const content = asRecord(artifact.content)
  const heatmap = asArray(content.sector_heatmap)
  const constituents = asArray(content.constituents)
  const findings = asArray(content.findings)
  const policy = asArray(content.policy_snapshots)
  return (
    <article className="intent-dossier intent-dossier--sector" data-intent-artifact="sector_analysis">
      <DossierHeader type="sector_analysis" artifact={artifact} countLabel={`${text(content.sector, '未命名板块')} · ${findings.length} 条来源快照`} />
      <div className="intent-dossier-kpis">
        <span><small>可用来源</small><strong>{asOptionalNumber(content.source_count) ?? findings.length}</strong></span>
        <span><small>热力信号</small><strong>{heatmap.length}</strong></span>
        <span><small>成分样本</small><strong>{constituents.length}</strong></span>
        <span><small>政策快照</small><strong>{policy.length}</strong></span>
      </div>
      <section className="intent-dossier-section">
        <header><Scale size={15} /><h4>板块信号、广度与相对强弱</h4></header>
        <BusinessDataView value={{
          sector: content.sector,
          theme: content.theme,
          signal_scorecard: content.signal_scorecard,
          breadth: content.breadth,
          relative_strength: content.relative_strength,
          sector_heatmap: content.sector_heatmap,
        }} path="sector_analysis.signals" />
      </section>
      <section className="intent-dossier-section">
        <header><Layers3 size={15} /><h4>全部成分、催化剂与政策证据</h4></header>
        <BusinessDataView value={{
          constituents: content.constituents,
          catalysts: content.catalysts,
          policy_snapshots: content.policy_snapshots,
        }} path="sector_analysis.drivers" />
      </section>
      <section className="intent-dossier-section">
        <header><BookOpenCheck size={15} /><h4>全部来源快照与分析发现</h4><span>{findings.length} 项</span></header>
        <BusinessDataView value={{ findings: content.findings, related_snapshots: content.related_snapshots }} path="sector_analysis.evidence" />
      </section>
      <section className="intent-dossier-section intent-dossier-section--audit">
        <header><AlertTriangle size={15} /><h4>风险与分析说明</h4></header>
        <BusinessDataView value={{ risks: content.risks, analysis_notes: content.analysis_notes }} path="sector_analysis.audit" compact />
      </section>
    </article>
  )
}

function ThesisDossier({ artifact }: { artifact: ArtifactRecordView }) {
  const content = asRecord(artifact.content)
  const comparison = asRecord(content.evidence_comparison)
  const targetFound = content.review_target_found === true
  return (
    <article className="intent-dossier intent-dossier--thesis" data-intent-artifact="thesis_review">
      <DossierHeader type="thesis_review" artifact={artifact} countLabel={`复核结论：${text(content.overall_verdict || content.verdict, '证据不足')}`} />
      <div className="intent-dossier-kpis">
        <span><small>目标运行</small><strong>{targetFound ? '已读取' : '未找到'}</strong></span>
        <span><small>历史产物</small><strong>{asOptionalNumber(comparison.target_artifact_count) ?? asArray(content.review_target_artifact_ids).length}</strong></span>
        <span><small>新增证据</small><strong>{asOptionalNumber(comparison.fresh_evidence_count) ?? 0}</strong></span>
        <span><small>总体结论</small><strong>{text(content.overall_verdict || content.verdict, '证据不足')}</strong></span>
      </div>
      <section className="intent-dossier-section">
        <header><BookOpenCheck size={15} /><h4>目标运行与原始投资逻辑</h4></header>
        <BusinessDataView value={{
          review_target_run_id: content.review_target_run_id,
          review_target_found: content.review_target_found,
          review_target_artifact_ids: content.review_target_artifact_ids,
          target_thesis: content.target_thesis,
        }} path="thesis_review.target" />
      </section>
      <section className="intent-dossier-section">
        <header><Scale size={15} /><h4>完整新旧证据比较与发现</h4></header>
        <BusinessDataView value={{
          evidence_comparison: content.evidence_comparison,
          findings: content.findings,
          strengths: content.strengths,
          weaknesses: content.weaknesses,
        }} path="thesis_review.comparison" />
      </section>
      <section className="intent-dossier-section intent-dossier-section--audit">
        <header><Sparkles size={15} /><h4>结论与全部后续动作</h4></header>
        <BusinessDataView value={{ verdict: content.verdict, overall_verdict: content.overall_verdict, follow_up: content.follow_up }} path="thesis_review.delivery" compact />
      </section>
    </article>
  )
}

function DistillationDossier({ source, result, review }: { source: ArtifactRecordView | null; result: ArtifactRecordView | null; review: ArtifactRecordView | null }) {
  const sourceContent = asRecord(source?.content)
  const resultContent = asRecord(result?.content)
  const reviewContent = asRecord(review?.content)
  const dimensions = asArray(resultContent.dimensions)
  const records = asArray(sourceContent.records)
  const auditArtifact = review || result || source
  if (!auditArtifact) return null
  return (
    <article className="intent-dossier intent-dossier--distillation" data-intent-artifact="distillation_delivery">
      <DossierHeader type="distillation_review" artifact={auditArtifact} countLabel={`${records.length} 条原始记录、${dimensions.length} 个蒸馏维度`} />
      <div className="intent-dossier-kpis">
        <span><small>来源记录</small><strong>{records.length}</strong></span>
        <span><small>逻辑维度</small><strong>{dimensions.length}</strong></span>
        <span><small>审核发现</small><strong>{asArray(reviewContent.findings).length}</strong></span>
        <span><small>审核结论</small><strong>{text(reviewContent.verdict, '尚未审核')}</strong></span>
      </div>
      {source && (
        <section className="intent-dossier-section">
          <header><FileSearch size={15} /><h4>完整来源记录</h4><span>{records.length} 项</span></header>
          <BusinessDataView value={sourceContent} path="source_record_set" />
        </section>
      )}
      {result && (
        <section className="intent-dossier-section">
          <header><Layers3 size={15} /><h4>全部投资逻辑维度</h4><span>{dimensions.length} 项</span></header>
          <BusinessDataView value={resultContent} path="distillation_result" />
        </section>
      )}
      {review && (
        <section className="intent-dossier-section intent-dossier-section--audit">
          <header><BookOpenCheck size={15} /><h4>完整蒸馏审核</h4></header>
          <BusinessDataView value={reviewContent} path="distillation_review" />
        </section>
      )}
    </article>
  )
}

export function IntentDeliveryDossier({ artifacts, includeTypes = INTENT_TYPES }: IntentDeliveryDossierProps) {
  const normalized = activeArtifacts(artifacts)
  const enabled = new Set(includeTypes)
  const screening = enabled.has('screening_result') ? latestByType(normalized, 'screening_result') : null
  const sector = enabled.has('sector_analysis') ? latestByType(normalized, 'sector_analysis') : null
  const thesis = enabled.has('thesis_review') ? latestByType(normalized, 'thesis_review') : null
  const source = enabled.has('source_record_set') ? latestByType(normalized, 'source_record_set') : null
  const result = enabled.has('distillation_result') ? latestByType(normalized, 'distillation_result') : null
  const review = enabled.has('distillation_review') ? latestByType(normalized, 'distillation_review') : null
  if (!screening && !sector && !thesis && !source && !result && !review) return null
  return (
    <section className="intent-delivery-dossier" aria-label="专属研究交付">
      {screening && <ScreeningDossier artifact={screening} />}
      {sector && <SectorDossier artifact={sector} />}
      {thesis && <ThesisDossier artifact={thesis} />}
      {(source || result || review) && <DistillationDossier source={source} result={result} review={review} />}
    </section>
  )
}
