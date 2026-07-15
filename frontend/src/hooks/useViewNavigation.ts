import { useCallback, useEffect, useState } from 'react'
import type { ViewId } from '../data'

const VIEW_PATHS: Record<ViewId, string> = {
  workbench: '/',
  overview: '/overview',
  market: '/market-data',
  paper: '/paper-trading',
  screening: '/screening',
  methodology: '/methodology',
  runs: '/runs',
}

const PATH_VIEWS = Object.fromEntries(
  Object.entries(VIEW_PATHS).map(([view, path]) => [path, view]),
) as Record<string, ViewId>

export function pathForView(view: ViewId): string {
  return VIEW_PATHS[view]
}

export function viewFromPath(pathname: string): ViewId {
  const normalized = `/${String(pathname || '').split('?')[0].split('#')[0].replace(/^\/+|\/+$/g, '')}`
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
