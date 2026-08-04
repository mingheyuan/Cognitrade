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
import { type FormEvent, type ReactNode, useMemo, useState } from 'react'
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
  assetClass: string
  assetTarget: string
}

const emptyHolding = (): HoldingDraft => ({
  ticker: '', name: '', weight: '', returnPct: '', target: '', value: '', price: '', market: 'A', assetClass: '', assetTarget: '',
})

const stockMethods = [
  { id: 'ai_readiness', label: 'AI 就绪度', description: '核验产业链、真实需求与不可替代性三道证据门。', icon: Bot },
  { id: 'earnings_preview', label: '财报前瞻', description: '分离已披露基线、卖方一致预期与明确标记的情景假设。', icon: TrendingUp },
  { id: 'model_update', label: '模型更新', description: '只接受显式新假设，不从缺失字段制造演示增量。', icon: BrainCircuit },
  { id: 'three_statement', label: '三表联动', description: '以披露财务为锚，只有显式预测参数齐全时才计算。', icon: BrainCircuit },
  { id: 'quick_lbo', label: '快速 LBO', description: '要求真实 EBITDA 与完整交易假设，不用营收替代。', icon: Scale },
  { id: 'accretion_dilution', label: '并购增厚摊薄', description: '基于显式交易参数计算备考 EPS。', icon: ArrowRightLeft },
  { id: 'segmental', label: '分部模型', description: '只展示已披露分部收入、占比和利润数据。', icon: Gauge },
  { id: 'initiation_report', label: '首次覆盖', description: '组织公司、业务、财务、催化剂与估值证据包。', icon: Database },
  { id: 'earnings_review', label: '财报解读', description: '逐项比较真实披露值与已获取的一致预期。', icon: TrendingUp },
  { id: 'catalyst_calendar', label: '催化剂日历', description: '仅将有明确日期的事件纳入可执行日历。', icon: Play },
  { id: 'thesis_tracker', label: '投资逻辑追踪', description: '追踪增长、现金生成与估值三类证据柱。', icon: BrainCircuit },
  { id: 'morning_note', label: '晨报', description: '整合行情、资金、情绪与事件的已观测快照。', icon: TrendingUp },
  { id: 'ic_memo', label: '投委会备忘录', description: '汇总证据与风险，最终决策保留人工复核。', icon: Database },
  { id: 'porter_bcg', label: '竞争格局', description: '五力与 BCG 均不使用中性默认分数。', icon: Scale },
  { id: 'dd_checklist', label: '尽调清单', description: '按财务、商业、治理与市场风险展示覆盖率。', icon: CheckCircle2 },
  { id: 'unit_economics', label: '单位经济', description: '只有客户、流失、毛利与获客成本齐备才计算。', icon: Gauge },
  { id: 'value_creation_plan', label: '价值创造计划', description: '杠杆及 EBITDA 影响必须由用户明确输入。', icon: TrendingUp },
] as const

const methodArtifactTypes = [
  'ai_readiness', 'earnings_preview', 'model_update', 'returns_attribution', 'holding_rebalance', 'portfolio_rebalance',
  'three_statement_model', 'quick_lbo', 'accretion_dilution', 'segmental_model', 'initiation_report', 'earnings_review',
  'catalyst_calendar', 'thesis_tracker', 'morning_note', 'ic_memo', 'porter_bcg', 'dd_checklist', 'unit_economics', 'value_creation_plan',
]

export function UziMethods({ onOpenRun }: UziMethodsProps) {
  const { createRun, selectedRun, selectedRunId, selectRun, submitting } = useCognitrade()
  const [mode, setMode] = useState<'stock' | 'portfolio'>('stock')
  const [symbol, setSymbol] = useState('')
  const [method, setMethod] = useState<(typeof stockMethods)[number]['id']>('ai_readiness')
  const [modelField, setModelField] = useState('net_margin')
  const [modelValue, setModelValue] = useState('')
  const [holdings, setHoldings] = useState<HoldingDraft[]>([emptyHolding(), emptyHolding()])
  const [benchmark, setBenchmark] = useState('')
  const [threshold, setThreshold] = useState('5')
  const [commission, setCommission] = useState('')
  const [buyStamp, setBuyStamp] = useState('')
  const [sellStamp, setSellStamp] = useState('')
  const [assumptionInputs, setAssumptionInputs] = useState<Record<string, string>>({})

  const artifacts = useMemo(() => {
    const rows = Array.isArray(selectedRun?.replay?.artifacts) ? selectedRun.replay.artifacts : []
    return [...rows].reverse().map(asRecord)
  }, [selectedRun])
  const result = useMemo(
    () => artifacts.find((row) => methodArtifactTypes.includes(String(row.artifact_type))),
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
    const methodAssumptions = buildMethodAssumptions(method, assumptionInputs)
    if (methodNeedsAssumptions(method) && !methodAssumptions) return
    if (methodAssumptions) constraints.method_assumptions = { [method]: methodAssumptions }
    const response = await createRun(
      `${normalized} ${stockMethods.find((item) => item.id === method)?.label || method}`,
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
        ...(row.assetClass.trim() ? { asset_class: row.assetClass.trim() } : {}),
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
    const assetTargets = Object.fromEntries(
      holdings
        .filter((row) => row.assetClass.trim() && optionalNumber(row.assetTarget) != null)
        .map((row) => [row.assetClass.trim(), optionalNumber(row.assetTarget)]),
    )
    const observedClasses = [...new Set(holdings.filter((row) => row.ticker.trim()).map((row) => row.assetClass.trim()).filter(Boolean))]
    if (observedClasses.length && Object.keys(assetTargets).length === observedClasses.length) {
      constraints.asset_class_targets = assetTargets
      constraints.requested_methods = ['portfolio_rebalance']
    }
    const response = await createRun('对显式持仓做收益归因与策略再平衡', 'portfolio_research', constraints)
    if (response.run_id) selectRun(response.run_id)
  }

  return (
    <main className="finance-page uzi-methods-page">
      <section className="finance-hero uzi-methods-hero">
        <div>
          <span className="section-kicker">UZI 3.9.2 · COMPLETE METHODS · PACK NATIVE</span>
          <h1>专项研究与组合方法</h1>
          <p>方法运行在 Agent OS / DomainPack 内；缺失观测只形成数据缺口，模型情景和策略目标单独标记为假设。</p>
        </div>
        {selectedRunId && <button className="secondary-button" onClick={() => onOpenRun(selectedRunId)}><Database size={15} />打开当前证据链</button>}
      </section>

      <div className="uzi-mode-tabs" role="tablist" aria-label="UZI 专项研究类型">
        <button className={mode === 'stock' ? 'is-active' : ''} onClick={() => setMode('stock')}><BrainCircuit size={15} />个股方法库</button>
        <button className={mode === 'portfolio' ? 'is-active' : ''} onClick={() => setMode('portfolio')}><Scale size={15} />组合归因与再平衡</button>
      </div>

      <div className="uzi-methods-layout">
        {mode === 'stock' ? (
          <form className="uzi-config-card" onSubmit={submitStock}>
            <header><div><span className="section-kicker">STOCK METHOD</span><h2>创建个股专项任务</h2></div><span className="quality-pill quality-pill--available">真实快照</span></header>
            <label><span>股票代码</span><input value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如 300750" /></label>
            <div className="uzi-method-picker">
              {stockMethods.map((item) => {
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
            <MethodAssumptionFields method={method} values={assumptionInputs} onChange={(key, value) => setAssumptionInputs((current) => ({ ...current, [key]: value }))} />
            <button className="primary-button uzi-run-button" disabled={submitting || !symbol.trim() || (method === 'model_update' && optionalNumber(modelValue) == null) || (methodNeedsAssumptions(method) && !buildMethodAssumptions(method, assumptionInputs))}>{submitting ? <RefreshCw size={15} className="is-spinning" /> : <Play size={15} />}{submitting ? '正在运行' : '运行专项方法'}</button>
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
                  <input aria-label={`持仓 ${index + 1} 资产类别`} value={holding.assetClass} onChange={(event) => updateHolding(setHoldings, index, 'assetClass', event.target.value)} placeholder="资产类别（可选）" />
                  <input aria-label={`持仓 ${index + 1} 类别目标`} type="number" step="any" value={holding.assetTarget} onChange={(event) => updateHolding(setHoldings, index, 'assetTarget', event.target.value)} placeholder="类别目标 %" />
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
            <p className="uzi-policy-note"><AlertCircle size={14} />单只持仓目标未完整填写时仅输出等权策略假设；资产类别及类别目标完整填写后才运行战略配置再平衡；费用未填写时不会估算成本。</p>
            <button className="primary-button uzi-run-button" disabled={submitting || !holdings.some((row) => row.ticker.trim() && optionalNumber(row.weight) != null)}>{submitting ? <RefreshCw size={15} className="is-spinning" /> : <ArrowRightLeft size={15} />}{submitting ? '正在运行' : '运行归因与再平衡'}</button>
          </form>
        )}

        <section className="uzi-result-card">
          <header><div><span className="section-kicker">ACTIVE ARTIFACT</span><h2>专项结果</h2></div>{result && <span className={`quality-pill quality-pill--${String(asRecord(result.content).quality_status || 'data_gap')}`}>{qualityLabel(String(asRecord(result.content).quality_status || 'data_gap'))}</span>}</header>
          {result ? <Tier1Result artifact={result} /> : <div className="uzi-empty"><Gauge size={24} /><strong>暂无专项 Artifact</strong><p>运行上方方法后，这里会展示结构化证据、覆盖率、贡献或漂移，不直接展示原始 JSON。</p></div>}
        </section>
      </div>
    </main>
  )
}

function MethodAssumptionFields({ method, values, onChange }: { method: string; values: Record<string, string>; onChange: (key: string, value: string) => void }) {
  const field = (key: string, label: string, placeholder: string) => (
    <label key={key}><span>{label}</span><input value={values[key] || ''} onChange={(event) => onChange(key, event.target.value)} placeholder={placeholder} /></label>
  )
  let fields: ReactNode[] = []
  if (method === 'three_statement') fields = [field('growthPath', '营收增速路径', '例如 0.10,0.08,0.06'), field('capexRate', '资本开支/营收', '例如 0.05'), field('workingCapitalRate', '增量营收营运资金率', '例如 0.10')]
  if (method === 'quick_lbo') fields = [field('entryMultiple', '入场 EBITDA 倍数', '例如 8'), field('debtMultiple', '债务/EBITDA', '例如 4'), field('exitMultiple', '退出 EBITDA 倍数', '例如 8'), field('ebitdaGrowth', '年 EBITDA 增速', '例如 0.08'), field('debtPaydown', '年偿债比例', '例如 0.10'), field('holdYears', '持有年数', '例如 5')]
  if (method === 'accretion_dilution') fields = [field('targetNetIncome', '标的净利润', '与快照同口径'), field('newShares', '新增股份', '与总股本同口径'), field('afterTaxSynergies', '税后协同', '显式输入，可为 0'), field('afterTaxFinancingCost', '税后融资成本', '显式输入，可为 0')]
  if (method === 'value_creation_plan') fields = [field('leverName', '价值创造杠杆', '例如 提升产品组合'), field('ebitdaImpact', 'EBITDA 影响', '与披露 EBITDA 同口径')]
  if (!fields.length) return null
  return <div className="uzi-assumption-panel"><div><strong>显式方法假设</strong><small>以下参数全部由你提供；系统不会填默认值。</small></div><div className="uzi-inline-form">{fields}</div></div>
}

function methodNeedsAssumptions(method: string): boolean {
  return ['three_statement', 'quick_lbo', 'accretion_dilution', 'value_creation_plan'].includes(method)
}

function buildMethodAssumptions(method: string, values: Record<string, string>): Record<string, unknown> | null {
  const number = (key: string) => optionalNumber(values[key])
  if (method === 'three_statement') {
    const growthPath = String(values.growthPath || '').split(',').map((item) => optionalNumber(item.trim()))
    if (!growthPath.length || growthPath.some((item) => item == null) || number('capexRate') == null || number('workingCapitalRate') == null) return null
    return { revenue_growth_path: growthPath, capex_to_revenue: number('capexRate'), working_capital_to_incremental_revenue: number('workingCapitalRate') }
  }
  if (method === 'quick_lbo') {
    const keys = ['entryMultiple', 'debtMultiple', 'exitMultiple', 'ebitdaGrowth', 'debtPaydown', 'holdYears']
    if (keys.some((key) => number(key) == null)) return null
    return { entry_multiple: number('entryMultiple'), debt_multiple: number('debtMultiple'), exit_multiple: number('exitMultiple'), annual_ebitda_growth: number('ebitdaGrowth'), annual_debt_paydown_pct: number('debtPaydown'), hold_years: number('holdYears') }
  }
  if (method === 'accretion_dilution') {
    const keys = ['targetNetIncome', 'newShares', 'afterTaxSynergies', 'afterTaxFinancingCost']
    if (keys.some((key) => number(key) == null)) return null
    return { target_net_income: number('targetNetIncome'), new_shares: number('newShares'), after_tax_synergies: number('afterTaxSynergies'), after_tax_financing_cost: number('afterTaxFinancingCost') }
  }
  if (method === 'value_creation_plan') {
    const name = String(values.leverName || '').trim()
    const impact = number('ebitdaImpact')
    if (!name || impact == null) return null
    return { levers: [{ name, ebitda_impact: impact }] }
  }
  return null
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
  if (type === 'portfolio_rebalance') {
    const rows = Array.isArray(content.asset_classes) ? content.asset_classes.map(asRecord) : []
    return <div className="uzi-result-body"><ResultHeadline label="资产配置再平衡" value={`${rows.filter((row) => row.breached).length} 类触发漂移`} detail="目标来自显式战略资产配置" /><div className="uzi-mini-table"><div><strong>资产类别</strong><strong>当前 → 目标</strong><strong>动作 / 金额</strong></div>{rows.map((row) => <div key={String(row.asset_class)}><span>{String(row.asset_class)}</span><span>{percentFromRatio(row.current_weight)} → {percentFromRatio(row.target_weight)}</span><span>{String(row.action)} / {money(row.suggested_value_change)}</span></div>)}</div><GapList gaps={gaps} /></div>
  }
  if (type !== 'holding_rebalance') {
    const methodResult = asRecord((Array.isArray(content.results) ? content.results : [])[0])
    const rows = flattenDisplay({ observed: methodResult.observed, derived: methodResult.derived, methodology: methodResult.methodology }).slice(0, 18)
    const methodLabel = stockMethods.find((item) => item.id === content.method)?.label || String(content.method || type)
    return <div className="uzi-result-body"><ResultHeadline label={methodLabel} value={String(methodResult.symbol || '证据结果')} detail="只展示结构化字段；缺失数据不会由默认值补齐" /><div className="uzi-mini-table uzi-method-result-table"><div><strong>数据项</strong><strong>值</strong><strong>来源类型</strong></div>{rows.map((row, index) => <div key={`${row.label}-${index}`}><span>{fieldLabel(row.label)}</span><span>{row.value}</span><span>{row.group}</span></div>)}</div><GapList gaps={gaps} /></div>
  }
  const trades = Array.isArray(content.trades) ? content.trades.map(asRecord) : []
  return <div className="uzi-result-body"><ResultHeadline label="持仓再平衡" value={`${trades.length} 笔策略建议`} detail={String(content.target_mode || '')} /><div className="uzi-mini-table"><div><strong>持仓</strong><strong>动作 / 漂移</strong><strong>金额 / 成本</strong></div>{trades.map((row) => <div key={String(row.ticker)}><span>{String(row.ticker)}</span><span>{String(row.action)} · {percent(row.drift_pp)}</span><span>{money(row.trade_amount)} / {money(row.estimated_cost)}</span></div>)}</div><GapList gaps={gaps} /></div>
}

function flattenDisplay(value: unknown, path = '', group = '观测', depth = 0): Array<{ label: string; value: string; group: string }> {
  if (depth > 4 || value == null) return []
  if (Array.isArray(value)) return value.flatMap((item, index) => flattenDisplay(item, `${path}.${index + 1}`, group, depth + 1))
  if (typeof value === 'object') {
    return Object.entries(asRecord(value)).flatMap(([key, item]) => {
      const nextGroup = path === '' ? (key === 'derived' ? '推导' : key === 'methodology' ? '显式假设' : '观测') : group
      return flattenDisplay(item, path ? `${path}.${key}` : key, nextGroup, depth + 1)
    })
  }
  return [{ label: path.replace(/^(observed|derived|methodology)\./, ''), value: typeof value === 'number' ? formatNumber(value) : typeof value === 'boolean' ? (value ? '是' : '否') : String(value), group }]
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

function fieldLabel(value: string): string {
  const leaf = value.split('.').filter((item) => !/^\d+$/.test(item)).pop() || value
  return ({
    revenue: '营业收入', net_income: '净利润', ebitda: 'EBITDA', total_assets: '总资产', total_liabilities: '总负债',
    operating_cash_flow: '经营现金流', projection: '预测期', entry_equity: '入场权益价值', exit_equity: '退出权益价值',
    moic: '投资倍数', irr: '内部收益率', standalone_eps: '独立每股收益', pro_forma_eps: '备考每股收益',
    accretion_pct: '增厚/摊薄比例', coverage_state: '覆盖状态', dated_event_count: '有日期事件数', coverage: '证据覆盖率',
    target_ebitda: '目标 EBITDA', recommendation: '投资建议', committee_state: '投委会状态', bcg_position: 'BCG 定位',
  } as Record<string, string>)[leaf] || leaf.replaceAll('_', ' ')
}

function gapLabel(value: string): string {
  const [head] = value.split(':')
  return ({ benchmark_return_missing: '缺少基准收益', portfolio_value_missing: '缺少完整组合市值', earnings_event_or_catalyst_missing: '缺少财报事件或催化剂', options_implied_volatility_not_collected: '未采集期权隐含波动', financial_baseline_missing: '缺少财务基线' } as Record<string, string>)[head] || value.replaceAll('_', ' ')
}
