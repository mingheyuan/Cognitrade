import {
  ArrowRight,
  BadgeCheck,
  BookOpenCheck,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  Database,
  FileCheck2,
  FlaskConical,
  History,
  Info,
  Layers3,
  LockKeyhole,
  PackageCheck,
  ShieldCheck,
} from 'lucide-react'
import { useCognitrade } from '../CognitradeContext'
import { asRecord, asString, dashboardItems, dashboardValue } from '../live-data'

const lifecycleSteps = [
  {
    id: 'contract',
    label: 'Pack 声明',
    description: '定义筛选风格、证据要求与治理边界。',
    icon: PackageCheck,
  },
  {
    id: 'execution',
    label: '研究执行',
    description: '按选定契约运行，不用中性值填补缺失事实。',
    icon: Layers3,
  },
  {
    id: 'evaluation',
    label: '评测与审核',
    description: '保留冲突与数据缺口，高影响变更必须经过审核。',
    icon: FlaskConical,
  },
  {
    id: 'memory',
    label: 'Policy Memory',
    description: '只有 verified / promoted 记录可以成为复用策略。',
    icon: Database,
  },
] as const

function formatTimestamp(value: unknown): string {
  const text = asString(value)
  if (!text) return '时间未记录'
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return text
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function memoryStatusLabel(status: unknown): string {
  return asString(status) === 'promoted' ? '已晋级' : '已验证'
}

export function Methodology() {
  const { methodologyDashboard, connectionState } = useCognitrade()
  const profile = dashboardValue(methodologyDashboard, 'profile')
  const screenStyles = Array.isArray(profile.screen_styles) ? profile.screen_styles.map(asRecord) : []
  const guardrails = Array.isArray(profile.guardrails)
    ? profile.guardrails.map((item) => asString(item)).filter(Boolean)
    : []
  const verifiedPolicyMemory = dashboardItems(methodologyDashboard, 'verified_policy_memory')
  const profileName = asString(profile.name)
  const profileDescription = asString(profile.description)
  const packVersion = asString(profile.version)
  const hasPack = Boolean(profileName || profileDescription || screenStyles.length || guardrails.length)

  if (!methodologyDashboard && connectionState === 'connecting') {
    return (
      <main className="page-content methodology-page" data-testid="methodology-page">
        <header className="page-heading methodology-page-heading">
          <div><span className="page-kicker">METHODOLOGY · AGENT OS</span><h1>方法论治理</h1><p>正在读取 DomainPack 契约与已验证 Policy Memory。</p></div>
        </header>
        <section className="methodology-loading" role="status" aria-label="正在加载方法论">
          <span /><span /><span /><span />
        </section>
      </main>
    )
  }

  return (
    <main className="page-content methodology-page" data-testid="methodology-page">
      <header className="page-heading methodology-page-heading">
        <div>
          <span className="page-kicker">METHODOLOGY · AGENT OS</span>
          <h1>方法论治理</h1>
          <p>从 DomainPack 专业契约到可复用策略记忆，完整呈现声明、执行、评测与写回边界。</p>
        </div>
        <div className={`methodology-pack-state ${hasPack ? 'is-ready' : ''}`}>
          {hasPack ? <CheckCircle2 size={15} /> : <Clock3 size={15} />}
          <span>{hasPack ? 'Pack 已加载' : '等待 Pack 配置'}</span>
          <small>{methodologyDashboard ? formatTimestamp(methodologyDashboard.generated_at) : '尚未连接 Dashboard'}</small>
        </div>
      </header>

      <section className="methodology-command-card" aria-labelledby="active-methodology-title">
        <div className="methodology-command-copy">
          <div className="methodology-command-icon"><BrainCircuit size={24} /></div>
          <div>
            <div className="methodology-command-meta">
              <span>ACTIVE DOMAIN CONTRACT</span>
              <span className="methodology-version-chip">{packVersion ? `VERSION ${packVersion}` : 'VERSION PENDING'}</span>
            </div>
            <h2 id="active-methodology-title">{profileName || '等待受控方法论契约'}</h2>
            <p>{profileDescription || '后端尚未返回 finance_ashare DomainPack 的方法论配置。连接成功后，这里只展示真实声明内容。'}</p>
            <div className="methodology-trust-row">
              <span><FileCheck2 size={14} />证据优先</span>
              <span><ShieldCheck size={14} />变更受控</span>
              <span><LockKeyhole size={14} />禁止隐式写回</span>
            </div>
          </div>
        </div>

        <div className="methodology-kpi-grid" aria-label="方法论配置摘要">
          <article>
            <span className="methodology-kpi-icon methodology-kpi-icon--gold"><Layers3 size={17} /></span>
            <div><strong>{screenStyles.length}</strong><span>专业筛选配置</span><small>由 Pack 显式声明</small></div>
          </article>
          <article>
            <span className="methodology-kpi-icon methodology-kpi-icon--rose"><LockKeyhole size={17} /></span>
            <div><strong>{guardrails.length}</strong><span>治理约束</span><small>约束评测与写回</small></div>
          </article>
          <article>
            <span className="methodology-kpi-icon methodology-kpi-icon--blue"><BadgeCheck size={17} /></span>
            <div><strong>{verifiedPolicyMemory.length}</strong><span>可复用策略记忆</span><small>verified / promoted</small></div>
          </article>
        </div>
      </section>

      <section className="dashboard-panel methodology-lifecycle-panel" aria-labelledby="methodology-lifecycle-title">
        <div className="panel-heading methodology-section-heading">
          <div><span className="section-kicker">CONTROLLED LIFECYCLE</span><h2 id="methodology-lifecycle-title">从声明到策略记忆</h2></div>
          <span className="methodology-section-note"><Info size={13} />流程表示治理顺序，不代表数量转化</span>
        </div>
        <ol className="methodology-lifecycle" data-testid="methodology-lifecycle">
          {lifecycleSteps.map((step, index) => {
            const Icon = step.icon
            const stepHasData = step.id === 'contract'
              ? hasPack
              : step.id === 'execution'
                ? screenStyles.length > 0
                : step.id === 'evaluation'
                  ? guardrails.length > 0
                  : verifiedPolicyMemory.length > 0
            const stepMeta = step.id === 'contract'
              ? packVersion ? `Pack v${packVersion}` : '等待配置'
              : step.id === 'execution'
                ? `${screenStyles.length} 种受控风格`
                : step.id === 'evaluation'
                  ? `${guardrails.length} 条治理约束`
                  : `${verifiedPolicyMemory.length} 条已验证记录`
            return (
              <li key={step.id} className={stepHasData ? 'is-active' : ''}>
                <div className="methodology-lifecycle-node">
                  <span className="methodology-step-index">0{index + 1}</span>
                  <span className="methodology-step-icon"><Icon size={18} /></span>
                  <div><strong>{step.label}</strong><small>{stepMeta}</small></div>
                </div>
                <p>{step.description}</p>
                {index < lifecycleSteps.length - 1 && <ArrowRight className="methodology-flow-arrow" size={16} aria-hidden="true" />}
              </li>
            )
          })}
        </ol>
      </section>

      <div className="methodology-content-grid">
        <section className="dashboard-panel methodology-profiles-panel" aria-labelledby="screening-profiles-title">
          <div className="panel-heading methodology-section-heading">
            <div><span className="section-kicker">SCREENING PROFILES</span><h2 id="screening-profiles-title">专业筛选配置</h2></div>
            <span className="count-badge">{screenStyles.length}</span>
          </div>
          {screenStyles.length ? (
            <div className="methodology-profile-grid">
              {screenStyles.map((style, index) => {
                const styleId = asString(style.id, `profile-${index + 1}`)
                return (
                  <article className="methodology-profile-card" key={styleId}>
                    <div className="methodology-profile-topline">
                      <span className="methodology-profile-number">{String(index + 1).padStart(2, '0')}</span>
                      <code>{styleId}</code>
                    </div>
                    <h3>{asString(style.name, '未命名筛选配置')}</h3>
                    <p>{asString(style.purpose, 'Pack 尚未提供该筛选配置的用途说明。')}</p>
                    <footer><FileCheck2 size={13} />证据不足时保留数据缺口</footer>
                  </article>
                )
              })}
            </div>
          ) : (
            <div className="live-empty-state methodology-empty-state"><Layers3 size={21} /><strong>暂无专业筛选配置</strong><p>DomainPack 尚未返回 screen_styles；页面不会生成演示配置。</p></div>
          )}
        </section>

        <section className="dashboard-panel methodology-governance-panel" aria-labelledby="guardrails-title">
          <div className="panel-heading methodology-section-heading">
            <div><span className="section-kicker">GOVERNANCE BOUNDARY</span><h2 id="guardrails-title">写回与审核规则</h2></div>
            <ShieldCheck size={17} className="muted-icon" />
          </div>
          {guardrails.length ? (
            <ol className="methodology-guardrail-list">
              {guardrails.map((rule, index) => (
                <li key={rule}>
                  <span><ShieldCheck size={15} /></span>
                  <div><small>RULE {String(index + 1).padStart(2, '0')}</small><strong>{rule}</strong></div>
                </li>
              ))}
            </ol>
          ) : (
            <div className="inline-empty-state"><LockKeyhole size={17} />暂无来自 DomainPack 的治理规则。</div>
          )}
          <div className="methodology-boundary-note">
            <LockKeyhole size={15} />
            <div><strong>展示边界</strong><p>不展示未验证回测、待审提案或模型内部推理；只呈现可追溯契约与已晋级记忆。</p></div>
          </div>
        </section>
      </div>

      <section className="dashboard-panel methodology-memory-panel" aria-labelledby="policy-memory-title">
        <div className="panel-heading methodology-section-heading">
          <div><span className="section-kicker">VERIFIED POLICY MEMORY</span><h2 id="policy-memory-title">已验证方法论记录</h2></div>
          <span className="methodology-memory-count"><History size={14} />{verifiedPolicyMemory.length} 条</span>
        </div>
        {verifiedPolicyMemory.length ? (
          <div className="methodology-memory-list">
            {verifiedPolicyMemory.map((item, index) => {
              const record = asRecord(item.data)
              const title = asString(record.title || record.name, '已验证方法论记录')
              const summary = asString(record.summary || record.description, '该记录保留来源运行、Artifact 与评测，可沿 provenance 完整回放。')
              const status = asString(item.provenance.status, 'verified')
              const sourceRuns = item.provenance.source_run_ids?.length ?? 0
              const sourceArtifacts = item.provenance.source_artifact_ids?.length ?? 0
              const evaluationCount = Array.isArray(item.provenance.evaluations) ? item.provenance.evaluations.length : 0
              return (
                <article className="methodology-memory-card" key={item.provenance.memory_id || String(index)}>
                  <div className="methodology-memory-marker"><BookOpenCheck size={17} /></div>
                  <div className="methodology-memory-body">
                    <div className="methodology-memory-topline">
                      <span className={`methodology-memory-status ${status === 'promoted' ? 'is-promoted' : ''}`}><CheckCircle2 size={12} />{memoryStatusLabel(status)}</span>
                      <time>{formatTimestamp(item.provenance.created_at)}</time>
                    </div>
                    <h3>{title}</h3>
                    <p>{summary}</p>
                    <div className="methodology-provenance-row" aria-label="来源信息">
                      <span>{sourceRuns} 个来源运行</span>
                      <span>{sourceArtifacts} 个来源 Artifact</span>
                      <span>{evaluationCount} 项评测</span>
                      {item.provenance.memory_id && <code>{item.provenance.memory_id}</code>}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        ) : (
          <div className="live-empty-state methodology-empty-state methodology-memory-empty">
            <BookOpenCheck size={22} />
            <strong>暂无已验证的 Policy Memory</strong>
            <p>方法论记录完成评测并进入 verified 或 promoted 状态后，会携带来源运行与 Artifact 出现在这里。</p>
          </div>
        )}
      </section>
    </main>
  )
}
