import { ArchiveRestore, CheckCircle2, PlugZap, RefreshCw, ServerOff, ShieldCheck } from 'lucide-react'
import { useMemo, useRef } from 'react'
import { formatDateTime } from './status'
import { EmptyState, LoadingState, StatusBadge } from './shared'
import type { MarketDataProvider, MarketDataSubscription, PageResult } from './types'

function exchangeTimezone(market: string) {
  const normalized = market.trim().toUpperCase()
  if (normalized.startsWith('HK')) return 'Asia/Hong_Kong'
  if (normalized.startsWith('US')) return 'America/New_York'
  return 'Asia/Shanghai'
}

export function ProviderSubscriptionPanel({ symbol, market, providers, subscriptions, loading, actionLoading, onRefresh, onUpdate, onTest }: {
  symbol: string
  market: string
  providers: PageResult<MarketDataProvider>
  subscriptions: PageResult<MarketDataSubscription>
  loading: boolean
  actionLoading: boolean
  onRefresh: () => Promise<unknown>
  onUpdate: (subscription: MarketDataSubscription) => Promise<unknown>
  onTest: (providerId: string) => Promise<unknown>
}) {
  const current = useMemo(() => subscriptions.items.find((item) => item.symbol === symbol), [subscriptions.items, symbol])
  const storedSchedule = current?.schedule_time || '23:15'
  const scheduleRef = useRef<HTMLInputElement>(null)
  const timezone = current?.timezone || exchangeTimezone(market)
  const update = (enabled: boolean) => {
    void onUpdate({
      ...current,
      symbol,
      market: current?.market || market || 'CN',
      enabled,
      schedule_time: scheduleRef.current?.value || storedSchedule,
      timezone,
      dimensions: current?.dimensions || 'all',
      max_concurrency: current?.max_concurrency || 5,
    }).catch(() => undefined)
  }
  return (
    <div className="mdc-archive-layout">
      <section className="mdc-panel mdc-subscription-card">
        <header className="mdc-panel-header">
          <div><span className="mdc-eyebrow">OPT-IN DAILY ARCHIVE</span><h2>日级归档订阅</h2></div>
          <ArchiveRestore size={20} />
        </header>
        <p className="mdc-form-note">默认关闭。只有你在此显式开启后，调度器才会按日创建采集任务；失败时仅重试失败槽。</p>
        <div className="mdc-subscription-state">
          <span className={`mdc-toggle-indicator ${current?.enabled ? 'is-enabled' : ''}`} aria-hidden="true" />
          <div><strong>{symbol}</strong><span>{current?.enabled ? (current.scheduler_enabled === false ? '订阅已保存；全局调度关闭' : '归档已开启') : '归档未开启'}</span></div>
          <StatusBadge label={current?.enabled ? (current.scheduler_enabled === false ? '等待全局开关' : '显式启用') : '默认关闭'} tone={current?.enabled && current.scheduler_enabled !== false ? 'good' : 'muted'} />
        </div>
        <div className="mdc-form-grid">
          <label>执行时间<input key={`${symbol}:${storedSchedule}`} ref={scheduleRef} type="time" defaultValue={storedSchedule} /></label>
          <label>交易所时区<input value={timezone} readOnly aria-readonly="true" /></label>
        </div>
        <div className="mdc-panel-actions">
          <button className={current?.enabled ? 'mdc-danger-button' : 'primary-button'} type="button" disabled={actionLoading} onClick={() => update(!current?.enabled)}>
            {current?.enabled ? '关闭日级归档' : '显式开启归档'}
          </button>
          {current?.enabled && <button className="secondary-button" type="button" disabled={actionLoading} onClick={() => update(true)}>保存执行时间</button>}
        </div>
        <dl className="mdc-mini-audit">
          <div><dt>上次运行</dt><dd>{formatDateTime(current?.last_run_at)}</dd></div>
          <div><dt>上次状态</dt><dd>{current?.last_status || '尚未运行'}</dd></div>
          <div><dt>失败原因</dt><dd>{current?.last_error || '—'}</dd></div>
        </dl>
      </section>

      <section className="mdc-panel mdc-provider-panel">
        <header className="mdc-panel-header">
          <div><span className="mdc-eyebrow">CAPABILITY, NOT SECRETS</span><h2>数据源能力</h2></div>
          <button className="secondary-button" type="button" disabled={loading} onClick={() => void onRefresh()}><RefreshCw className={loading ? 'is-spinning' : ''} size={14} />刷新状态</button>
        </header>
        <p className="mdc-form-note">这里只展示是否安装/配置，不返回 Cookie、密钥或凭据内容。配置检查不联网；实际可用性只由显式采集任务证明。</p>
        {loading && !providers.items.length ? <LoadingState label="正在读取 Provider 能力…" /> : null}
        {!loading && !providers.items.length ? <EmptyState title="没有 Provider 状态" detail="后端尚未注册可用数据源适配器。" /> : null}
        <div className="mdc-provider-grid">
          {providers.items.map((provider) => {
            const capable = provider.installed && provider.configured
            return (
              <article key={provider.provider_id} className={capable ? 'is-capable' : 'is-gap'}>
                <header>
                  <span>{capable ? <PlugZap size={18} /> : <ServerOff size={18} />}</span>
                  <div><strong>{provider.label}</strong><code>{provider.provider_id}</code></div>
                  <StatusBadge label={capable ? '配置就绪' : '能力缺口'} tone={capable ? 'good' : 'warning'} />
                </header>
                <div className="mdc-provider-flags">
                  <span>{provider.installed ? <CheckCircle2 size={13} /> : <ServerOff size={13} />}依赖{provider.installed ? '已安装' : '未安装'}</span>
                  <span>{provider.configured ? <ShieldCheck size={13} /> : <ServerOff size={13} />}配置{provider.configured ? '已就绪' : '缺失'}</span>
                </div>
                {!!provider.capabilities?.length && <p>{provider.capabilities.join(' · ')}</p>}
                {!!provider.missing_requirements?.length && <p className="is-warning">缺少：{provider.missing_requirements.join('、')}</p>}
                {provider.last_error ? <p className="is-error">配置检查：{provider.last_error}</p> : null}
                <footer><small>{provider.last_tested_at ? `检查于 ${formatDateTime(provider.last_tested_at)}` : '尚未手动检查'}</small><button className="secondary-button" type="button" disabled={!capable || actionLoading} onClick={() => void onTest(provider.provider_id).catch(() => undefined)}>检查配置</button></footer>
              </article>
            )
          })}
        </div>
      </section>
    </div>
  )
}
