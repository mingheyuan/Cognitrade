import { ArrowUp, Check, ChevronDown, Paperclip, SlidersHorizontal, Sparkles } from 'lucide-react'
import { type ChangeEvent, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from 'react'
import type { ResearchIntent } from '../research-contracts'

type ResearchComposerProps = {
  onSubmit: (query: string, intent?: ResearchIntent) => void
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

type IntentValue = keyof typeof intentLabels

const intentEntries = Object.entries(intentLabels) as Array<[IntentValue, string]>

export function ResearchComposer({ onSubmit, running, compact = false }: ResearchComposerProps) {
  const [query, setQuery] = useState('')
  const [intent, setIntent] = useState<IntentValue>('auto')
  const [intentMenuOpen, setIntentMenuOpen] = useState(false)
  const intentSelectRef = useRef<HTMLDivElement>(null)
  const intentTriggerRef = useRef<HTMLButtonElement>(null)
  const materialInputRef = useRef<HTMLInputElement>(null)
  const intentOptionRefs = useRef<Partial<Record<IntentValue, HTMLButtonElement | null>>>({})
  const [localMaterials, setLocalMaterials] = useState<Array<{ name: string; size: number }>>([])

  useEffect(() => {
    if (!intentMenuOpen) return

    const focusFrame = window.requestAnimationFrame(() => intentOptionRefs.current[intent]?.focus())
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!intentSelectRef.current?.contains(event.target as Node)) setIntentMenuOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setIntentMenuOpen(false)
      intentTriggerRef.current?.focus()
    }

    document.addEventListener('pointerdown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      document.removeEventListener('pointerdown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [intent, intentMenuOpen])

  const focusIntentOption = (current: IntentValue, offset: number) => {
    const currentIndex = intentEntries.findIndex(([value]) => value === current)
    const nextIndex = (currentIndex + offset + intentEntries.length) % intentEntries.length
    intentOptionRefs.current[intentEntries[nextIndex][0]]?.focus()
  }

  const handleIntentOptionKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, value: IntentValue) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      focusIntentOption(value, event.key === 'ArrowDown' ? 1 : -1)
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const target = event.key === 'Home' ? intentEntries[0][0] : intentEntries.at(-1)![0]
      intentOptionRefs.current[target]?.focus()
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const normalized = query.trim()
    if (!normalized || running) return
    onSubmit(normalized, intent === 'auto' ? undefined : intent)
    setQuery('')
  }

  const previewMaterials = (event: ChangeEvent<HTMLInputElement>) => {
    setLocalMaterials(
      Array.from(event.target.files || []).map((file) => ({
        name: file.name,
        size: file.size,
      })),
    )
    event.target.value = ''
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
        <div className={`intent-select ${intentMenuOpen ? 'is-open' : ''}`} ref={intentSelectRef}>
          <button
            ref={intentTriggerRef}
            className="intent-select-trigger"
            type="button"
            aria-label={`选择研究意图，当前为${intentLabels[intent]}`}
            aria-haspopup="menu"
            aria-expanded={intentMenuOpen}
            aria-controls="research-intent-menu"
            onClick={() => setIntentMenuOpen((open) => !open)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault()
                setIntentMenuOpen(true)
              }
            }}
          >
            <SlidersHorizontal size={14} />
            <span>{intentLabels[intent]}</span>
            <ChevronDown className="intent-select-chevron" size={13} />
          </button>
          {intentMenuOpen && (
            <div id="research-intent-menu" className="intent-select-menu" role="menu" aria-label="研究意图">
              {intentEntries.map(([value, label]) => (
                <button
                  key={value}
                  ref={(node) => { intentOptionRefs.current[value] = node }}
                  className="intent-select-option"
                  type="button"
                  role="menuitemradio"
                  aria-checked={intent === value}
                  onClick={() => {
                    setIntent(value)
                    setIntentMenuOpen(false)
                    intentTriggerRef.current?.focus()
                  }}
                  onKeyDown={(event) => handleIntentOptionKeyDown(event, value)}
                >
                  <span className="intent-select-option-check">{intent === value && <Check size={13} />}</span>
                  <span>{label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <input
          ref={materialInputRef}
          className="visually-hidden"
          type="file"
          multiple
          onChange={previewMaterials}
          tabIndex={-1}
        />
        <button
          className="composer-tool-button"
          type="button"
          title="本地预览：只读取文件名和大小，不上传文件"
          onClick={() => materialInputRef.current?.click()}
        >
          <Paperclip size={14} />
          添加材料 <em>本地预览</em>
        </button>
        {localMaterials.length > 0 && (
          <span className="local-materials-preview" title={localMaterials.map((file) => `${file.name} · ${Math.ceil(file.size / 1024)} KB`).join('\n')}>
            已选择 {localMaterials.length} 个文件 · 未上传
            <button type="button" onClick={() => setLocalMaterials([])} aria-label="清除本地材料预览">清除</button>
          </span>
        )}
        <span className="composer-hint">Enter 发送 · Shift + Enter 换行</span>
      </div>
    </form>
  )
}
