import { AlertTriangle, FileLock2, ShieldAlert, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import type { CollectionJob, DimensionDefinition, ExpandedFundHolding, ManualSnapshotInput, MarketDataConflict, ReportManifestResult, ResolvedAssetFamily, SnapshotDetail } from './types'

function DialogFrame({ title, description, children, onClose, danger = false }: {
  title: string
  description: string
  children: ReactNode
  onClose: () => void
  danger?: boolean
}) {
  const titleRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    titleRef.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])
  return (
    <div className="mdc-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className={`mdc-dialog ${danger ? 'is-danger' : ''}`} role="dialog" aria-modal="true" aria-labelledby="mdc-dialog-title" aria-describedby="mdc-dialog-description">
        <header>
          <span>{danger ? <ShieldAlert size={20} /> : <FileLock2 size={20} />}</span>
          <div><h2 id="mdc-dialog-title" ref={titleRef} tabIndex={-1}>{title}</h2><p id="mdc-dialog-description">{description}</p></div>
          <button type="button" onClick={onClose} aria-label="关闭对话框"><X size={18} /></button>
        </header>
        {children}
      </section>
    </div>
  )
}

function datetimeLocalValue(date = new Date()) {
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function toIso(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString()
}

export function ManualSnapshotDialog({ symbol, market, asOfDate, cutoffAt, dimensions, initialDimension, busy, onClose, onSubmit }: {
  symbol: string
  market: string
  asOfDate: string
  cutoffAt?: string
  dimensions: DimensionDefinition[]
  initialDimension: string
  busy: boolean
  onClose: () => void
  onSubmit: (payload: ManualSnapshotInput) => Promise<unknown>
}) {
  const [dimensionId, setDimensionId] = useState(initialDimension || dimensions[0]?.id || '')
  const [status, setStatus] = useState<'available' | 'verified_empty'>('available')
  const [effectiveAt, setEffectiveAt] = useState(`${asOfDate}T00:00`)
  const [publishedAt, setPublishedAt] = useState(datetimeLocalValue())
  const [observedAt, setObservedAt] = useState(datetimeLocalValue())
  const [provider, setProvider] = useState('manual.evidence')
  const [sourceUrl, setSourceUrl] = useState('')
  const [reason, setReason] = useState('')
  const [payloadText, setPayloadText] = useState('{}')
  const [fieldSourcesText, setFieldSourcesText] = useState('{\n  "$": "manual.evidence"\n}')
  const [manifestText, setManifestText] = useState(() => JSON.stringify({
    query_window: { start: asOfDate, end: asOfDate },
    required_sources: ['manual.evidence'],
    sources: [{ source: 'manual.evidence', status: 'success', result_count: 0 }],
    result_count: 0,
  }, null, 2))
  const [proofConfirmed, setProofConfirmed] = useState(false)
  const [validationError, setValidationError] = useState('')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    try {
      const payload = JSON.parse(payloadText) as ManualSnapshotInput['payload']
      const fieldSources = JSON.parse(fieldSourcesText) as ManualSnapshotInput['field_sources']
      const queryManifest = JSON.parse(manifestText) as ManualSnapshotInput['query_manifest']
      if (!payload || Array.isArray(payload) || typeof payload !== 'object') throw new Error('payload 必须是 JSON 对象')
      if (!fieldSources || Array.isArray(fieldSources) || typeof fieldSources !== 'object') throw new Error('field_sources 必须是 JSON 对象')
      if (!queryManifest || Array.isArray(queryManifest) || typeof queryManifest !== 'object') throw new Error('query_manifest 必须是 JSON 对象')
      if (!proofConfirmed) throw new Error('必须确认存在可验证的不可变历史证据')
      setValidationError('')
      void onSubmit({
        symbol,
        market,
        dimension_id: dimensionId,
        as_of_date: asOfDate,
        cutoff_at: cutoffAt || `${asOfDate}T23:59:59+08:00`,
        status,
        evidence_grade: 'B',
        effective_at: toIso(effectiveAt),
        published_at: toIso(publishedAt),
        reason,
        empty_reason: status === 'verified_empty' ? reason : undefined,
        payload,
        field_sources: fieldSources,
        query_manifest: queryManifest,
        evidence: [{
          provider,
          source_url: sourceUrl,
          source_kind: 'manual_document',
          published_at: toIso(publishedAt),
          observed_at: toIso(observedAt),
          grade: 'B',
          immutable_proof: true,
          is_manual: true,
          metadata: { manual_reason: reason },
        }],
      }).then(onClose).catch(() => undefined)
    } catch (error) {
      setValidationError(error instanceof Error ? `JSON 无法解析：${error.message}` : 'JSON 无法解析')
    }
  }
  return (
    <DialogFrame title="录入带证据的不可变快照" description="人工数据最高为 B 级，不能绕过时间、来源、字段或 verified_empty 证明门槛。" onClose={onClose}>
      <form className="mdc-dialog-form" onSubmit={submit}>
        <div className="mdc-form-grid">
          <label>采集槽<select value={dimensionId} onChange={(event) => setDimensionId(event.target.value)}>{dimensions.map((dimension) => <option key={dimension.id} value={dimension.id}>{dimension.label} · {dimension.id}</option>)}</select></label>
          <label>数据状态<select value={status} onChange={(event) => setStatus(event.target.value as 'available' | 'verified_empty')}><option value="available">available · 有结构化数据</option><option value="verified_empty">verified_empty · 已证明为空</option></select></label>
          <label>证据等级<input value="B · 人工数据最高等级" readOnly /></label>
          <label>Provider<input value={provider} onChange={(event) => setProvider(event.target.value)} required /></label>
          <label>经济生效时间<input type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} required /></label>
          <label>首次公开时间<input type="datetime-local" value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} required /></label>
          <label>系统观测时间<input type="datetime-local" value={observedAt} onChange={(event) => setObservedAt(event.target.value)} required /></label>
          <label>证据 URL<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" required /></label>
        </div>
        <label>录入原因<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} required placeholder="说明为何需要人工补录以及如何证明截止日前可见" /></label>
        <label>规范化 payload<textarea className="mdc-code-input" value={payloadText} onChange={(event) => setPayloadText(event.target.value)} rows={8} spellCheck={false} required /></label>
        <label>字段来源 field_sources<textarea className="mdc-code-input" value={fieldSourcesText} onChange={(event) => setFieldSourcesText(event.target.value)} rows={5} spellCheck={false} required /></label>
        <label>{status === 'verified_empty' ? '空值查询证明' : '查询清单'} query_manifest<textarea className="mdc-code-input" value={manifestText} onChange={(event) => setManifestText(event.target.value)} rows={8} spellCheck={false} required /></label>
        <label className="mdc-check-row"><input type="checkbox" checked={proofConfirmed} onChange={(event) => setProofConfirmed(event.target.checked)} required /><span>我确认该证据不可变，且能证明内容在报告截止时点前已经公开。</span></label>
        {validationError ? <p className="mdc-form-error" role="alert">{validationError}</p> : null}
        <footer><button className="secondary-button" type="button" onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={busy || !dimensionId || !proofConfirmed}>{busy ? '正在校验并落库…' : '提交严格校验'}</button></footer>
      </form>
    </DialogFrame>
  )
}

export function ConflictResolutionDialog({ conflict, busy, onClose, onSubmit }: {
  conflict: MarketDataConflict
  busy: boolean
  onClose: () => void
  onSubmit: (candidateId: string, reason: string) => Promise<unknown>
}) {
  const [candidateId, setCandidateId] = useState(conflict.candidates?.[0]?.candidate_id || '')
  const [reason, setReason] = useState('')
  return (
    <DialogFrame title={`解决字段冲突：${conflict.field_path}`} description="决议会写入审计记录并触发完整度重算；原候选值和证据不会被删除。" onClose={onClose} danger>
      <form className="mdc-dialog-form" onSubmit={(event) => { event.preventDefault(); void onSubmit(candidateId, reason).then(onClose).catch(() => undefined) }}>
        <fieldset><legend>选择确定性候选值</legend>{conflict.candidates?.map((candidate) => <label className="mdc-candidate" key={candidate.candidate_id}><input type="radio" name="candidate" value={candidate.candidate_id} checked={candidateId === candidate.candidate_id} onChange={() => setCandidateId(candidate.candidate_id)} /><span><strong>{String(candidate.value)}</strong><small>{candidate.provider || '未知来源'} · {candidate.evidence_id || '无证据 ID'}</small></span></label>)}</fieldset>
        <label>决议依据<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={4} required /></label>
        <footer><button className="secondary-button" type="button" onClick={onClose}>取消</button><button className="mdc-danger-button" type="submit" disabled={busy || !candidateId || !reason.trim()}>保存冲突决议</button></footer>
      </form>
    </DialogFrame>
  )
}

export function RevokeSnapshotDialog({ snapshot, busy, onClose, onSubmit }: {
  snapshot: SnapshotDetail
  busy: boolean
  onClose: () => void
  onSubmit: (reason: string, supersededById?: string) => Promise<unknown>
}) {
  const [reason, setReason] = useState('')
  const [replacement, setReplacement] = useState('')
  return (
    <DialogFrame title="撤销不可变快照" description="不会物理删除数据。关联 Bundle 与报告将显示警告，并退出有效回测集合。" onClose={onClose} danger>
      <form className="mdc-dialog-form" onSubmit={(event) => { event.preventDefault(); void onSubmit(reason, replacement || undefined).then(onClose).catch(() => undefined) }}>
        <div className="mdc-impact-warning"><AlertTriangle size={18} /><span><strong>{snapshot.dimension_id}</strong><code>{snapshot.snapshot_id}</code></span></div>
        <label>撤销原因<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={4} required /></label>
        <label>替代快照 ID（可选）<input value={replacement} onChange={(event) => setReplacement(event.target.value)} /></label>
        <footer><button className="secondary-button" type="button" onClick={onClose}>取消</button><button className="mdc-danger-button" type="submit" disabled={busy || !reason.trim()}>确认撤销并保留审计链</button></footer>
      </form>
    </DialogFrame>
  )
}

export function ConfirmBundleFreezeDialog({ job, busy, onClose, onSubmit }: {
  job: CollectionJob
  busy: boolean
  onClose: () => void
  onSubmit: (acknowledgeDegraded: boolean) => Promise<unknown>
}) {
  const [confirmed, setConfirmed] = useState(false)
  const candidate = job.publication_candidate
  const scopes = candidate?.previews?.length ? candidate.previews : candidate?.scopes || []
  const qualityGate = String(candidate?.quality_gate || 'strict').toLowerCase()
  const degraded = Boolean(candidate?.degraded) || qualityGate.includes('degrad') || Boolean(candidate?.missing_dimensions?.length)
  return (
    <DialogFrame
      title="确认冻结不可变 Bundle"
      description="这是采集流程的提交边界：已落库快照会被固定为不可变 Bundle。后续补采不会修改这个版本，只能发布新的修订。"
      onClose={onClose}
      danger={degraded}
    >
      <form className="mdc-dialog-form" onSubmit={(event) => {
        event.preventDefault()
        if (!confirmed) return
        void onSubmit(degraded).then(onClose).catch(() => undefined)
      }}>
        <div className="mdc-freeze-candidate-summary">
          <span><strong>{job.asset_family === 'fund' ? '基金前十大持仓组合' : `${job.symbols.length} 只股票`}</strong><small>{job.start_date} — {job.end_date}</small></span>
          <code>{candidate?.quality_gate || 'strict'}</code>
        </div>
        {scopes.length ? <ol className="mdc-freeze-scope-list" aria-label="将发布的 Bundle 范围">{scopes.slice(0, 12).map((scope, index) => (
          <li key={`${scope.symbol || 'scope'}-${scope.as_of_date || index}`}>
            <span>{scope.symbol || `范围 ${index + 1}`}</span>
            <small>{scope.as_of_date || job.start_date}{scope.quality ? ` · ${scope.quality}` : ''}</small>
            <strong>{scope.can_publish === false ? '不可发布' : '候选就绪'}</strong>
          </li>
        ))}</ol> : null}
        {candidate?.missing_dimensions?.length ? (
          <div className="mdc-freeze-degraded-warning" role="alert">
            <AlertTriangle size={18} />
            <span><strong>包含降级披露</strong><small>{candidate.missing_dimensions.length} 个维度缺口：{candidate.missing_dimensions.slice(0, 6).join('、')}{candidate.missing_dimensions.length > 6 ? '…' : ''}</small></span>
          </div>
        ) : null}
        <label className="mdc-check-row mdc-freeze-confirmation"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>{degraded ? '我已阅读缺口与降级披露，仍确认冻结当前候选版本' : '我确认将当前采集草稿发布为不可变 Bundle'}</span></label>
        <footer><button className="secondary-button" type="button" onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={busy || !confirmed}>{busy ? '正在冻结 Bundle…' : '确认冻结 Bundle'}</button></footer>
      </form>
    </DialogFrame>
  )
}

export function ReportLaunchDialog({ symbol, asOfDate, bundleId, assetFamily, holdings, busy, onClose, onSubmit, onLaunch }: {
  symbol: string
  asOfDate: string
  bundleId: string
  assetFamily: ResolvedAssetFamily
  holdings: ExpandedFundHolding[]
  busy: boolean
  onClose: () => void
  onSubmit: (profile: 'lite' | 'medium' | 'deep') => Promise<ReportManifestResult>
  onLaunch: (profile: 'lite' | 'medium' | 'deep', manifest: ReportManifestResult) => Promise<unknown>
}) {
  const [profile, setProfile] = useState<'lite' | 'medium' | 'deep'>('lite')
  const [frozen, setFrozen] = useState<ReportManifestResult | null>(null)
  const [launching, setLaunching] = useState(false)
  const options = useMemo(() => [
    { id: 'lite' as const, label: 'Lite', detail: '完整数据合同，精简推理与输出' },
    { id: 'medium' as const, label: 'Medium', detail: '完整数据合同，中等深度工作流' },
    { id: 'deep' as const, label: 'Deep', detail: '完整数据合同，全量机构级方法' },
  ], [])
  const isFund = assetFamily === 'fund'
  return (
    <DialogFrame title={isFund ? '创建前十大股票组合 Manifest' : '创建正式报告 Manifest'} description={isFund ? '基金 Bundle 负责证明前十大股票的选择与权重；基金代码不会被当成股票分析。后端会把已确认的 10 只股票 Bundle 绑定进同一不可变 Manifest。报告阶段外部数据调用数必须为 0。' : '从已确认冻结的 Bundle 创建不可变报告 Manifest，再用 Manifest ID 与 hash 精确启动报告；报告阶段外部数据调用数必须为 0。'} onClose={onClose}>
      <form className="mdc-dialog-form" onSubmit={(event) => {
        event.preventDefault()
        if (frozen) return
        void onSubmit(profile).then(setFrozen).catch(() => undefined)
      }}>
        <div className="mdc-manifest-preview"><span>{symbol} · {asOfDate}{isFund ? ` · ${holdings.length} 只股票` : ''}</span><code>{bundleId}</code></div>
        {isFund ? <ol className="mdc-manifest-holdings" aria-label="将被冻结的前十大股票">{holdings.map((holding) => <li key={holding.symbol}><span>{holding.rank}. {holding.name || holding.symbol}</span><code>{holding.symbol}</code><strong>{holding.fund_weight_pct == null ? '—' : `${holding.fund_weight_pct.toFixed(2)}%`}</strong></li>)}</ol> : null}
        {!frozen ? <fieldset><legend>{isFund ? '组合内股票报告强度' : '报告强度'}</legend>{options.map((option) => <label className="mdc-candidate" key={option.id}><input type="radio" name="profile" value={option.id} checked={profile === option.id} onChange={() => setProfile(option.id)} /><span><strong>{option.label}</strong><small>{option.detail}</small></span></label>)}</fieldset> : (
          <div className="mdc-manifest-ready" role="status">
            <FileLock2 size={20} aria-hidden="true" />
            <span><strong>报告输入已冻结</strong><small>后续运行将校验此 Manifest 的数据库归属与 hash。</small></span>
            <code>{frozen.manifest_id}</code>
          </div>
        )}
        <footer>
          <button className="secondary-button" type="button" onClick={onClose}>{frozen ? '稍后生成' : '取消'}</button>
          {!frozen ? (
            <button className="primary-button" type="submit" disabled={busy}>{busy ? '正在创建 Manifest…' : isFund ? `创建十大持仓 · ${profile.toUpperCase()} Manifest` : `创建 ${profile.toUpperCase()} Manifest`}</button>
          ) : (
            <button className="primary-button" type="button" disabled={launching} onClick={() => {
              setLaunching(true)
              void onLaunch(profile, frozen).catch(() => setLaunching(false))
            }}>{launching ? '正在启动报告…' : `使用此数据生成 ${profile.toUpperCase()} 报告`}</button>
          )}
        </footer>
      </form>
    </DialogFrame>
  )
}
