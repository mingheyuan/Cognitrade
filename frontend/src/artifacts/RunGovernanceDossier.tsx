import { Activity, CheckCircle2, ChevronDown, GitBranch, ShieldCheck, Waypoints } from 'lucide-react'
import { BusinessDataView } from './BusinessDataView'
import type { RunDetail, RunStep } from '../research-contracts'

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function text(value: unknown, fallback = '未提供'): string {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

function statusLabel(value: unknown): string {
  const status = text(value, 'unknown')
  return ({
    completed: '已完成', running: '运行中', pending: '等待执行', skipped: '已跳过',
    failed: '失败', cancelled: '已取消', gated: '质量门禁', completed_partial: '部分完成',
  } as Record<string, string>)[status] || status
}

function StepCard({ step, index }: { step: RunStep; index: number }) {
  const dependsOn = list(step.depends_on)
  const tags = list(step.tags)
  const inputs = list(step.input_artifact_types)
  const outputs = list(step.output_artifact_types)
  const nodeId = text(step.node_id || step.action_id, `step-${index + 1}`)
  return (
    <details className="run-governance-step" open>
      <summary>
        <span className="run-governance-step__index">{String(index + 1).padStart(2, '0')}</span>
        <span><strong>{text(step.objective, nodeId)}</strong><small>{nodeId}</small></span>
        <span className={`run-governance-step__status is-${text(step.status, 'pending')}`}>{statusLabel(step.status)}</span>
        <ChevronDown size={15} />
      </summary>
      <dl>
        <div><dt>节点类型</dt><dd>{text(step.node_kind)}</dd></div>
        <div><dt>执行能力</dt><dd>{text(step.capability)}</dd></div>
        <div><dt>执行器</dt><dd>{text(step.executor || step.agent)}</dd></div>
        <div><dt>执行阶段</dt><dd>{text(step.phase)}</dd></div>
        <div><dt>依赖节点</dt><dd>{dependsOn.length ? dependsOn.join('、') : '无'}</dd></div>
        <div><dt>输入产物</dt><dd>{inputs.length ? inputs.join('、') : '无'}</dd></div>
        <div><dt>输出产物</dt><dd>{outputs.length ? outputs.join('、') : '无'}</dd></div>
        <div><dt>节点标签</dt><dd>{tags.length ? tags.join('、') : '无'}</dd></div>
        {step.turn_id != null && <div><dt>Turn 编号</dt><dd>{text(step.turn_id)}</dd></div>}
        {step.task_type != null && <div><dt>任务类型</dt><dd>{text(step.task_type)}</dd></div>}
        {step.execution_mode != null && <div><dt>执行模式</dt><dd>{text(step.execution_mode)}</dd></div>}
        {step.supervisor_note != null && <div><dt>治理备注</dt><dd>{text(step.supervisor_note)}</dd></div>}
      </dl>
    </details>
  )
}

export function RunGovernanceDossier({ detail }: { detail: RunDetail }) {
  const stepsPayload = detail.steps
  const metrics = detail.metrics
  const events = detail.events
  const turn = asRecord(detail.replay.turn || stepsPayload.turn || events.turn)
  const selection = asRecord(stepsPayload.task_selection || events.task_selection || turn.task_selection)
  const notes = list(stepsPayload.notes)
  const metricSections = ([
    ['运行计数', metrics.counts],
    ['资源使用', metrics.usage],
    ['运行比率', metrics.rates],
    ['延迟统计', metrics.latency_ms],
    ['节点状态分布', metrics.node_status_counts],
    ['事件类型分布', metrics.event_type_counts],
    ['图事件分布', metrics.graph_event_counts],
    ['降级原因分布', metrics.degrade_reason_counts],
    ['步骤计数', stepsPayload.step_counts],
    ['步骤状态分布', stepsPayload.status_counts],
    ['事件计数', events.event_counts],
  ] as Array<[string, unknown]>).filter(([, value]) => value && Object.keys(asRecord(value)).length > 0)

  return (
    <section className="run-governance-dossier" aria-label="完整运行治理档案">
      <header className="run-governance-dossier__header">
        <div><span className="section-kicker">GOVERNED RUN DOSSIER</span><h2>完整运行治理档案</h2><p>展示后端返回的执行图、步骤、资源用量与质量统计；内部提示词、密钥和诊断堆栈不会进入界面。</p></div>
        <span><ShieldCheck size={14} />{detail.steps.steps.length} 个执行节点</span>
      </header>

      <div className="run-governance-overview">
        <article><GitBranch size={17} /><span><small>运行模式</small><strong>{text(stepsPayload.mode || turn.execution_mode, 'core')}</strong></span></article>
        <article><Activity size={17} /><span><small>运行状态</small><strong>{statusLabel(stepsPayload.status || metrics.status)}</strong></span></article>
        <article><Waypoints size={17} /><span><small>执行策略</small><strong>{text(stepsPayload.strategy)}</strong></span></article>
        <article><CheckCircle2 size={17} /><span><small>图修订版本</small><strong>{text(stepsPayload.revision)}</strong></span></article>
      </div>

      <div className="run-governance-identity">
        <dl>
          <div><dt>Run 编号</dt><dd>{detail.runId}</dd></div>
          <div><dt>Graph 编号</dt><dd>{text(stepsPayload.graph_id)}</dd></div>
          <div><dt>Domain</dt><dd>{text(stepsPayload.domain_id)}</dd></div>
          <div><dt>研究意图</dt><dd>{text(stepsPayload.intent)}</dd></div>
          <div><dt>Turn 编号</dt><dd>{text(turn.turn_id)}</dd></div>
          <div><dt>任务类型</dt><dd>{text(turn.task_type)}</dd></div>
          <div><dt>Tool 调用总数</dt><dd>{text(metrics.tool_call_count, '0')}</dd></div>
          <div><dt>事件总数</dt><dd>{events.events.length.toLocaleString()}</dd></div>
        </dl>
        {notes.length > 0 && <div className="run-governance-notes"><h3>执行图说明</h3><ul>{notes.map((note, index) => <li key={`${note}-${index}`}>{note}</li>)}</ul></div>}
        {Object.keys(selection).length > 0 && <div className="run-governance-selection"><h3>任务选择依据</h3><BusinessDataView value={selection} fieldKey="task_selection" path="run.task_selection" compact /></div>}
      </div>

      <section className="run-governance-section">
        <header><div><span className="section-kicker">EXECUTION GRAPH</span><h3>全部执行步骤</h3></div><span>{detail.steps.steps.length} 项</span></header>
        {detail.steps.steps.length > 0
          ? <div className="run-governance-steps">{detail.steps.steps.map((step, index) => <StepCard key={text(step.node_id || step.action_id, String(index))} step={step} index={index} />)}</div>
          : <p className="artifact-empty-value">该运行没有图执行步骤。</p>}
      </section>

      <section className="run-governance-section">
        <header><div><span className="section-kicker">QUALITY &amp; USAGE</span><h3>完整质量与资源统计</h3></div><span>{metricSections.length} 组</span></header>
        <div className="run-governance-metrics">
          {metricSections.map(([title, value]) => <article key={title}><h4>{title}</h4><BusinessDataView value={value} path={`run.metrics.${title}`} compact /></article>)}
        </div>
      </section>
    </section>
  )
}
