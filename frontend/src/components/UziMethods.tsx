import {
  AlertCircle,
  ArrowRightLeft,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Database,
  Gauge,
  Play,
  Plus,
  RefreshCw,
  Scale,
  Trash2,
  TrendingUp,
} from 'lucide-react'
import { type FormEvent, useMemo, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'

type UziMethodsProps = {
  onOpenRun: (runId: string) => void
}

type HoldingDraft = {
  ticker: string
  name: string
  weight: string
  returnPct: string
  target: string
  value: string
  price: string
  market: string
}

const emptyHolding = (): HoldingDraft => ({
  ticker: '', name: '', weight: '', returnPct: '', target: '', value: '', price: '', market: 'A',
})

const tier1Methods = [
  { id: 'ai_readiness', label: 'AI 就绪度', description: '核验产业链、真实需求与不可替代性三道证据门。', icon: Bot },
  { id: 'earnings_preview', label: '财报前瞻', description: '分离已披露基线、卖方一致预期与明确标记的情景假设。', icon: TrendingUp },
  { id: 'model_update', label: '模型更新', description: '只接受显式新假设，不从缺失字段制造演示增量。', icon: BrainCircuit },
] as const

export function UziMethods({ onOpenRun }: UziMethodsProps) {
  const { createRun, selectedRun, selectedRunId, selectRun, submitting } = useCognitrade()
  const [mode, setMode] = useState<'stock' | 'portfolio'>('stock')
  const [symbol, setSymbol] = useState('')
  const [method, setMethod] = useState<(typeof tier1Methods)[number]['id']>('ai_readiness')
  const [modelField, setModelField] = useState('net_margin')
  const [modelValue, setModelValue] = useState('')
  const [holdings, setHoldings] = useState<HoldingDraft[]>([emptyHolding(), emptyHolding()])
  const [benchmark, setBenchmark] = useState('')
  const [threshold, setThreshold] = useState('5')
  const [commission, setCommission] = useState('')
  const [buyStamp, setBuyStamp] = useState('')
  const [sellStamp, setSellStamp] = useState('')

  const artifacts = useMemo(() => {
    const rows = Array.isArray(selectedRun?.replay?.artifacts) ? selectedRun.replay.artifacts : []
    return [...rows].reverse().map(asRecord)
  }, [selectedRun])
  const result = useMemo(
    () => artifacts.find((row) => ['ai_readiness', 'earnings_preview', 'model_update', 'returns_attribution', 'holding_rebalance'].includes(String(row.artifact_type))),
    [artifacts],
  )

  const submitStock = async (event: FormEvent) => {
    event.preventDefault()
    const normalized = symbol.trim()
    if (!normalized) return
    const constraints: Record<string, unknown> = {
      symbol: normalized,
      requested_methods: [method],
    }
    if (method === 'model_update') {
      const value = optionalNumber(modelValue)
      if (value == null) return
      constraints.model_updates = { [modelField]: value }
    }
    const response = await createRun(
      `${normalized} ${tier1Methods.find((item) => item.id === method)?.label || method}`,
      'stock_check',
      constraints,
    )
    if (response.run_id) selectRun(response.run_id)
  }

  const submitPortfolio = async (event: FormEvent) => {
    event.preventDefault()
    const parsed = holdings
      .filter((row) => row.ticker.trim())
      .map((row) => ({
        ticker: row.ticker.trim(),
        ...(row.name.trim() ? { name: row.name.trim() } : {}),
        weight: optionalNumber(row.weight),
        ...(optionalNumber(row.returnPct) == null ? {} : { return_pct: optionalNumber(row.returnPct) }),
        ...(optionalNumber(row.value) == null ? {} : { value: optionalNumber(row.value) }),
        ...(optionalNumber(row.price) == null ? {} : { price: optionalNumber(row.price) }),
        market: row.market,
      }))
    if (!parsed.length || parsed.some((row) => row.weight == null)) return
    const constraints: Record<string, unknown> = { portfolio_holdings: parsed }
    const benchmarkValue = optionalNumber(benchmark)
    if (benchmarkValue != null) constraints.benchmark_return = benchmarkValue
    const thresholdValue = optionalNumber(threshold)
    if (thresholdValue != null) constraints.drift_threshold = thresholdValue
    const targets = Object.fromEntries(
      holdings
        .filter((row) => row.ticker.trim() && optionalNumber(row.target) != null)
        .map((row) => [row.ticker.trim(), optionalNumber(row.target)]),
    )
    if (Object.keys(targets).length === parsed.length) constraints.rebalance_targets = targets
    const feeSpec = {
      ...(optionalNumber(commission) == null ? {} : { commission_rate: optionalNumber(commission) }),
      ...(optionalNumber(buyStamp) == null ? {} : { buy_stamp_rate: optionalNumber(buyStamp) }),
      ...(optionalNumber(sellStamp) == null ? {} : { sell_stamp_rate: optionalNumber(sellStamp) }),
    }
    if (Object.keys(feeSpec).length) constraints.fee_model = { A: feeSpec }
    const response = await createRun('对显式持仓做收益归因与策略再平衡', 'portfolio_research', constraints)
    if (response.run_id) selectRun(response.run_id)
  }

  return (
    <main className="finance-page uzi-methods-page">
      <section className="finance-hero uzi-methods-hero">
        <div>
          <span className="section-kicker">UZI 3.9.2 · TIER‑1 · PACK NATIVE</span>
          <h1>专项研究与组合方法</h1>
          <p>方法运行在 Agent OS / DomainPack 内；缺失观测只形成数据缺口，模型情景和策略目标单独标记为假设。</p>
        </div>
        {selectedRunId && <button className="secondary-button" onClick={() => onOpenRun(selectedRunId)}><Database size={15} />打开当前证据链</button>}
      </section>

      <div className="uzi-mode-tabs" role="tablist" aria-label="UZI 专项研究类型">
        <button className={mode === 'stock' ? 'is-active' : ''} onClick={() => setMode('stock')}><BrainCircuit size={15} />个股 Tier‑1</button>
        <button className={mode === 'portfolio' ? 'is-active' : ''} onClick={() => setMode('portfolio')}><Scale size={15} />组合归因与再平衡</button>
      </div>

      <div className="uzi-methods-layout">
        {mode === 'stock' ? (
          <form className="uzi-config-card" onSubmit={submitStock}>
            <header><div><span className="section-kicker">STOCK METHOD</span><h2>创建个股专项任务</h2></div><span className="quality-pill quality-pill--available">真实快照</span></header>
            <label><span>股票代码</span><input value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如 300750" /></label>
            <div className="uzi-method-picker">
              {tier1Methods.map((item) => {
                const Icon = item.icon
                return <button type="button" key={item.id} className={method === item.id ? 'is-active' : ''} onClick={() => setMethod(item.id)}><Icon size={17} /><span><strong>{item.label}</strong><small>{item.description}</small></span></button>
              })}
            </div>
            {method === 'model_update' && (
              <div className="uzi-inline-form">
                <label><span>更新字段</span><select value={modelField} onChange={(event) => setModelField(event.target.value)}><option value="rev_growth">营收增速 %</option><option value="gross_margin">毛利率 %</option><option value="net_margin">净利率 %</option><option value="capex_pct">资本开支/营收 %</option><option value="stage1_growth">DCF 第一阶段增速 %</option><option value="terminal_g">DCF 永续增长率 %</option><option value="beta">Beta</option><option value="target_pe">目标市盈率</option><option value="target_price">目标价</option></select></label>
                <label><span>显式新值</span><input type="number" step="any" value={modelValue} onChange={(event) => setModelValue(event.target.value)} placeholder="必须填写" /></label>
              </div>
            )}
            <button className="primary-button uzi-run-button" disabled={submitting || !symbol.trim() || (method === 'model_update' && optionalNumber(modelValue) == null)}>{submitting ? <RefreshCw size={15} className="is-spinning" /> : <Play size={15} />}{submitting ? '正在运行' : '运行专项方法'}</button>
          </form>
        ) : (
          <form className="uzi-config-card uzi-portfolio-form" onSubmit={submitPortfolio}>
            <header><div><span className="section-kicker">PORTFOLIO INPUT</span><h2>录入显式持仓</h2></div><button type="button" className="secondary-button" onClick={() => setHoldings((rows) => [...rows, emptyHolding()])}><Plus size={14} />添加持仓</button></header>
            <div className="uzi-holding-list">
              {holdings.map((holding, index) => (
                <div className="uzi-holding-row" key={index}>
                  <input aria-label={`持仓 ${index + 1} 代码`} value={holding.ticker} onChange={(event) => updateHolding(setHoldings, index, 'ticker', event.target.value)} placeholder="代码" />
                  <input aria-label={`持仓 ${index + 1} 名称`} value={holding.name} onChange={(event) => updateHolding(setHoldings, index, 'name', event.target.value)} placeholder="名称（可选）" />
                  <input aria-label={`持仓 ${index + 1} 权重`} type="number" step="any" value={holding.weight} onChange={(event) => updateHolding(setHoldings, index, 'weight', event.target.value)} placeholder="当前权重" />
                  <input aria-label={`持仓 ${index + 1} 收益`} type="number" step="any" value={holding.returnPct} onChange={(event) => updateHolding(setHoldings, index, 'returnPct', event.target.value)} placeholder="区间收益 %" />
                  <input aria-label={`持仓 ${index + 1} 目标`} type="number" step="any" value={holding.target} onChange={(event) => updateHolding(setHoldings, index, 'target', event.target.value)} placeholder="目标权重" />
                  <input aria-label={`持仓 ${index + 1} 市值`} type="number" step="any" value={holding.value} onChange={(event) => updateHolding(setHoldings, index, 'value', event.target.value)} placeholder="市值" />
                  <input aria-label={`持仓 ${index + 1} 价格`} type="number" step="any" value={holding.price} onChange={(event) => updateHolding(setHoldings, index, 'price', event.target.value)} placeholder="现价" />
                  <button type="button" aria-label={`删除持仓 ${index + 1}`} disabled={holdings.length <= 1} onClick={() => setHoldings((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}><Trash2 size={14} /></button>
                </div>
              ))}
            </div>
            <div className="uzi-portfolio-settings">
              <label><span>基准收益 %（可选）</span><input type="number" step="any" value={benchmark} onChange={(event) => setBenchmark(event.target.value)} /></label>
              <label><span>漂移阈值 pp</span><input type="number" min="0" step="any" value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
              <label><span>A 股佣金率（小数，可选）</span><input type="number" min="0" max="1" step="any" value={commission} onChange={(event) => setCommission(event.target.value)} placeholder="未填则不估成本" /></label>
              <label><span>买入印花税率（小数，可选）</span><input type="number" min="0" max="1" step="any" value={buyStamp} onChange={(event) => setBuyStamp(event.target.value)} /></label>
              <label><span>卖出印花税率（小数，可选）</span><input type="number" min="0" max="1" step="any" value={sellStamp} onChange={(event) => setSellStamp(event.target.value)} /></label>
            </div>
            <p className="uzi-policy-note"><AlertCircle size={14} />目标权重未完整填写时使用“等权策略假设”；费用未填写时不会套用默认费率，也不会估算成本。</p>
            <button className="primary-button uzi-run-button" disabled={submitting || !holdings.some((row) => row.ticker.trim() && optionalNumber(row.weight) != null)}>{submitting ? <RefreshCw size={15} className="is-spinning" /> : <ArrowRightLeft size={15} />}{submitting ? '正在运行' : '运行归因与再平衡'}</button>
          </form>
        )}

        <section className="uzi-result-card">
          <header><div><span className="section-kicker">ACTIVE ARTIFACT</span><h2>专项结果</h2></div>{result && <span className={`quality-pill quality-pill--${String(asRecord(result.content).quality_status || 'data_gap')}`}>{qualityLabel(String(asRecord(result.content).quality_status || 'data_gap'))}</span>}</header>
          {result ? <Tier1Result artifact={result} /> : <div className="uzi-empty"><Gauge size={24} /><strong>暂无 Tier‑1 Artifact</strong><p>运行上方方法后，这里会展示结构化证据门、覆盖率、贡献或漂移，不直接展示原始 JSON。</p></div>}
        </section>
      </div>
    </main>
  )
}

function Tier1Result({ artifact }: { artifact: Record<string, unknown> }) {
  const type = String(artifact.artifact_type || '')
  const content = asRecord(artifact.content)
  const gaps = Array.isArray(content.data_gap) ? content.data_gap.map(String) : []
  if (type === 'ai_readiness') {
    const company = asRecord((Array.isArray(content.companies) ? content.companies : [])[0])
    const gates = Array.isArray(company.gates) ? company.gates.map(asRecord) : []
    return <div className="uzi-result-body"><ResultHeadline label="AI 就绪度" value={String(company.rating || '证据不足')} detail={String(company.verdict || '')} /><div className="uzi-gate-list">{gates.map((gate) => <article key={String(gate.gate_id)} className={`is-${String(gate.status)}`}><span>{String(gate.status) === 'pass' ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}</span><div><strong>{String(gate.label || gate.gate_id)}</strong><small>{Array.isArray(gate.evidence) && gate.evidence.length ? gate.evidence.map(String).join('、') : String(gate.reason || '暂无证据')}</small></div></article>)}</div><GapList gaps={gaps} /></div>
  }
  if (type === 'earnings_preview') {
    const preview = asRecord((Array.isArray(content.previews) ? content.previews : [])[0])
    const consensus = Array.isArray(preview.consensus_table) ? preview.consensus_table.map(asRecord) : []
    const scenarios = Array.isArray(preview.scenarios) ? preview.scenarios.map(asRecord) : []
    return <div className="uzi-result-body"><ResultHeadline label="财报前瞻" value={String(preview.name || preview.symbol || '—')} detail="情景值均为 assumption_only，不冒充一致预期" /><div className="uzi-mini-table"><div><strong>指标</strong><strong>一致预期</strong><strong>状态</strong></div>{consensus.map((row) => <div key={String(row.metric)}><span>{metricLabel(String(row.metric))}</span><span>{formatNumber(row.value)}</span><span>{qualityLabel(String(row.status || 'data_gap'))}</span></div>)}</div><div className="uzi-scenario-row">{scenarios.map((row) => <article key={String(row.scenario)}><span>{String(row.scenario).toUpperCase()}</span><strong>显式情景</strong><small>{String(row.assumption || row.reason || '')}</small></article>)}</div><GapList gaps={gaps} /></div>
  }
  if (type === 'model_update') {
    const updates = Array.isArray(content.updates) ? content.updates.map(asRecord) : []
    return <div className="uzi-result-body"><ResultHeadline label="模型更新" value={`${updates.length} 项显式假设`} detail="只给一阶方向影响；完整重估需重新运行估值模型" /><div className="uzi-mini-table"><div><strong>字段</strong><strong>原值 → 新值</strong><strong>方向</strong></div>{updates.map((row) => <div key={`${String(row.symbol)}-${String(row.key)}`}><span>{String(row.label || row.key)}</span><span>{formatNumber(row.before)} → {formatNumber(row.after)}</span><span>{String(row.first_order_impact || '—')}</span></div>)}</div><GapList gaps={gaps} /></div>
  }
  if (type === 'returns_attribution') {
    const holdings = Array.isArray(content.holdings) ? content.holdings.map(asRecord) : []
    return <div className="uzi-result-body"><div className="uzi-result-kpis"><article><span>收益覆盖权重</span><strong>{percentFromRatio(content.observed_weight_coverage)}</strong></article><article><span>完整组合收益</span><strong>{percent(content.total_return_pct)}</strong></article><article><span>已观测贡献</span><strong>{percent(content.observed_contribution_pct)}</strong></article></div><div className="uzi-mini-table"><div><strong>持仓</strong><strong>区间收益</strong><strong>贡献</strong></div>{holdings.map((row) => <div key={String(row.ticker)}><span>{String(row.name || row.ticker)}</span><span>{percent(row.return_pct)}</span><span>{percent(row.contribution_pct)}</span></div>)}</div><GapList gaps={gaps} /></div>
  }
  const trades = Array.isArray(content.trades) ? content.trades.map(asRecord) : []
  return <div className="uzi-result-body"><ResultHeadline label="持仓再平衡" value={`${trades.length} 笔策略建议`} detail={String(content.target_mode || '')} /><div className="uzi-mini-table"><div><strong>持仓</strong><strong>动作 / 漂移</strong><strong>金额 / 成本</strong></div>{trades.map((row) => <div key={String(row.ticker)}><span>{String(row.ticker)}</span><span>{String(row.action)} · {percent(row.drift_pp)}</span><span>{money(row.trade_amount)} / {money(row.estimated_cost)}</span></div>)}</div><GapList gaps={gaps} /></div>
}

function ResultHeadline({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="uzi-result-headline"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>
}

function GapList({ gaps }: { gaps: string[] }) {
  return gaps.length ? <div className="uzi-gap-list"><AlertCircle size={14} /><span>{gaps.length} 项数据缺口</span><p>{gaps.slice(0, 5).map(gapLabel).join('、')}</p></div> : <div className="uzi-no-gap"><CheckCircle2 size={14} />当前方法所需观测完整</div>
}

function updateHolding(setter: React.Dispatch<React.SetStateAction<HoldingDraft[]>>, index: number, field: keyof HoldingDraft, value: string) {
  setter((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row))
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function optionalNumber(value: unknown): number | null {
  if (value == null || String(value).trim() === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function formatNumber(value: unknown): string {
  const number = optionalNumber(value)
  return number == null ? '—' : number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

function percent(value: unknown): string {
  const number = optionalNumber(value)
  return number == null ? '—' : `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function percentFromRatio(value: unknown): string {
  const number = optionalNumber(value)
  return number == null ? '—' : `${(number * 100).toFixed(1)}%`
}

function money(value: unknown): string {
  const number = optionalNumber(value)
  return number == null ? '—' : `¥${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function qualityLabel(value: string): string {
  return value === 'available' ? '证据可用' : value === 'degraded' ? '部分可用' : value === 'assumption_only' ? '策略假设' : value === 'pass' ? '通过' : '数据缺口'
}

function metricLabel(value: string): string {
  return ({ revenue: '营收', eps: '每股收益', net_profit: '净利润', target_price: '目标价' } as Record<string, string>)[value] || value
}

function gapLabel(value: string): string {
  const [head] = value.split(':')
  return ({ benchmark_return_missing: '缺少基准收益', portfolio_value_missing: '缺少完整组合市值', earnings_event_or_catalyst_missing: '缺少财报事件或催化剂', options_implied_volatility_not_collected: '未采集期权隐含波动', financial_baseline_missing: '缺少财务基线' } as Record<string, string>)[head] || value.replaceAll('_', ' ')
}
