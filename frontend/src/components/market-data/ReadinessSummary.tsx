import { Archive, CheckCircle2, CircleDashed, Clock3, FileLock2, ShieldAlert } from 'lucide-react'
import type { MarketDataContract, MarketDataCoverage } from './types'
import { shortHash } from './status'
import { StatusBadge } from './shared'

export function ReadinessSummary({ contract, coverage }: { contract: MarketDataContract; coverage: MarketDataCoverage }) {
  const ready = coverage.counts.available + coverage.counts.verified_empty
  const expected = coverage.expected_dimension_count || contract.expected_dimension_count
  const progress = expected ? Math.round((ready / expected) * 100) : 0
  const isFund = contract.asset_family === 'fund'
  return (
    <section className="mdc-readiness" aria-labelledby="mdc-readiness-title">
      <div className="mdc-readiness-primary">
        <div className={`mdc-readiness-ring ${coverage.report_ready ? 'is-ready' : ''}`} style={{ '--readiness': `${progress * 3.6}deg` } as React.CSSProperties}>
          <span><strong>{ready}</strong><small>/ {expected}</small></span>
        </div>
        <div>
          <span className="mdc-eyebrow">POINT-IN-TIME COVERAGE</span>
          <h2 id="mdc-readiness-title">{coverage.symbol} · {coverage.as_of_date}</h2>
          <p>{isFund ? `截止 ${coverage.cutoff_at || '市场本地日终'}，此处只验证基金持仓披露入口；正式组合还要求展开的 10 只股票 Bundle 全部就绪。` : `截止 ${coverage.cutoff_at || '市场本地日终'}，仅 A/B 级证据、无未来数据且无未解决冲突才计入。`}</p>
        </div>
      </div>
      <div className="mdc-readiness-gates">
        <article>
          {coverage.structurally_complete ? <CheckCircle2 size={18} /> : <CircleDashed size={18} />}
          <span>结构完整</span>
          <StatusBadge label={coverage.structurally_complete ? '通过' : '未通过'} tone={coverage.structurally_complete ? 'good' : 'warning'} />
        </article>
        <article>
          {coverage.report_ready ? <FileLock2 size={18} /> : <ShieldAlert size={18} />}
          <span>{isFund ? '持仓入口' : '报告就绪'}</span>
          <StatusBadge label={coverage.report_ready ? (isFund ? '父 Bundle 可用' : '可冻结') : (isFund ? '持仓槽未就绪' : '禁止正式报告')} tone={coverage.report_ready ? 'good' : 'danger'} />
        </article>
        <article>
          <Archive size={18} />
          <span>最新 Bundle</span>
          <strong title={coverage.latest_bundle?.manifest_hash || undefined}>{coverage.latest_bundle ? shortHash(coverage.latest_bundle.manifest_hash || coverage.latest_bundle.bundle_id) : '尚未发布'}</strong>
        </article>
        <article>
          <Clock3 size={18} />
          <span>契约版本</span>
          <strong>{coverage.contract_version || contract.contract_version}</strong>
        </article>
      </div>
    </section>
  )
}
