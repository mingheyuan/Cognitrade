import { useCallback, useEffect, useState } from 'react'
import type { ViewId } from '../data'

const VIEW_PATHS: Record<ViewId, string> = {
  workbench: '/',
  overview: '/overview',
  market: '/market-data',
  paper: '/paper-trading',
  screening: '/screening',
  funds: '/funds',
  uzi: '/uzi-methods',
  strategies: '/strategies',
  runs: '/runs',
}

const PATH_VIEWS = Object.fromEntries(
  Object.entries(VIEW_PATHS).map(([view, path]) => [path, view]),
) as Record<string, ViewId>

function normalizeBasePath(basePath: string): string {
  const segment = String(basePath || '').replace(/^\/+|\/+$/g, '')
  return segment ? `/${segment}` : ''
}

export function pathForView(view: ViewId, basePath = import.meta.env.BASE_URL): string {
  return `${normalizeBasePath(basePath)}${VIEW_PATHS[view]}` || '/'
}

export function viewFromPath(pathname: string, basePath = import.meta.env.BASE_URL): ViewId {
  const base = normalizeBasePath(basePath)
  const rawPath = `/${String(pathname || '').split('?')[0].split('#')[0].replace(/^\/+|\/+$/g, '')}`
  const routePath = base && (rawPath === base || rawPath.startsWith(`${base}/`))
    ? rawPath.slice(base.length) || '/'
    : rawPath
  const normalized = `/${routePath.replace(/^\/+|\/+$/g, '')}`
  return PATH_VIEWS[normalized === '/' ? '/' : normalized] || 'workbench'
}

export function useViewNavigation() {
  const [activeView, setActiveView] = useState<ViewId>(() => viewFromPath(window.location.pathname))

  useEffect(() => {
    const handlePopState = () => setActiveView(viewFromPath(window.location.pathname))
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const navigate = useCallback((view: ViewId) => {
    const nextPath = pathForView(view)
    if (window.location.pathname !== nextPath) window.history.pushState({ view }, '', nextPath)
    setActiveView(view)
  }, [])

  return { activeView, navigate }
}
