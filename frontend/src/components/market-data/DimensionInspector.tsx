import { AlertOctagon, ExternalLink, FileKey2, RotateCcw, ShieldCheck } from 'lucide-react'
import { dimensionStatusMeta, evidenceGradeLabel, formatDateTime, formatFreshness, shortHash } from './status'
import { DefinitionList, EmptyState, JsonTree, LoadingState, StatusBadge } from './shared'
import type { DimensionDefinition, DimensionCoverage, PageResult, SnapshotDetail, SnapshotSummary } from './types'

export function DimensionInspector({
  definition,
  coverage,
  snapshots,
  selectedSnapshot,
  loading,
  onSelectSnapshot,
  onManual,
  onResolveConflict,
  onRevoke,
}: {
  definition: DimensionDefinition | null
  coverage: DimensionCoverage | null
  snapshots: PageResult<SnapshotSummary>
  selectedSnapshot: SnapshotDetail | null
  loading: boolean
  onSelectSnapshot: (snapshotId: string) => void
  onManual: () => void
  onResolveConflict: (conflictId: string) => void
  onRevoke: (snapshot: SnapshotDetail) => void
}) {
  if (!definition) return <EmptyState title="选择采集槽" detail="从完整度矩阵选择一个槽位查看版本、字段来源和证据。" />
  const status = dimensionStatusMeta(coverage?.status)
  return (
    <section className="mdc-inspector" aria-labelledby="mdc-inspector-title">
      <header className="mdc-panel-header">
        <div>
          <span className="mdc-eyebrow">DATA &amp; EVIDENCE</span>
          <h2 id="mdc-inspector-title">{definition.label}</h2>
          <p>{definition.id} · {definition.description || '按当前契约执行结构、时间与来源校验。'}</p>
        </div>
        <div className="mdc-panel-actions">
          <StatusBadge label={status.label} tone={status.tone} />
          <button className="secondary-button" type="button" onClick={onManual}><FileKey2 size={14} />手工证据</button>
        </div>
      </header>

      <div className="mdc-contract-strip">
        <DefinitionList items={[
          { label: '快照类型', value: definition.snapshot_type || '—' },
          { label: '当前日 freshness', value: formatFreshness(definition.freshness_seconds) },
          { label: '最低证据数', value: String(definition.min_evidence_count || 1) },
          { label: '历史窗口', value: definition.history_requirement || '由后端契约校验' },
          { label: '必需字段', value: definition.required_fields?.join(' · ') || '由后端契约校验' },
          { label: 'verified_empty', value: definition.verified_empty_requirement || definition.verified_empty_policy || 'never' },
        ]} />
      </div>

      <div className="mdc-inspector-layout">
        <aside className="mdc-version-list" aria-label="不可变快照版本">
          <header><strong>快照版本</strong><span>{snapshots.total_count}</span></header>
          {loading && !snapshots.items.length ? <LoadingState label="正在读取快照…" /> : null}
          {!loading && !snapshots.items.length ? <EmptyState title="没有已落库版本" detail="可显式采集或录入带证据的历史数据。" /> : null}
          {snapshots.items.map((snapshot) => {
            const itemStatus = dimensionStatusMeta(snapshot.status)
            return (
              <button
                key={snapshot.snapshot_id}
                type="button"
                className={selectedSnapshot?.snapshot_id === snapshot.snapshot_id ? 'is-current' : undefined}
                onClick={() => onSelectSnapshot(snapshot.snapshot_id)}
              >
                <span><StatusBadge label={itemStatus.label} tone={itemStatus.tone} /><small>{snapshot.evidence_grade || '—'} 级</small></span>
                <strong>{formatDateTime(snapshot.published_at)}</strong>
                <code>{shortHash(snapshot.content_hash || snapshot.snapshot_id)}</code>
              </button>
            )
          })}
        </aside>

        <div className="mdc-snapshot-detail">
          {loading && !selectedSnapshot ? <LoadingState label="正在验证快照内容…" /> : null}
          {!selectedSnapshot && !loading ? <EmptyState title="没有可审计快照" detail={coverage?.reason || '该槽位尚未落库。'} /> : null}
          {selectedSnapshot ? (
            <>
              <div className="mdc-snapshot-banner">
                <div>
                  <ShieldCheck size={18} aria-hidden="true" />
                  <span><strong>{evidenceGradeLabel(selectedSnapshot.evidence_grade)}</strong><small>content hash {shortHash(selectedSnapshot.content_hash)}</small></span>
                </div>
                {!selectedSnapshot.revoked_at && (
                  <button className="mdc-danger-button" type="button" onClick={() => onRevoke(selectedSnapshot)}><RotateCcw size={14} />撤销快照</button>
                )}
              </div>
              {selectedSnapshot.revoked_at && (
                <div className="mdc-revoked-warning" role="alert">
                  <AlertOctagon size={17} /><span>该版本已于 {formatDateTime(selectedSnapshot.revoked_at)} 撤销：{selectedSnapshot.revoke_reason || '未填写原因'}</span>
                </div>
              )}
              <DefinitionList items={[
                { label: '经济生效时间', value: formatDateTime(selectedSnapshot.effective_at) },
                { label: '首次公开时间', value: formatDateTime(selectedSnapshot.published_at) },
                { label: '系统观测时间', value: formatDateTime(selectedSnapshot.observed_at) },
                { label: '数据库写入时间', value: formatDateTime(selectedSnapshot.ingested_at) },
                { label: '来源 Provider', value: selectedSnapshot.source_provider || '—' },
                { label: '替代版本', value: selectedSnapshot.superseded_by_id || '—' },
              ]} />

              {!!selectedSnapshot.conflicts?.length && (
                <section className="mdc-conflict-list" aria-labelledby="mdc-conflict-title">
                  <h3 id="mdc-conflict-title"><AlertOctagon size={16} />未解决冲突</h3>
                  {selectedSnapshot.conflicts.map((conflict) => (
                    <article key={conflict.conflict_id}>
                      <div><strong>{conflict.field_path}</strong><span>{conflict.reason || `${conflict.candidates?.length || 0} 个候选值不一致`}</span></div>
                      <button className="secondary-button" type="button" onClick={() => onResolveConflict(conflict.conflict_id)}>查看并解决</button>
                    </article>
                  ))}
                </section>
              )}

              <details className="mdc-audit-section" open>
                <summary>规范化字段 <span>payload</span></summary>
                <JsonTree value={selectedSnapshot.payload} />
              </details>
              <details className="mdc-audit-section">
                <summary>字段级来源 <span>field provenance</span></summary>
                <JsonTree value={selectedSnapshot.field_provenance} />
              </details>
              <details className="mdc-audit-section">
                <summary>查询证明 <span>query manifest</span></summary>
                <JsonTree value={selectedSnapshot.query_manifest} />
              </details>
              <details className="mdc-audit-section">
                <summary>扩展审计结果 <span>validation</span></summary>
                <JsonTree value={selectedSnapshot.validation} />
              </details>
              <section className="mdc-evidence-list" aria-labelledby="mdc-evidence-title">
                <h3 id="mdc-evidence-title">证据链 <span>{selectedSnapshot.evidence?.length || 0}</span></h3>
                {!selectedSnapshot.evidence?.length ? <p>此版本没有关联证据，不能用于正式 Bundle。</p> : null}
                {selectedSnapshot.evidence?.map((evidence) => (
                  <article key={evidence.evidence_id}>
                    <span className={`mdc-evidence-grade is-${(evidence.grade || 'none').toLowerCase()}`}>{evidence.grade || '—'}</span>
                    <div>
                      <strong>{evidence.provider || '未知来源'}</strong>
                      <small>公开 {formatDateTime(evidence.published_at)} · 观测 {formatDateTime(evidence.observed_at)}</small>
                      <code>{shortHash(evidence.content_hash)}</code>
                    </div>
                    {evidence.source_url ? <a href={evidence.source_url} target="_blank" rel="noreferrer" aria-label="打开原始证据"><ExternalLink size={15} /></a> : null}
                  </article>
                ))}
              </section>
            </>
          ) : null}
        </div>
      </div>
    </section>
  )
}
