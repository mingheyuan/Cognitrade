import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleAlert,
  Database,
  FileSearch,
  Lightbulb,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Target,
  UserRound,
} from 'lucide-react'
import { useCognitrade } from '../CognitradeContext'
import { INTENT_LABELS } from '../data'
import {
  asRecord,
  asString,
  evidenceFromReport,
  formatDate,
  intentFromDetail,
  queryFromDetail,
  reportFollowUps,
  reportFromDetail,
  reportRisks,
  statusFromDetail,
  statusLabel,
} from '../live-data'
import type { ResearchIntent } from '../research-contracts'
import { ResearchComposer } from './ResearchComposer'

type WorkbenchProps = {
  onSubmit: (query: string, intent?: ResearchIntent) => void
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

export function Workbench({ onSubmit, onSelectEvidence }: WorkbenchProps) {
  const {
    selectedRunId,
    selectedRun,
    selectedRunLoading,
    submitting,
    submittedQuery,
    runs,
  } = useCognitrade()
  const report = reportFromDetail(selectedRun)
  const evidences = evidenceFromReport(report)
  const risks = reportRisks(report)
  const followUps = reportFollowUps(report)
  const intent = intentFromDetail(selectedRun) || asString(runs.find((run) => run.run_id === selectedRunId)?.intent)
  const status = statusFromDetail(selectedRun) || asString(runs.find((run) => run.run_id === selectedRunId)?.status)
  const query = queryFromDetail(selectedRun) || submittedQuery
  const counts = asRecord(selectedRun?.metrics.counts)
  const conclusion = asString(report.conclusion)
  const scorecard = asRecord(report.scorecard || report.screening_scorecard)
  const methodologies = Array.isArray(report.methodologies) ? report.methodologies.map(asRecord) : []
  const artifactBackedReport = asString(report.protocol) === 'agent_os' && Array.isArray(report.final_artifact_ids)
  const updatedAt = selectedRun?.events.events.at(-1)?.created_at

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
          <span className={`run-state-badge ${status === 'completed' ? 'is-complete' : 'is-running'}`}>
            {status === 'completed' ? <CheckCircle2 size={12} /> : <Activity size={12} />}
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
              <strong>后端正在执行研究任务</strong>
              <span>POST /api/v1/runs 当前为同步接口，完成后将加载真实 run、报告和证据。</span>
            </div>
            <RefreshCw size={16} className="spin-icon" />
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

              <section className="report-hero live-report-hero">
                <div className="company-identity">
                  <span className="company-mark">C</span>
                  <div><span>{selectedRunId}</span><h2>{INTENT_LABELS[intent] || intent || '研究报告'}</h2><small>真实 API 回放</small></div>
                </div>
                <div className="research-verdict">
                  <span>运行状态</span><strong>{statusLabel(status)}</strong><small>{asString(selectedRun.replay.summary?.message, '后端未提供摘要')}</small>
                </div>
                <div className="research-verdict">
                  <span>数据模式</span><strong>{report.degraded ? '降级' : '标准'}</strong><small>{asString(report.degrade_reason || report.degraded_note, '未报告降级原因')}</small>
                </div>
              </section>

              <section className="thesis-callout">
                <div className="thesis-icon"><Lightbulb size={17} /></div>
                <div><span>核心结论</span><p>{conclusion || (artifactBackedReport ? '该运行已生成 Agent OS 最终 artifact，但 /api/v1 只返回 artifact ID，尚无版本化接口读取内容。' : '该运行未返回 report.conclusion。请检查运行状态、质量门禁或错误信息。')}</p></div>
              </section>

              <section className="report-section">
                <div className="report-section-heading"><div><span className="section-index">01</span><div><h3>运行指标</h3><p>来自 `/metrics` 的实时持久化统计</p></div></div></div>
                <div className="metric-grid">
                  <MetricCard label="事件" value={Number(counts.events || selectedRun.events.events.length)} note="events.jsonl" />
                  <MetricCard label="步骤" value={selectedRun.steps.steps.length} note="执行图顶层节点" />
                  <MetricCard label="子 Agent" value={Number(counts.subagents || 0)} note="运行时调用" />
                  <MetricCard label="降级项" value={Number(counts.degradations || 0)} note="未进行中性填充" />
                </div>
              </section>

              <section className="report-section">
                <div className="report-section-heading"><div><span className="section-index">02</span><div><h3>关键证据</h3><p>直接渲染 report.evidence</p></div></div></div>
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
                <div className="report-section-heading"><div><span className="section-index">03</span><div><h3>风险与下一步</h3><p>保持后端原始语义，不生成界面假设</p></div></div></div>
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

              {methodologies.length > 0 && (
                <section className="report-section">
                  <div className="report-section-heading"><div><span className="section-index">04</span><div><h3>Methodology Views</h3><p>Comps, DCF, and cashflow resilience are produced by the Finance Pack.</p></div></div></div>
                  <div className="live-key-value-grid">
                    {methodologies.slice(0, 12).map((methodology, index) => (
                      <div key={`${asString(methodology.methodology_type, 'method')}-${asString(methodology.target_symbol, String(index))}`}>
                        <span>{asString(methodology.target_symbol, 'symbol')} | {asString(methodology.methodology_type, 'methodology')}</span>
                        <strong>{methodologySummary(methodology)}</strong>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {Object.keys(scorecard).length > 0 && (
                <section className="report-section">
                  <div className="report-section-heading"><div><span className="section-index">05</span><div><h3>结构化评分卡</h3><p>原样展示后端 scorecard 字段</p></div></div></div>
                  <div className="live-key-value-grid">
                    {Object.entries(scorecard).slice(0, 12).map(([key, value]) => (
                      <div key={key}><span>{key}</span><strong>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</strong></div>
                    ))}
                  </div>
                </section>
              )}

              {(selectedRun.replay.summary?.status === 'failed' || report.degraded) && (
                <div className="model-warning"><CircleAlert size={15} /><p>{report.degraded ? `本次研究发生降级：${asString(report.degrade_reason, '原因未提供')}` : '本次运行未通过质量门禁，请查看事件与步骤。'}</p></div>
              )}

              <footer className="report-footer">
                <div><ShieldAlert size={14} /><span>报告来自实时后端持久化产物，仅用于研究辅助，不构成投资建议。</span></div>
                <span className="text-button"><Database size={14} />{selectedRun.events.events.length} 条运行事件</span>
              </footer>
            </div>
          </article>
        )}
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
