import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ApiConnectionSettings } from '../../api-client'
import { errorMessageWithRequestId, localizeErrorMessage } from '../../utils/error-message'
import { CognitradeApiError } from '../../api-client'
import { MarketDataCenterApi } from './api'
import type {
  AssetFamily,
  CollectionJob,
  CollectionJobInput,
  InstrumentIdentity,
  ManualSnapshotInput,
  MarketDataBundle,
  MarketDataContract,
  MarketDataCoverage,
  MarketDataProvider,
  MarketDataSubscription,
  MarketDataTab,
  MarketDataUrlState,
  PageResult,
  ReportManifestResult,
  ResolvedAssetFamily,
  SnapshotDetail,
  SnapshotSummary,
} from './types'

const VALID_TABS = new Set<MarketDataTab>(['coverage', 'evidence', 'jobs', 'bundles', 'archive'])
const VALID_ASSET_FAMILIES = new Set<AssetFamily>(['auto', 'equity', 'fund'])

function localDate() {
  const date = new Date()
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

function inferredMarket(symbol: string) {
  const normalized = symbol.trim().toUpperCase()
  if (normalized.endsWith('.HK')) return 'HK_EQUITY'
  if (normalized.endsWith('.US') || /^[A-Z]{1,6}$/.test(normalized)) return 'US_EQUITY'
  return 'CN_EQUITY'
}

function identityAssetFamily(identity: InstrumentIdentity): ResolvedAssetFamily | null {
  if (identity.asset_type === 'stock') return 'equity'
  if (identity.asset_type === 'exchange_fund' || identity.asset_type === 'otc_fund') return 'fund'
  return null
}

function readUrlState(initialSymbol: string): MarketDataUrlState {
  const query = new URLSearchParams(window.location.search)
  const requestedTab = query.get('tab') as MarketDataTab | null
  const requestedAssetFamily = query.get('asset_family') as AssetFamily | null
  return {
    symbol: query.get('symbol') || initialSymbol || '600519.SH',
    market: query.get('market') || inferredMarket(query.get('symbol') || initialSymbol || '600519.SH'),
    asset_family: requestedAssetFamily && VALID_ASSET_FAMILIES.has(requestedAssetFamily) ? requestedAssetFamily : 'auto',
    as_of_date: query.get('as_of_date') || localDate(),
    cutoff: query.get('cutoff') || 'eod',
    tab: requestedTab && VALID_TABS.has(requestedTab) ? requestedTab : 'coverage',
    dimension: query.get('dimension') || '',
  }
}

function updateBrowserUrl(state: MarketDataUrlState) {
  const query = new URLSearchParams(window.location.search)
  query.set('symbol', state.symbol)
  query.set('market', state.market)
  query.set('asset_family', state.asset_family)
  query.set('as_of_date', state.as_of_date)
  query.set('cutoff', state.cutoff)
  query.set('tab', state.tab)
  if (state.dimension) query.set('dimension', state.dimension)
  else query.delete('dimension')
  window.history.replaceState(window.history.state, '', `${window.location.pathname}?${query.toString()}`)
}

function emptyPage<T>(): PageResult<T> {
  return { items: [], total_count: 0, limit: 0, offset: 0 }
}

function displayError(error: unknown) {
  if (error instanceof CognitradeApiError) return errorMessageWithRequestId(error.message, error.requestId)
  return localizeErrorMessage(error, '行情数据中心请求失败。')
}

type LoadingKey = 'identity' | 'contract' | 'coverage' | 'snapshots' | 'jobs' | 'bundles' | 'archive' | 'action'

export function useMarketDataCenter(
  settings: ApiConnectionSettings,
  initialSymbol: string,
  onSymbolChange: (symbol: string) => void,
) {
  const api = useMemo(
    () => new MarketDataCenterApi({ baseUrl: settings.baseUrl, token: settings.token }),
    [settings.baseUrl, settings.token],
  )
  const [urlState, setUrlStateInternal] = useState<MarketDataUrlState>(() => readUrlState(initialSymbol))
  const [symbolDraft, setSymbolDraft] = useState(urlState.symbol)
  const [resolvedInstrument, setResolvedInstrument] = useState<InstrumentIdentity | null>(null)
  const [contract, setContract] = useState<MarketDataContract | null>(null)
  const [coverage, setCoverage] = useState<MarketDataCoverage | null>(null)
  const [snapshots, setSnapshots] = useState<PageResult<SnapshotSummary>>(emptyPage)
  const [selectedSnapshot, setSelectedSnapshot] = useState<SnapshotDetail | null>(null)
  const [jobs, setJobs] = useState<PageResult<CollectionJob>>(emptyPage)
  const [bundles, setBundles] = useState<PageResult<MarketDataBundle>>(emptyPage)
  const [selectedBundle, setSelectedBundle] = useState<MarketDataBundle | null>(null)
  const [providers, setProviders] = useState<PageResult<MarketDataProvider>>(emptyPage)
  const [subscriptions, setSubscriptions] = useState<PageResult<MarketDataSubscription>>(emptyPage)
  const [loading, setLoading] = useState<Record<LoadingKey, boolean>>({
    identity: true, contract: true, coverage: false, snapshots: false, jobs: false, bundles: false, archive: false, action: false,
  })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const operationRef = useRef(0)
  const identityOperationRef = useRef(0)
  const initialResolutionRef = useRef({ query: urlState.symbol, assetFamily: urlState.asset_family })
  const resolvedAssetFamily = resolvedInstrument ? identityAssetFamily(resolvedInstrument) : null

  const setLoadingKey = useCallback((key: LoadingKey, value: boolean) => {
    setLoading((current) => ({ ...current, [key]: value }))
  }, [])

  const setUrlState = useCallback((patch: Partial<MarketDataUrlState>) => {
    setUrlStateInternal((current) => {
      const next = { ...current, ...patch }
      updateBrowserUrl(next)
      return next
    })
  }, [])

  const resolveAndLoadContract = useCallback(async (query: string, assetFamily: AssetFamily, signal?: AbortSignal) => {
    const normalizedQuery = query.trim()
    if (!normalizedQuery) return null
    const sequence = ++identityOperationRef.current
    setLoadingKey('identity', true)
    setLoadingKey('contract', true)
    setError('')
    setNotice('')
    setResolvedInstrument(null)
    setContract(null)
    setCoverage(null)
    try {
      const resolution = await api.resolveInstrument(normalizedQuery, assetFamily, signal)
      if (resolution.ambiguous) {
        const choices = resolution.candidates.map((candidate) => candidate.symbol).join('、')
        throw new Error(choices ? `标的存在歧义，请补充代码或明确资产类型：${choices}` : '标的存在歧义，请补充代码或明确资产类型。')
      }
      if (!resolution.resolved) throw new Error(`无法解析标的：${normalizedQuery}`)
      const family = identityAssetFamily(resolution.resolved)
      if (!family) throw new Error('行情数据中心暂不支持指数作为采集或报告标的。')
      const response = await api.getContract(resolution.resolved.market, signal)
      if (sequence !== identityOperationRef.current) return null
      if (response.asset_family !== family) throw new Error('后端返回的资产身份与采集契约不一致。')
      const dimensions = [...(response.dimensions || [])].sort((left, right) => left.order - right.order)
      const identity = resolution.resolved
      setResolvedInstrument(identity)
      setContract({ ...response, dimensions })
      setSymbolDraft(identity.symbol)
      onSymbolChange(identity.symbol)
      setUrlStateInternal((current) => {
        const dimension = dimensions.some((item) => item.id === current.dimension) ? current.dimension : dimensions[0]?.id || ''
        const next = { ...current, symbol: identity.symbol, market: identity.market, dimension }
        updateBrowserUrl(next)
        return next
      })
      return identity
    } catch (loadError) {
      if (!signal?.aborted && sequence === identityOperationRef.current) setError(displayError(loadError))
      return null
    } finally {
      if (!signal?.aborted && sequence === identityOperationRef.current) {
        setLoadingKey('identity', false)
        setLoadingKey('contract', false)
      }
    }
  }, [api, onSymbolChange, setLoadingKey])

  useEffect(() => {
    const controller = new AbortController()
    const initial = initialResolutionRef.current
    queueMicrotask(() => {
      if (!controller.signal.aborted) void resolveAndLoadContract(initial.query, initial.assetFamily, controller.signal)
    })
    return () => controller.abort()
  }, [resolveAndLoadContract])

  const loadCoverage = useCallback(async (signal?: AbortSignal) => {
    if (!resolvedInstrument || !contract) return
    setLoadingKey('coverage', true)
    setError('')
    try {
      let response = await api.getCoverage({
        symbol: resolvedInstrument.symbol, market: resolvedInstrument.market, as_of_date: urlState.as_of_date, cutoff: urlState.cutoff,
      }, signal)
      if (!response.latest_bundle) {
        const published = await api.listBundles({
          symbol: response.symbol,
          as_of_date: response.as_of_date,
          cutoff: urlState.cutoff,
          limit: 1,
          offset: 0,
        }, signal)
        const latest = published.items[0]
        if (latest) {
          response = {
            ...response,
            latest_bundle: {
              bundle_id: latest.bundle_id,
              status: latest.status,
              manifest_hash: latest.manifest_hash,
              revision: latest.revision,
              published_at: latest.published_at,
            },
          }
        }
      }
      setCoverage(response)
    } catch (loadError) {
      if (!signal?.aborted) {
        setCoverage(null)
        setError(displayError(loadError))
      }
    } finally {
      if (!signal?.aborted) setLoadingKey('coverage', false)
    }
  }, [api, contract, resolvedInstrument, setLoadingKey, urlState.as_of_date, urlState.cutoff])

  useEffect(() => {
    const controller = new AbortController()
    queueMicrotask(() => {
      if (!controller.signal.aborted) void loadCoverage(controller.signal)
    })
    return () => controller.abort()
  }, [loadCoverage])

  const loadSnapshots = useCallback(async (signal?: AbortSignal) => {
    setLoadingKey('snapshots', true)
    try {
      const page = await api.listSnapshots({
        symbol: urlState.symbol,
        as_of_date: urlState.as_of_date,
        cutoff: urlState.cutoff,
        dimension_id: urlState.dimension || undefined,
        limit: 50,
        offset: 0,
      }, signal)
      setSnapshots(page)
      const targetId = page.items[0]?.snapshot_id
      setSelectedSnapshot(targetId ? await api.getSnapshot(targetId, signal) : null)
    } catch (loadError) {
      if (!signal?.aborted) setError(displayError(loadError))
    } finally {
      if (!signal?.aborted) setLoadingKey('snapshots', false)
    }
  }, [api, setLoadingKey, urlState.as_of_date, urlState.cutoff, urlState.dimension, urlState.symbol])

  const loadJobs = useCallback(async (signal?: AbortSignal) => {
    setLoadingKey('jobs', true)
    try {
      setJobs(await api.listJobs({ symbol: urlState.symbol, limit: 50, offset: 0 }, signal))
    } catch (loadError) {
      if (!signal?.aborted) setError(displayError(loadError))
    } finally {
      if (!signal?.aborted) setLoadingKey('jobs', false)
    }
  }, [api, setLoadingKey, urlState.symbol])

  const loadBundles = useCallback(async (signal?: AbortSignal) => {
    setLoadingKey('bundles', true)
    try {
      const page = await api.listBundles({ ...urlState, limit: 50, offset: 0 }, signal)
      setBundles(page)
      const targetId = page.items[0]?.bundle_id
      setSelectedBundle(targetId ? await api.getBundle(targetId, signal) : null)
    } catch (loadError) {
      if (!signal?.aborted) setError(displayError(loadError))
    } finally {
      if (!signal?.aborted) setLoadingKey('bundles', false)
    }
  }, [api, setLoadingKey, urlState])

  const loadArchive = useCallback(async (signal?: AbortSignal) => {
    setLoadingKey('archive', true)
    try {
      const [subscriptionPage, providerPage] = await Promise.all([
        api.listSubscriptions(urlState.symbol, signal),
        api.listProviders(signal),
      ])
      setSubscriptions(subscriptionPage)
      setProviders(providerPage)
    } catch (loadError) {
      if (!signal?.aborted) setError(displayError(loadError))
    } finally {
      if (!signal?.aborted) setLoadingKey('archive', false)
    }
  }, [api, setLoadingKey, urlState.symbol])

  useEffect(() => {
    const controller = new AbortController()
    queueMicrotask(() => {
      if (controller.signal.aborted) return
      if (urlState.tab === 'evidence') void loadSnapshots(controller.signal)
      if (urlState.tab === 'jobs') void loadJobs(controller.signal)
      if (urlState.tab === 'bundles') void loadBundles(controller.signal)
      if (urlState.tab === 'archive') void loadArchive(controller.signal)
    })
    return () => controller.abort()
  }, [loadArchive, loadBundles, loadJobs, loadSnapshots, urlState.tab])

  useEffect(() => {
    if (resolvedAssetFamily !== 'fund') return
    const controller = new AbortController()
    queueMicrotask(() => {
      if (!controller.signal.aborted) void loadJobs(controller.signal)
    })
    return () => controller.abort()
  }, [loadJobs, resolvedAssetFamily])

  const runAction = useCallback(async <T,>(action: () => Promise<T>, success: (value: T) => string, refresh?: () => Promise<unknown>) => {
    const sequence = ++operationRef.current
    setLoadingKey('action', true)
    setError('')
    setNotice('')
    try {
      const value = await action()
      if (sequence !== operationRef.current) return value
      setNotice(success(value))
      if (refresh) await refresh()
      return value
    } catch (actionError) {
      if (sequence === operationRef.current) setError(displayError(actionError))
      throw actionError
    } finally {
      if (sequence === operationRef.current) setLoadingKey('action', false)
    }
  }, [setLoadingKey])

  const submitSymbol = useCallback(() => {
    const normalized = symbolDraft.trim()
    if (!normalized) return Promise.resolve(null)
    return resolveAndLoadContract(normalized, urlState.asset_family)
  }, [resolveAndLoadContract, symbolDraft, urlState.asset_family])

  const selectSnapshot = useCallback(async (snapshotId: string) => {
    setLoadingKey('snapshots', true)
    try {
      setSelectedSnapshot(await api.getSnapshot(snapshotId))
    } catch (loadError) {
      setError(displayError(loadError))
    } finally {
      setLoadingKey('snapshots', false)
    }
  }, [api, setLoadingKey])

  const selectBundle = useCallback(async (bundleId: string) => {
    setLoadingKey('bundles', true)
    try {
      setSelectedBundle(await api.getBundle(bundleId))
    } catch (loadError) {
      setError(displayError(loadError))
    } finally {
      setLoadingKey('bundles', false)
    }
  }, [api, setLoadingKey])

  const latestFundJob = useMemo(() => {
    if (!resolvedInstrument || resolvedAssetFamily !== 'fund') return null
    return jobs.items.find((job) => job.asset_family === 'fund'
      && job.symbols.some((symbol) => symbol === resolvedInstrument.symbol || symbol === resolvedInstrument.code)
      && job.start_date <= urlState.as_of_date
      && job.end_date >= urlState.as_of_date) || null
  }, [jobs.items, resolvedAssetFamily, resolvedInstrument, urlState.as_of_date])

  const canFreezeReport = Boolean(coverage?.report_ready && (resolvedAssetFamily === 'equity'
    || (latestFundJob?.parent_bundle_id
      && latestFundJob.expanded_holdings.length === 10
      && ['completed', 'succeeded'].includes(latestFundJob.status))))

  return {
    urlState,
    setUrlState,
    symbolDraft,
    setSymbolDraft,
    submitSymbol,
    resolvedInstrument,
    resolvedAssetFamily,
    contract,
    coverage,
    snapshots,
    selectedSnapshot,
    selectSnapshot,
    jobs,
    latestFundJob,
    canFreezeReport,
    bundles,
    selectedBundle,
    selectBundle,
    providers,
    subscriptions,
    loading,
    error,
    setError,
    notice,
    setNotice,
    refreshCoverage: () => loadCoverage(),
    refreshSnapshots: () => loadSnapshots(),
    refreshJobs: () => loadJobs(),
    refreshBundles: () => loadBundles(),
    refreshArchive: () => loadArchive(),
    createJob: (payload: CollectionJobInput) => runAction(
      async () => {
        const resolutions = await Promise.all(payload.symbols.map((query) => api.resolveInstrument(query, payload.asset_family)))
        if (resolutions.some((resolution) => resolution.ambiguous || !resolution.resolved)) {
          throw new Error('批量任务中有标的无法唯一解析，请拆分任务并明确资产类型。')
        }
        const identities = resolutions.map((resolution) => resolution.resolved as InstrumentIdentity)
        const families = identities.map(identityAssetFamily)
        if (families.some((family) => family === null)) throw new Error('批量采集暂不支持指数标的。')
        const uniqueFamilies = new Set(families)
        if (uniqueFamilies.size !== 1) throw new Error('同一采集任务不能混合股票与基金，请分别创建任务。')
        const family = families[0] as ResolvedAssetFamily
        if (payload.asset_family !== 'auto' && payload.asset_family !== family) {
          throw new Error(`当前任务声明为${payload.asset_family === 'fund' ? '基金' : '股票'}，但输入解析为${family === 'fund' ? '基金' : '股票'}。`)
        }
        if (family === 'fund' && identities.length !== 1) throw new Error('基金持仓入口一次只支持一只基金，请分别创建任务。')
        return api.createJob({
          ...payload,
          symbols: identities.map((identity) => identity.symbol),
          asset_family: family,
          top_n: 10,
        })
      },
      (job) => job.asset_family === 'fund'
        ? `基金任务 ${job.job_id} 已创建；将先冻结一个持仓披露槽，再展开前十大股票子任务。`
        : `采集任务 ${job.job_id} 已创建；数据源调用只会发生在该显式任务中。`,
      loadJobs,
    ),
    retryFailed: (jobId: string) => runAction(
      () => api.retryFailed(jobId),
      () => '已仅重新入队失败槽位。',
      loadJobs,
    ),
    cancelJob: (jobId: string) => runAction(
      () => api.cancelJob(jobId),
      () => '取消请求已提交。',
      loadJobs,
    ),
    createManualSnapshot: (payload: ManualSnapshotInput) => runAction(
      () => api.createManualSnapshot(payload),
      (snapshot) => `人工快照 ${snapshot.snapshot_id} 已落库并进入严格校验。`,
      async () => { await loadCoverage(); await loadSnapshots() },
    ),
    revokeSnapshot: (snapshotId: string, reason: string, supersededById?: string) => runAction(
      () => api.revokeSnapshot(snapshotId, { reason, superseded_by_id: supersededById || undefined }),
      () => '快照已撤销；既有报告将保留审计警告。',
      async () => { await loadCoverage(); await loadSnapshots() },
    ),
    resolveConflict: (conflictId: string, candidateId: string, reason: string) => runAction(
      () => api.resolveConflict(conflictId, { selected_snapshot_id: candidateId, resolution: 'select_candidate', reason }),
      () => '冲突决议已保存，完整度正在重新计算。',
      async () => { await loadCoverage(); await loadSnapshots() },
    ),
    publishBundle: () => runAction(
      () => api.publishBundle({
        symbol: urlState.symbol,
        market: coverage?.market || urlState.market,
        as_of_date: urlState.as_of_date,
        cutoff_at: coverage?.cutoff_at || (urlState.cutoff !== 'eod' ? urlState.cutoff : undefined),
      }),
      (bundle) => `Bundle ${bundle.bundle_id} 已不可变发布。`,
      async () => { await loadCoverage(); await loadBundles() },
    ),
    updateSubscription: (payload: MarketDataSubscription) => runAction(
      () => api.updateSubscription(payload.symbol, payload),
      (subscription) => subscription.enabled ? '日级归档已显式开启。' : '日级归档已关闭。',
      loadArchive,
    ),
    testProvider: (providerId: string) => runAction(
      () => api.testProvider(providerId),
      (provider) => provider.healthy ? `${provider.label} 配置检查通过；尚未执行网络查询。` : `${provider.label} 配置检查未通过。`,
      loadArchive,
    ),
    createReportManifest: (profile: 'lite' | 'medium' | 'deep') => runAction<ReportManifestResult>(
      async () => {
        if (!resolvedInstrument || !resolvedAssetFamily) throw new Error('请先解析一个受支持的标的。')
        if (resolvedAssetFamily === 'fund' && !latestFundJob?.parent_bundle_id) {
          throw new Error('基金持仓父 Bundle 尚未冻结，不能创建前十大股票组合报告输入。')
        }
        return api.createReportManifest({
          symbol: resolvedAssetFamily === 'fund' ? resolvedInstrument.code : resolvedInstrument.symbol,
          market: resolvedInstrument.market,
          as_of_date: urlState.as_of_date,
          evaluation_at: coverage?.cutoff_at,
          profile,
          asset_family: resolvedAssetFamily,
          fund_bundle_id: latestFundJob?.parent_bundle_id || undefined,
        })
      },
      (result) => result.report_run_id
        ? `${profile.toUpperCase()} 报告已启动，运行 ID：${result.report_run_id}`
        : resolvedAssetFamily === 'fund'
          ? `前十大股票组合输入 Manifest ${result.manifest_id} 已冻结。`
          : `报告输入 Manifest ${result.manifest_id} 已冻结。`,
    ),
  }
}

export type MarketDataCenterController = ReturnType<typeof useMarketDataCenter>
