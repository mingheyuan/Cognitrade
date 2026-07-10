import {
  BrainCircuit,
  CheckCircle2,
  CircleAlert,
  Clock3,
  History,
  Info,
  LockKeyhole,
  Scale,
  ShieldCheck,
  Sparkles,
  XCircle,
} from 'lucide-react'
import { useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import { asString, formatDate, runListItem } from '../live-data'

export function Methodology() {
  const { proposals, runs, reviewProposal } = useCognitrade()
  const [reviewing, setReviewing] = useState('')
  const pending = proposals.filter((proposal) => proposal.status === 'pending')
  const approved = proposals.filter((proposal) => proposal.status === 'approved')
  const evolutionRuns = runs.filter((run) => run.intent === 'methodology_evolution').map(runListItem)

  const review = async (proposalId: string, decision: 'approve' | 'reject') => {
    const reasonInput = window.prompt(decision === 'approve' ? '批准理由（可选）' : '拒绝理由（可选）')
    if (reasonInput === null) return
    const reason = reasonInput.trim() || undefined
    setReviewing(proposalId)
    try {
      await reviewProposal(proposalId, decision, reason)
    } catch {
      // Shared error banner contains the API response.
    } finally {
      setReviewing('')
    }
  }

  return (
    <main className="page-content methodology-page">
      <header className="page-heading">
        <div><span className="page-kicker">METHODOLOGY · LIVE API</span><h1>方法论</h1><p>提案列表和审批使用真实管理接口；未暴露的权重与版本信息不再由前端模拟。</p></div>
      </header>

      <section className="methodology-summary-grid">
        <article><span className="summary-icon summary-icon--mint"><BrainCircuit size={17} /></span><div><span>提案总数</span><strong>{proposals.length}</strong><small>GET /admin/proposals</small></div></article>
        <article><span className="summary-icon summary-icon--gold"><Clock3 size={17} /></span><div><span>待审核</span><strong>{pending.length}</strong><small>需要人工决策</small></div></article>
        <article><span className="summary-icon summary-icon--blue"><CheckCircle2 size={17} /></span><div><span>已批准</span><strong>{approved.length}</strong><small>真实持久化状态</small></div></article>
        <article><span className="summary-icon summary-icon--rose"><History size={17} /></span><div><span>演进运行</span><strong>{evolutionRuns.length}</strong><small>methodology_evolution</small></div></article>
      </section>

      <div className="methodology-grid">
        <section className="dashboard-panel methodology-radar-panel api-gap-panel">
          <div className="panel-heading"><div><span className="section-kicker">ACTIVE METHODOLOGY</span><h2>当前权重与版本</h2></div><CircleAlert size={16} /></div>
          <div className="live-empty-state live-empty-state--large"><CircleAlert size={22} /><strong>v1 暂无方法论读取 API</strong><p>无法可靠展示当前版本、维度权重、样本数或历史权重变化。缺口已经写入 `api-todo/`。</p></div>
        </section>

        <section className="dashboard-panel proposal-list-panel">
          <div className="panel-heading"><div><span className="section-kicker">REVIEW QUEUE</span><h2>方法论提案</h2></div><span className="count-badge">{pending.length} pending</span></div>
          <div className="proposal-live-list">
            {proposals.map((proposal) => (
              <article className="proposal-card" key={proposal.proposal_id}>
                <div className="proposal-card-topline"><span className="proposal-type">{asString(proposal.proposal_type, 'proposal')}</span><span className={`status-table-badge status-table-badge--${proposal.status === 'approved' ? 'complete' : proposal.status === 'pending' ? 'running' : 'review'}`}>{proposal.status}</span></div>
                <h3>{asString(proposal.title, proposal.proposal_id)}</h3>
                <p>{asString(proposal.summary || proposal.reason, '后端未提供提案摘要')}</p>
                <div className="proposal-meta-row"><span><Scale size={13} />{proposal.proposal_id}</span><span><Clock3 size={13} />{formatDate(asString(proposal.updated_at || proposal.created_at))}</span></div>
                {proposal.status === 'pending' && (
                  <div className="proposal-review-actions">
                    <button className="secondary-button" onClick={() => void review(proposal.proposal_id, 'reject')} disabled={reviewing === proposal.proposal_id}><XCircle size={14} />拒绝</button>
                    <button className="primary-button" onClick={() => void review(proposal.proposal_id, 'approve')} disabled={reviewing === proposal.proposal_id}><CheckCircle2 size={14} />批准</button>
                  </div>
                )}
              </article>
            ))}
            {!proposals.length && <div className="live-empty-state"><Sparkles size={20} /><strong>暂无提案</strong><p>后端 `/admin/proposals` 返回空列表。</p></div>}
          </div>
        </section>

        <section className="dashboard-panel evolution-panel">
          <div className="panel-heading"><div><span className="section-kicker">EVOLUTION RUNS</span><h2>认知演进运行</h2></div><History size={16} /></div>
          <ol className="evolution-timeline">
            {evolutionRuns.map((run) => <li key={run.id}><span className="evolution-marker is-current"><Sparkles size={13} /></span><div><div className="evolution-title-row"><strong>{run.title}</strong><time>{run.time}</time></div><p>{run.intentLabel} · {run.statusLabel} · {run.id}</p></div></li>)}
          </ol>
          {!evolutionRuns.length && <div className="inline-empty-state"><History size={16} />暂无 methodology_evolution 运行。</div>}
        </section>

        <section className="dashboard-panel methodology-guardrails-panel">
          <div className="panel-heading"><div><span className="section-kicker">API BOUNDARY</span><h2>审核边界</h2></div><LockKeyhole size={16} className="muted-icon" /></div>
          <div className="guardrail-list">
            <div><span className="guardrail-icon"><ShieldCheck size={15} /></span><span><strong>服务端身份</strong><small>reviewer 由 Bearer principal 派生，前端不能注入</small></span></div>
            <div><span className="guardrail-icon"><Scale size={15} /></span><span><strong>终态保护</strong><small>已批准与已拒绝提案不能反向覆盖</small></span></div>
            <div><span className="guardrail-icon"><LockKeyhole size={15} /></span><span><strong>串行写入</strong><small>审批操作由后端 mutation lock 保护</small></span></div>
          </div>
          <div className="guardrail-note"><Info size={13} />这里只展示后端返回的提案与状态，不暴露模型内部推理过程。</div>
        </section>
      </div>
    </main>
  )
}
