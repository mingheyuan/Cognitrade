import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FlaskConical,
  GitCommitHorizontal,
  History,
  Info,
  LockKeyhole,
  Scale,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import {
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import { methodologyWeights } from '../data'

const chartTooltipStyle = {
  background: '#101c19',
  border: '1px solid rgba(148, 180, 169, 0.18)',
  borderRadius: '8px',
  color: '#eaf4f0',
  fontSize: '12px',
}

export function Methodology() {
  return (
    <main className="page-content methodology-page">
      <header className="page-heading">
        <div>
          <span className="page-kicker">EVOLUTIONARY COGNITION</span>
          <h1>方法论与认知演进</h1>
          <p>用后验硬数据验证研究维度，让有效逻辑沉淀、无效噪音降权。</p>
        </div>
        <div className="version-selector">
          <span>当前版本</span>
          <button>
            v1.8 · 2026 Q3
            <ChevronRight size={14} />
          </button>
        </div>
      </header>

      <section className="methodology-overview-grid">
        <article className="methodology-summary-card">
          <div className="methodology-summary-icon">
            <BrainCircuit size={22} />
          </div>
          <div>
            <span>当前方法论</span>
            <h2>现金流韧性 × 相对趋势</h2>
            <p>在震荡环境中，提高硬数据质量与相对强度权重，降低单一情绪热度的解释权。</p>
          </div>
          <div className="methodology-version-score">
            <small>后验有效率</small>
            <strong>68.4%</strong>
            <span className="signal-positive">
              <ArrowUpRight size={12} />
              +6.2 pct
            </span>
          </div>
        </article>
        <article className="methodology-mini-card">
          <span className="stat-icon stat-icon--mint">
            <FlaskConical size={17} />
          </span>
          <div>
            <span>已验证样本</span>
            <strong>186</strong>
            <small>T+5 / T+20 双窗口</small>
          </div>
        </article>
        <article className="methodology-mini-card">
          <span className="stat-icon stat-icon--gold">
            <GitCommitHorizontal size={17} />
          </span>
          <div>
            <span>本季变更</span>
            <strong>7</strong>
            <small>3 项升权 · 4 项降权</small>
          </div>
        </article>
        <article className="methodology-mini-card">
          <span className="stat-icon stat-icon--rose">
            <Clock3 size={17} />
          </span>
          <div>
            <span>待审核提案</span>
            <strong>2</strong>
            <small className="warning-copy">需人工确认后写回</small>
          </div>
        </article>
      </section>

      <div className="methodology-main-grid">
        <section className="dashboard-panel factor-radar-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">FACTOR WEIGHTS</span>
              <h2>研究维度权重</h2>
            </div>
            <div className="chart-legend-inline">
              <span><i className="legend-current" /> 当前</span>
              <span><i className="legend-previous" /> 上一版本</span>
            </div>
          </div>
          <div className="factor-radar-chart" aria-label="当前和上一版本研究维度权重雷达图">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={methodologyWeights} outerRadius="68%">
                <PolarGrid stroke="rgba(148,180,169,.18)" />
                <PolarAngleAxis dataKey="factor" tick={{ fill: '#9bb0a9', fontSize: 11 }} />
                <Tooltip contentStyle={chartTooltipStyle} />
                <Radar
                  name="上一版本"
                  dataKey="previous"
                  stroke="#667d76"
                  fill="#667d76"
                  fillOpacity={0.08}
                  strokeDasharray="4 4"
                />
                <Radar
                  name="当前版本"
                  dataKey="score"
                  stroke="#7de2bd"
                  fill="#7de2bd"
                  fillOpacity={0.18}
                  strokeWidth={2}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
          <div className="factor-list">
            {methodologyWeights.map((factor) => {
              const delta = factor.score - factor.previous
              return (
                <div className="factor-row" key={factor.factor}>
                  <span>{factor.factor}</span>
                  <div className="factor-track">
                    <i style={{ width: `${factor.score}%` }} />
                  </div>
                  <strong>{factor.score}</strong>
                  <small className={delta >= 0 ? 'signal-positive' : 'market-down'}>
                    {delta >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                    {delta >= 0 ? '+' : ''}{delta}
                  </small>
                </div>
              )
            })}
          </div>
        </section>

        <section className="dashboard-panel proposals-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">REVIEW QUEUE</span>
              <h2>待审核提案</h2>
            </div>
            <span className="count-badge">2</span>
          </div>
          <article className="proposal-card is-priority">
            <div className="proposal-heading">
              <span className="proposal-type">权重调整</span>
              <span className="warning-badge">高影响</span>
            </div>
            <h3>下调“情绪热度”在震荡市中的前置权重</h3>
            <p>连续 42 个后验样本显示，单独使用情绪热度的 T+5 方向解释力降至 31%，且与资金流信号出现显著背离。</p>
            <div className="proposal-change">
              <span>
                <small>当前权重</small>
                <strong>0.62</strong>
              </span>
              <ArrowRight size={16} />
              <span>
                <small>建议权重</small>
                <strong>0.38</strong>
              </span>
              <span className="proposal-sample">n = 42 · T+5</span>
            </div>
            <div className="proposal-actions">
              <button className="reject-button">
                <X size={14} />
                拒绝
              </button>
              <button className="approve-button">
                <Check size={14} />
                审核并应用
              </button>
            </div>
          </article>
          <article className="proposal-card">
            <div className="proposal-heading">
              <span className="proposal-type">新增规则</span>
              <span className="neutral-badge">中影响</span>
            </div>
            <h3>现金流改善需与资本开支斜率交叉验证</h3>
            <p>建议将“经营现金流 / 净利润”信号与未来两期资本开支变化联合判断，减少周期错配。</p>
            <div className="proposal-meta-row">
              <span><Scale size={13} /> 58 个验证样本</span>
              <span><ShieldCheck size={13} /> 可信度 76%</span>
            </div>
            <button className="proposal-detail-button">
              查看证据与前后对比
              <ChevronRight size={14} />
            </button>
          </article>
        </section>

        <section className="dashboard-panel evolution-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">EVOLUTION LOG</span>
              <h2>认知演进记录</h2>
            </div>
            <button className="text-button">
              完整历史
              <History size={13} />
            </button>
          </div>
          <ol className="evolution-timeline">
            <li>
              <span className="evolution-marker is-current"><Sparkles size={13} /></span>
              <div>
                <div className="evolution-title-row">
                  <strong>v1.8 · 现金流韧性增强</strong>
                  <time>2026-07-02</time>
                </div>
                <p>提高经营现金流与资本开支联合信号的研究优先级，新增 3 条降级保护规则。</p>
                <div className="evolution-chips">
                  <span className="positive-chip">现金流 +14</span>
                  <span className="positive-chip">趋势 +13</span>
                  <span className="negative-chip">资金面 -6</span>
                </div>
              </div>
            </li>
            <li>
              <span className="evolution-marker"><CheckCircle2 size={13} /></span>
              <div>
                <div className="evolution-title-row">
                  <strong>v1.7 · 相对基准校准</strong>
                  <time>2026-05-18</time>
                </div>
                <p>个股方向判断由绝对涨跌升级为相对所属板块表现，板块判断统一对照沪深 300。</p>
              </div>
            </li>
            <li>
              <span className="evolution-marker"><CheckCircle2 size={13} /></span>
              <div>
                <div className="evolution-title-row">
                  <strong>v1.6 · 证据完整度门槛</strong>
                  <time>2026-03-11</time>
                </div>
                <p>核心结论至少需要两类独立来源，缺失项不再自动填充为中性信号。</p>
              </div>
            </li>
          </ol>
        </section>

        <section className="dashboard-panel methodology-guardrails-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">GUARDRAILS</span>
              <h2>写回与审核规则</h2>
            </div>
            <LockKeyhole size={16} className="muted-icon" />
          </div>
          <div className="guardrail-list">
            <div>
              <span className="guardrail-icon"><ShieldCheck size={15} /></span>
              <span><strong>硬数据优先</strong><small>软逻辑必须经过价格或财务数据验证</small></span>
            </div>
            <div>
              <span className="guardrail-icon"><Scale size={15} /></span>
              <span><strong>样本门槛</strong><small>不足 20 个有效样本不会自动建议改权</small></span>
            </div>
            <div>
              <span className="guardrail-icon"><LockKeyhole size={15} /></span>
              <span><strong>人工批准</strong><small>高影响方法论变更不会自动写回</small></span>
            </div>
          </div>
          <div className="guardrail-note">
            <Info size={13} />
            这里只展示提炼后的判断与验证结果，不暴露模型内部推理过程。
          </div>
        </section>
      </div>
    </main>
  )
}
