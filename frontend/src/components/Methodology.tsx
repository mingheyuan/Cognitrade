import {
  BrainCircuit,
  CheckCircle2,
  Clock3,
  History,
  Info,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useCognitrade } from '../CognitradeContext'
import { asRecord, asString, dashboardItems, dashboardValue } from '../live-data'

export function Methodology() {
  const { methodologyDashboard } = useCognitrade()
  const profile = dashboardValue(methodologyDashboard, 'profile')
  const screenStyles = Array.isArray(profile.screen_styles) ? profile.screen_styles.map(asRecord) : []
  const guardrails = Array.isArray(profile.guardrails) ? profile.guardrails.map((item) => asString(item)).filter(Boolean) : []
  const verifiedPolicyMemory = dashboardItems(methodologyDashboard, 'verified_policy_memory')

  return (
    <main className="page-content methodology-page">
      <header className="page-heading">
        <div><span className="page-kicker">METHODOLOGY · AGENT OS</span><h1>方法论</h1><p>展示 DomainPack 已声明的专业筛选契约，以及经过评测与治理后进入 Policy Memory 的记录。</p></div>
      </header>

      <section className="methodology-summary-grid">
        <article><span className="summary-icon summary-icon--mint"><BrainCircuit size={17} /></span><div><span>Pack 版本</span><strong>v{asString(profile.version, '—')}</strong><small>{asString(profile.name, '等待 DomainPack 配置')}</small></div></article>
        <article><span className="summary-icon summary-icon--gold"><Clock3 size={17} /></span><div><span>筛选风格</span><strong>{screenStyles.length}</strong><small>由 finance_ashare Pack 声明</small></div></article>
        <article><span className="summary-icon summary-icon--blue"><CheckCircle2 size={17} /></span><div><span>已验证策略记忆</span><strong>{verifiedPolicyMemory.length}</strong><small>verified / promoted</small></div></article>
        <article><span className="summary-icon summary-icon--rose"><LockKeyhole size={17} /></span><div><span>治理规则</span><strong>{guardrails.length}</strong><small>高影响变更需审核</small></div></article>
      </section>

      <div className="methodology-grid">
        <section className="dashboard-panel methodology-radar-panel">
          <div className="panel-heading"><div><span className="section-kicker">ACTIVE METHODOLOGY</span><h2>{asString(profile.name, '当前方法论')}</h2></div><BrainCircuit size={16} /></div>
          <div className="live-empty-state live-empty-state--large"><BrainCircuit size={22} /><strong>{asString(profile.description, '尚无可展示的受控方法论契约。')}</strong><p>筛选方法由 Pack 配置，缺失证据不会自动补为中性结果。</p></div>
        </section>

        <section className="dashboard-panel proposal-list-panel">
          <div className="panel-heading"><div><span className="section-kicker">SCREENING PROFILES</span><h2>可选专业筛选风格</h2></div><span className="count-badge">{screenStyles.length}</span></div>
          <div className="proposal-live-list">
            {screenStyles.map((style) => <article className="proposal-card" key={asString(style.id)}><div className="proposal-card-topline"><span className="proposal-type">{asString(style.id)}</span><span className="status-table-badge status-table-badge--complete">Pack</span></div><h3>{asString(style.name)}</h3><p>{asString(style.purpose, '后端未提供说明')}</p></article>)}
            {!screenStyles.length && <div className="live-empty-state"><Sparkles size={20} /><strong>暂无方法论配置</strong><p>DomainPack 尚未返回 screening profile。</p></div>}
          </div>
        </section>

        <section className="dashboard-panel evolution-panel">
          <div className="panel-heading"><div><span className="section-kicker">POLICY MEMORY</span><h2>已验证方法论记录</h2></div><History size={16} /></div>
          <ol className="evolution-timeline">
            {verifiedPolicyMemory.map((item, index) => {
              const record = asRecord(item.data)
              return <li key={item.provenance.memory_id || String(index)}><span className="evolution-marker is-current"><CheckCircle2 size={13} /></span><div><div className="evolution-title-row"><strong>{asString(record.title || record.name, '已验证方法论记录')}</strong><time>{asString(item.provenance.created_at)}</time></div><p>{asString(record.summary || record.description, '该记录保留来源运行和 Artifact，可完整回放。')}</p></div></li>
            })}
          </ol>
          {!verifiedPolicyMemory.length && <div className="inline-empty-state"><History size={16} />暂无已验证的 Policy Memory。</div>}
        </section>

        <section className="dashboard-panel methodology-guardrails-panel">
          <div className="panel-heading"><div><span className="section-kicker">GUARDRAILS</span><h2>写回与审核规则</h2></div><LockKeyhole size={16} className="muted-icon" /></div>
          <div className="guardrail-list">
            {guardrails.map((rule) => <div key={rule}><span className="guardrail-icon"><ShieldCheck size={15} /></span><span><strong>{rule}</strong></span></div>)}
          </div>
          {!guardrails.length && <div className="inline-empty-state"><Info size={16} />暂无来自 DomainPack 的治理规则。</div>}
          <div className="guardrail-note"><Info size={13} />本页不展示未验证的回测、提案或模型内部推理。</div>
        </section>
      </div>
    </main>
  )
}
