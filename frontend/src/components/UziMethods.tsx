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
import { BusinessDataView } from '../artifacts/BusinessDataView'
import { MethodDeliveryDossier } from '../artifacts/MethodDeliveryDossier'
import type { ResearchIntent } from '../research-contracts'
import { asRecord } from '../utils/value'
import { AppSelect } from './AppSelect'

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
  { id: 'analyze_stock', intent: 'uzi_analyze_stock', label: '完整个股分析', description: 'UZI 原生 Stage 1–4、机械自审与严格报告门。', icon: BrainCircuit },
  { id: 'quick_scan', intent: 'quick_scan', label: '股票速判', description: '独立执行 1/2/10/18 四维、10 席评审，只产出 Markdown。', icon: Gauge },
  { id: 'panel_only', intent: 'uzi_panel_only', label: '独立评审团', description: '单独运行 UZI 原生规则评审团，不生成完整报告。', icon: Bot },
  { id: 'scan_trap', intent: 'uzi_scan_trap', label: '杀猪盘扫描', description: '独立核验 UZI 八类推广、操纵与舆情风险信号。', icon: AlertCircle },
  { id: 'dcf', intent: 'uzi_dcf', label: 'DCF 估值', description: 'UZI 原生两阶段现金流折现与敏感性表。', icon: TrendingUp },
  { id: 'comps', intent: 'uzi_comps', label: '可比公司', description: 'UZI 原生同行倍数、分位与隐含价格。', icon: Scale },
  { id: 'ai_readiness', intent: 'uzi_ai_readiness', label: 'AI 就绪度', description: '核验产业链、真实需求与不可替代性三道证据门。', icon: Bot },
  { id: 'earnings_preview', intent: 'uzi_earnings_preview', label: '财报前瞻', description: '调用 UZI 原生财报前瞻方法。', icon: TrendingUp },
  { id: 'model_update', intent: 'uzi_model_update', label: '模型更新', description: '只接受显式新假设，不执行上游演示模式。', icon: BrainCircuit },
  { id: 'three_statement', intent: 'uzi_three_statement_method', label: '三表联动', description: '完整填写 UZI 原生 11 项预测参数后计算。', icon: BrainCircuit },
  { id: 'quick_lbo', intent: 'uzi_lbo', label: '快速 LBO', description: '要求真实 EBITDA 与完整原生交易参数。', icon: Scale },
  { id: 'accretion_dilution', intent: 'uzi_accretion_dilution_method', label: '并购增厚摊薄', description: '基于显式标的与交易参数计算备考 EPS。', icon: ArrowRightLeft },
  { id: 'segmental', intent: 'uzi_segmental_model', label: '分部模型', description: '先发现真实披露骨架，再补充假设并校验。', icon: Gauge },
  { id: 'initiation_report', intent: 'uzi_initiate', label: '首次覆盖', description: '调用 UZI 原生首次覆盖产物。', icon: Database },
  { id: 'earnings_review', intent: 'uzi_earnings', label: '财报解读', description: '调用 UZI 原生财报解读产物。', icon: TrendingUp },
  { id: 'catalyst_calendar', intent: 'uzi_catalysts', label: '催化剂日历', description: '仅将有明确日期的事件纳入可执行日历。', icon: Play },
  { id: 'thesis_tracker', intent: 'uzi_thesis', label: '投资逻辑追踪', description: '追踪增长、现金生成与估值证据柱。', icon: BrainCircuit },
  { id: 'quantitative_screen', intent: 'uzi_screen', label: '量化筛选', description: '运行 UZI 原生五类选股标准。', icon: Gauge },
  { id: 'morning_note', intent: 'uzi_morning_note_method', label: '晨报', description: '调用 UZI 原生研究晨报。', icon: TrendingUp },
  { id: 'sector_review', intent: 'uzi_sector_review_method', label: '行业综述', description: '调用 UZI 原生行业综述方法。', icon: Database },
  { id: 'ic_memo', intent: 'uzi_ic_memo', label: '投委会备忘录', description: '汇总证据与风险，最终决策保留人工复核。', icon: Database },
  { id: 'porter_bcg', intent: 'uzi_porter_bcg_method', label: '竞争格局', description: '调用 UZI 原生 Porter / BCG 分析。', icon: Scale },
  { id: 'dd_checklist', intent: 'uzi_dd', label: '尽调清单', description: '调用 UZI 原生尽调清单。', icon: CheckCircle2 },
  { id: 'unit_economics', intent: 'uzi_unit_economics_method', label: '单位经济', description: '需显式接受 UZI 原生代理假设。', icon: Gauge },
  { id: 'value_creation_plan', intent: 'uzi_value_creation_plan_method', label: '价值创造计划', description: '需显式接受 UZI 原生代理假设。', icon: TrendingUp },
] as const

const methodArtifactTypes = [
  'uzi_native_analysis', 'quick_scan', 'research_report',
  'ai_readiness', 'earnings_preview', 'model_update', 'returns_attribution', 'holding_rebalance', 'portfolio_rebalance',
  'three_statement_model', 'quick_lbo', 'accretion_dilution', 'segmental_model', 'initiation_report', 'earnings_review',
  'catalyst_calendar', 'thesis_tracker', 'morning_note', 'ic_memo', 'porter_bcg', 'dd_checklist', 'unit_economics', 'value_creation_plan',
  'uzi_native_method_result', 'uzi_native_command_result',
]

export function UziMethods({ onOpenRun }: UziMethodsProps) {
  const { createRun, selectedRun, selectedRunId, selectRun, submitting } = useCognitrade()
  const [mode, setMode] = useState<'stock' | 'portfolio'>('stock')
  const [symbol, setSymbol] = useState('')
  const [method, setMethod] = useState<(typeof stockMethods)[number]['id']>('ai_readiness')
  const [analysisProfile, setAnalysisProfile] = useState<'lite' | 'medium' | 'deep' | ''>('')
  const [modelField, setModelField] = useState('net_margin')
  const [modelValue, setModelValue] = useState('')
  const [holdings, setHoldings] = useState<HoldingDraft[]>([emptyHolding(), emptyHolding()])
  const [benchmark, setBenchmark] = useState('')
  const [threshold, setThreshold] = useState('5')
  const [portfolioMethod, setPortfolioMethod] = useState<'returns' | 'rebalance' | 'portfolio_rebalance'>('returns')
  const [acceptNativeDefaults, setAcceptNativeDefaults] = useState(false)
  const [acceptNativeFees, setAcceptNativeFees] = useState(false)
  const [segmentalJson, setSegmentalJson] = useState('')
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
    const selected = stockMethods.find((item) => item.id === method)
    if (!selected) return
    const constraints: Record<string, unknown> = { symbol: normalized }
    if (method === 'analyze_stock') {
      if (!analysisProfile) return
      constraints.research_profile = analysisProfile
    }
    if (method === 'model_update') {
      const value = optionalNumber(modelValue)
      if (value == null) return
      constraints.model_updates = { [modelField]: value }
    }
    const methodAssumptions = buildMethodAssumptions(method, assumptionInputs, acceptNativeDefaults)
    if (methodNeedsAssumptions(method) && !methodAssumptions) return
    if (methodAssumptions) constraints.method_assumptions = { [method]: methodAssumptions }
    if (method === 'segmental' && segmentalJson.trim()) {
      try {
        constraints.segmental_model = JSON.parse(segmentalJson)
      } catch {
        return
      }
    }
    const response = await createRun(
      `${normalized} ${stockMethods.find((item) => item.id === method)?.label || method}`,
      selected.intent as ResearchIntent,
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
    if (portfolioMethod === 'returns' && parsed.some((row) => row.return_pct == null)) return
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
    const assetTargets = Object.fromEntries(
      holdings
        .filter((row) => row.assetClass.trim() && optionalNumber(row.assetTarget) != null)
        .map((row) => [row.assetClass.trim(), optionalNumber(row.assetTarget)]),
    )
    const observedClasses = [...new Set(holdings.filter((row) => row.ticker.trim()).map((row) => row.assetClass.trim()).filter(Boolean))]
    if (portfolioMethod === 'rebalance') {
      if (Object.keys(targets).length !== parsed.length || !acceptNativeFees) return
      constraints.accept_uzi_native_fee_schedule = true
    }
    if (portfolioMethod === 'portfolio_rebalance') {
      if (!observedClasses.length || Object.keys(assetTargets).length !== observedClasses.length) return
      constraints.asset_class_targets = assetTargets
    }
    const intent = ({
      returns: 'uzi_returns',
      rebalance: 'uzi_rebalance',
      portfolio_rebalance: 'uzi_portfolio_rebalance_method',
    } as const)[portfolioMethod]
    const response = await createRun(
      portfolioMethod === 'returns' ? '对显式持仓做 UZI 收益归因' : portfolioMethod === 'rebalance' ? '对显式持仓做 UZI 再平衡' : '对显式资产类别做 UZI 战略再平衡',
      intent,
      constraints,
    )
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
            {method === 'analyze_stock' && (
              <label><span>研究深度（必须显式选择）</span><AppSelect value={analysisProfile} onChange={(event) => setAnalysisProfile(event.target.value as typeof analysisProfile)}><option value="">请选择</option><option value="lite">快速 Lite</option><option value="medium">标准 Medium</option><option value="deep">深度 Deep</option></AppSelect></label>
            )}
            {method === 'model_update' && (
              <div className="uzi-inline-form">
                <label><span>更新字段</span><AppSelect value={modelField} onChange={(event) => setModelField(event.target.value)}><option value="rev_growth">营收增速 %</option><option value="gross_margin">毛利率 %</option><option value="net_margin">净利率 %</option><option value="capex_pct">资本开支/营收 %</option><option value="stage1_growth">DCF 第一阶段增速 %</option><option value="terminal_g">DCF 永续增长率 %</option><option value="beta">Beta</option><option value="target_pe">目标市盈率</option><option value="target_price">目标价</option></AppSelect></label>
                <label><span>显式新值</span><input type="number" step="any" value={modelValue} onChange={(event) => setModelValue(event.target.value)} placeholder="必须填写" /></label>
              </div>
            )}
            <MethodAssumptionFields method={method} values={assumptionInputs} onChange={(key, value) => setAssumptionInputs((current) => ({ ...current, [key]: value }))} />
            {['unit_economics', 'value_creation_plan'].includes(method) && (
              <label className="uzi-policy-checkbox"><input type="checkbox" checked={acceptNativeDefaults} onChange={(event) => setAcceptNativeDefaults(event.target.checked)} /><span>我已知晓并接受 UZI 原生方法内置的代理假设；产物必须展示这些假设。</span></label>
            )}
            {method === 'segmental' && (
              <label><span>已填分部模型 JSON（可选；留空先生成发现骨架）</span><textarea rows={8} value={segmentalJson} onChange={(event) => setSegmentalJson(event.target.value)} placeholder="从首次运行产物复制 skeleton，补齐 drivers、thesis_tag 与三情景 CAGR 后粘贴回来" /></label>
            )}
            <button className="primary-button uzi-run-button" disabled={submitting || !symbol.trim() || (method === 'analyze_stock' && !analysisProfile) || (method === 'model_update' && optionalNumber(modelValue) == null) || (methodNeedsAssumptions(method) && !buildMethodAssumptions(method, assumptionInputs, acceptNativeDefaults))}>{submitting ? <RefreshCw size={15} className="is-spinning" /> : <Play size={15} />}{submitting ? '正在运行' : '运行 UZI 原生命令 / 方法'}</button>
          </form>
        ) : (
          <form className="uzi-config-card uzi-portfolio-form" onSubmit={submitPortfolio}>
            <header><div><span className="section-kicker">PORTFOLIO INPUT</span><h2>录入显式持仓</h2></div><button type="button" className="secondary-button" onClick={() => setHoldings((rows) => [...rows, emptyHolding()])}><Plus size={14} />添加持仓</button></header>
            <label><span>组合方法</span><AppSelect value={portfolioMethod} onChange={(event) => setPortfolioMethod(event.target.value as typeof portfolioMethod)}><option value="returns">收益归因</option><option value="rebalance">持仓再平衡</option><option value="portfolio_rebalance">资产类别战略再平衡</option></AppSelect></label>
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
            </div>
            {portfolioMethod === 'rebalance' && <label className="uzi-policy-checkbox"><input type="checkbox" checked={acceptNativeFees} onChange={(event) => setAcceptNativeFees(event.target.checked)} /><span>我已核对并接受 UZI 原生 A/HK/US 固定费率表；每只目标权重必须填写。</span></label>}
            <p className="uzi-policy-note"><AlertCircle size={14} />收益归因要求每只区间收益；持仓再平衡要求每只目标权重；战略再平衡要求每个资产类别目标。缺一项即不提交，不以零或等权静默补齐。</p>
            <button className="primary-button uzi-run-button" disabled={submitting || !holdings.some((row) => row.ticker.trim() && optionalNumber(row.weight) != null)}>{submitting ? <RefreshCw size={15} className="is-spinning" /> : <ArrowRightLeft size={15} />}{submitting ? '正在运行' : '运行归因与再平衡'}</button>
          </form>
        )}

        <section className="uzi-result-card">
          <header><div><span className="section-kicker">ACTIVE ARTIFACT</span><h2>专项结果</h2></div>{result && <span className={`quality-pill quality-pill--${String(asRecord(result.content).quality_status || 'data_gap')}`}>{qualityLabel(String(asRecord(result.content).quality_status || 'data_gap'))}</span>}</header>
          {result ? <Tier1Result artifact={result} /> : <div className="uzi-empty"><Gauge size={24} /><strong>暂无专项 Artifact</strong><p>运行上方方法后，这里会展示结构化证据、覆盖率、贡献或漂移，不直接展示原始 JSON。</p></div>}
        </section>
      </div>
      <MethodDeliveryDossier artifacts={selectedRun?.replay.artifacts || []} />
    </main>
  )
}

function MethodAssumptionFields({ method, values, onChange }: { method: string; values: Record<string, string>; onChange: (key: string, value: string) => void }) {
  const field = (key: string, label: string, placeholder: string, type: 'number' | 'text' = 'number') => (
    <label key={key}><span>{label}</span><input type={type} step={type === 'number' ? 'any' : undefined} value={values[key] || ''} onChange={(event) => onChange(key, event.target.value)} placeholder={placeholder} /></label>
  )
  let fields: ReactNode[] = []
  if (method === 'three_statement') fields = [
    field('revenueGrowthY1', 'Y1 营收增速', '例如 0.12'), field('revenueGrowthY2', 'Y2 营收增速', '例如 0.10'),
    field('revenueGrowthY3', 'Y3 营收增速', '例如 0.08'), field('revenueGrowthY4', 'Y4 营收增速', '例如 0.06'),
    field('revenueGrowthY5', 'Y5 营收增速', '例如 0.05'), field('grossMargin', '毛利率', '例如 0.35'),
    field('opexPctRevenue', '运营费用/营收', '例如 0.18'), field('taxRate', '税率', '例如 0.25'),
    field('capexPctRevenue', '资本开支/营收', '例如 0.05'), field('depPctRevenue', '折旧摊销/营收', '例如 0.04'),
    field('nwcPctRevenue', '营运资金/增量营收', '例如 0.10'),
  ]
  if (method === 'quick_lbo') fields = [
    field('entryMultiple', '入场 EBITDA 倍数', '例如 8'), field('debtMultiple', '债务/EBITDA', '例如 5'),
    field('exitMultiple', '退出 EBITDA 倍数', '例如 8'), field('holdYears', '持有年数', '例如 5'),
    field('ebitdaGrowth', '年 EBITDA 增速', '例如 0.08'), field('interestRate', '债务利率', '例如 0.06'),
  ]
  if (method === 'accretion_dilution') fields = [
    field('targetTicker', '标的代码', '例如 000001', 'text'), field('targetName', '标的名称', '例如 平安银行', 'text'),
    field('targetShares', '标的总股本（亿股）', '例如 200'), field('targetPrice', '标的股价', '例如 10'),
    field('targetEps', '标的 EPS', '例如 0.5'), field('targetPe', '标的 PE', '例如 20'),
    field('targetNetIncome', '标的净利润（亿元）', '例如 100'), field('premiumPct', '收购溢价率', '例如 0.30'),
    field('cashPct', '现金支付比例', '例如 0.50'), field('synergiesYi', '税前年化协同（亿元）', '可为 0'),
    field('newDebtRate', '新增债务利率', '例如 0.05'),
  ]
  if (method === 'thesis_tracker') fields = [
    <label key="direction"><span>投资方向</span><AppSelect value={values.direction || ''} onChange={(event) => onChange('direction', event.target.value)}><option value="">请选择</option><option value="long">多头</option><option value="short">空头</option></AppSelect></label>,
  ]
  if (method === 'quantitative_screen') fields = [
    <label key="style"><span>筛选风格</span><AppSelect value={values.style || ''} onChange={(event) => onChange('style', event.target.value)}><option value="">请选择</option><option value="value">价值</option><option value="growth">成长</option><option value="quality">质量</option><option value="short">做空风险</option><option value="gulp">GULP 合理价成长</option></AppSelect></label>,
  ]
  if (!fields.length) return null
  return <div className="uzi-assumption-panel"><div><strong>显式方法假设</strong><small>以下参数全部由你提供；系统不会填默认值。</small></div><div className="uzi-inline-form">{fields}</div></div>
}

function methodNeedsAssumptions(method: string): boolean {
  return ['three_statement', 'quick_lbo', 'accretion_dilution', 'thesis_tracker', 'quantitative_screen', 'unit_economics', 'value_creation_plan'].includes(method)
}

function buildMethodAssumptions(method: string, values: Record<string, string>, acceptNativeDefaults: boolean): Record<string, unknown> | null {
  const number = (key: string) => optionalNumber(values[key])
  if (method === 'three_statement') {
    const keys = ['revenueGrowthY1', 'revenueGrowthY2', 'revenueGrowthY3', 'revenueGrowthY4', 'revenueGrowthY5', 'grossMargin', 'opexPctRevenue', 'taxRate', 'capexPctRevenue', 'depPctRevenue', 'nwcPctRevenue']
    if (keys.some((key) => number(key) == null)) return null
    return {
      revenue_growth_y1: number('revenueGrowthY1'), revenue_growth_y2: number('revenueGrowthY2'), revenue_growth_y3: number('revenueGrowthY3'),
      revenue_growth_y4: number('revenueGrowthY4'), revenue_growth_y5: number('revenueGrowthY5'), gross_margin: number('grossMargin'),
      opex_pct_revenue: number('opexPctRevenue'), tax_rate: number('taxRate'), capex_pct_revenue: number('capexPctRevenue'),
      dep_pct_revenue: number('depPctRevenue'), nwc_pct_revenue: number('nwcPctRevenue'),
    }
  }
  if (method === 'quick_lbo') {
    const keys = ['entryMultiple', 'debtMultiple', 'exitMultiple', 'holdYears', 'ebitdaGrowth', 'interestRate']
    if (keys.some((key) => number(key) == null)) return null
    return { entry_multiple: number('entryMultiple'), debt_multiple: number('debtMultiple'), exit_multiple: number('exitMultiple'), hold_years: number('holdYears'), ebitda_growth: number('ebitdaGrowth'), interest_rate: number('interestRate') }
  }
  if (method === 'accretion_dilution') {
    const targetTicker = String(values.targetTicker || '').trim()
    const targetName = String(values.targetName || '').trim()
    const keys = ['targetShares', 'targetPrice', 'targetEps', 'targetPe', 'targetNetIncome', 'premiumPct', 'cashPct', 'synergiesYi', 'newDebtRate']
    if (!targetTicker || !targetName || keys.some((key) => number(key) == null)) return null
    return {
      target: { ticker: targetTicker, name: targetName, shares_yi: number('targetShares'), price: number('targetPrice'), eps: number('targetEps'), pe: number('targetPe'), net_income_yi: number('targetNetIncome') },
      premium_pct: number('premiumPct'), cash_pct: number('cashPct'), synergies_yi: number('synergiesYi'), new_debt_rate: number('newDebtRate'),
    }
  }
  if (method === 'thesis_tracker') return ['long', 'short'].includes(values.direction) ? { direction: values.direction } : null
  if (method === 'quantitative_screen') return ['value', 'growth', 'quality', 'short', 'gulp'].includes(values.style) ? { style: values.style } : null
  if (['unit_economics', 'value_creation_plan'].includes(method)) return acceptNativeDefaults ? { accept_uzi_native_defaults: true } : null
  return null
}

function Tier1Result({ artifact }: { artifact: Record<string, unknown> }) {
  const type = String(artifact.artifact_type || '')
  const content = asRecord(artifact.content)
  const gaps = Array.isArray(content.data_gap) ? content.data_gap.map(String) : []
  if (type === 'uzi_native_method_result') {
    const methodResult = asRecord((Array.isArray(content.results) ? content.results : [])[0])
    const nativeResult = methodResult.result
    const methodId = String(content.method_id || '')
    const methodLabel = stockMethods.find((item) => item.id === methodId)?.label || ({ returns_attribution: '收益归因', holding_rebalance: '持仓再平衡', portfolio_rebalance: '资产配置再平衡' } as Record<string, string>)[methodId] || methodId
    return <div className="uzi-result-body"><ResultHeadline label={methodLabel} value={nativeResult && typeof nativeResult === 'object' ? 'UZI 原生结果' : '数据不足'} detail={`固定来源版本 ${String(content.source_commit || '未提供')}`} />{nativeResult && typeof nativeResult === 'object' ? <BusinessDataView value={nativeResult} fieldKey={methodId} path={`uzi_native_method_result.${methodId}`} /> : null}<GapList gaps={gaps} /></div>
  }
  if (type === 'uzi_native_command_result') {
    const commandResults = Array.isArray(content.results) ? content.results : []
    const commandId = String(content.command_id || 'UZI command')
    return <div className="uzi-result-body"><ResultHeadline label={`/${commandId}`} value={commandResults.length ? 'UZI 原生命令结果' : '数据不足'} detail={`执行阶段：${String(content.phase || 'complete')}`} />{commandResults.length ? <BusinessDataView value={commandResults} fieldKey={commandId} path={`uzi_native_command_result.${commandId}.results`} /> : null}<GapList gaps={gaps} /></div>
  }
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
    const rows = flattenDisplay({ observed: methodResult.observed, derived: methodResult.derived, methodology: methodResult.methodology })
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
  return gaps.length ? <div className="uzi-gap-list"><AlertCircle size={14} /><span>{gaps.length} 项数据缺口</span><p>{gaps.map(gapLabel).join('、')}</p></div> : <div className="uzi-no-gap"><CheckCircle2 size={14} />当前方法所需观测完整</div>
}

function updateHolding(setter: React.Dispatch<React.SetStateAction<HoldingDraft[]>>, index: number, field: keyof HoldingDraft, value: string) {
  setter((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row))
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
  return number == null ? '—' : number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
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
