import { useEffect, useState } from 'react'
import type { CognitradeApiClient } from '../api-client'
import type { ReportEvidenceCollection, ReportRepairPlan } from '../strategy-contracts'

const active = new Set(['queued', 'running', 'pending', 'cancelling'])

export function ReportEvidenceRepair({ client, jobId, onRevised }: {
  client: CognitradeApiClient; jobId: string; onRevised: () => Promise<void>
}) {
  const [plan, setPlan] = useState<ReportRepairPlan | null>(null)
  const [jobs, setJobs] = useState<Record<string, ReportEvidenceCollection>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const running = Object.entries(jobs).filter(([, job]) => active.has(job.status))
    .map(([key, job]) => `${key}=${job.id}`).sort().join('|')

  useEffect(() => {
    if (!running) return
    let disposed = false
    let polling = false
    const refresh = async () => {
      if (polling || document.visibilityState === 'hidden') return
      polling = true
      try {
        for (const entry of running.split('|')) {
          const [key, id] = entry.split('=')
          const value = await client.getReportEvidenceCollection(id)
          if (!disposed) setJobs((previous) => ({ ...previous, [key]: value }))
        }
      } catch (cause) {
        if (!disposed) setError(cause instanceof Error ? cause.message : '补证进度读取失败')
      } finally { polling = false }
    }
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [client, running])

  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await action() } catch (cause) { setError(cause instanceof Error ? cause.message : '补证操作失败') }
    finally { setBusy(false) }
  }

  return <div className="report-evidence-repair">
    <button disabled={busy} onClick={() => void perform(async () => setPlan(await client.getReportRepairPlan(jobId)))}>查看补证与修订方案</button>
    {error && <p role="alert">{error}</p>}
    {plan && <div>
      <p>研究可补充资料，旧版本不覆盖。补采需手动启动；新来源先核实归档，再用于报告修订。</p>
      {!plan.items.length && <p>当前没有待处理的补证或恢复项。</p>}
      {plan.items.map((item) => {
        const key = `${item.symbol}:${item.as_of_date}`
        const job = jobs[key]
        const candidate = job?.publication_candidate
        const runningCollection = job && active.has(job.status)
        const missingNewOriginals = Boolean(item.pending_evidence?.length && job && !runningCollection
          && !(job.research_archive?.persisted_snapshot_count))
        return <section key={key} aria-label={`${item.symbol} 补证`}>
          <strong>{item.symbol} · {item.as_of_date}</strong>
          <p>{item.dimensions.map((dimension) => dimension.dimension).join('、') || '本项是执行或输出错误，可使用原数据定向重试。'}</p>
          <ul>{(item.pending_evidence || []).map((lead) => <li key={`${lead.dimension}:${lead.url}`}><span>{lead.dimension} · 待核实来源：</span><code>{lead.url}</code></li>)}</ul>
          {item.collection_request && <button disabled={busy || Boolean(runningCollection) || job?.freeze_status === 'frozen'} onClick={() => {
            if (window.confirm(`显式启动 ${item.symbol} 的数据中心补证？只处理列出的维度和来源，不重采基金持仓，不自动修改报告。`)) {
              void perform(async () => {
                const created = await client.collectReportEvidence(item.collection_request!)
                setJobs((previous) => ({ ...previous, [key]: created }))
              })
            }
          }}>{job ? '再次手动补证' : '启动该股票补证'}</button>}
          {job && <div>
            <p>数据中心任务 {job.id} · {job.status}</p>
            {job.research_archive && <p>原文归档：成功 {job.research_archive.succeeded ?? 0}，未完成 {job.research_archive.failed ?? 0}。归档成功不等于结论已核验。</p>}
            {missingNewOriginals && <p>本次尚未归档新的原文，不会使用未核实 URL 重跑报告。可在数据中心按任务 ID 查看来源失败详情，处理后再次手动补证。</p>}
            <button disabled={busy} onClick={() => void perform(async () => {
              const current = await client.getReportEvidenceCollection(job.id)
              setJobs((previous) => ({ ...previous, [key]: current }))
            })}>刷新补证进度</button>
            {((job.can_confirm_freeze && candidate) || job.freeze_status === 'frozen') && !runningCollection && !missingNewOriginals && <button disabled={busy} onClick={() => {
              if (window.confirm(`确认发布本次数据版本，并仅修订 ${item.symbol} 的报告输入？${candidate?.degraded ? '仍有数据缺口，将保留披露。' : ''}旧报告和其他持仓不覆盖。`)) {
                void perform(async () => {
                  if (job.freeze_status !== 'frozen' && candidate) {
                    await client.confirmReportEvidenceCollection(job.id, candidate.proposal_hash, Boolean(candidate.degraded))
                    setJobs((previous) => ({ ...previous, [key]: { ...job, freeze_status: 'frozen' } }))
                  }
                  await client.retryReportJob(jobId, { symbols: [item.symbol], refresh_inputs: true, confirmed: true })
                  await onRevised()
                })
              }
            }}>使用补证数据修订该股票报告</button>}
          </div>}
        </section>
      })}
    </div>}
  </div>
}
