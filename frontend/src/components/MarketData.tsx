import {
  Archive,
  ClipboardCheck,
  Database,
  FileSearch,
  Layers3,
  ListChecks,
  ShieldCheck,
  X,
} from 'lucide-react'
import { type Dispatch, type SetStateAction, useCallback, useMemo, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import type { ResearchIntent } from '../research-contracts'
import { BundleHistoryPanel } from './market-data/BundleHistoryPanel'
import { CollectionJobsPanel } from './market-data/CollectionJobsPanel'
import { DimensionInspector } from './market-data/DimensionInspector'
import { DimensionReadinessMatrix } from './market-data/DimensionReadinessMatrix'
import {
  ConflictResolutionDialog,
  ManualSnapshotDialog,
  ReportLaunchDialog,
  RevokeSnapshotDialog,
} from './market-data/dialogs'
import { MarketDataToolbar } from './market-data/MarketDataToolbar'
import { ProviderSubscriptionPanel } from './market-data/ProviderSubscriptionPanel'
import { ReadinessSummary } from './market-data/ReadinessSummary'
import { EmptyState, InlineMessage, LoadingState } from './market-data/shared'
import type { MarketDataConflict, MarketDataTab, ReportManifestResult, SnapshotDetail } from './market-data/types'
import { useMarketDataCenter } from './market-data/useMarketDataCenter'

export interface MarketDataViewState {
  query: string
  data: unknown | null
  loading: boolean
  error: string
}

interface MarketDataProps {
  state: MarketDataViewState
  setState: Dispatch<SetStateAction<MarketDataViewState>>
  onResearch: (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => Promise<void>
}

const TABS: Array<{ id: MarketDataTab; label: string; detail: string; icon: typeof Layers3 }> = [
  { id: 'coverage', label: '完整度', detail: '动态契约与门禁', icon: ClipboardCheck },
  { id: 'evidence', label: '数据与证据', detail: '版本、来源与冲突', icon: FileSearch },
  { id: 'jobs', label: '采集任务', detail: 'Checkpoint 与重试', icon: ListChecks },
  { id: 'bundles', label: 'Bundle 历史', detail: '不可变修订链', icon: Layers3 },
  { id: 'archive', label: '归档与数据源', detail: '显式订阅与能力', icon: Archive },
]

export function MarketData({ state, setState, onResearch }: MarketDataProps) {
  const { settings } = useCognitrade()
  const handleSymbolChange = useCallback(
    (symbol: string) => setState((current) => ({ ...current, query: symbol })),
    [setState],
  )
  const controller = useMarketDataCenter(
    settings,
    state.query,
    handleSymbolChange,
  )
  const [manualOpen, setManualOpen] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [conflictId, setConflictId] = useState('')
  const [revokeTarget, setRevokeTarget] = useState<SnapshotDetail | null>(null)
  const selectedDefinition = useMemo(
    () => controller.contract?.dimensions.find((item) => item.id === controller.urlState.dimension) || null,
    [controller.contract, controller.urlState.dimension],
  )
  const selectedCoverage = useMemo(
    () => controller.coverage?.dimensions.find((item) => item.dimension_id === controller.urlState.dimension) || null,
    [controller.coverage, controller.urlState.dimension],
  )
  const selectedConflict = useMemo<MarketDataConflict | null>(
    () => controller.selectedSnapshot?.conflicts?.find((item) => item.conflict_id === conflictId) || null,
    [conflictId, controller.selectedSnapshot],
  )

  return (
    <main className="finance-page market-data-page mdc-page">
      <section className="mdc-hero">
        <div className="mdc-hero-copy">
          <span className="mdc-eyebrow">POINT-IN-TIME · {controller.resolvedAssetFamily === 'fund' ? 'FUND HOLDINGS ENTRY' : 'DATABASE SOURCE OF TRUTH'}</span>
          <h1>行情数据中心</h1>
          <p>{controller.resolvedAssetFamily === 'fund'
            ? '基金本身只占一个持仓披露槽，用于严格展开前十大股票；持仓与股票数据先落库，缺口由 Agent 补采，只有用户确认后才冻结组合 Bundle。'
            : '先按 UZI 原生逻辑显式采集，缺口可由 Agent 补采；数据默认只落库为草稿，只有用户确认后才发布不可变 Bundle。'}</p>
        </div>
        <div className="mdc-hero-principles" aria-label="数据中心原则">
          <span><Database size={15} />数据库唯一事实源</span>
          <span><ShieldCheck size={15} />A / B 级时点证据</span>
          <span><Layers3 size={15} />{controller.resolvedAssetFamily === 'fund' ? '基金单槽 → 十大股票' : '报告阶段不联网补数'}</span>
        </div>
      </section>

      <MarketDataToolbar controller={controller} onManual={() => setManualOpen(true)} onReport={() => setReportOpen(true)} />

      {controller.error ? (
        <div className="mdc-dismissible-message">
          <InlineMessage tone="error">{controller.error}</InlineMessage>
          <button type="button" onClick={() => controller.setError('')} aria-label="关闭错误"><X size={15} /></button>
        </div>
      ) : null}
      {controller.notice ? (
        <div className="mdc-dismissible-message">
          <InlineMessage tone="success">{controller.notice}</InlineMessage>
          <button type="button" onClick={() => controller.setNotice('')} aria-label="关闭提示"><X size={15} /></button>
        </div>
      ) : null}

      <nav className="mdc-tabs" role="tablist" aria-label="行情数据中心视图">
        {TABS.map((tab) => {
          const Icon = tab.icon
          const selected = controller.urlState.tab === tab.id
          return (
            <button
              key={tab.id}
              id={`mdc-tab-${tab.id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`mdc-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              className={selected ? 'is-active' : undefined}
              onClick={() => controller.setUrlState({ tab: tab.id })}
            >
              <Icon size={17} aria-hidden="true" />
              <span><strong>{tab.label}</strong><small>{tab.detail}</small></span>
            </button>
          )
        })}
      </nav>

      <section
        className="mdc-tab-panel"
        id={`mdc-panel-${controller.urlState.tab}`}
        role="tabpanel"
        aria-labelledby={`mdc-tab-${controller.urlState.tab}`}
      >
        {controller.loading.contract ? <LoadingState label="正在读取后端维度契约…" /> : null}
        {!controller.loading.contract && !controller.contract ? (
          <EmptyState title="维度契约不可用" detail="无法自行猜测槽位数、顺序或时效规则；请恢复后端 contract API。" />
        ) : null}

        {controller.contract && controller.urlState.tab === 'coverage' ? (
          controller.loading.coverage && !controller.coverage ? <LoadingState label="正在读取数据库覆盖率…" /> : controller.coverage ? (
            <div className="mdc-stack">
              <ReadinessSummary contract={controller.contract} coverage={controller.coverage} fundJob={controller.latestFundJob} />
              <DimensionReadinessMatrix
                contract={controller.contract}
                coverage={controller.coverage}
                selected={controller.urlState.dimension}
                onSelect={(dimension) => controller.setUrlState({ dimension, tab: 'evidence' })}
              />
            </div>
          ) : (
            <EmptyState
              title="数据库没有该时点的覆盖记录"
              detail="这不是空数据证明。请点击“补缺 / 补旧”显式创建采集任务，或录入带证据的历史快照。"
              action={<button className="secondary-button" type="button" onClick={() => setManualOpen(true)}>录入历史证据</button>}
            />
          )
        ) : null}

        {controller.contract && controller.urlState.tab === 'evidence' ? (
          <DimensionInspector
            definition={selectedDefinition}
            coverage={selectedCoverage}
            snapshots={controller.snapshots}
            selectedSnapshot={controller.selectedSnapshot}
            loading={controller.loading.snapshots}
            onSelectSnapshot={controller.selectSnapshot}
            onManual={() => setManualOpen(true)}
            onResolveConflict={setConflictId}
            onRevoke={setRevokeTarget}
          />
        ) : null}

        {controller.contract && controller.urlState.tab === 'jobs' ? (
          <CollectionJobsPanel
            key={`${controller.urlState.symbol}:${controller.urlState.as_of_date}:${controller.contract.contract_version}`}
            contract={controller.contract}
            urlState={controller.urlState}
            jobs={controller.jobs}
            jobDetails={controller.jobDetails}
            loading={controller.loading.jobs}
            actionLoading={controller.loading.action}
            onCreate={controller.createJob}
            onRefresh={controller.refreshJobs}
            onLoadDetail={controller.loadJobDetail}
            onRetry={controller.retryFailed}
            onAgentRecovery={controller.runAgentRecovery}
            onConfirmFreeze={controller.confirmJobFreeze}
            onCancel={controller.cancelJob}
          />
        ) : null}

        {controller.contract && controller.urlState.tab === 'bundles' ? (
          <BundleHistoryPanel
            bundles={controller.bundles}
            selected={controller.selectedBundle}
            loading={controller.loading.bundles}
            onRefresh={controller.refreshBundles}
            onSelect={controller.selectBundle}
          />
        ) : null}

        {controller.contract && controller.urlState.tab === 'archive' ? (
          <ProviderSubscriptionPanel
            symbol={controller.urlState.symbol}
            market={controller.coverage?.market || controller.urlState.market}
            providers={controller.providers}
            subscriptions={controller.subscriptions}
            loading={controller.loading.archive}
            actionLoading={controller.loading.action}
            onRefresh={controller.refreshArchive}
            onUpdate={controller.updateSubscription}
            onTest={controller.testProvider}
          />
        ) : null}
      </section>

      {manualOpen && controller.contract ? (
        <ManualSnapshotDialog
          symbol={controller.urlState.symbol}
          market={controller.coverage?.market || controller.urlState.market}
          asOfDate={controller.urlState.as_of_date}
          cutoffAt={controller.coverage?.cutoff_at}
          dimensions={controller.contract.dimensions}
          initialDimension={controller.urlState.dimension}
          busy={controller.loading.action}
          onClose={() => setManualOpen(false)}
          onSubmit={controller.createManualSnapshot}
        />
      ) : null}
      {selectedConflict ? (
        <ConflictResolutionDialog
          conflict={selectedConflict}
          busy={controller.loading.action}
          onClose={() => setConflictId('')}
          onSubmit={(candidateId, reason) => controller.resolveConflict(selectedConflict.conflict_id, candidateId, reason)}
        />
      ) : null}
      {revokeTarget ? (
        <RevokeSnapshotDialog
          snapshot={revokeTarget}
          busy={controller.loading.action}
          onClose={() => setRevokeTarget(null)}
          onSubmit={(reason, replacement) => controller.revokeSnapshot(revokeTarget.snapshot_id, reason, replacement)}
        />
      ) : null}
      {reportOpen && controller.coverage?.latest_bundle ? (
        <ReportLaunchDialog
          symbol={controller.urlState.symbol}
          asOfDate={controller.urlState.as_of_date}
          bundleId={controller.resolvedAssetFamily === 'fund' ? controller.latestFundJob?.parent_bundle_id || controller.coverage.latest_bundle.bundle_id : controller.coverage.latest_bundle.bundle_id}
          assetFamily={controller.resolvedAssetFamily || 'equity'}
          holdings={controller.latestFundJob?.expanded_holdings || []}
          busy={controller.loading.action}
          onClose={() => setReportOpen(false)}
          onSubmit={controller.createReportManifest}
          onLaunch={async (profile: 'lite' | 'medium' | 'deep', manifest: ReportManifestResult) => {
            if (!manifest.manifest_hash) throw new Error('冻结结果缺少 Manifest hash，无法启动精确报告。')
            const pinned = {
              research_profile: profile,
              analysis_as_of_date: controller.urlState.as_of_date,
              report_manifest_id: manifest.manifest_id,
              report_manifest_hash: manifest.manifest_hash,
            }
            if (controller.resolvedAssetFamily === 'fund') {
              const fundName = controller.resolvedInstrument?.name || controller.resolvedInstrument?.code || controller.urlState.symbol
              await onResearch(
                `使用冻结数据分析 ${fundName} 的前十大持仓组合`,
                'uzi_portfolio_batch',
                {
                  ...pinned,
                  portfolio_name: `${fundName} 前十大持仓`,
                  portfolio_holdings: (controller.latestFundJob?.expanded_holdings || []).map((holding) => ({
                    ticker: holding.symbol,
                    name: holding.name || holding.symbol,
                    market: holding.market,
                    ...(holding.fund_weight_pct == null ? {} : { weight: holding.fund_weight_pct }),
                  })),
                },
              )
            } else {
              const symbol = controller.resolvedInstrument?.symbol || controller.urlState.symbol
              await onResearch(
                `使用冻结数据分析 ${symbol}`,
                'stock_check',
                { ...pinned, symbol },
              )
            }
            setReportOpen(false)
          }}
        />
      ) : null}
    </main>
  )
}
