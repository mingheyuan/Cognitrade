import { ChevronRight, CircleAlert } from 'lucide-react'
import { dimensionStatusMeta, evidenceGradeLabel, formatFreshness, freshnessStatusMeta } from './status'
import { StatusBadge } from './shared'
import type { MarketDataContract, MarketDataCoverage } from './types'

export function DimensionReadinessMatrix({ contract, coverage, selected, onSelect }: {
  contract: MarketDataContract
  coverage: MarketDataCoverage
  selected: string
  onSelect: (dimensionId: string) => void
}) {
  const coverageById = new Map(coverage.dimensions.map((item) => [item.dimension_id, item]))
  return (
    <section className="mdc-panel" aria-labelledby="mdc-matrix-title">
      <header className="mdc-panel-header">
        <div>
          <span className="mdc-eyebrow">DYNAMIC CONTRACT</span>
          <h2 id="mdc-matrix-title">{contract.asset_family === 'fund' ? '基金持仓入口完整度' : '采集槽完整度'}</h2>
        </div>
        <div className="mdc-count-strip" aria-label="完整度统计">
          <span className="is-good">可用 {coverage.counts.available}</span>
          <span className="is-info">空值 {coverage.counts.verified_empty}</span>
          <span className="is-warning">缺口 {coverage.counts.data_gap}</span>
          <span className="is-danger">冲突 {coverage.counts.conflict}</span>
          <span>过期 {coverage.counts.stale}</span>
        </div>
      </header>
      <div className="mdc-table-scroll">
        <table className="mdc-table mdc-dimension-table">
          <thead>
            <tr>
              <th scope="col">槽位</th>
              <th scope="col">数据状态</th>
              <th scope="col">证据</th>
              <th scope="col">时效</th>
              <th scope="col">完整性说明</th>
              <th scope="col"><span className="sr-only">查看详情</span></th>
            </tr>
          </thead>
          <tbody>
            {contract.dimensions.map((definition) => {
              const item = coverageById.get(definition.id)
              const status = dimensionStatusMeta(item?.status)
              const freshness = freshnessStatusMeta(item?.freshness_status)
              return (
                <tr key={definition.id} className={selected === definition.id ? 'is-selected' : undefined}>
                  <th scope="row">
                    <button type="button" onClick={() => onSelect(definition.id)} aria-current={selected === definition.id ? 'true' : undefined}>
                      <span>{String(definition.order + 1).padStart(2, '0')}</span>
                      <span><strong>{definition.label}</strong><small>{definition.id}</small></span>
                    </button>
                  </th>
                  <td><StatusBadge label={status.label} tone={status.tone} /></td>
                  <td><span className={`mdc-evidence-grade is-${(item?.evidence_grade || 'none').toLowerCase()}`} title={evidenceGradeLabel(item?.evidence_grade)}>{item?.evidence_grade || '—'}</span></td>
                  <td>
                    <StatusBadge label={freshness.label} tone={freshness.tone} />
                    <small className="mdc-cell-note">TTL {formatFreshness(definition.freshness_seconds)}</small>
                  </td>
                  <td>
                    <span className="mdc-reason">{item?.reason || definition.description || '该槽位尚无状态说明'}</span>
                    {item?.status === 'data_gap' && <CircleAlert size={14} aria-label="该槽位阻止正式报告" />}
                  </td>
                  <td><button className="mdc-row-action" type="button" onClick={() => onSelect(definition.id)} aria-label={`查看 ${definition.label} 数据与证据`}><ChevronRight size={16} /></button></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
