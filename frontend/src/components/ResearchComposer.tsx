import { ArrowUp, ChevronDown, Paperclip, SlidersHorizontal, Sparkles } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import type { ComposerIntent } from '../research-contracts'

type ResearchComposerProps = {
  onSubmit: (query: string, intent: ComposerIntent) => void
  running: boolean
  compact?: boolean
}

const intentLabels = {
  auto: '自动识别',
  stock_check: '个股体检',
  stock_compare: '多股比较',
  stock_screen: '股票筛选',
  sector_research: '行业研究',
}

export function ResearchComposer({ onSubmit, running, compact = false }: ResearchComposerProps) {
  const [query, setQuery] = useState('')
  const [intent, setIntent] = useState<keyof typeof intentLabels>('auto')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const normalized = query.trim()
    if (!normalized || running) return
    onSubmit(normalized, intent)
    setQuery('')
  }

  return (
    <form className={`research-composer ${compact ? 'is-compact' : ''}`} onSubmit={submit}>
      <div className="composer-main">
        <Sparkles size={17} className="composer-spark" />
        <textarea
          id="research-composer"
          rows={compact ? 1 : 2}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
          placeholder="输入股票、板块或研究问题，例如：比较三家银行的盈利质量与估值…"
          aria-label="研究问题"
        />
        <button
          className="submit-research-button"
          type="submit"
          disabled={!query.trim() || running}
          aria-label={running ? '研究正在运行' : '开始研究'}
        >
          <ArrowUp size={17} />
        </button>
      </div>
      <div className="composer-toolbar">
        <label className="intent-select">
          <SlidersHorizontal size={14} />
          <select
            value={intent}
            onChange={(event) => setIntent(event.target.value as keyof typeof intentLabels)}
            aria-label="选择研究意图"
          >
            {Object.entries(intentLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <ChevronDown size={13} />
        </label>
        <button className="composer-tool-button" type="button">
          <Paperclip size={14} />
          添加材料
        </button>
        <span className="composer-hint">Enter 发送 · Shift + Enter 换行</span>
      </div>
    </form>
  )
}
