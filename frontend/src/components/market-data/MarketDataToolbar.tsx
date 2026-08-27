import { CalendarDays, Database, FilePlus2, Play, RefreshCw, Search, Sparkles } from 'lucide-react'
import type { FormEvent } from 'react'
import type { MarketDataCenterController } from './useMarketDataCenter'

export function MarketDataToolbar({ controller, onManual, onReport }: {
  controller: MarketDataCenterController
  onManual: () => void
  onReport: () => void
}) {
  const { contract, loading, symbolDraft, urlState } = controller
  const submit = (event: FormEvent) => {
    event.preventDefault()
    controller.submitSymbol()
  }
  const createQuickJob = (mode: 'missing_or_stale' | 'force_all') => {
    void controller.createJob({
      symbols: [urlState.symbol],
      asset_family: controller.resolvedAssetFamily || urlState.asset_family,
      top_n: 10,
      start_date: urlState.as_of_date,
      end_date: urlState.as_of_date,
      dimensions: 'all',
      mode,
      max_concurrency: 5,
      publish_when_complete: true,
    }).then(() => controller.setUrlState({ tab: 'jobs' })).catch(() => undefined)
  }

  return (
    <section className="mdc-toolbar" aria-label="数据时点与操作">
      <form className="mdc-security-form" onSubmit={submit}>
        <label htmlFor="mdc-symbol"><span>代码或基金查询</span><div className="mdc-input-with-icon">
          <Search size={16} aria-hidden="true" />
          <input id="mdc-symbol" value={symbolDraft} onChange={(event) => controller.setSymbolDraft(event.target.value)} placeholder="600519.SH / 020464" autoComplete="off" />
        </div></label>
        <label htmlFor="mdc-asset-family"><span>资产类型</span><select id="mdc-asset-family" value={urlState.asset_family} onChange={(event) => controller.setUrlState({ asset_family: event.target.value as typeof urlState.asset_family })}>
          <option value="auto">自动识别</option><option value="equity">股票</option><option value="fund">基金</option>
        </select></label>
        <label htmlFor="mdc-date"><span>可见日期</span><div className="mdc-input-with-icon">
          <CalendarDays size={16} aria-hidden="true" />
          <input id="mdc-date" type="date" value={urlState.as_of_date} onChange={(event) => controller.setUrlState({ as_of_date: event.target.value })} />
        </div></label>
        <label htmlFor="mdc-cutoff"><span>截止时点</span><select id="mdc-cutoff" value={urlState.cutoff} onChange={(event) => controller.setUrlState({ cutoff: event.target.value })}>
          <option value="eod">市场本地日终</option><option value="now">当前时点</option>
        </select></label>
        <button className="primary-button" type="submit" disabled={loading.identity || loading.coverage || !symbolDraft.trim()}>
          {loading.identity || loading.coverage ? <RefreshCw className="is-spinning" size={15} aria-hidden="true" /> : <Database size={15} aria-hidden="true" />}
          解析并读取
        </button>
      </form>

      <div className="mdc-toolbar-side">
        {controller.resolvedInstrument ? (
          <div className="mdc-instrument-identity" aria-live="polite">
            <span>{controller.resolvedAssetFamily === 'fund' ? '基金持仓入口' : '股票标的'}</span>
            <strong>{controller.resolvedInstrument.name || controller.resolvedInstrument.symbol}</strong>
            <small>{controller.resolvedInstrument.symbol} · {controller.resolvedInstrument.market} · {controller.resolvedInstrument.security_type}</small>
          </div>
        ) : null}
        <div className="mdc-toolbar-actions" aria-label="显式数据操作">
          <button className="secondary-button" type="button" disabled={loading.action || !contract || !controller.resolvedInstrument} onClick={() => createQuickJob('missing_or_stale')}>
            <Sparkles size={15} aria-hidden="true" />补缺 / 补旧
          </button>
          <button className="secondary-button" type="button" disabled={loading.action || !contract || !controller.resolvedInstrument} onClick={() => createQuickJob('force_all')}>
            <RefreshCw size={15} aria-hidden="true" />全量刷新
          </button>
          <button className="secondary-button" type="button" disabled={loading.action || !contract} onClick={onManual}>
            <FilePlus2 size={15} aria-hidden="true" />手工录入
          </button>
          <button className="primary-button" type="button" disabled={loading.action || !controller.canFreezeReport} onClick={onReport} title={!controller.canFreezeReport ? controller.freezeBlockReason : undefined}>
            <Play size={15} aria-hidden="true" />{controller.resolvedAssetFamily === 'fund' ? '冻结十大持仓组合' : '冻结报告输入'}
          </button>
        </div>
        {!controller.canFreezeReport && controller.freezeBlockReason ? <p className="mdc-freeze-block-reason">冻结受阻：{controller.freezeBlockReason}</p> : null}
      </div>
    </section>
  )
}
