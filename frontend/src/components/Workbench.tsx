import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleAlert,
  Database,
  Download,
  ExternalLink,
  FileText,
  FileSearch,
  Lightbulb,
  ListChecks,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Target,
  UserRound,
} from 'lucide-react'
import { useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import { INTENT_LABELS } from '../data'
import {
  asNumber,
  asRecord,
  asString,
  activityFromEvents,
  assistantMessageFromDetail,
  evidenceFromReport,
  formatDate,
  hasFinalDelivery,
  intentFromDetail,
  isActiveStatus,
  queryFromDetail,
  reportFollowUps,
  reportFromDetail,
  reportRisks,
  statusFromDetail,
  statusLabel,
} from '../live-data'
import type { ResearchIntent } from '../research-contracts'
import type { RunReportFormat } from '../api-client'
import { ArtifactArchive } from '../artifacts/ArtifactArchive'
import { BusinessDataView } from '../artifacts/BusinessDataView'
import { IntentDeliveryDossier } from '../artifacts/IntentDeliveryDossier'
import { MethodDeliveryDossier } from '../artifacts/MethodDeliveryDossier'
import { RunGovernanceDossier } from '../artifacts/RunGovernanceDossier'
import { ResearchComposer } from './ResearchComposer'
import { localizeErrorMessage } from '../utils/error-message'

type WorkbenchProps = {
  onSubmit: (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => void
  onSelectEvidence: (id: string) => void
}

function MetricCard({ label, value, note }: { label: string; value: string | number; note: string }) {
  return <div className="metric-card"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function methodologySummary(raw: Record<string, unknown>): string {
  const outputs = asRecord(raw.outputs)
  const valuationBand = asRecord(raw.valuation_band)
  const values = [
    outputs.relative_view,
    outputs.market_to_intrinsic_gap,
    outputs.cashflow_state,
    outputs.debt_capacity_state,
    valuationBand.base,
  ].filter((value) => value !== undefined && value !== null)
  return values.length
    ? values.map((value) => String(value)).join(' | ')
    : 'No displayable model output was returned.'
}

function methodologySignal(raw: Record<string, unknown>): string {
  const outputs = asRecord(raw.outputs)
  const type = asString(raw.methodology_type)
  if (type === 'comps') return localizeSignal(asString(outputs.relative_view, 'insufficient peer signal'))
  if (type === 'dcf') return asString(outputs.market_to_intrinsic_gap, 'not available')
  if (type === 'cashflow_resilience') {
    return [outputs.cashflow_state, outputs.debt_capacity_state].filter(Boolean).map((value) => localizeSignal(String(value))).join(' / ') || 'not available'
  }
  return methodologySummary(raw)
}

function methodologyMetric(raw: Record<string, unknown>): string {
  const outputs = asRecord(raw.outputs)
  const type = asString(raw.methodology_type)
  if (type === 'dcf') return asString(raw.valuation_band && asRecord(raw.valuation_band).base, '—')
  if (type === 'cashflow_resilience') return asString(outputs.resilience_score, '—')
  return asString(outputs.score_gap_vs_peer_median, '—')
}

function localizeSignal(value: string): string {
  const labels: Record<string, string> = {
    'DCF proxy is above the latest market price': 'DCF 估值代理高于当前市场价格',
    'DCF proxy is below the latest market price': 'DCF 估值代理低于当前市场价格',
    'DCF proxy is close to the latest market price': 'DCF 估值代理接近当前市场价格',
    'cashflow resilient, debt capacity comfortable': '现金流韧性良好，债务承受能力良好',
    'above the latest market price': '高于当前市场价格',
    'below the latest market price': '低于当前市场价格',
    'close to the latest market price': '接近当前市场价格',
    'insufficient_peer_signal': '同行样本不足，无法形成有效相对估值',
    resilient: '现金流韧性良好',
    comfortable: '债务承受能力良好',
  }
  return labels[value] || value
}

function localizeMethodology(value: string): string {
  return {
    comps: '可比估值',
    dcf: 'DCF 估值',
    cashflow_resilience: '现金流韧性',
  }[value] || value
}

function localizeAssessment(factors: Array<Record<string, unknown>>, fallback: string): string {
  if (!factors.length) return fallback
  const grouped = new Map<string, string[]>()
  for (const factor of factors) {
    const symbol = asString(factor.symbol, '当前标的')
    const signals = grouped.get(symbol) || []
    signals.push(localizeSignal(asString(factor.signal, '暂无判断')))
    grouped.set(symbol, signals)
  }
  const rows = Array.from(grouped.entries()).map(([symbol, signals]) => `${symbol}：${signals.join('；')}`)
  return `${rows.join('；')}。这是基于当前数据和模型假设的初步判断，不构成投资建议。`
}

function localizeWarning(value: string): string {
  const labels: Record<string, string> = {
    total_assets_missing: '总资产缺失',
    total_liabilities_missing: '总负债缺失',
    leverage_assumed: '杠杆率为假设值',
    peer_group_missing: '同行样本缺失',
  }
  return labels[value] ? `${labels[value]} (${value})` : value
}

function localizeReportText(value: string): string {
  const exactLabels: Record<string, string> = {
    'Evidence may be incomplete or stale.': '证据可能不完整或已过时。',
    'No financial evidence was collected, so no investment conclusion can be made.': '未采集到金融证据，无法形成投资结论。',
    'Professional review is required before acting.': '采取行动前需要专业人员复核。',
    'Sector evidence may be incomplete or stale.': '板块证据可能不完整或已过时。',
    'No related sector or constituent snapshots were supplied.': '未提供相关板块或成分股快照。',
    'The requested policy snapshot was unavailable.': '请求的政策快照不可用。',
    'Independent evidence remains limited.': '独立证据仍然有限。',
    'Distilled logic remains decision support and requires source verification.': '提炼结果仅用于辅助决策，仍需核验来源。',
    'Review the cited snapshots and refresh material filings and market data before acting.': '行动前请复核引用的快照，并刷新重要公告与市场数据。',
    'Provide one or more six-digit A-share codes, or supply source snapshots.': '请提供一个或多个六位 A 股代码，或提供来源快照。',
    'Refresh the candidate universe and verify material disclosures before acting.': '行动前请刷新候选标的范围，并核验重要披露。',
    'Review constituent, policy, and catalyst evidence before acting on the sector view.': '采取板块观点前，请复核成分股、政策和催化剂证据。',
    'Refresh filings, valuation, and risk evidence before acting.': '行动前请刷新公告、估值和风险证据。',
    'Verify attribution and refresh the original source records before reuse.': '再次使用前请核验归属，并刷新原始来源记录。',
  }
  if (exactLabels[value]) return exactLabels[value]

  const degradedMatch = value.match(/^(\d+) provider snapshot\(s\) were degraded; inspect the evidence source details\.$/)
  if (degradedMatch) {
    return `${degradedMatch[1]} 个数据提供方快照已降级，请查看证据来源详情。`
  }

  const assumptionsPrefix = 'Model assumptions or incomplete inputs: '
  if (value.startsWith(assumptionsPrefix)) {
    const warnings = value
      .slice(assumptionsPrefix.length)
      .split(',')
      .map((warning) => warning.trim())
      .filter(Boolean)
      .map((warning) => localizeWarning(warning).replace(` (${warning})`, ''))
    return `模型假设或输入不完整：${warnings.join('、')}`
  }

  if (value.startsWith('missing evidence: ')) {
    return `缺失证据：${value.slice('missing evidence: '.length)}`
  }
  if (value.startsWith('degraded source: ')) {
    return `数据源降级：${value.slice('degraded source: '.length)}`
  }
  return value
}

export function Workbench({ onSubmit, onSelectEvidence }: WorkbenchProps) {
  const {
    selectedRunId,
    selectedRun,
    selectedRunLoading,
    submitting,
    submittedQuery,
    runs,
    lastResponse,
    liveProgress,
    liveAnswer,
    liveEvents,
    exportRunReport,
  } = useCognitrade()
  const [exportingReport, setExportingReport] = useState<'' | RunReportFormat>('')
  const [reportExportError, setReportExportError] = useState('')
  const report = reportFromDetail(selectedRun)
  const evidences = evidenceFromReport(report)
  const risks = reportRisks(report).map(localizeReportText)
  const followUps = reportFollowUps(report).map(localizeReportText)
  const intent = intentFromDetail(selectedRun) || asString(runs.find((run) => run.run_id === selectedRunId)?.intent)
  const status = statusFromDetail(selectedRun) || asString(runs.find((run) => run.run_id === selectedRunId)?.status)
  const query = queryFromDetail(selectedRun) || submittedQuery
  const counts = asRecord(selectedRun?.metrics.counts)
  const conclusion = asString(report.conclusion)
  const scorecard = asRecord(report.scorecard || report.screening_scorecard)
  const methodologies = Array.isArray(report.methodologies) ? report.methodologies.map(asRecord) : []
  const artifactBackedReport = asString(report.protocol) === 'agent_os' && Array.isArray(report.final_artifact_ids)
  const deliveryType = asString(report.delivery_type)
  const hasUziReport = new Set(['uzi_native_3_9_4', 'uzi_versus_3_9_4', 'uzi_portfolio_3_9_4']).has(deliveryType)
    && report.report_allowed === true
    && asNumber(report.critical_count) === 0
  const hasUziSingleReport = deliveryType === 'uzi_native_3_9_4'
  const updatedAt = selectedRun?.events.events.at(-1)?.created_at
  const snapshot = asRecord(selectedRun?.replay.snapshot)
  const isConversation = intent === 'core.conversation.answer' || asString(snapshot.request_kind) === 'conversation'
  const clarification = asRecord(selectedRun?.replay.result?.clarification)
  const clarificationQuestion = asString(
    clarification.question
      || snapshot.clarification_question
      || lastResponse?.clarification?.question,
  )
  const isClarification = status === 'needs_clarification' || Boolean(clarificationQuestion)
  const assessment = asRecord(report.assessment)
  const assessmentSummary = asString(assessment.summary)
  const assessmentFactors = Array.isArray(assessment.factors) ? assessment.factors.map(asRecord) : []
  const degradedEvidenceCount = Array.isArray(report.evidence)
    ? report.evidence.filter((item) => Boolean(asRecord(asRecord(item).metadata).degraded)).length
    : 0

  const handleReportExport = async (format: RunReportFormat) => {
    if (!selectedRunId || exportingReport) return
    setExportingReport(format)
    setReportExportError('')
    try {
      const exported = await exportRunReport(selectedRunId, format)
      const objectUrl = URL.createObjectURL(exported.blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.rel = 'noopener noreferrer'
      if (format === 'html') {
        anchor.target = '_blank'
      } else {
        anchor.download = exported.filename
      }
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
    } catch (error) {
      setReportExportError(localizeErrorMessage(error, '报告导出失败，请稍后重试。'))
    } finally {
      setExportingReport('')
    }
  }
  const reportHasDegradedEvidence = Boolean(report.degraded) || degradedEvidenceCount > 0
  const hasMainAgentSynthesis = report.synthesis_available === true
  const synthesisUnavailable = report.synthesis_unavailable === true
  const synthesisPending = artifactBackedReport
    && !hasMainAgentSynthesis
    && !synthesisUnavailable
    && !hasFinalDelivery(selectedRun)
  const displayConclusion = hasMainAgentSynthesis
    ? localizeAssessment(assessmentFactors, assessmentSummary || conclusion)
    : ''
  const activityEvents = activityFromEvents([
    ...(selectedRun?.events.events || []),
    ...liveEvents,
  ])
  const assistantMessage = (isConversation || hasMainAgentSynthesis ? assistantMessageFromDetail(selectedRun) : '')
    || (hasMainAgentSynthesis ? asString(report.conclusion) : '')
    || liveAnswer
    || asString(lastResponse?.message)
  const showMainAgentAnswer = Boolean(
    !isConversation
      && assistantMessage
      && !(asString(report.protocol) === 'agent_os' && !hasMainAgentSynthesis),
  )
  const activityFeed = activityEvents.length > 0 ? (
    <section className="agent-progress-feed" aria-label="Agent OS 运行轨迹">
      <div className="agent-progress-feed__heading">
        <span><ListChecks size={14} /> 可审计分析过程</span>
        <small>{activityEvents.length} 条 Agent OS 更新</small>
      </div>
      <ol>
        {activityEvents.map((item) => (
          <li key={item.id}>
            <span className={`agent-progress-feed__dot is-${item.kind}`} />
            <div>
              <strong>{item.actor}</strong>
              <p>{item.message}</p>
              {item.currentAction && (
                <p className="agent-progress-feed__detail"><b>当前</b>{item.currentAction}</p>
              )}
              {item.nextAction && (
                <p className="agent-progress-feed__detail"><b>下一步</b>{item.nextAction}</p>
              )}
            </div>
            {item.createdAt && <time>{formatDate(item.createdAt)}</time>}
          </li>
        ))}
      </ol>
    </section>
  ) : null

  return (
    <div className="workbench-column">
      <header className="research-thread-header">
        <div>
          <div className="thread-context-line">
            <span className="intent-badge">{INTENT_LABELS[intent] || intent || '等待研究'}</span>
            {selectedRunId && <span className="stock-code-badge">{selectedRunId}</span>}
            <span className="data-cutoff">{updatedAt ? `最后事件 ${formatDate(updatedAt)}` : '数据时间由后端证据决定'}</span>
          </div>
          <h1>{selectedRun?.replay.display_summary || query || '从一个真实研究问题开始'}</h1>
        </div>
        <div className="thread-header-actions">
          <span className={`run-state-badge ${['completed', 'completed_partial'].includes(status) ? 'is-complete' : 'is-running'}`}>
            {['completed', 'completed_partial'].includes(status) ? <CheckCircle2 size={12} /> : <Activity size={12} />}
            {selectedRunId ? statusLabel(status) : '未运行'}
          </span>
        </div>
      </header>

      <main className="conversation-stream">
        {query && (
          <article className="message-row message-row--user">
            <div className="message-avatar"><UserRound size={15} /></div>
            <div className="user-message">
              <div className="message-meta"><strong>你</strong><time>{selectedRunId ? '已持久化' : '正在提交'}</time></div>
              <p>{query}</p>
            </div>
          </article>
        )}

        {submitting && (
          <section className="live-research-strip" aria-live="polite">
            <span className="live-agent-orbit"><Sparkles size={15} /></span>
            <div>
              <strong>Agent OS 正在分析</strong>
              <span>{liveProgress || '正在创建动态任务图并调度受治理 Subagent。'}</span>
            </div>
            <RefreshCw size={16} className="spin-icon" />
          </section>
        )}

        {!submitting && isActiveStatus(status) && liveProgress && activityEvents.length > 0 && (
          <section className="live-research-strip" aria-live="polite">
            <span className="live-agent-orbit"><Sparkles size={15} /></span>
            <div>
              <strong>Agent OS 分析进度</strong>
              <span>{liveProgress}</span>
            </div>
            <Activity size={16} />
          </section>
        )}

        {liveAnswer && (!selectedRun || selectedRunLoading || isActiveStatus(status)) && (
          <article className="message-row message-row--assistant" aria-live="polite">
            <div className="message-avatar message-avatar--assistant"><span>C</span></div>
            <div className="assistant-message assistant-message--live">
              <div className="assistant-response-header">
                <span className="assistant-label">COGNITRADE LIVE ANSWER</span>
                <Activity size={14} />
              </div>
              <p className="live-answer-copy">{liveAnswer}</p>
            </div>
          </article>
        )}

        {(!selectedRun || isActiveStatus(status)) && activityFeed}

        {lastResponse?.status === 'needs_clarification' && lastResponse.clarification && (
          <section className="live-research-strip" role="status">
            <span className="live-agent-orbit"><MessageSquareText size={15} /></span>
            <div>
              <strong>需要补充信息</strong>
              <span>{lastResponse.clarification.question}（缺少：{lastResponse.clarification.missing_fields.join('、')}）</span>
            </div>
          </section>
        )}

        {selectedRunLoading && <div className="live-empty-state"><RefreshCw size={20} className="spin-icon" /><strong>正在读取运行回放</strong></div>}

        {!selectedRun && !selectedRunLoading && !submitting && (
          <section className="live-empty-state live-empty-state--large">
            <Database size={26} />
            <h2>暂无可展示的真实研究运行</h2>
            <p>连接后端后，历史 run 会从 `/api/v1/runs` 载入。提交下方问题可创建第一条研究记录。</p>
          </section>
        )}

        {selectedRun && (
          isClarification ? (
            <article className="message-row message-row--assistant">
              <div className="message-avatar message-avatar--assistant"><span>C</span></div>
              <div className="assistant-message">
                <div className="assistant-response-header">
                  <div>
                    <span className="assistant-label">COGNITRADE CLARIFICATION</span>
                    <span className="confidence-label"><MessageSquareText size={13} /> Main Agent</span>
                  </div>
                  <time>{updatedAt ? formatDate(updatedAt) : statusLabel(status)}</time>
                </div>
                <section className="thesis-callout">
                  <div className="thesis-icon"><MessageSquareText size={17} /></div>
                  <div><span>需要补充信息</span><p>{clarificationQuestion || 'Main Agent 正在等待下一条信息。'}</p></div>
                </section>
                <footer className="report-footer">
                  <div><ShieldAlert size={14} /><span>补充信息后，Agent OS 将继续当前运行。</span></div>
                </footer>
              </div>
            </article>
          ) : isConversation ? (
            <article className="message-row message-row--assistant">
              <div className="message-avatar message-avatar--assistant"><span>C</span></div>
              <div className="assistant-message assistant-message--conversation">
                <div className="assistant-response-header">
                  <div>
                    <span className="assistant-label">COGNITRADE ANSWER</span>
                    <span className="confidence-label"><MessageSquareText size={13} /> 普通对话</span>
                  </div>
                  <time>{updatedAt ? formatDate(updatedAt) : statusLabel(status)}</time>
                </div>
                <section className="main-agent-answer main-agent-answer--conversation" aria-label="Cognitrade answer">
                  <div className="main-agent-answer__heading">
                    <span><Sparkles size={14} /> Cognitrade</span>
                    <small>{statusLabel(status)}</small>
                  </div>
                  <p>{assistantMessage || '本次对话已完成，但后端没有返回可展示的回答。'}</p>
                </section>
              </div>
            </article>
          ) : (
          <article className="message-row message-row--assistant">
            <div className="message-avatar message-avatar--assistant"><span>C</span></div>
            <div className="assistant-message">
              <div className="assistant-response-header">
                <div>
                  <span className="assistant-label">COGNITRADE LIVE RESULT</span>
                  <span className="confidence-label"><CheckCircle2 size={13} />{evidences.length} 条结构化证据</span>
                </div>
                <time>{updatedAt ? formatDate(updatedAt) : statusLabel(status)}</time>
              </div>

              {showMainAgentAnswer && (
                <section className="main-agent-answer" aria-label="Main Agent answer">
                  <div className="main-agent-answer__heading">
                    <span><Sparkles size={14} /> Main Agent</span>
                    <small>{statusLabel(status)}</small>
                  </div>
                  <p>{assistantMessage}</p>
                </section>
              )}

              {hasUziReport && (
                <section className="uzi-report-export" aria-label="UZI 原生报告导出">
                  <div className="uzi-report-export__identity">
                    <span><FileText size={16} /></span>
                    <div>
                      <strong>UZI 原生报告</strong>
                      <small>同一 ArtifactBoard 数据生成 HTML 与 PDF，不调用旧 UZI 运行时。</small>
                    </div>
                  </div>
                  <div className="uzi-report-export__actions">
                    <button type="button" onClick={() => void handleReportExport('html')} disabled={Boolean(exportingReport)}>
                      {exportingReport === 'html' ? <LoaderCircle className="is-spinning" size={15} /> : <ExternalLink size={15} />}
                      {exportingReport === 'html' ? '正在生成' : '打开 HTML'}
                    </button>
                    <button type="button" onClick={() => void handleReportExport('pdf')} disabled={Boolean(exportingReport)}>
                      {exportingReport === 'pdf' ? <LoaderCircle className="is-spinning" size={15} /> : <Download size={15} />}
                      {exportingReport === 'pdf' ? '正在生成' : '下载 PDF'}
                    </button>
                    {hasUziSingleReport && <button type="button" onClick={() => void handleReportExport('share-card')} disabled={Boolean(exportingReport)}><Download size={15} />{exportingReport === 'share-card' ? '正在生成' : '分享卡 PNG'}</button>}
                    {hasUziSingleReport && <button type="button" onClick={() => void handleReportExport('war-report')} disabled={Boolean(exportingReport)}><Download size={15} />{exportingReport === 'war-report' ? '正在生成' : '战报 PNG'}</button>}
                    {hasUziSingleReport && <button type="button" onClick={() => void handleReportExport('one-liner')} disabled={Boolean(exportingReport)}><Download size={15} />{exportingReport === 'one-liner' ? '正在生成' : 'One-liner'}</button>}
                  </div>
                  {reportExportError && <p role="alert">{reportExportError}</p>}
                </section>
              )}

              <section className="report-hero live-report-hero">
                <div className="company-identity">
                  <span className="company-mark">C</span>
                  <div><span>{selectedRunId}</span><h2>{INTENT_LABELS[intent] || intent || '研究报告'}</h2><small>真实 API 回放</small></div>
                </div>
                <div className="research-verdict">
                  <span>运行状态</span><strong>{statusLabel(status)}</strong><small>{updatedAt ? `最后事件 ${formatDate(updatedAt)}` : '状态来自后端运行记录'}</small>
                </div>
                <div className="research-verdict">
                  <span>数据模式</span><strong>{reportHasDegradedEvidence ? '部分降级' : '标准'}</strong><small>{reportHasDegradedEvidence ? `${degradedEvidenceCount || '部分'} 条证据源不可用或不完整` : '所有返回证据均可用'}</small>
                </div>
              </section>

              <section className="thesis-callout">
                <div className="thesis-icon"><Lightbulb size={17} /></div>
                <div>
                  <span>{hasMainAgentSynthesis
                    ? '最终结论 · Main Agent synthesis'
                    : synthesisUnavailable
                      ? '研究产物已完成 · Main Agent 暂不可用'
                    : synthesisPending
                      ? 'Main Agent 正在生成动态结论'
                      : 'Pack 原始摘要 · 需要重试'}</span>
                  <p>{displayConclusion || (synthesisPending
                    ? 'Pack 已完成证据整理，Main Agent 正在结合证据生成最终回答。'
                    : synthesisUnavailable
                      ? '研究事实已保存到 ArtifactBoard，但本次未生成 Main Agent 最终答复。请重新运行。'
                    : artifactBackedReport
                      ? '该运行已完成交付，但没有保存 Main Agent 动态结论，请重试。'
                      : '该运行未返回可展示的最终结论。请检查运行状态、质量门禁或错误信息。')}</p>
                </div>
              </section>

              {assessmentFactors.length > 0 && (
                <section className="report-section assessment-section">
                  <div className="report-section-heading"><div><span className="section-index">01</span><div><h3>分析判断</h3><p>由 Pack 方法论输出的可追溯判断依据</p></div></div></div>
                  <div className="report-table-wrap">
                    <table className="report-table">
                      <thead><tr><th>标的</th><th>方法</th><th>信号</th><th>依据</th><th>置信度</th></tr></thead>
                      <tbody>
                        {assessmentFactors.map((factor, index) => (
                          <tr key={`${asString(factor.symbol, String(index))}-${asString(factor.methodology, String(index))}`}>
                            <td>{asString(factor.symbol, '—')}</td>
                            <td>{localizeMethodology(asString(factor.methodology, '—'))}</td>
                            <td><strong>{localizeSignal(asString(factor.signal, '—'))}</strong></td>
                            <td>{asString(factor.basis, '—')}</td>
                            <td>{asNumber(factor.confidence)?.toFixed(2) ?? '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <section className="report-section">
                  <div className="report-section-heading"><div><span className="section-index">02</span><div><h3>运行指标</h3><p>来自 `/metrics` 的实时持久化统计</p></div></div></div>
                <div className="metric-grid">
                  <MetricCard label="事件" value={Number(counts.events || selectedRun.events.events.length)} note="events.jsonl" />
                  <MetricCard label="步骤" value={selectedRun.steps.steps.length} note="执行图顶层节点" />
                  <MetricCard label="子 Agent" value={Number(counts.subagents || 0)} note="运行时调用" />
                  <MetricCard label="降级项" value={Number(counts.degradations || 0)} note="未进行中性填充" />
                </div>
              </section>

              <section className="report-section">
                <div className="report-section-heading"><div><span className="section-index">03</span><div><h3>关键证据</h3><p>直接渲染 report.evidence</p></div></div></div>
                {evidences.length ? (
                  <div className="live-evidence-grid">
                    {evidences.map((evidence) => (
                      <button key={evidence.id} className="live-evidence-card" onClick={() => onSelectEvidence(evidence.id)}>
                        <span>{evidence.id} · {evidence.source}</span><strong>{evidence.title}</strong><small>{evidence.meta}</small>
                      </button>
                    ))}
                  </div>
                ) : <div className="inline-empty-state"><FileSearch size={17} />{artifactBackedReport ? 'v1 未暴露最终 artifact 内容，暂时无法读取其中的 evidence。' : '该报告没有返回结构化 evidence 数组。'}</div>}
              </section>

              <section className="report-section">
                <div className="report-section-heading"><div><span className="section-index">04</span><div><h3>风险与下一步</h3><p>保持后端原始语义，不生成界面假设</p></div></div></div>
                <div className="thesis-columns">
                  <article className="argument-card argument-card--bear">
                    <div className="argument-title"><span><AlertTriangle size={15} /></span><div><strong>风险</strong><small>{risks.length} 项</small></div></div>
                    {risks.length ? <ul>{risks.map((risk, index) => <li key={`${risk}-${index}`}><span>{String(index + 1).padStart(2, '0')}</span><p>{risk}</p></li>)}</ul> : <p className="muted-copy">后端未返回 risk。</p>}
                  </article>
                  <article className="argument-card argument-card--bull">
                    <div className="argument-title"><span><Target size={15} /></span><div><strong>后续研究</strong><small>{followUps.length} 项</small></div></div>
                    {followUps.length ? <ul>{followUps.map((item, index) => <li key={`${item}-${index}`}><span>{String(index + 1).padStart(2, '0')}</span><p>{item}</p></li>)}</ul> : <p className="muted-copy">后端未返回 follow_up。</p>}
                  </article>
                </div>
              </section>

              <IntentDeliveryDossier artifacts={selectedRun.replay.artifacts || []} />

              {methodologies.length > 0 && (
                <section className="report-section">
                  <div className="report-section-heading"><div><span className="section-index">06</span><div><h3>Methodology 输出表</h3><p>Comps、DCF 和现金流韧性均来自 Finance Pack Artifact。</p></div></div></div>
                  <div className="report-table-wrap">
                    <table className="report-table">
                      <thead><tr><th>标的</th><th>方法</th><th>主要信号</th><th>核心数值</th><th>置信度</th><th>警告</th></tr></thead>
                      <tbody>
                    {methodologies.map((methodology, index) => (
                      <tr key={`${asString(methodology.methodology_type, 'method')}-${asString(methodology.target_symbol, String(index))}`}>
                        <td>{asString(methodology.target_symbol, '—')}</td>
                        <td>{localizeMethodology(asString(methodology.methodology_type, 'methodology'))}</td>
                        <td><strong>{methodologySignal(methodology)}</strong></td>
                        <td>{methodologyMetric(methodology)}</td>
                        <td>{asNumber(methodology.confidence)?.toFixed(2) ?? '—'}</td>
                        <td>{Array.isArray(methodology.warnings) && methodology.warnings.length ? methodology.warnings.map((warning) => localizeWarning(String(warning))).join(', ') : '—'}</td>
                      </tr>
                    ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <MethodDeliveryDossier artifacts={selectedRun.replay.artifacts || []} />

              {Object.keys(scorecard).length > 0 && (
                <section className="report-section">
                  <div className="report-section-heading"><div><span className="section-index">08</span><div><h3>结构化评分卡</h3><p>使用中文业务字段完整展示，不渲染原始 JSON。</p></div></div></div>
                  <BusinessDataView value={scorecard} fieldKey="scorecard" path="research_report.scorecard" />
                </section>
              )}

              {(selectedRun.replay.summary?.status === 'failed' || reportHasDegradedEvidence) && (
                <div className="model-warning"><CircleAlert size={15} /><p>{reportHasDegradedEvidence ? `本次研究有 ${degradedEvidenceCount || '部分'} 条证据源降级，请查看关键证据与风险。` : '本次运行未通过质量门禁，请查看事件与步骤。'}</p></div>
              )}

              <footer className="report-footer">
                <div><ShieldAlert size={14} /><span>报告来自实时后端持久化产物，仅用于研究辅助，不构成投资建议。</span></div>
                <span className="text-button"><Database size={14} />{selectedRun.events.events.length} 条运行事件</span>
              </footer>
            </div>
          </article>
          )
        )}

        {selectedRun && <RunGovernanceDossier detail={selectedRun} />}

        {selectedRun && !isClarification && !isActiveStatus(status) && (
          <ArtifactArchive artifacts={selectedRun.replay.artifacts || []} />
        )}

        {selectedRun && !isClarification && !isActiveStatus(status) && activityFeed}
      </main>

      <div className="composer-dock">
        <ResearchComposer onSubmit={onSubmit} running={submitting} compact />
        <div className="composer-context-note">
          <MessageSquareText size={12} />当前 v1 每次提交创建独立研究 run
          <Database size={12} />{selectedRunId || '尚未选择 run'}
        </div>
      </div>
    </div>
  )
}
