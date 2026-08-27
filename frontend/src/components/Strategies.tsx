import {
  Archive,
  BadgeCheck,
  BarChart3,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Copy,
  FileDiff,
  FilePlus2,
  FlaskConical,
  GitBranch,
  History,
  Import,
  Link2,
  ListRestart,
  LoaderCircle,
  LockKeyhole,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  type LucideIcon,
} from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { CognitradeApiClient, CognitradeApiError } from '../api-client'
import { useCognitrade } from '../CognitradeContext'
import type {
  BacktestMode,
  BacktestPreflight,
  DailyReportSet,
  PaperStrategyBinding,
  ResearchProfile,
  ReportJob,
  StrategyBacktest,
  StrategyBacktestResult,
  StrategyDocument,
  StrategyEvent,
  StrategyRecord,
  StrategyVerification,
  StrategyWorkspaceContext,
  TradeSignal,
  VersionDiff,
} from '../strategy-contracts'
import { asString, dashboardItems, dashboardValue } from '../live-data'
import { AppSelect } from './AppSelect'

const LEGACY_POLICY_KEY = 'cognitrade.report-signal-policy.v1'
const UNSAVED_FORM_KEY = 'cognitrade.strategy-draft.v1'

type StrategyTab = 'rules' | 'signals' | 'versions' | 'assignments' | 'backtests' | 'governance'
type StrategyFilter = 'all' | 'template' | 'draft' | 'published' | 'archived'

const tabs: Array<{ id: StrategyTab; label: string; icon: LucideIcon }> = [
  { id: 'rules', label: '规则编辑', icon: SlidersHorizontal },
  { id: 'signals', label: '信号预览', icon: GitBranch },
  { id: 'versions', label: '版本历史', icon: History },
  { id: 'assignments', label: '基金绑定', icon: Link2 },
  { id: 'backtests', label: '基金回测', icon: FlaskConical },
  { id: 'governance', label: '方法论治理', icon: ShieldCheck },
]

export function Strategies({ initialContext = null }: { initialContext?: StrategyWorkspaceContext | null }) {
  const { settings, methodologyDashboard } = useCognitrade()
  const client = useMemo(
    () => new CognitradeApiClient({ baseUrl: settings.baseUrl, token: settings.token }),
    [settings.baseUrl, settings.token],
  )
  const queryContext = useMemo(() => readWorkspaceContext(), [])
  const context = useMemo(
    () => ({ ...queryContext, ...(initialContext || {}) }),
    [initialContext, queryContext],
  )
  const [strategies, setStrategies] = useState<StrategyRecord[]>([])
  const [detail, setDetail] = useState<StrategyRecord | null>(null)
  const [selectedId, setSelectedId] = useState(context.strategyId || '')
  const [filter, setFilter] = useState<StrategyFilter>('all')
  const [tab, setTab] = useState<StrategyTab>(context.tab || 'rules')
  const [document, setDocument] = useState<StrategyDocument | null>(null)
  const [name, setName] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [publishOpen, setPublishOpen] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [legacyAvailable, setLegacyAvailable] = useState(() => Boolean(localStorage.getItem(LEGACY_POLICY_KEY)))
  const [reportJobs, setReportJobs] = useState<ReportJob[]>([])
  const [reportSets, setReportSets] = useState<DailyReportSet[]>([])
  const [signals, setSignals] = useState<TradeSignal[]>([])
  const [backtests, setBacktests] = useState<StrategyBacktest[]>([])
  const [verifications, setVerifications] = useState<StrategyVerification[]>([])
  const [events, setEvents] = useState<StrategyEvent[]>([])
  const [bindings, setBindings] = useState<PaperStrategyBinding[]>([])
  const [conflictLatest, setConflictLatest] = useState<StrategyRecord | null>(null)
  const [compareConflict, setCompareConflict] = useState(false)

  const loadList = useCallback(async (preferredId?: string) => {
    const payload = await client.listStrategies(true)
    setStrategies(payload.strategies)
    setSelectedId((current) => preferredId || current || context.strategyId || payload.strategies.find((row) => !row.is_system_template)?.id || payload.strategies[0]?.id || '')
  }, [client, context.strategyId])

  const loadDetail = useCallback(async (strategyId: string) => {
    if (!strategyId) return
    setLoading(true)
    setError('')
    try {
      const row = await client.getStrategy(strategyId)
      let nextDocument = row.draft?.document || null
      if (!nextDocument && row.latest_version) {
        const version = await client.getStrategyVersion(row.id, row.latest_version.id)
        nextDocument = version.document as StrategyDocument
      }
      const cached = readUnsavedForm(strategyId, row.draft_revision)
      setDetail(row)
      setName(cached?.name || row.name)
      setDocument(cached?.document || nextDocument)
      setDirty(Boolean(cached))
      setConflict(false)
      const [jobs, reports, runs, verificationRows, eventRows, bindingRows, signalRows] = await Promise.all([
        client.listReportJobs(),
        client.listDailyReportSets(),
        client.listStrategyBacktests({ strategyId: row.id, limit: 200 }),
        row.latest_version ? client.listStrategyVerifications(row.latest_version.id) : Promise.resolve({ verifications: [] }),
        row.is_system_template ? Promise.resolve({ events: [] }) : client.listStrategyEvents(row.id),
        client.listPaperStrategyBindings(),
        Promise.all((row.assignments || []).map((assignment) => client.listTradeSignals({ assignmentId: assignment.id }))),
      ])
      const assignmentIds = new Set((row.assignments || []).map((assignment) => assignment.id))
      const fundCodes = new Set((row.assignments || []).map((assignment) => assignment.fund_code))
      setReportJobs(jobs.jobs.filter((job) => row.assignments?.some((assignment) => assignment.id === String(job.request.assignment_id))))
      setReportSets(reports.report_sets.filter((report) => fundCodes.has(report.fund_code)))
      setSignals(signalRows.flatMap((payload) => payload.signals).filter((signal) => assignmentIds.has(signal.assignment_id)))
      setBacktests(runs.backtests)
      setVerifications(verificationRows.verifications)
      setEvents(eventRows.events)
      setBindings(bindingRows.bindings.filter((binding) => row.versions?.some((version) => version.id === binding.strategy_version_id)))
    } catch (loadError) {
      setError(errorMessage(loadError))
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadList().catch((loadError) => {
        setError(errorMessage(loadError))
        setLoading(false)
      })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [loadList])

  useEffect(() => {
    if (!selectedId) return undefined
    const timer = window.setTimeout(() => void loadDetail(selectedId), 0)
    return () => window.clearTimeout(timer)
  }, [loadDetail, selectedId])

  useEffect(() => {
    if (!dirty || !detail || !document || detail.is_system_template) return
    localStorage.setItem(UNSAVED_FORM_KEY, JSON.stringify({
      strategy_id: detail.id,
      server_revision: detail.draft_revision,
      unsaved_form: { name, document },
    }))
  }, [detail, dirty, document, name])

  const activeReportJobIds = reportJobs
    .filter((job) => ['queued', 'running'].includes(job.status))
    .map((job) => job.id)
    .sort()
    .join(',')

  useEffect(() => {
    if (!activeReportJobIds) return undefined
    let polling = false
    let disposed = false
    const ids = activeReportJobIds.split(',')
    const poll = async () => {
      if (polling || disposed) return
      polling = true
      try {
        const nextJobs = await Promise.all(ids.map((jobId) => client.getReportJob(jobId)))
        if (disposed) return
        const byId = new Map(nextJobs.map((job) => [job.id, job]))
        setReportJobs((current) => current.map((job) => byId.get(job.id) || job))
        if (nextJobs.some((job) => !['queued', 'running'].includes(job.status)) && detail?.id) {
          await loadDetail(detail.id)
        }
      } catch {
        // Keep the last persisted progress during transient transport errors.
      } finally {
        polling = false
      }
    }
    const timer = window.setInterval(() => { void poll() }, 2000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [activeReportJobIds, client, detail?.id, loadDetail])

  const activeBacktestIds = backtests
    .filter((run) => ['queued', 'running'].includes(run.status))
    .map((run) => run.id)
    .sort()
    .join(',')

  useEffect(() => {
    if (!activeBacktestIds) return undefined
    let polling = false
    let disposed = false
    const ids = activeBacktestIds.split(',')
    const poll = async () => {
      if (polling || disposed) return
      polling = true
      try {
        const nextRuns = await Promise.all(ids.map((backtestId) => client.getStrategyBacktest(backtestId)))
        if (disposed) return
        const byId = new Map(nextRuns.map((run) => [run.id, run]))
        setBacktests((current) => current.map((run) => byId.get(run.id) || run))
        if (nextRuns.some((run) => !['queued', 'running'].includes(run.status)) && detail?.id) {
          await loadDetail(detail.id)
        }
      } catch {
        // Keep the last persisted state during transient transport errors.
      } finally {
        polling = false
      }
    }
    const timer = window.setInterval(() => { void poll() }, 2000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [activeBacktestIds, client, detail?.id, loadDetail])

  const filteredStrategies = strategies.filter((row) => {
    if (filter === 'all') return true
    if (filter === 'template') return row.is_system_template
    if (filter === 'draft') return !row.is_system_template && row.has_draft
    return !row.is_system_template && row.lifecycle === filter
  })

  const perform = async (key: string, action: () => Promise<void>) => {
    setBusy(key)
    setError('')
    setNotice('')
    try {
      await action()
    } catch (actionError) {
      if (actionError instanceof CognitradeApiError && actionError.code === 'draft_revision_conflict') setConflict(true)
      if (actionError instanceof CognitradeApiError && actionError.code === 'draft_revision_conflict' && detail) {
        try {
          setConflictLatest(await client.getStrategy(detail.id))
        } catch {
          setConflictLatest(null)
        }
      }
      setError(errorMessage(actionError))
    } finally {
      setBusy('')
    }
  }

  const createBlank = () => perform('create', async () => {
    const created = await client.createStrategy({ name: `新策略 ${strategies.filter((row) => !row.is_system_template).length + 1}` })
    await loadList(created.id)
    setSelectedId(created.id)
    setNotice('已创建空白策略，可从零配置。')
  })

  const clone = (source = detail) => {
    if (!source) return
    void perform('clone', async () => {
      const created = await client.cloneStrategy(source.id)
      await loadList(created.id)
      setSelectedId(created.id)
      setNotice('已复制为个人草稿。')
    })
  }

  const save = async () => {
    if (!detail || !document || detail.is_system_template) return
    const validation = validateDocument(document, name)
    if (validation) {
      setError(validation)
      return
    }
    await perform('save', async () => {
      const saved = await client.updateStrategyDraft(detail.id, {
        expected_revision: detail.draft_revision,
        document,
        name,
      })
      localStorage.removeItem(UNSAVED_FORM_KEY)
      setDirty(false)
      setConflict(false)
      setConflictLatest(null)
      setCompareConflict(false)
      setDetail(saved)
      await loadList(saved.id)
      setNotice(`草稿已保存，服务端 revision ${saved.draft_revision}。`)
    })
  }

  const publish = () => {
    if (!detail || dirty || detail.is_system_template) return
    void perform('publish', async () => {
      await client.publishStrategy(detail.id)
      setPublishOpen(false)
      await loadList(detail.id)
      await loadDetail(detail.id)
      setNotice('策略版本已发布；已有绑定将在下一个交易日按新版本生效。')
    })
  }

  const removeOrArchive = () => {
    if (!detail || detail.is_system_template) return
    const isDraftOnly = detail.version_count === 0
    if (!window.confirm(isDraftOnly ? '确认永久删除这个未发布草稿？' : '确认归档策略并暂停全部基金绑定？')) return
    void perform('remove', async () => {
      if (isDraftOnly) await client.deleteStrategy(detail.id)
      else await client.archiveStrategy(detail.id)
      const nextId = strategies.find((row) => row.id !== detail.id)?.id || ''
      setSelectedId(nextId)
      await loadList(nextId)
      setNotice(isDraftOnly ? '草稿已删除。' : '策略已归档，绑定已暂停。')
    })
  }

  const restore = () => {
    if (!detail) return
    void perform('restore', async () => {
      await client.restoreStrategy(detail.id)
      await loadList(detail.id)
      await loadDetail(detail.id)
      setNotice('策略已恢复，原绑定已解除暂停。')
    })
  }

  const importLegacy = () => perform('legacy', async () => {
    const raw = localStorage.getItem(LEGACY_POLICY_KEY)
    const template = strategies.find((row) => row.is_system_template)
    if (!raw || !template) throw new Error('没有可导入的旧草稿或系统模板。')
    const legacy = JSON.parse(raw) as Record<string, unknown>
    const created = await client.cloneStrategy(template.id, `${String(legacy.policyName || '旧规则')}（已导入）`)
    const base = created.draft?.document
    if (!base) throw new Error('复制模板后没有生成工作草稿。')
    const imported = structuredClone(base)
    imported.signal_thresholds.buy = Number(legacy.buyThreshold ?? imported.signal_thresholds.buy)
    imported.signal_thresholds.watch = Number(legacy.watchThreshold ?? imported.signal_thresholds.watch)
    imported.signal_thresholds.minimum_confidence = Number(legacy.minimumConfidence ?? imported.signal_thresholds.minimum_confidence)
    imported.quality_gates.require_report_allowed = Boolean(legacy.requireReportAllowed ?? true)
    imported.quality_gates.require_no_critical = Boolean(legacy.requireNoCritical ?? true)
    imported.quality_gates.require_symbol_match = Boolean(legacy.requireSymbolMatch ?? true)
    imported.execution.max_single_stock_pct = Number(legacy.maxPositionPct ?? imported.execution.max_single_stock_pct)
    await client.updateStrategyDraft(created.id, {
      expected_revision: created.draft_revision,
      document: imported,
      name: created.name,
    })
    setLegacyAvailable(false)
    await loadList(created.id)
    setSelectedId(created.id)
    setNotice('旧本机草稿已显式导入为服务端策略；原 localStorage 记录未自动删除。')
  })

  const mutateDocument = (mutator: (next: StrategyDocument) => void) => {
    if (!document || detail?.is_system_template) return
    const next = structuredClone(document)
    mutator(next)
    setDocument(next)
    setDirty(true)
    setConflict(false)
  }

  return (
    <main className="page-content strategies-page" data-testid="strategies-page">
      <header className="page-heading strategies-heading">
        <div><span className="page-kicker">REPORT → SIGNAL → BACKTEST</span><h1>策略工作台</h1><p>用服务端版本合同管理基金报告、持仓 UZI、复合信号、回测与模拟盘晋级。</p></div>
        <div className="strategies-heading-actions">
          {legacyAvailable && <button className="secondary-button" onClick={() => void importLegacy()} disabled={Boolean(busy)}><Import size={14} />导入旧本机草稿</button>}
          <button className="secondary-button" onClick={() => void createBlank()} disabled={Boolean(busy)}><FilePlus2 size={14} />空白策略</button>
          <button className="primary-button" onClick={() => clone(strategies.find((row) => row.is_system_template) || null)} disabled={Boolean(busy)}><Copy size={14} />复制系统模板</button>
        </div>
      </header>

      {(error || notice) && <div className={`strategy-message ${error ? 'is-error' : 'is-notice'}`} role={error ? 'alert' : 'status'}>{error ? <CircleAlert size={15} /> : <Check size={15} />}<span>{error || notice}</span></div>}
      {conflict && <div className="strategy-conflict"><strong>草稿发生并发冲突</strong><p>本机未保存内容已保留。请选择加载服务端最新、比较差异，或另存为新策略。</p><div className="strategy-conflict-actions"><button className="secondary-button" onClick={() => detail && void loadDetail(detail.id)}><RefreshCw size={13} />加载最新</button><button className="secondary-button" onClick={() => setCompareConflict((value) => !value)} disabled={!conflictLatest}><FileDiff size={13} />比较</button><button className="secondary-button" onClick={() => document && void perform('save-as', async () => { const created = await client.createStrategy({ name: `${name}（冲突副本）`, document }); localStorage.removeItem(UNSAVED_FORM_KEY); await loadList(created.id); setSelectedId(created.id) })}><Copy size={13} />另存为新策略</button></div>{compareConflict && conflictLatest && document && <div className="strategy-conflict-compare"><section><strong>本机未保存</strong><pre>{JSON.stringify(document, null, 2)}</pre></section><section><strong>服务端 revision {conflictLatest.draft_revision}</strong><pre>{JSON.stringify(conflictLatest.draft?.document || conflictLatest.latest_version || {}, null, 2)}</pre></section></div>}</div>}

      <div className="strategies-layout">
        <aside className="strategy-catalog" aria-label="策略列表">
          <div className="strategy-filter" role="group" aria-label="策略状态筛选">
            {(['all', 'template', 'draft', 'published', 'archived'] as StrategyFilter[]).map((value) => <button key={value} className={filter === value ? 'is-active' : ''} onClick={() => setFilter(value)}>{filterLabel(value)}</button>)}
          </div>
          <div className="strategy-list">
            {filteredStrategies.map((row) => (
              <button key={row.id} className={selectedId === row.id ? 'is-active' : ''} onClick={() => setSelectedId(row.id)}>
                <span className={`strategy-list-mark is-${row.is_system_template ? 'template' : row.lifecycle}`}>{row.is_system_template ? <LockKeyhole size={14} /> : <GitBranch size={14} />}</span>
                <span><strong>{row.name}</strong><small>{row.is_system_template ? '系统模板 · 只读' : `${lifecycleLabel(row.lifecycle)} · ${row.version_count} 个版本`}</small><em>{row.assignment_count} 个基金绑定</em></span>
                <ChevronRight size={14} />
              </button>
            ))}
            {!filteredStrategies.length && <div className="strategy-list-empty">当前筛选下没有策略。</div>}
          </div>
        </aside>

        <section className="strategy-detail-shell">
          {loading || !detail || !document ? <div className="strategy-loading"><LoaderCircle className="is-spinning" size={24} /><strong>正在加载策略合同</strong></div> : <>
            <header className="strategy-detail-header">
              <div>
                <div className="strategy-detail-state"><span className={`is-${detail.is_system_template ? 'template' : detail.lifecycle}`}>{detail.is_system_template ? '系统模板' : lifecycleLabel(detail.lifecycle)}</span>{detail.latest_version && <span>v{detail.latest_version.version_number} · {governanceLabel(detail.latest_version.governance_state)}</span>}{dirty && <span className="is-dirty">未保存</span>}</div>
                <h2>{detail.name}</h2>
                <p>{document.metadata.description || '尚未填写策略说明。'}</p>
              </div>
              <div className="strategy-detail-actions">
                {detail.is_system_template ? <button className="primary-button" onClick={() => clone()}><Copy size={14} />复制后编辑</button> : detail.lifecycle === 'archived' ? <button className="primary-button" onClick={restore}><RotateCcw size={14} />恢复</button> : <>
                  <button className="secondary-button" onClick={() => clone()}><Copy size={14} />复制</button>
                  <button className="secondary-button" onClick={removeOrArchive}>{detail.version_count ? <Archive size={14} /> : <Trash2 size={14} />}{detail.version_count ? '归档' : '删除草稿'}</button>
                  <button className="secondary-button" onClick={() => void save()} disabled={!dirty || Boolean(busy)}>{busy === 'save' ? <LoaderCircle className="is-spinning" size={14} /> : <Save size={14} />}保存草稿</button>
                  <button className="primary-button" onClick={() => setPublishOpen(true)} disabled={dirty || !detail.has_draft || Boolean(busy)}><BadgeCheck size={14} />发布版本</button>
                </>}
              </div>
            </header>

            <nav className="strategy-tabs" aria-label="策略详情">
              {tabs.map(({ id, label, icon: Icon }) => <button key={id} className={tab === id ? 'is-active' : ''} onClick={() => setTab(id)}><Icon size={14} />{label}</button>)}
            </nav>

            <div className="strategy-tab-content">
              {tab === 'rules' && <RulesEditor detail={detail} name={name} document={document} setName={(value) => { setName(value); setDirty(true) }} mutate={mutateDocument} onReplace={(next) => { setDocument(next); setDirty(true) }} />}
              {tab === 'signals' && <SignalsPanel key={detail.id} client={client} detail={detail} context={context} reportJobs={reportJobs} reportSets={reportSets} signals={signals} setSignals={setSignals} reload={() => loadDetail(detail.id)} perform={perform} />}
              {tab === 'versions' && <VersionsPanel key={detail.id} client={client} detail={detail} />}
              {tab === 'assignments' && <AssignmentsPanel key={detail.id} client={client} detail={detail} context={context} reload={() => loadDetail(detail.id)} perform={perform} />}
              {tab === 'backtests' && <BacktestsPanel key={detail.id} client={client} detail={detail} context={context} reportJobs={reportJobs} backtests={backtests} reload={() => loadDetail(detail.id)} perform={perform} />}
              {tab === 'governance' && <GovernancePanel key={detail.id} client={client} detail={detail} backtests={backtests} verifications={verifications} events={events} bindings={bindings} methodologyDashboard={methodologyDashboard} reload={() => loadDetail(detail.id)} perform={perform} />}
            </div>
          </>}
        </section>
      </div>

      {publishOpen && detail && document && <PublishReview detail={detail} document={document} busy={busy === 'publish'} onCancel={() => setPublishOpen(false)} onPublish={publish} />}
    </main>
  )
}

function RulesEditor({ detail, name, document, setName, mutate, onReplace }: { detail: StrategyRecord; name: string; document: StrategyDocument; setName: (value: string) => void; mutate: (fn: (next: StrategyDocument) => void) => void; onReplace: (value: StrategyDocument) => void }) {
  const readOnly = detail.is_system_template || detail.lifecycle === 'archived'
  return <div className={`strategy-rule-editor ${readOnly ? 'is-readonly' : ''}`}>
    {readOnly && <div className="strategy-readonly-note"><LockKeyhole size={15} />{detail.is_system_template ? '系统模板只读，请先复制为个人策略。' : '归档策略恢复后才能编辑。'}</div>}
    <RuleSection index="01" title="策略身份与报告输入" description="持仓个股 UZI 是权益基金策略的必需输入，不能关闭。">
      <div className="strategy-form-grid">
        <Field label="策略名称"><input value={name} disabled={readOnly} onChange={(event) => setName(event.target.value)} /></Field>
        <Field label="持仓分析强度"><AppSelect aria-label="持仓分析强度" value={document.report_inputs.research_profile} disabled={readOnly} onChange={(event) => mutate((next) => { next.report_inputs.research_profile = event.target.value as ResearchProfile })}><option value="lite">Lite · 快速回测补跑</option><option value="medium">Medium · 标准研究</option><option value="deep">Deep · 完整深度研究</option></AppSelect></Field>
        <Field label="UZI 持仓数量"><input type="number" min="5" max="20" value={document.report_inputs.top_n} disabled={readOnly} onChange={(event) => mutate((next) => { next.report_inputs.top_n = Number(event.target.value) })} /></Field>
        <Field label="策略说明" wide><textarea value={document.metadata.description} disabled={readOnly} onChange={(event) => mutate((next) => { next.metadata.description = event.target.value })} /></Field>
      </div>
      <div className="strategy-lock-contract"><LockKeyhole size={14} /><span><strong>holding_uzi_required = true</strong>基金个股分析为强制合同；缺失时输出 BLOCKED，不用中性分填充。</span></div>
      <div className="strategy-checkbox-row">
        <CheckField label="生成独立个股信号" checked={document.report_inputs.independent_holding_signals_enabled} disabled={readOnly} onChange={(value) => mutate((next) => { next.report_inputs.independent_holding_signals_enabled = value })} />
        {(['fund_only', 'holdings_portfolio'] as BacktestMode[]).map((mode) => <CheckField key={mode} label={mode === 'fund_only' ? '基金本体回测' : '持仓组合回测'} checked={document.report_inputs.enabled_backtest_modes.includes(mode)} disabled={readOnly} onChange={(value) => mutate((next) => { next.report_inputs.enabled_backtest_modes = value ? [...new Set([...next.report_inputs.enabled_backtest_modes, mode])] : next.report_inputs.enabled_backtest_modes.filter((item) => item !== mode) })} />)}
      </div>
    </RuleSection>

    <RuleSection index="02" title="基金复合模型" description="默认 40 / 40 / 20；三组权重固定相加为 100%，缺失模块不重分配。">
      <div className="strategy-weight-grid">
        <PercentField label="基金表现/风险" value={document.fund_composite.fund_performance_risk} disabled={readOnly} onChange={(value) => mutate((next) => { next.fund_composite.fund_performance_risk = value })} />
        <PercentField label="持仓个股质量" value={document.fund_composite.holding_stock_quality} disabled={readOnly} onChange={(value) => mutate((next) => { next.fund_composite.holding_stock_quality = value })} />
        <PercentField label="持仓结构质量" value={document.fund_composite.holding_structure_quality} disabled={readOnly} onChange={(value) => mutate((next) => { next.fund_composite.holding_structure_quality = value })} />
      </div>
      <strong className={`weight-total ${Math.abs(Object.values(document.fund_composite).reduce((sum, value) => sum + value, 0) - 1) < .0001 ? 'is-valid' : 'is-invalid'}`}>当前合计 {(Object.values(document.fund_composite).reduce((sum, value) => sum + value, 0) * 100).toFixed(1)}%</strong>
      <div className="strategy-factor-columns">
        <FactorGroup title="基金表现/风险因子" weights={document.factor_weights.fund_performance_risk} disabled={readOnly} onChange={(key, value) => mutate((next) => { next.factor_weights.fund_performance_risk[key] = value })} />
        <FactorGroup title="持仓质量因子" weights={document.factor_weights.holding_stock_quality} disabled={readOnly} onChange={(key, value) => mutate((next) => { next.factor_weights.holding_stock_quality[key] = value })} />
        <FactorGroup title="持仓结构因子" weights={document.factor_weights.holding_structure_quality} disabled={readOnly} onChange={(key, value) => mutate((next) => { next.factor_weights.holding_structure_quality[key] = value })} />
      </div>
    </RuleSection>

    <RuleSection index="03" title="信号状态与质量门" description="四态状态机：ALLOW_BUY / WATCH / AVOID / BLOCKED；覆盖不足不会偷偷补分。">
      <div className="strategy-form-grid is-four">
        <NumberField label="买入阈值" value={document.signal_thresholds.buy} min={0} max={100} disabled={readOnly} onChange={(value) => mutate((next) => { next.signal_thresholds.buy = value })} />
        <NumberField label="观察阈值" value={document.signal_thresholds.watch} min={0} max={100} disabled={readOnly} onChange={(value) => mutate((next) => { next.signal_thresholds.watch = value })} />
        <PercentField label="最低置信度" value={document.signal_thresholds.minimum_confidence} disabled={readOnly} onChange={(value) => mutate((next) => { next.signal_thresholds.minimum_confidence = value })} />
        <NumberField label="缺报沿用天数" value={document.signal_thresholds.missing_report_carry_days} min={0} max={20} disabled={readOnly} onChange={(value) => mutate((next) => { next.signal_thresholds.missing_report_carry_days = value })} />
        <PercentField label="正常 coverage" value={document.quality_gates.normal_coverage} disabled={readOnly} onChange={(value) => mutate((next) => { next.quality_gates.normal_coverage = value })} />
        <PercentField label="最低 coverage" value={document.quality_gates.minimum_coverage} disabled={readOnly} onChange={(value) => mutate((next) => { next.quality_gates.minimum_coverage = value })} />
        <NumberField label="持仓披露最大陈旧天数" value={document.quality_gates.max_holding_staleness_days} min={1} max={730} disabled={readOnly} onChange={(value) => mutate((next) => { next.quality_gates.max_holding_staleness_days = value })} />
      </div>
      <div className="strategy-checkbox-row">
        <CheckField label="报告允许交付" checked={document.quality_gates.require_report_allowed} disabled={readOnly} onChange={(value) => mutate((next) => { next.quality_gates.require_report_allowed = value })} />
        <CheckField label="无关键问题" checked={document.quality_gates.require_no_critical} disabled={readOnly} onChange={(value) => mutate((next) => { next.quality_gates.require_no_critical = value })} />
        <CheckField label="标的代码一致" checked={document.quality_gates.require_symbol_match} disabled={readOnly} onChange={(value) => mutate((next) => { next.quality_gates.require_symbol_match = value })} />
        <CheckField label="严格 point-in-time" checked disabled />
      </div>
    </RuleSection>

    <RuleSection index="04" title="Hikyuu 技术规则与执行" description="报告决定交易许可，Hikyuu MA 交叉产生技术事件；费率按资产类型分别固化。">
      <div className="strategy-form-grid is-four">
        <NumberField label="快均线" value={document.execution.fast_period} min={2} max={120} disabled={readOnly} onChange={(value) => mutate((next) => { next.execution.fast_period = value })} />
        <NumberField label="慢均线" value={document.execution.slow_period} min={3} max={250} disabled={readOnly} onChange={(value) => mutate((next) => { next.execution.slow_period = value })} />
        <NumberField label="单票仓位上限 (%)" value={document.execution.max_single_stock_pct} min={1} max={100} disabled={readOnly} onChange={(value) => mutate((next) => { next.execution.max_single_stock_pct = value })} />
        <NumberField label="再平衡偏离百分点" value={document.execution.rebalance_drift_pct_points} min={0} max={100} disabled={readOnly} onChange={(value) => mutate((next) => { next.execution.rebalance_drift_pct_points = value })} />
      </div>
      <div className="strategy-form-grid is-four">
        <NumberField label="基金最大仓位 (%)" value={Number(document.asset_execution.otc_fund.max_position_pct ?? 100)} min={1} max={100} disabled={readOnly} onChange={(value) => mutate((next) => { next.asset_execution.otc_fund.max_position_pct = value })} />
        <NumberField label="单次建仓比例 (%)" value={Number(document.asset_execution.otc_fund.entry_tranche_pct ?? 100)} min={1} max={100} disabled={readOnly} onChange={(value) => mutate((next) => { next.asset_execution.otc_fund.entry_tranche_pct = value })} />
        <NumberField label="最大建仓次数" value={Number(document.asset_execution.otc_fund.max_entry_tranches ?? 1)} min={1} max={100} disabled={readOnly} onChange={(value) => mutate((next) => { next.asset_execution.otc_fund.max_entry_tranches = value })} />
        <NumberField label="建仓间隔估值日" value={Number(document.asset_execution.otc_fund.minimum_entry_interval_days ?? 0)} min={0} max={250} disabled={readOnly} onChange={(value) => mutate((next) => { next.asset_execution.otc_fund.minimum_entry_interval_days = value })} />
      </div>
      <div className="strategy-lock-contract"><ShieldCheck size={14} /><span>场外基金每批金额按当日账户权益计算，并受最大仓位约束；一个买入事件可按确认周期与间隔持续分批，直到达到仓位或次数上限。</span></div>
      <AssetFeeEditor document={document} disabled={readOnly} mutate={mutate} />
    </RuleSection>

    <RuleSection index="05" title="调度、数据 Pin 与完整合同" description="版本发布后这些值共同进入 content hash；回测不能临时覆盖。">
      <div className="strategy-form-grid is-four">
        <Field label="日报时间"><input type="time" disabled={readOnly} value={document.schedule_defaults.local_time} onChange={(event) => mutate((next) => { next.schedule_defaults.local_time = event.target.value })} /></Field>
        <Field label="结算截止"><input type="time" disabled={readOnly} value={document.schedule_defaults.settlement_cutoff} onChange={(event) => mutate((next) => { next.schedule_defaults.settlement_cutoff = event.target.value })} /></Field>
        <Field label="UZI 方法版本"><input disabled={readOnly} value={document.pins.uzi_methodology_version} onChange={(event) => mutate((next) => { next.pins.uzi_methodology_version = event.target.value })} /></Field>
        <Field label="模型 ID"><input disabled={readOnly} value={document.pins.model_id} onChange={(event) => mutate((next) => { next.pins.model_id = event.target.value })} /></Field>
        <Field label="源码 commit"><input disabled={readOnly} value={document.pins.uzi_source_commit} onChange={(event) => mutate((next) => { next.pins.uzi_source_commit = event.target.value })} /></Field>
        <Field label="Prompt hash"><input disabled={readOnly} value={document.pins.prompt_hash} onChange={(event) => mutate((next) => { next.pins.prompt_hash = event.target.value })} /></Field>
      </div>
      <CheckField label="启用自动日报" checked={document.schedule_defaults.enabled} disabled={readOnly} onChange={(value) => mutate((next) => { next.schedule_defaults.enabled = value })} />
      {!readOnly && <AdvancedDocumentEditor document={document} onReplace={onReplace} />}
    </RuleSection>
  </div>
}

function SignalsPanel({ client, detail, context, reportJobs, reportSets, signals, setSignals, reload, perform }: { client: CognitradeApiClient; detail: StrategyRecord; context: StrategyWorkspaceContext | null; reportJobs: ReportJob[]; reportSets: DailyReportSet[]; signals: TradeSignal[]; setSignals: (value: TradeSignal[]) => void; reload: () => Promise<void>; perform: (key: string, action: () => Promise<void>) => Promise<void> }) {
  const [assignmentId, setAssignmentId] = useState(context?.assignmentId || detail.assignments?.find((row) => row.fund_code === context?.fundCode)?.id || detail.assignments?.[0]?.id || '')
  const [reportSetId, setReportSetId] = useState(context?.reportSetId || reportSets[0]?.id || '')
  const [asOfDate, setAsOfDate] = useState(today())
  const [startDate, setStartDate] = useState(today(-30))
  const [endDate, setEndDate] = useState(today())
  const [estimate, setEstimate] = useState<Record<string, unknown> | null>(null)
  const assignment = detail.assignments?.find((row) => row.id === assignmentId)
  const matchingReportSets = reportSets.filter((row) => !assignment || row.fund_code === assignment.fund_code)
  const contextualReportSetId = matchingReportSets.some((row) => row.id === context?.reportSetId)
    ? context?.reportSetId || ''
    : ''
  const effectiveReportSetId = matchingReportSets.some((row) => row.id === reportSetId)
    ? reportSetId
    : contextualReportSetId || matchingReportSets[0]?.id || ''

  const preview = () => perform('preview', async () => {
    const result = await client.previewTradeSignal({ assignment_id: assignmentId, report_set_id: effectiveReportSetId, ...(detail.has_draft ? { draft_strategy_id: detail.id } : {}) })
    const report = reportSets.find((row) => row.id === effectiveReportSetId)
    const normalize = (value: typeof result.fund_signal, kind: TradeSignal['kind'], subjectCode: string, index: number): TradeSignal => ({
      id: `preview-${kind}-${subjectCode}-${index}`,
      assignment_id: result.assignment_id,
      strategy_version_id: result.strategy_version_id,
      source_report_set_id: result.report_set_id,
      kind,
      subject_code: subjectCode,
      as_of_date: report?.as_of_date || '',
      effective_from: report?.effective_date || '',
      state: value.state,
      score_lower_bound: value.score_lower_bound,
      score_upper_bound: value.score_upper_bound,
      coverage: value.coverage,
      confidence: value.confidence,
      reasons: value.reasons,
    })
    setSignals([
      normalize(result.fund_signal, 'fund', assignment?.fund_code || '', 0),
      ...(result.stock_signals || []).map((row, index) => normalize(row, 'holding_stock', row.subject_code || `holding-${index + 1}`, index + 1)),
    ])
  })
  const runDay = () => perform('report-day', async () => { await client.createReportJob({ assignment_id: assignmentId, as_of_date: asOfDate }); await reload() })
  const estimateBackfill = () => perform('estimate', async () => { setEstimate(await client.estimateReportBackfill({ assignment_id: assignmentId, start_date: startDate, end_date: endDate })) })
  const runBackfill = () => perform('backfill', async () => { await client.createReportJob({ assignment_id: assignmentId, start_date: startDate, end_date: endDate }, true); await reload() })

  if (!detail.assignments?.length) return <EmptyPanel icon={Link2} title="先创建基金绑定" text="真实信号预览必须使用 Assignment 和已经生成的当日日报，不能使用手工示例分数。" />
  return <div className="strategy-panel-stack">
    <section className="strategy-subpanel">
      <PanelTitle kicker="DAILY REPORT" title="日报生成、补跑与手动重试" description="一天一份 point-in-time 报告；失败节点可以重试，完整重跑必须二次确认。" />
      <div className="strategy-inline-form">
        <Field label="基金绑定"><AppSelect value={assignmentId} onChange={(event) => setAssignmentId(event.target.value)}>{detail.assignments.map((row) => <option key={row.id} value={row.id}>{row.fund_name || row.fund_code} · {modeLabel(row.mode)}</option>)}</AppSelect></Field>
        <Field label="报告日期"><input type="date" value={asOfDate} onChange={(event) => setAsOfDate(event.target.value)} /></Field>
        <button className="primary-button" onClick={runDay} disabled={!assignmentId}><Play size={13} />生成当日报告</button>
      </div>
      <div className="strategy-inline-form">
        <Field label="补跑开始"><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></Field>
        <Field label="补跑结束"><input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></Field>
        <button className="secondary-button" onClick={estimateBackfill}><BarChart3 size={13} />估算</button>
        <button className="secondary-button" onClick={runBackfill} disabled={!estimate}><ListRestart size={13} />确认补跑</button>
      </div>
      {estimate && <pre className="strategy-json-summary">{JSON.stringify(estimate, null, 2)}</pre>}
      <div className="strategy-record-list">
        {reportJobs.map((job) => <article key={job.id}><span className={`record-state is-${job.status}`}>{job.status}</span><div><strong>{String(job.request.start_date || job.request.as_of_date || '')} — {String(job.request.end_date || job.request.as_of_date || '')}</strong><small>{job.id} · 尝试 {job.attempts?.length || 0} 次</small><ReportJobProgressMeter job={job} />{job.error_summary && <p>{job.error_summary}</p>}</div><div className="record-actions">{['queued', 'running'].includes(job.status) && <button onClick={() => void perform(`pause-${job.id}`, async () => { await client.setReportJobPaused(job.id, true); await reload() })}><Pause size={12} />暂停</button>}{job.status === 'paused' && <button onClick={() => void perform(`resume-${job.id}`, async () => { await client.setReportJobPaused(job.id, false); await reload() })}><Play size={12} />恢复</button>}{['failed', 'completed_partial'].includes(job.status) && <button onClick={() => void perform(`retry-${job.id}`, async () => { await client.retryReportJob(job.id, {}); await reload() })}><RefreshCw size={12} />失败节点重试</button>}{!['queued', 'running'].includes(job.status) && <button onClick={() => { if (window.confirm('完整重跑会生成新 revision，确认继续？')) void perform(`rerun-${job.id}`, async () => { await client.retryReportJob(job.id, { full_rerun: true, confirmed: true }); await reload() }) }}><RotateCcw size={12} />完整重跑</button>}</div></article>)}
        {!reportJobs.length && <p className="strategy-inline-empty">暂无日报任务。</p>}
      </div>
    </section>
    <section className="strategy-subpanel">
      <PanelTitle kicker="REAL REPORT PREVIEW" title="基于真实日报预演信号" description="草稿只替换映射规则；报告、持仓、UZI Artifact 与可获得时间均来自已落库日报。" />
      <div className="strategy-inline-form"><Field label="日报 revision"><AppSelect value={effectiveReportSetId} onChange={(event) => setReportSetId(event.target.value)}><option value="">请选择真实日报</option>{matchingReportSets.map((row) => <option key={row.id} value={row.id}>{row.as_of_date} · r{row.revision} · coverage {(row.coverage * 100).toFixed(0)}%</option>)}</AppSelect></Field><button className="primary-button" disabled={!effectiveReportSetId} onClick={preview}><GitBranch size={13} />预演四态信号</button></div>
      <SignalTable signals={signals.filter((row) => row.assignment_id === assignmentId && (!effectiveReportSetId || row.source_report_set_id === effectiveReportSetId))} />
    </section>
  </div>
}

function VersionsPanel({ client, detail }: { client: CognitradeApiClient; detail: StrategyRecord }) {
  const versions = detail.versions || []
  const [fromId, setFromId] = useState(versions[1]?.id || '')
  const [toId, setToId] = useState(versions[0]?.id || '')
  const [diff, setDiff] = useState<VersionDiff | null>(null)
  const [error, setError] = useState('')
  const compare = async () => { try { setError(''); setDiff(await client.getStrategyVersionDiff(detail.id, fromId, toId)) } catch (value) { setError(errorMessage(value)) } }
  return <div className="strategy-panel-stack"><section className="strategy-subpanel"><PanelTitle kicker="IMMUTABLE VERSIONS" title="已发布版本" description="发布快照不可编辑和删除；每个版本都有稳定 content hash 与 Artifact 来源。" /><div className="strategy-version-grid">{versions.map((version) => <article key={version.id}><span>v{version.version_number}</span><strong>{governanceLabel(version.governance_state)}</strong><code>{version.content_hash.slice(0, 16)}…</code><small>{formatDate(version.published_at)} · {version.published_by}</small></article>)}{!versions.length && <p className="strategy-inline-empty">尚未发布任何版本。</p>}</div></section>{versions.length >= 2 && <section className="strategy-subpanel"><PanelTitle kicker="VERSION DIFF" title="版本比较" description="比较完整结构化合同，不只比较页面上可见字段。" /><div className="strategy-inline-form"><Field label="基准版本"><AppSelect value={fromId} onChange={(event) => setFromId(event.target.value)}>{versions.map((row) => <option key={row.id} value={row.id}>v{row.version_number}</option>)}</AppSelect></Field><Field label="目标版本"><AppSelect value={toId} onChange={(event) => setToId(event.target.value)}>{versions.map((row) => <option key={row.id} value={row.id}>v{row.version_number}</option>)}</AppSelect></Field><button className="secondary-button" onClick={() => void compare()} disabled={!fromId || !toId || fromId === toId}><FileDiff size={13} />比较</button></div>{error && <p className="strategy-inline-error">{error}</p>}{diff && <div className="strategy-diff-list">{diff.changes.map((change, index) => <article key={`${change.path}-${index}`}><code>{change.path}</code><del>{JSON.stringify(change.before)}</del><ins>{JSON.stringify(change.after)}</ins></article>)}</div>}</section>}</div>
}

function AssignmentsPanel({ client, detail, context, reload, perform }: { client: CognitradeApiClient; detail: StrategyRecord; context: StrategyWorkspaceContext | null; reload: () => Promise<void>; perform: (key: string, action: () => Promise<void>) => Promise<void> }) {
  const [fundCode, setFundCode] = useState(context?.fundCode || new URLSearchParams(window.location.search).get('fund_code') || '')
  const [fundName, setFundName] = useState(context?.fundName || '')
  const [mode, setMode] = useState<BacktestMode>(context?.mode || (new URLSearchParams(window.location.search).get('mode') as BacktestMode) || 'fund_only')
  const create = (event: FormEvent) => { event.preventDefault(); void perform('assignment', async () => { await client.createStrategyAssignment({ strategy_id: detail.id, fund_code: fundCode, fund_name: fundName, mode }); setFundCode(''); setFundName(''); await reload() }) }
  if (!detail.latest_version || detail.is_system_template) return <EmptyPanel icon={BadgeCheck} title="发布后才能绑定基金" text={detail.is_system_template ? '请先复制系统模板并发布个人版本。' : 'Assignment 固定到已发布版本，草稿不能直接运行日报。'} />
  return <div className="strategy-panel-stack"><section className="strategy-subpanel"><PanelTitle kicker="ASSIGNMENT" title="创建基金绑定" description="默认绑定基金本体；披露持仓组合只用于高级穿透研究。绑定不允许覆盖权重、阈值、MA 或费率。" /><form className="strategy-inline-form" onSubmit={create}><Field label="基金代码"><input value={fundCode} onChange={(event) => setFundCode(event.target.value)} required /></Field><Field label="基金名称"><input value={fundName} onChange={(event) => setFundName(event.target.value)} /></Field><Field label="回测模式"><AppSelect value={mode} onChange={(event) => setMode(event.target.value as BacktestMode)}><option value="fund_only">基金本体（推荐）</option><option value="holdings_portfolio">披露持仓组合（高级）</option></AppSelect></Field><button className="primary-button"><Plus size={13} />创建绑定</button></form></section><section className="strategy-subpanel"><PanelTitle kicker="ACTIVE TARGETS" title="已绑定基金" description="暂停不会删除历史日报和信号；已有信号的绑定删除时只会禁用。" /><div className="strategy-record-list">{detail.assignments?.map((row) => { const inactive = row.paused || !row.enabled; return <article key={row.id}><span className={`record-state ${inactive ? 'is-paused' : 'is-completed'}`}>{row.paused ? 'paused' : row.enabled ? 'active' : 'disabled'}</span><div><strong>{row.fund_name || row.fund_code}</strong><small>{row.fund_code} · {modeLabel(row.mode)} · {row.active_version_id}</small><p>下次生效 {row.effective_from}{row.pending_version_id ? ` · 待切换 ${row.pending_version_id}` : ''}</p></div><div className="record-actions"><button onClick={() => void perform(`toggle-${row.id}`, async () => { if (inactive) await client.updateStrategyAssignment(row.id, { enabled: true, paused: false }); else await client.setStrategyAssignmentPaused(row.id, true); await reload() })}>{inactive ? <Play size={12} /> : <Pause size={12} />}{inactive ? '恢复' : '暂停'}</button><button onClick={() => { if (window.confirm('确认删除或禁用该绑定？')) void perform(`delete-${row.id}`, async () => { await client.deleteStrategyAssignment(row.id); await reload() }) }}><Trash2 size={12} />删除</button></div></article> })}{!detail.assignments?.length && <p className="strategy-inline-empty">尚未绑定基金。</p>}</div></section></div>
}

function compactBacktestRuns(runs: StrategyBacktest[]): Array<{ run: StrategyBacktest; count: number }> {
  const groups = new Map<string, { run: StrategyBacktest; count: number }>()
  const ordered = [...runs].sort((left, right) => right.created_at.localeCompare(left.created_at))
  for (const run of ordered) {
    const key = run.input_snapshot_hash || [
      run.strategy_version_id,
      run.fund_code,
      run.mode,
      run.start_date,
      run.end_date,
      run.initial_cash,
    ].join('|')
    const current = groups.get(key)
    if (current) current.count += 1
    else groups.set(key, { run, count: 1 })
  }
  return [...groups.values()]
}

function BacktestsPanel({ client, detail, context, reportJobs, backtests, reload, perform }: { client: CognitradeApiClient; detail: StrategyRecord; context: StrategyWorkspaceContext | null; reportJobs: ReportJob[]; backtests: StrategyBacktest[]; reload: () => Promise<void>; perform: (key: string, action: () => Promise<void>) => Promise<void> }) {
  const query = new URLSearchParams(window.location.search)
  const preferredAssignment = detail.assignments?.find((row) => row.mode === 'fund_only') || detail.assignments?.[0]
  const [versionId, setVersionId] = useState(context?.strategyVersionId || query.get('strategy_version_id') || detail.latest_version?.id || '')
  const [fundCode, setFundCode] = useState(context?.fundCode || query.get('fund_code') || preferredAssignment?.fund_code || '')
  const [mode, setMode] = useState<BacktestMode>(context?.mode || (query.get('mode') as BacktestMode) || preferredAssignment?.mode || 'fund_only')
  const [startDate, setStartDate] = useState(today(-365))
  const [endDate, setEndDate] = useState(today())
  const [initialCash, setInitialCash] = useState(1_000_000)
  const [reportPolicy, setReportPolicy] = useState<'existing_only' | 'require_complete'>('existing_only')
  const [benchmarkChoice, setBenchmarkChoice] = useState<'default' | 'hs300' | 'none'>('default')
  const [preflight, setPreflight] = useState<BacktestPreflight | null>(null)
  const [estimate, setEstimate] = useState<Record<string, unknown> | null>(null)
  const [submittedBackfill, setSubmittedBackfill] = useState<ReportJob | null>(null)
  const [selectedBacktest, setSelectedBacktest] = useState<StrategyBacktest | null>(null)
  const [resultLoadingId, setResultLoadingId] = useState('')
  const [resultError, setResultError] = useState('')
  const [showAllRuns, setShowAllRuns] = useState(false)
  const request = { strategy_version_id: versionId, fund_code: fundCode, mode, start_date: startDate, end_date: endDate }
  const assignmentOptions = detail.assignments?.filter((row) => row.mode === mode) || []
  const selectedVersion = detail.versions?.find((row) => row.id === versionId) || null
  const latestVersion = detail.latest_version || detail.versions?.[0] || null
  const assignment = detail.assignments?.find((row) => row.fund_code === fundCode && row.mode === mode)
  const persistedBackfill = reportJobs.find((job) => (
    job.job_type === 'historical_backfill'
    && String(job.request.assignment_id || '') === String(assignment?.id || '')
    && String(job.request.strategy_version_id || '') === versionId
    && String(job.request.start_date || '') === startDate
    && String(job.request.end_date || '') === endDate
  )) || null
  const backfillJob = persistedBackfill || submittedBackfill
  const backfillPending = Boolean(backfillJob && ['queued', 'running', 'paused'].includes(backfillJob.status))
  const scopedBacktests = backtests.filter((run) => (!fundCode || run.fund_code === fundCode) && run.mode === mode)
  const compactedBacktests = compactBacktestRuns(scopedBacktests)
  const compactVisibleBacktests = compactedBacktests.slice(0, 12).map((group) => group.run)
  const visibleBacktests = showAllRuns ? scopedBacktests : compactVisibleBacktests
  const duplicateCountById = new Map(compactedBacktests.map((group) => [group.run.id, group.count]))
  const hiddenRunCount = Math.max(0, scopedBacktests.length - compactVisibleBacktests.length)
  const versionSignalsMissing = Boolean(
    preflight
    && preflight.available_report_count > 0
    && preflight.missing_report_dates.length === 0
    && preflight.signal_count === 0,
  )
  const resetPreflight = () => { setPreflight(null); setEstimate(null); setSubmittedBackfill(null) }
  const runPreflight = () => perform('preflight', async () => { setPreflight(await client.preflightStrategyBacktest(request)); setEstimate(null) })
  const estimateMissing = () => perform('estimate-missing', async () => { if (!assignment) throw new Error('没有匹配的基金绑定。'); setEstimate(await client.estimateReportBackfill({ assignment_id: assignment.id, strategy_version_id: versionId, start_date: startDate, end_date: endDate })) })
  const backfillMissing = () => perform('backfill-missing', async () => {
    if (!assignment) throw new Error('没有匹配的基金绑定。')
    const job = await client.createReportJob({ assignment_id: assignment.id, strategy_version_id: versionId, start_date: startDate, end_date: endDate }, true)
    setSubmittedBackfill(job)
    setEstimate(null)
    await reload()
  })
  const create = () => perform('backtest', async () => { await client.createStrategyBacktest({ ...request, initial_cash: initialCash, benchmark: { fund_buy_and_hold: mode === 'fund_only', initial_disclosed_weights_static: mode === 'holdings_portfolio', hs300: benchmarkChoice === 'hs300' || (benchmarkChoice === 'default' && mode === 'holdings_portfolio'), category_index: benchmarkChoice === 'default' && mode === 'fund_only' }, report_policy: reportPolicy }); setPreflight(null); await reload() })
  const openResult = async (run: StrategyBacktest) => {
    if (selectedBacktest?.id === run.id) {
      setSelectedBacktest(null)
      return
    }
    setResultLoadingId(run.id)
    setResultError('')
    try {
      setSelectedBacktest(run.result ? run : await client.getStrategyBacktest(run.id))
    } catch (value) {
      setResultError(errorMessage(value))
    } finally {
      setResultLoadingId('')
    }
  }
  if (!detail.versions?.length) return <EmptyPanel icon={FlaskConical} title="没有可回测的发布版本" text="回测只接受不可变 strategy_version_id，不提供临时权重、阈值、MA 或费率参数。" />
  return <div className="strategy-panel-stack">
    <section className="strategy-subpanel">
      <PanelTitle kicker="STRICT BACKTEST CONTRACT" title="基金回测预检" description="默认回测基金本体净值；披露持仓组合仅作为高级研究模拟。先检查日报与信号覆盖。" />
      <div className="strategy-form-grid is-four">
        <Field label="策略版本"><AppSelect value={versionId} onChange={(event) => { setVersionId(event.target.value); resetPreflight() }}>{detail.versions.map((row) => <option key={row.id} value={row.id}>v{row.version_number}{row.id === latestVersion?.id ? ' · 最新' : ''} · {row.content_hash.slice(0, 8)}</option>)}</AppSelect></Field>
        <Field label="目标基金"><AppSelect value={fundCode} onChange={(event) => { setFundCode(event.target.value); resetPreflight(); setShowAllRuns(false) }}><option value="">请选择绑定基金</option>{assignmentOptions.map((row) => <option key={row.id} value={row.fund_code}>{row.fund_name || row.fund_code}</option>)}</AppSelect></Field>
        <Field label="模式"><AppSelect value={mode} onChange={(event) => { const nextMode = event.target.value as BacktestMode; const matching = detail.assignments?.find((row) => row.mode === nextMode && row.fund_code === fundCode) || detail.assignments?.find((row) => row.mode === nextMode); setMode(nextMode); setFundCode(matching?.fund_code || ''); resetPreflight(); setShowAllRuns(false) }}><option value="fund_only">基金本体（推荐）</option><option value="holdings_portfolio">披露持仓组合（高级）</option></AppSelect></Field>
        <NumberField label="初始资金" value={initialCash} min={10000} max={1_000_000_000} onChange={setInitialCash} />
        <Field label="开始日期"><input type="date" value={startDate} onChange={(event) => { setStartDate(event.target.value); resetPreflight() }} /></Field>
        <Field label="结束日期"><input type="date" value={endDate} onChange={(event) => { setEndDate(event.target.value); resetPreflight() }} /></Field>
        <Field label="报告获取策略"><AppSelect value={reportPolicy} onChange={(event) => setReportPolicy(event.target.value as 'existing_only' | 'require_complete')}><option value="existing_only">使用现有日报（允许 BLOCKED/沿用）</option><option value="require_complete">要求日报完整</option></AppSelect></Field>
        <Field label="附加基准"><AppSelect value={benchmarkChoice} onChange={(event) => setBenchmarkChoice(event.target.value as 'default' | 'hs300' | 'none')}><option value="default">模式默认（类别指数 / 沪深300）</option><option value="hs300">沪深300</option><option value="none">仅基金/期初持仓基准</option></AppSelect></Field>
      </div>
      <div className={`backtest-version-selection ${selectedVersion?.id === latestVersion?.id ? 'is-current' : 'is-stale'}`}>
        <div><strong>本次回测固定使用 {selectedVersion ? `v${selectedVersion.version_number}` : '未选择版本'}</strong><small>{selectedVersion?.id || '请选择一个不可变版本'}{selectedVersion ? ` · ${selectedVersion.content_hash.slice(0, 12)}` : ''}</small></div>
        {latestVersion && selectedVersion?.id !== latestVersion.id && <button className="secondary-button" onClick={() => { setVersionId(latestVersion.id); resetPreflight() }}><RefreshCw size={13} />切换到最新版 v{latestVersion.version_number}</button>}
      </div>
      <div className="strategy-action-row"><button className="secondary-button" onClick={runPreflight} disabled={!versionId || !fundCode}><ShieldCheck size={13} />执行预检</button><button className="primary-button" onClick={create} disabled={!preflight || !preflight.signal_count || (reportPolicy === 'require_complete' && Boolean(preflight.missing_report_dates.length))}><Play size={13} />启动回测</button></div>
      {preflight && <div className={`backtest-preflight ${preflight.ready ? 'is-ready' : 'is-warning'}`}><strong>{preflight.signal_count} 个基金信号 · {preflight.available_report_count}/{preflight.trading_day_count} 个日报交易日</strong><p>当前预检版本：{selectedVersion ? `v${selectedVersion.version_number} · ` : ''}{preflight.strategy_content_hash}</p>{versionSignalsMissing && <><p>日报已经齐备，但所选版本尚未生成信号；系统将复用现有日报，不会重新运行持仓 UZI 分析。</p><div className="strategy-action-row"><button className="primary-button" disabled={backfillPending} onClick={backfillMissing}>{backfillJob?.status === 'paused' ? '信号生成已暂停' : backfillPending ? '新版本信号生成中' : `生成 ${selectedVersion ? `v${selectedVersion.version_number}` : '新版本'} 信号`}</button></div></>}{preflight.missing_report_dates.length > 0 && <><p>缺失 {preflight.missing_report_dates.length} 天：{preflight.missing_report_dates.slice(0, 12).join('、')}{preflight.missing_report_dates.length > 12 ? '…' : ''}</p><div className="strategy-action-row"><button className="secondary-button" onClick={estimateMissing} disabled={backfillPending}>估算补跑成本</button><button className="secondary-button" disabled={!estimate || backfillPending} onClick={backfillMissing}>{backfillJob?.status === 'paused' ? '补跑已暂停' : backfillPending ? '补跑进行中' : '确认补跑'}</button></div>{estimate && <pre className="strategy-json-summary">{JSON.stringify(estimate, null, 2)}</pre>}</>}</div>}
      {backfillJob && <BackfillProgress job={backfillJob} onPreflight={runPreflight} />}
    </section>
    <section className="strategy-subpanel">
      <PanelTitle kicker="TRACEABLE RESULTS" title="基金回测记录" description={`仅显示当前基金与模式；默认合并相同输入并保留最近 12 组。共 ${scopedBacktests.length} 次原始运行。`} />
      {hiddenRunCount > 0 && <div className="strategy-action-row"><button className="secondary-button" onClick={() => setShowAllRuns((value) => !value)}>{showAllRuns ? '收起重复与较早运行' : `展开全部运行（另 ${hiddenRunCount} 次）`}</button></div>}
      <div className="strategy-record-list">
        {visibleBacktests.map((run) => <article key={run.id} className={selectedBacktest?.id === run.id ? 'is-selected' : ''}>
          <span className={`record-state is-${run.status}`}>{run.status}</span>
          <div><strong>{run.fund_name || run.fund_code} · {modeLabel(run.mode)}</strong><small>{run.start_date} — {run.end_date} · {run.strategy_content_hash.slice(0, 12)}{Number(duplicateCountById.get(run.id) || 1) > 1 ? ` · ${duplicateCountById.get(run.id)} 次相同输入` : ''}</small><p>{metricSummary(run.metrics)}{run.error_summary ? ` · ${run.error_summary}` : ''}</p></div>
          <div className="record-actions">
            {run.status === 'completed' && <button aria-expanded={selectedBacktest?.id === run.id} aria-controls="backtest-result-panel" disabled={resultLoadingId === run.id} onClick={() => void openResult(run)}>{resultLoadingId === run.id ? <LoaderCircle className="is-spinning" size={12} /> : <BarChart3 size={12} />}{selectedBacktest?.id === run.id ? '收起图表' : '查看图表'}</button>}
            {['failed', 'cancelled'].includes(run.status) && <button onClick={() => void perform(`retry-bt-${run.id}`, async () => { await client.retryStrategyBacktest(run.id); await reload() })}><RefreshCw size={12} />重试</button>}
            {['queued', 'running'].includes(run.status) && <button onClick={() => void perform(`cancel-bt-${run.id}`, async () => { await client.cancelStrategyBacktest(run.id); await reload() })}><Pause size={12} />取消</button>}
          </div>
        </article>)}
        {!visibleBacktests.length && <p className="strategy-inline-empty">当前基金与模式暂无回测。</p>}
      </div>
      {resultError && <p className="strategy-inline-error">{resultError}</p>}
      {selectedBacktest?.result && <BacktestResultPanel run={selectedBacktest} onClose={() => setSelectedBacktest(null)} />}
    </section>
  </div>
}

type BacktestChartDatum = {
  date: string
  strategy?: number
  fundBuyHold?: number
  categoryIndex?: number
  hs300?: number
}

function BacktestResultPanel({ run, onClose }: { run: StrategyBacktest; onClose: () => void }) {
  const result = run.result as StrategyBacktestResult
  const metrics = { ...run.metrics, ...(result.metrics || {}) }
  const chartData = backtestChartData(result, run.initial_cash)
  const fundBenchmark = result.benchmarks?.fund_buy_and_hold
  const benchmarkReturn = finiteNumber(fundBenchmark?.return_pct)
  const strategyReturn = finiteNumber(metrics.total_return_pct) || 0
  const excessReturn = benchmarkReturn === null ? null : strategyReturn - benchmarkReturn
  const hasFundCurve = chartData.some((row) => row.fundBuyHold !== undefined)
  const hasCategoryCurve = chartData.some((row) => row.categoryIndex !== undefined)
  const hasHs300Curve = chartData.some((row) => row.hs300 !== undefined)
  const benchmarkRows = Object.entries(result.benchmarks || {})
  const trades = result.trades || []
  const openStatusAssumptions = Array.isArray(result.execution_adapter?.open_status_assumptions)
    ? result.execution_adapter.open_status_assumptions as Array<Record<string, unknown>>
    : []
  return <section id="backtest-result-panel" className="backtest-result-panel" aria-labelledby="backtest-result-title">
    <header className="backtest-result-header">
      <div><span className="section-kicker">BACKTEST RESULT</span><h4 id="backtest-result-title">{run.fund_name || run.fund_code} · {modeLabel(run.mode)}</h4><p>{run.start_date} — {run.end_date} · {run.id}</p></div>
      <button className="secondary-button" onClick={onClose}><ChevronDown size={13} />收起</button>
    </header>
    <div className="backtest-result-metrics">
      <BacktestMetric label="策略收益" value={formatSignedPercent(strategyReturn)} tone={strategyReturn > 0 ? 'positive' : strategyReturn < 0 ? 'negative' : 'neutral'} />
      <BacktestMetric label="基金买入持有" value={benchmarkReturn === null ? '不可用' : formatSignedPercent(benchmarkReturn)} tone={benchmarkReturn !== null && benchmarkReturn > 0 ? 'positive' : benchmarkReturn !== null && benchmarkReturn < 0 ? 'negative' : 'neutral'} />
      <BacktestMetric label="相对基金超额" value={excessReturn === null ? '—' : formatSignedPercent(excessReturn)} tone={excessReturn !== null && excessReturn > 0 ? 'positive' : excessReturn !== null && excessReturn < 0 ? 'negative' : 'neutral'} />
      <BacktestMetric label="最大回撤" value={formatSignedPercent(Number(metrics.max_drawdown_pct || 0))} tone={Number(metrics.max_drawdown_pct || 0) < 0 ? 'negative' : 'neutral'} />
      <BacktestMetric label="成交 / 换手" value={`${Number(metrics.trade_count || 0)} 笔 · ${Number(metrics.turnover_pct || 0).toFixed(2)}%`} />
      <BacktestMetric label="期末权益" value={formatCurrency(Number(metrics.final_equity || run.initial_cash))} />
    </div>
    <div className="backtest-chart-card">
      <header><div><strong>累计收益曲线</strong><small>全部序列以回测起点归一为 0%</small></div><span>{chartData.length} 个估值日</span></header>
      {chartData.length ? <div className="backtest-chart" role="img" aria-label={`策略累计收益 ${formatSignedPercent(strategyReturn)}，基金买入持有 ${benchmarkReturn === null ? '不可用' : formatSignedPercent(benchmarkReturn)}`}>
        <ResponsiveContainer width="100%" height="100%">
          <RechartsLineChart data={chartData} margin={{ top: 12, right: 14, left: 0, bottom: 4 }} accessibilityLayer>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 5" vertical={false} />
            <XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fill: 'var(--muted)', fontSize: 9 }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
            <YAxis tickFormatter={(value) => `${Number(value).toFixed(1)}%`} tick={{ fill: 'var(--muted)', fontSize: 9 }} axisLine={false} tickLine={false} width={46} />
            <Tooltip formatter={(value) => `${Number(value).toFixed(3)}%`} labelFormatter={(value) => `估值日 ${value}`} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 8, color: 'var(--text)', fontSize: 10 }} />
            <Legend wrapperStyle={{ color: 'var(--text-soft)', fontSize: 9, paddingTop: 8 }} />
            <Line type="monotone" dataKey="strategy" name="策略" stroke="var(--mint)" strokeWidth={2.4} dot={{ r: 2.5 }} activeDot={{ r: 4 }} connectNulls isAnimationActive={false} />
            {hasFundCurve && <Line type="monotone" dataKey="fundBuyHold" name="基金买入持有" stroke="var(--gold)" strokeWidth={2} strokeDasharray="6 4" dot={{ r: 2 }} connectNulls isAnimationActive={false} />}
            {hasCategoryCurve && <Line type="monotone" dataKey="categoryIndex" name="类别指数" stroke="var(--blue)" strokeWidth={1.7} dot={false} connectNulls isAnimationActive={false} />}
            {hasHs300Curve && <Line type="monotone" dataKey="hs300" name="沪深300" stroke="var(--rose)" strokeWidth={1.7} dot={false} connectNulls isAnimationActive={false} />}
          </RechartsLineChart>
        </ResponsiveContainer>
      </div> : <p className="strategy-inline-empty">结果中没有可绘制的权益曲线。</p>}
      <details className="backtest-data-table"><summary>查看逐日数据</summary><div><table><thead><tr><th>日期</th><th>策略</th><th>基金基准</th><th>类别指数</th><th>沪深300</th></tr></thead><tbody>{chartData.map((row) => <tr key={row.date}><td>{row.date}</td><td>{formatChartValue(row.strategy)}</td><td>{formatChartValue(row.fundBuyHold)}</td><td>{formatChartValue(row.categoryIndex)}</td><td>{formatChartValue(row.hs300)}</td></tr>)}</tbody></table></div></details>
    </div>
    <div className="backtest-result-grid">
      <section><h5>基准与数据质量</h5><div className="backtest-quality-list">{benchmarkRows.map(([key, benchmark]) => <div key={key}><strong>{benchmarkLabel(key)}</strong><span className={benchmark.status === 'available' ? 'is-available' : 'is-unavailable'}>{benchmark.status === 'available' ? `${formatSignedPercent(Number(benchmark.return_pct || 0))}` : `不可用 · ${benchmark.reason || '无有效序列'}`}</span></div>)}{!benchmarkRows.length && <p>未配置比较基准。</p>}</div><ul className="backtest-audit-notes"><li>{result.report_set_ids?.length || 0} 份日报进入本次计算</li><li>结果哈希：{run.result_content_hash || '未返回'}</li>{result.execution_adapter?.max_position_pct !== undefined && <li>基金建仓规则：仓位上限 {Number(result.execution_adapter.max_position_pct)}% · 单批 {Number(result.execution_adapter.entry_tranche_pct)}% · 最多 {Number(result.execution_adapter.max_entry_tranches)} 次 · 间隔 {Number(result.execution_adapter.minimum_entry_interval_days)} 个估值日</li>}{result.execution_adapter?.degraded_estimate === true && <li>场外基金费用与确认周期采用已发布策略版本中的兜底参数</li>}{String(result.execution_adapter?.open_status_validation || '') === 'order_date_and_side' && <li>申购/赎回开放状态仅在真实订单日按方向校验</li>}{openStatusAssumptions.length > 0 && <li>历史开放状态缺失，按开放估算 {openStatusAssumptions.length} 次：{openStatusAssumptions.map((row) => `${String(row.date || '—')} ${row.side === 'sell' ? '赎回' : '申购'}`).join('、')}</li>}</ul></section>
      <section><h5>成交与信号血缘</h5>{trades.length ? <div className="backtest-trade-table"><table><thead><tr><th>确认日</th><th>方向</th><th>标的</th><th>成交额</th><th>费用</th><th>信号</th></tr></thead><tbody>{trades.map((trade, index) => <tr key={`${trade.date}-${trade.symbol || ''}-${index}`}><td>{trade.date}<small>{trade.order_date ? `下单 ${trade.order_date}` : ''}</small></td><td><span className={`trade-side is-${trade.side}`}>{trade.side === 'buy' ? '申购/买入' : '赎回/卖出'}</span>{trade.entry_tranche_index ? <small>第 {trade.entry_tranche_index}/{trade.entry_tranche_limit} 批</small> : null}</td><td>{trade.symbol || trade.instrument_id || '—'}</td><td>{formatCurrency(Number(trade.gross_value || 0))}</td><td>{formatCurrency(Number(trade.fees?.total || 0))}</td><td><code>{trade.signal_id || trade.signal_ids?.[0] || '—'}</code></td></tr>)}</tbody></table></div> : <div className="backtest-no-trades"><ShieldCheck size={18} /><div><strong>本次没有形成成交</strong><p>策略权益曲线仍然有效：它表示信号门禁下保持现金或既有仓位的实际结果。</p></div></div>}</section>
    </div>
  </section>
}

function BacktestMetric({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'positive' | 'negative' | 'neutral' }) {
  return <article className={`backtest-metric is-${tone}`}><span>{label}</span><strong>{value}</strong></article>
}

function backtestChartData(result: StrategyBacktestResult, initialCash: number): BacktestChartDatum[] {
  const rows = new Map<string, BacktestChartDatum>()
  const ensure = (dateValue: string) => {
    const current = rows.get(dateValue) || { date: dateValue }
    rows.set(dateValue, current)
    return current
  }
  for (const row of result.equity_curve || []) {
    const equity = finiteNumber(row.equity)
    if (!row.date || equity === null || initialCash <= 0) continue
    ensure(row.date).strategy = (equity / initialCash - 1) * 100
  }
  const benchmarkKeys: Array<[string, keyof Omit<BacktestChartDatum, 'date'>]> = [
    ['fund_buy_and_hold', 'fundBuyHold'],
    ['category_index', 'categoryIndex'],
    ['hs300', 'hs300'],
  ]
  for (const [benchmarkKey, dataKey] of benchmarkKeys) {
    for (const row of result.benchmarks?.[benchmarkKey]?.equity_curve || []) {
      const value = finiteNumber(row.return_pct)
      if (!row.date || value === null) continue
      ensure(row.date)[dataKey] = value
    }
  }
  return [...rows.values()].sort((left, right) => left.date.localeCompare(right.date))
}

function benchmarkLabel(key: string): string {
  return ({ fund_buy_and_hold: '基金买入持有', category_index: '类别指数', hs300: '沪深300', initial_disclosed_weights_static: '期初披露持仓' } as Record<string, string>)[key] || key
}

function finiteNumber(value: unknown): number | null {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatSignedPercent(value: number): string { return `${value > 0 ? '+' : ''}${value.toFixed(2)}%` }
function formatChartValue(value: number | undefined): string { return value === undefined ? '—' : formatSignedPercent(value) }
function formatCurrency(value: number): string { return Number.isFinite(value) ? `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}` : '—' }

function BackfillProgress({ job, onPreflight }: { job: ReportJob; onPreflight: () => Promise<void> }) {
  const { total, completed, failed, processed, percent, completeReportDays, partialReportDays, blockedReportDays } = reportJobProgress(job)
  const terminal = ['completed', 'completed_partial', 'failed', 'cancelled'].includes(job.status)
  return <div className={`backfill-progress is-${job.status}`}>
    <header><div><strong>日报补跑 · {job.status.toUpperCase()}</strong><code>{job.id}</code></div><span>{processed}/{total} · {percent}%</span></header>
    <div className="backfill-progress-track" role="progressbar" aria-label="日报补跑进度" aria-valuemin={0} aria-valuemax={total} aria-valuenow={processed}><span style={{ width: `${percent}%` }} /></div>
    <p>已结算 {completed} · 完整日报 {completeReportDays} · 部分日报 {partialReportDays} · 阻断日报 {blockedReportDays} · 执行异常 {failed}{job.progress.current_date ? ` · 当前 ${String(job.progress.current_date)}` : ''}</p>
    {job.error_summary && <p className="strategy-inline-error">{job.error_summary}</p>}
    {terminal && <button className="secondary-button" onClick={() => void onPreflight()}><ShieldCheck size={13} />重新执行预检</button>}
  </div>
}

function ReportJobProgressMeter({ job }: { job: ReportJob }) {
  const { total, completed, failed, processed, percent, completeReportDays, partialReportDays, blockedReportDays } = reportJobProgress(job)
  return <div className="report-job-progress">
    <div><span>{processed}/{total} · {percent}%</span><small>已结算 {completed} · 完整 {completeReportDays} · 部分 {partialReportDays} · 阻断 {blockedReportDays} · 异常 {failed}{job.progress.current_date ? ` · 当前 ${String(job.progress.current_date)}` : ''}</small></div>
    <div className="backfill-progress-track" role="progressbar" aria-label={`日报任务进度 ${job.id}`} aria-valuemin={0} aria-valuemax={total} aria-valuenow={processed}><span style={{ width: `${percent}%` }} /></div>
  </div>
}

function reportJobProgress(job: ReportJob) {
  const total = Math.max(0, Number(job.progress.total) || 0)
  const completed = Math.max(0, Number(job.progress.completed) || 0)
  const failed = Math.max(0, Number(job.progress.failed) || 0)
  const processed = Math.min(total, completed + failed)
  const completeReportDays = Math.max(0, Number(job.progress.complete_report_days) || 0)
  const partialReportDays = Math.max(0, Number(job.progress.partial_report_days) || 0)
  const blockedReportDays = Math.max(0, Number(job.progress.blocked_report_days) || 0)
  return { total, completed, failed, processed, completeReportDays, partialReportDays, blockedReportDays, percent: total ? Math.round((processed / total) * 100) : 0 }
}

function GovernancePanel({ client, detail, backtests, verifications, events, bindings, methodologyDashboard, reload, perform }: { client: CognitradeApiClient; detail: StrategyRecord; backtests: StrategyBacktest[]; verifications: StrategyVerification[]; events: StrategyEvent[]; bindings: PaperStrategyBinding[]; methodologyDashboard: ReturnType<typeof useCognitrade>['methodologyDashboard']; reload: () => Promise<void>; perform: (key: string, action: () => Promise<void>) => Promise<void> }) {
  const completed = backtests.filter((row) => row.status === 'completed')
  const [versionId, setVersionId] = useState(detail.latest_version?.id || '')
  const [backtestId, setBacktestId] = useState(completed.find((row) => row.strategy_version_id === versionId)?.id || '')
  const [accountId, setAccountId] = useState('paper-default')
  const [fundCode, setFundCode] = useState(detail.assignments?.[0]?.fund_code || '')
  const [mode, setMode] = useState<BacktestMode>(detail.assignments?.[0]?.mode || 'holdings_portfolio')
  const [verificationThresholds, setVerificationThresholds] = useState<Record<string, number | boolean>>({ minimum_sample_days: 20, minimum_signal_count: 1, maximum_drawdown_pct: 40, minimum_annualized_return_pct: -100, minimum_sharpe: -10, minimum_excess_return_pct: -100, maximum_turnover_pct: 10000, maximum_fee_impact_pct: 10, require_deterministic_replay: true })
  const profile = dashboardValue(methodologyDashboard, 'profile')
  const guardrails = Array.isArray(profile.guardrails) ? profile.guardrails.map((item) => asString(item)).filter(Boolean) : []
  const memories = dashboardItems(methodologyDashboard, 'verified_policy_memory')
  const setVerificationThreshold = (key: string, value: number | boolean) => setVerificationThresholds((current) => ({ ...current, [key]: value }))
  const verify = () => perform('verify', async () => { await client.createStrategyVerification({ strategy_version_id: versionId, backtest_run_id: backtestId, thresholds: verificationThresholds }); await reload() })
  const promote = () => perform('promote', async () => { const version = detail.versions?.find((row) => row.id === versionId); if (!version || !['verified', 'promoted'].includes(version.governance_state)) throw new Error('只有 Verified 或已 Promoted 的版本可以绑定模拟交易。'); await client.promoteStrategyVersion(detail.id, versionId, { account_id: accountId, fund_code: fundCode, mode }); await reload() })
  return <div className="strategy-panel-stack"><section className="strategy-subpanel"><PanelTitle kicker="METHOD CONTRACT" title={asString(profile.name, '方法论与治理边界')} description={asString(profile.description, 'DomainPack 契约、质量门和 Policy Memory 在这里作为策略治理依据。')} /><div className="governance-grid"><article><strong>DomainPack</strong><span>{asString(profile.version, '未声明版本')}</span><p>证据优先 · 禁止隐式补值 · point-in-time</p></article><article><strong>治理约束</strong><span>{guardrails.length}</span><p>{guardrails.slice(0, 2).join('；') || '后端未返回 guardrails'}</p></article><article><strong>Policy Memory</strong><span>{memories.length}</span><p>只展示 verified / promoted 记录</p></article></div></section><section className="strategy-subpanel"><PanelTitle kicker="VERIFICATION" title="机器检查与人工确认" description="自动检查无前视、lineage、确定性复跑、coverage、样本、费用和绩效门槛；通过后仍需人工确认。" /><div className="strategy-inline-form"><Field label="策略版本"><AppSelect value={versionId} onChange={(event) => { setVersionId(event.target.value); setBacktestId('') }}>{detail.versions?.map((row) => <option key={row.id} value={row.id}>v{row.version_number} · {governanceLabel(row.governance_state)}</option>)}</AppSelect></Field><Field label="完成的回测"><AppSelect value={backtestId} onChange={(event) => setBacktestId(event.target.value)}><option value="">选择同版本回测</option>{completed.filter((row) => row.strategy_version_id === versionId).map((row) => <option key={row.id} value={row.id}>{row.id} · {row.fund_code}</option>)}</AppSelect></Field><button className="secondary-button" disabled={!backtestId} onClick={verify}><ShieldCheck size={13} />运行机器检查</button></div><div className="strategy-form-grid is-four"><NumberField label="最少样本交易日" value={Number(verificationThresholds.minimum_sample_days)} min={1} max={100000} onChange={(value) => setVerificationThreshold('minimum_sample_days', value)} /><NumberField label="最少信号数" value={Number(verificationThresholds.minimum_signal_count)} min={1} max={100000} onChange={(value) => setVerificationThreshold('minimum_signal_count', value)} /><NumberField label="最大回撤上限 %" value={Number(verificationThresholds.maximum_drawdown_pct)} min={0} max={100} step="any" onChange={(value) => setVerificationThreshold('maximum_drawdown_pct', value)} /><NumberField label="最低年化收益 %" value={Number(verificationThresholds.minimum_annualized_return_pct)} min={-1000} max={1000} step="any" onChange={(value) => setVerificationThreshold('minimum_annualized_return_pct', value)} /><NumberField label="最低 Sharpe" value={Number(verificationThresholds.minimum_sharpe)} min={-20} max={20} step="any" onChange={(value) => setVerificationThreshold('minimum_sharpe', value)} /><NumberField label="最低超额收益 %" value={Number(verificationThresholds.minimum_excess_return_pct)} min={-1000} max={1000} step="any" onChange={(value) => setVerificationThreshold('minimum_excess_return_pct', value)} /><NumberField label="最大换手率 %" value={Number(verificationThresholds.maximum_turnover_pct)} min={0} max={100000} step="any" onChange={(value) => setVerificationThreshold('maximum_turnover_pct', value)} /><NumberField label="最大费用影响 %" value={Number(verificationThresholds.maximum_fee_impact_pct)} min={0} max={100} step="any" onChange={(value) => setVerificationThreshold('maximum_fee_impact_pct', value)} /><CheckField label="要求确定性复跑一致" checked={Boolean(verificationThresholds.require_deterministic_replay)} onChange={(value) => setVerificationThreshold('require_deterministic_replay', value)} /></div><div className="verification-list">{verifications.map((row) => <article key={row.id}><header><span className={`record-state is-${row.status}`}>{row.status}</span><strong>{row.machine_passed ? '机器检查通过' : '机器检查失败'}</strong><small>{formatDate(row.created_at)}</small></header><div>{Object.entries(row.checks).map(([key, check]) => <span key={key} className={check.passed ? 'is-pass' : 'is-fail'}>{check.passed ? <Check size={11} /> : <CircleAlert size={11} />}{key}</span>)}</div>{row.machine_passed && !row.confirmed_at && <button className="primary-button" onClick={() => void perform(`confirm-${row.id}`, async () => { await client.confirmStrategyVerification(row.id); await reload() })}><BadgeCheck size={13} />人工确认 Verified</button>}</article>)}{!verifications.length && <p className="strategy-inline-empty">暂无验证记录。</p>}</div></section><section className="strategy-subpanel"><PanelTitle kicker="PAPER PROMOTION" title="晋级模拟交易" description="Published 版本不能由策略链路下单；同一账户、基金和模式只能有一个 Promoted 主策略。" /><div className="strategy-inline-form"><Field label="账户"><input value={accountId} onChange={(event) => setAccountId(event.target.value)} /></Field><Field label="基金"><input value={fundCode} onChange={(event) => setFundCode(event.target.value)} /></Field><Field label="模式"><AppSelect value={mode} onChange={(event) => setMode(event.target.value as BacktestMode)}><option value="fund_only">基金本体</option><option value="holdings_portfolio">持仓组合</option></AppSelect></Field><button className="primary-button" onClick={promote} disabled={!fundCode}><BadgeCheck size={13} />人工晋级 Promoted</button></div><div className="strategy-binding-list">{bindings.map((row) => <span key={row.id}><BadgeCheck size={12} />{row.account_id} · {row.fund_code} · {modeLabel(row.mode)} · {row.strategy_version_id}</span>)}{!bindings.length && <p className="strategy-inline-empty">暂无模拟盘主策略。</p>}</div></section><section className="strategy-subpanel"><PanelTitle kicker="AUDIT TRAIL" title="策略审计事件" description="创建、保存、发布、绑定、验证与晋级均记录 actor 和时间。" /><div className="strategy-event-timeline">{events.map((row) => <article key={row.id}><span /><div><strong>{eventLabel(row.event_type)}</strong><small>{row.actor_id} · {formatDate(row.created_at)}</small><code>{JSON.stringify(row.payload)}</code></div></article>)}{!events.length && <p className="strategy-inline-empty">系统模板没有个人审计事件。</p>}</div></section></div>
}

function PublishReview({ detail, document, busy, onCancel, onPublish }: { detail: StrategyRecord; document: StrategyDocument; busy: boolean; onCancel: () => void; onPublish: () => void }) {
  return <div className="strategy-modal-backdrop" role="presentation"><section className="strategy-modal" role="dialog" aria-modal="true" aria-labelledby="publish-review-title"><header><div><span className="section-kicker">CONTROLLED PUBLISH</span><h2 id="publish-review-title">确认发布不可变版本</h2><p>发布后该快照不可编辑或删除；已有 Assignment 在下一交易日切换。</p></div><button className="secondary-button" onClick={onCancel}>取消</button></header><div className="publish-review-grid"><article><strong>策略</strong><span>{detail.name}</span><small>draft revision {detail.draft_revision}</small></article><article><strong>复合权重</strong><span>{Object.values(document.fund_composite).map((value) => `${value * 100}%`).join(' / ')}</span><small>缺失不重分配</small></article><article><strong>质量门</strong><span>coverage {(document.quality_gates.minimum_coverage * 100).toFixed(0)}%–{(document.quality_gates.normal_coverage * 100).toFixed(0)}%</span><small>PIT 强制 · 持仓 UZI 强制</small></article><article><strong>信号</strong><span>买入 {document.signal_thresholds.buy} · 观察 {document.signal_thresholds.watch}</span><small>沿用 {document.signal_thresholds.missing_report_carry_days} 个交易日</small></article><article><strong>技术执行</strong><span>MA {document.execution.fast_period}/{document.execution.slow_period}</span><small>单票上限 {document.execution.max_single_stock_pct}%</small></article><article><strong>基金建仓</strong><span>上限 {Number(document.asset_execution.otc_fund.max_position_pct ?? 100)}% · 单批 {Number(document.asset_execution.otc_fund.entry_tranche_pct ?? 100)}%</span><small>最多 {Number(document.asset_execution.otc_fund.max_entry_tranches ?? 1)} 次 · 间隔 {Number(document.asset_execution.otc_fund.minimum_entry_interval_days ?? 0)} 个估值日</small></article><article><strong>数据 Pin</strong><span>UZI {document.pins.uzi_methodology_version}</span><small>{document.pins.model_id} · {document.pins.factor_extractor_version}</small></article></div><details><summary>查看完整发布合同</summary><pre>{JSON.stringify(document, null, 2)}</pre></details><footer><button className="primary-button" onClick={onPublish} disabled={busy}>{busy ? <LoaderCircle className="is-spinning" size={14} /> : <BadgeCheck size={14} />}确认发布</button></footer></section></div>
}

function AssetFeeEditor({ document, disabled, mutate }: { document: StrategyDocument; disabled: boolean; mutate: (fn: (next: StrategyDocument) => void) => void }) {
  const fields: Array<{ group: keyof StrategyDocument['asset_execution']; key: string; label: string }> = [
    { group: 'stock', key: 'commission_rate', label: '股票佣金率' }, { group: 'stock', key: 'minimum_commission', label: '股票最低佣金' }, { group: 'stock', key: 'stamp_duty_rate', label: '股票印花税率' },
    { group: 'exchange_fund', key: 'commission_rate', label: '场内基金佣金率' }, { group: 'exchange_fund', key: 'minimum_commission', label: '场内最低佣金' },
    { group: 'otc_fund', key: 'subscription_fee_rate_fallback', label: '场外申购费兜底' }, { group: 'otc_fund', key: 'redemption_fee_rate_fallback', label: '场外赎回费兜底' },
  ]
  return <div className="strategy-form-grid is-four">{fields.map((field) => <NumberField key={`${field.group}-${field.key}`} label={field.label} value={Number(document.asset_execution[field.group][field.key] || 0)} min={0} max={100} step="any" disabled={disabled} onChange={(value) => mutate((next) => { next.asset_execution[field.group][field.key] = value })} />)}</div>
}

function AdvancedDocumentEditor({ document, onReplace }: { document: StrategyDocument; onReplace: (value: StrategyDocument) => void }) {
  const [raw, setRaw] = useState(() => JSON.stringify(document, null, 2))
  const [error, setError] = useState('')
  return <details className="strategy-advanced-editor" onToggle={(event) => { if ((event.currentTarget as HTMLDetailsElement).open) setRaw(JSON.stringify(document, null, 2)) }}><summary>高级：编辑完整 JSON 合同</summary><textarea aria-label="完整策略 JSON" value={raw} onChange={(event) => setRaw(event.target.value)} /><div>{error && <span>{error}</span>}<button className="secondary-button" onClick={() => { try { const parsed = JSON.parse(raw) as StrategyDocument; setError(''); onReplace(parsed) } catch (value) { setError(value instanceof Error ? value.message : 'JSON 无效') } }}><Check size={13} />应用 JSON</button></div></details>
}

function SignalTable({ signals }: { signals: TradeSignal[] }) {
  return <div className="strategy-signal-table"><table><thead><tr><th>层级/标的</th><th>状态</th><th>分数区间</th><th>coverage</th><th>confidence</th><th>来源</th></tr></thead><tbody>{signals.map((row) => <tr key={row.id || `${row.kind}-${row.subject_code}`}><td><strong>{row.kind === 'fund' ? '基金' : '个股'} · {row.subject_code}</strong><small>{row.as_of_date}</small></td><td><span className={`signal-state is-${row.state.toLowerCase()}`}>{row.state}</span></td><td>{formatScore(row.score_lower_bound)}–{formatScore(row.score_upper_bound)}</td><td>{formatPercent(row.coverage)}</td><td>{formatPercent(row.confidence)}</td><td><code>{row.source_report_set_id}</code></td></tr>)}{!signals.length && <tr><td colSpan={6}>选择真实日报后预演；这里不会生成演示信号。</td></tr>}</tbody></table></div>
}

function RuleSection({ index, title, description, children }: { index: string; title: string; description: string; children: React.ReactNode }) { return <section className="strategy-rule-section"><header><span>{index}</span><div><h3>{title}</h3><p>{description}</p></div></header>{children}</section> }
function PanelTitle({ kicker, title, description }: { kicker: string; title: string; description: string }) { return <header className="strategy-panel-title"><div><span className="section-kicker">{kicker}</span><h3>{title}</h3><p>{description}</p></div></header> }
function Field({ label, wide, children }: { label: string; wide?: boolean; children: React.ReactNode }) { return <label className={`strategy-field ${wide ? 'is-wide' : ''}`}><span>{label}</span>{children}</label> }
function NumberField({ label, value, min, max, step = 1, disabled = false, onChange }: { label: string; value: number; min: number; max: number; step?: number | 'any'; disabled?: boolean; onChange: (value: number) => void }) { return <Field label={label}><input type="number" value={value} min={min} max={max} step={step} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))} /></Field> }
function PercentField({ label, value, disabled, onChange }: { label: string; value: number; disabled: boolean; onChange: (value: number) => void }) { return <Field label={label}><div className="strategy-percent-input"><input type="number" min="0" max="100" step="1" value={Number((value * 100).toFixed(4))} disabled={disabled} onChange={(event) => onChange(Number(event.target.value) / 100)} /><span>%</span></div></Field> }
function CheckField({ label, checked, disabled = false, onChange = () => undefined }: { label: string; checked: boolean; disabled?: boolean; onChange?: (value: boolean) => void }) { return <label className="strategy-check-field"><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span><i><Check size={10} /></i>{label}</span></label> }
function FactorGroup({ title, weights, disabled, onChange }: { title: string; weights: Record<string, number>; disabled: boolean; onChange: (key: string, value: number) => void }) { const total = Object.values(weights).reduce((sum, value) => sum + value, 0); return <section className="strategy-factor-group"><header><strong>{title}</strong><span className={Math.abs(total - 1) < .0001 ? 'is-valid' : 'is-invalid'}>{(total * 100).toFixed(0)}%</span></header>{Object.entries(weights).map(([key, value]) => <label key={key}><code>{factorLabel(key)}</code><div className="strategy-percent-input"><input type="number" min="0" max="100" value={Number((value * 100).toFixed(4))} disabled={disabled} onChange={(event) => onChange(key, Number(event.target.value) / 100)} /><span>%</span></div></label>)}</section> }
function EmptyPanel({ icon: Icon, title, text }: { icon: LucideIcon; title: string; text: string }) { return <div className="strategy-empty-panel"><Icon size={24} /><strong>{title}</strong><p>{text}</p></div> }

function readUnsavedForm(strategyId: string, revision: number): { name: string; document: StrategyDocument } | null { try { const value = JSON.parse(localStorage.getItem(UNSAVED_FORM_KEY) || '{}') as { strategy_id?: string; server_revision?: number; unsaved_form?: { name: string; document: StrategyDocument } }; return value.strategy_id === strategyId && value.server_revision === revision && value.unsaved_form ? value.unsaved_form : null } catch { return null } }
function readWorkspaceContext(): StrategyWorkspaceContext {
  const query = new URLSearchParams(window.location.search)
  const tabValue = query.get('tab') || ''
  const modeValue = query.get('mode') || ''
  const validTabs: StrategyTab[] = ['rules', 'signals', 'versions', 'assignments', 'backtests', 'governance']
  return {
    strategyId: query.get('strategy_id') || undefined,
    tab: validTabs.includes(tabValue as StrategyTab) ? tabValue as StrategyTab : undefined,
    fundCode: query.get('fund_code') || '',
    fundName: query.get('fund_name') || undefined,
    sourceRunId: query.get('source_run_id') || undefined,
    sourceArtifactId: query.get('source_artifact_id') || undefined,
    reportSetId: query.get('report_set_id') || undefined,
    strategyVersionId: query.get('strategy_version_id') || undefined,
    assignmentId: query.get('assignment_id') || undefined,
    mode: modeValue === 'fund_only' || modeValue === 'holdings_portfolio' ? modeValue : undefined,
    holdingSymbol: query.get('holding_symbol') || undefined,
  }
}
function validateDocument(document: StrategyDocument, name: string): string { if (!name.trim()) return '策略名称不能为空。'; if (Math.abs(Object.values(document.fund_composite).reduce((sum, value) => sum + value, 0) - 1) > .0001) return '复合模型三组权重之和必须等于 100%。'; for (const [group, weights] of Object.entries(document.factor_weights)) if (Math.abs(Object.values(weights).reduce((sum, value) => sum + value, 0) - 1) > .0001) return `${group} 因子权重之和必须等于 100%。`; if (document.signal_thresholds.watch >= document.signal_thresholds.buy) return '观察阈值必须低于买入阈值。'; if (document.quality_gates.minimum_coverage >= document.quality_gates.normal_coverage) return '最低 coverage 必须低于正常 coverage。'; if (document.execution.fast_period >= document.execution.slow_period) return '快均线周期必须小于慢均线周期。'; const otc = document.asset_execution.otc_fund; if (Number(otc.max_position_pct ?? 100) <= 0 || Number(otc.max_position_pct ?? 100) > 100) return '基金最大仓位必须在 0–100% 之间。'; if (Number(otc.entry_tranche_pct ?? 100) <= 0 || Number(otc.entry_tranche_pct ?? 100) > 100) return '单次建仓比例必须在 0–100% 之间。'; if (!Number.isInteger(Number(otc.max_entry_tranches ?? 1)) || Number(otc.max_entry_tranches ?? 1) < 1) return '最大建仓次数必须是正整数。'; if (!Number.isInteger(Number(otc.minimum_entry_interval_days ?? 0)) || Number(otc.minimum_entry_interval_days ?? 0) < 0) return '建仓间隔估值日必须是非负整数。'; return '' }
function filterLabel(value: StrategyFilter): string { return ({ all: '全部', template: '模板', draft: '草稿', published: '已发布', archived: '已归档' })[value] }
function lifecycleLabel(value: string): string { return ({ draft: '草稿', published: '已发布', archived: '已归档' } as Record<string, string>)[value] || value }
function governanceLabel(value: string): string { return ({ published: 'Published', verified: 'Verified', promoted: 'Promoted' } as Record<string, string>)[value] || value }
function modeLabel(value: BacktestMode): string { return value === 'fund_only' ? '基金本体' : '持仓组合' }
function factorLabel(value: string): string { return ({ year_1_return: '近一年收益', since_inception_annualized: '成立以来年化', benchmark_excess_year_1: '近一年超额', max_drawdown: '最大回撤', volatility: '波动率', fee: '费率', return_consistency: '收益一致性', uzi_overall_score: 'UZI 总评分', count_coverage: '数量覆盖', weight_coverage: '权重覆盖', disclosure_freshness: '披露新鲜度', concentration_risk: '集中风险', lookthrough_completeness: '穿透完整度', unsupported_weight: '不支持权重' } as Record<string, string>)[value] || value }
function eventLabel(value: string): string { return ({ created: '创建策略', draft_saved: '保存草稿', published: '发布版本', assignment_created: '创建绑定', assignment_updated: '更新绑定', verification_created: '创建验证', verified: '人工确认 Verified', promoted: '晋级 Promoted', archived: '归档策略', restored: '恢复策略' } as Record<string, string>)[value] || value }
function formatDate(value: string): string { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString('zh-CN', { hour12: false }) }
function formatPercent(value: unknown): string { const number = Number(value); return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : '—' }
function formatScore(value: unknown): string { const number = Number(value); return Number.isFinite(number) ? number.toFixed(1) : '—' }
function metricSummary(metrics: Record<string, number>): string { if (!Object.keys(metrics || {}).length) return '等待结果'; return `收益 ${Number(metrics.total_return_pct || 0).toFixed(2)}% · 回撤 ${Number(metrics.max_drawdown_pct || 0).toFixed(2)}% · Sharpe ${Number(metrics.sharpe || 0).toFixed(2)}` }
function today(offsetDays = 0): string { const value = new Date(); value.setDate(value.getDate() + offsetDays); return value.toISOString().slice(0, 10) }
function errorMessage(value: unknown): string { if (value instanceof CognitradeApiError) return `${value.message}${value.requestId ? `（request ${value.requestId}）` : ''}`; return value instanceof Error ? value.message : String(value || '未知错误') }
