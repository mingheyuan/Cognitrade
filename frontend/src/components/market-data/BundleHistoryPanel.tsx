import { Archive, ChevronRight, FileDiff, LockKeyhole, RefreshCw, UploadCloud } from 'lucide-react'
import { formatDateTime, jobStatusMeta, shortHash } from './status'
import { DefinitionList, EmptyState, LoadingState, StatusBadge } from './shared'
import type { MarketDataBundle, PageResult } from './types'

export function BundleHistoryPanel({ bundles, selected, loading, actionLoading, canPublish, onRefresh, onPublish, onSelect }: {
  bundles: PageResult<MarketDataBundle>
  selected: MarketDataBundle | null
  loading: boolean
  actionLoading: boolean
  canPublish: boolean
  onRefresh: () => Promise<unknown>
  onPublish: () => Promise<unknown>
  onSelect: (bundleId: string) => void
}) {
  const previous = selected ? bundles.items.find((item) => item.bundle_id !== selected.bundle_id) : null
  const previousItems = new Map((previous?.items || []).map((item) => [item.dimension_id, item.snapshot_id]))
  const changedItems = (selected?.items || []).filter((item) => previousItems.get(item.dimension_id) !== item.snapshot_id)
  return (
    <div className="mdc-bundle-layout">
      <section className="mdc-panel">
        <header className="mdc-panel-header">
          <div><span className="mdc-eyebrow">IMMUTABLE HISTORY</span><h2>Bundle 修订链</h2></div>
          <div className="mdc-panel-actions">
            <button className="secondary-button" type="button" disabled={loading} onClick={() => void onRefresh()}><RefreshCw className={loading ? 'is-spinning' : ''} size={14} />刷新</button>
            <button className="primary-button" type="button" disabled={actionLoading || !canPublish} onClick={() => void onPublish().catch(() => undefined)}><UploadCloud size={14} />发布当前 Bundle</button>
          </div>
        </header>
        {!canPublish && <p className="mdc-form-note">需要先让 22 个 UZI 采集槽全部形成日级快照；明确的数据源失败也可作为缺口快照冻结，交给 Agent 后续恢复。</p>}
        {loading && !bundles.items.length ? <LoadingState label="正在读取 Bundle 历史…" /> : null}
        {!loading && !bundles.items.length ? <EmptyState title="尚未发布 Bundle" detail="完成全部采集槽后可在此发布；已发布内容永不修改。" /> : null}
        <div className="mdc-bundle-list">
          {bundles.items.map((bundle) => {
            const status = jobStatusMeta(bundle.status)
            return (
              <button type="button" key={bundle.bundle_id} className={selected?.bundle_id === bundle.bundle_id ? 'is-current' : undefined} onClick={() => onSelect(bundle.bundle_id)}>
                <Archive size={18} />
                <span><strong>{bundle.as_of_date} · 修订 {bundle.revision || 1}</strong><small>{bundle.available_count + bundle.verified_empty_count} / {bundle.expected_dimension_count} · {formatDateTime(bundle.published_at)}</small></span>
                <StatusBadge label={status.label} tone={status.tone} />
                <ChevronRight size={16} />
              </button>
            )
          })}
        </div>
      </section>

      <section className="mdc-panel mdc-bundle-detail">
        <header className="mdc-panel-header"><div><span className="mdc-eyebrow">FROZEN MANIFEST</span><h2>冻结内容</h2></div><LockKeyhole size={20} /></header>
        {loading && !selected ? <LoadingState /> : null}
        {!selected && !loading ? <EmptyState title="选择一个 Bundle" detail="查看 manifest hash、引用关系和相邻修订差异。" /> : null}
        {selected ? (
          <>
            {selected.invalidated_at ? <div className="mdc-revoked-warning" role="alert">此 Bundle 已失效：{selected.invalidation_reason || '关联快照被撤销'}</div> : null}
            <DefinitionList items={[
              { label: 'Bundle ID', value: <code>{selected.bundle_id}</code> },
              { label: 'Manifest hash', value: <code title={selected.manifest_hash}>{shortHash(selected.manifest_hash)}</code> },
              { label: '契约', value: selected.contract_version },
              { label: '截止时点', value: selected.cutoff_at },
              { label: '被报告引用', value: `${selected.report_reference_count || 0} 次` },
              { label: '替代 Bundle', value: selected.supersedes_bundle_id || '—' },
            ]} />
            <div className="mdc-bundle-diff">
              <h3><FileDiff size={16} />与上一修订比较</h3>
              {!previous ? <p>这是当前列表中的首个修订。</p> : (
                <><p>{changedItems.length} 个槽位引用发生变化；原 Bundle 保持不可变。</p><ul>{changedItems.map((item) => <li key={item.dimension_id}><code>{item.dimension_id}</code><span>{shortHash(previousItems.get(item.dimension_id))} → {shortHash(item.snapshot_id)}</span></li>)}</ul></>
              )}
            </div>
            <div className="mdc-table-scroll"><table className="mdc-table"><thead><tr><th>槽位</th><th>快照</th><th>状态</th><th>内容 hash</th></tr></thead><tbody>{(selected.items || []).map((item) => <tr key={item.dimension_id}><td><code>{item.dimension_id}</code></td><td><code>{shortHash(item.snapshot_id)}</code></td><td>{item.status || '—'}</td><td><code>{shortHash(item.content_hash)}</code></td></tr>)}</tbody></table></div>
          </>
        ) : null}
      </section>
    </div>
  )
}
