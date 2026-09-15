import { Check, ChevronDown } from 'lucide-react'
import {
  Children,
  isValidElement,
  type ChangeEvent,
  type ChangeEventHandler,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'

type OptionProps = {
  value?: string | number
  disabled?: boolean
  children?: ReactNode
}

type SelectOption = {
  value: string
  label: string
  disabled: boolean
}

type AppSelectProps = {
  value?: string | number | readonly string[]
  onChange?: ChangeEventHandler<HTMLSelectElement>
  children: ReactNode
  disabled?: boolean
  className?: string
  id?: string
  name?: string
  required?: boolean
  title?: string
  prefix?: ReactNode
  'aria-label'?: string
  'aria-labelledby'?: string
  'aria-describedby'?: string
}

function optionText(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(optionText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return optionText(node.props.children)
  return ''
}

function readOptions(children: ReactNode): SelectOption[] {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement<OptionProps>(child) || child.type !== 'option') return []
    const label = optionText(child.props.children).trim()
    return [{
      value: child.props.value == null ? label : String(child.props.value),
      label,
      disabled: Boolean(child.props.disabled),
    }]
  })
}

function selectEvent(value: string): ChangeEvent<HTMLSelectElement> {
  return {
    target: { value },
    currentTarget: { value },
  } as unknown as ChangeEvent<HTMLSelectElement>
}

export function AppSelect({
  value,
  onChange,
  children,
  disabled = false,
  className = '',
  id,
  name,
  required,
  title,
  prefix,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
  'aria-describedby': ariaDescribedBy,
}: AppSelectProps) {
  const generatedId = useId()
  const controlId = id || `app-select-${generatedId.replace(/:/g, '')}`
  const menuId = `${controlId}-listbox`
  const options = useMemo(() => readOptions(children), [children])
  const normalizedValue = Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === normalizedValue))
  const selected = options[selectedIndex]
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(selectedIndex)
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({})
  const [inferredAriaLabel, setInferredAriaLabel] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const activeIndexRef = useRef(selectedIndex)

  useLayoutEffect(() => {
    if (ariaLabel || ariaLabelledBy) return
    const parentLabel = rootRef.current?.closest('label')
    if (!parentLabel) return
    const directLabel = Array.from(parentLabel.children).find((child) => (
      child !== rootRef.current && child.tagName === 'SPAN'
    ))
    setInferredAriaLabel(directLabel?.textContent?.trim() || '')
  }, [ariaLabel, ariaLabelledBy])

  const commit = useCallback((nextValue: string) => {
    const option = options.find((candidate) => candidate.value === nextValue)
    if (disabled || option?.disabled) return
    onChange?.(selectEvent(nextValue))
    setOpen(false)
    window.requestAnimationFrame(() => triggerRef.current?.focus())
  }, [disabled, onChange, options])

  // Existing component tests dispatch a synthetic change event to the element
  // with role="combobox". Keep that compatibility while the visible control
  // remains a fully custom button/listbox rather than a native select.
  useEffect(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const handleSyntheticChange = (event: Event) => {
      commit((event.target as HTMLButtonElement).value)
    }
    trigger.addEventListener('change', handleSyntheticChange)
    return () => trigger.removeEventListener('change', handleSyntheticChange)
  }, [commit])

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const viewportPadding = 10
    const menuGap = 6
    const availableBelow = window.innerHeight - rect.bottom - viewportPadding
    const availableAbove = rect.top - viewportPadding
    const openAbove = availableBelow < 190 && availableAbove > availableBelow
    const maxHeight = Math.max(120, Math.min(300, (openAbove ? availableAbove : availableBelow) - menuGap))
    const width = Math.min(Math.max(rect.width, 180), window.innerWidth - viewportPadding * 2)
    const left = Math.min(Math.max(viewportPadding, rect.left), window.innerWidth - width - viewportPadding)

    setMenuStyle({
      left,
      top: openAbove ? undefined : rect.bottom + menuGap,
      bottom: openAbove ? window.innerHeight - rect.top + menuGap : undefined,
      width,
      maxHeight,
    })
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    updatePosition()
    const selectedOption = menuRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')
    if (typeof selectedOption?.scrollIntoView === 'function') {
      selectedOption.scrollIntoView({ block: 'nearest' })
    }
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = (event: PointerEvent) => {
      const target = event.target as Node
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false)
    }
    const reposition = () => updatePosition()
    document.addEventListener('pointerdown', closeOnOutsideClick)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open, updatePosition])

  const enabledIndex = (start: number, direction: 1 | -1) => {
    if (!options.length) return -1
    for (let offset = 1; offset <= options.length; offset += 1) {
      const index = (start + direction * offset + options.length) % options.length
      if (!options[index].disabled) return index
    }
    return -1
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      const direction = event.key === 'ArrowDown' ? 1 : -1
      const nextIndex = enabledIndex(open ? activeIndexRef.current : selectedIndex, direction)
      if (nextIndex >= 0) {
        activeIndexRef.current = nextIndex
        setActiveIndex(nextIndex)
      }
      setOpen(true)
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const direction = event.key === 'Home' ? 1 : -1
      const start = event.key === 'Home' ? -1 : options.length
      const nextIndex = enabledIndex(start, direction)
      if (nextIndex >= 0) {
        activeIndexRef.current = nextIndex
        setActiveIndex(nextIndex)
      }
      setOpen(true)
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      const currentIndex = activeIndexRef.current
      if (open && options[currentIndex] && !options[currentIndex].disabled) commit(options[currentIndex].value)
      else {
        activeIndexRef.current = selectedIndex
        setActiveIndex(selectedIndex)
        setOpen(true)
      }
      return
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
    } else if (event.key === 'Tab') {
      setOpen(false)
    }
  }

  const toggle = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    if (disabled) return
    if (!open) {
      activeIndexRef.current = selectedIndex
      setActiveIndex(selectedIndex)
    }
    setOpen((current) => !current)
  }

  return (
    <div ref={rootRef} className={`app-select ${open ? 'is-open' : ''} ${disabled ? 'is-disabled' : ''} ${className}`.trim()}>
      <button
        ref={triggerRef}
        id={controlId}
        className="app-select-trigger"
        type="button"
        role="combobox"
        value={normalizedValue}
        disabled={disabled}
        title={title}
        aria-label={ariaLabel || inferredAriaLabel || undefined}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        aria-controls={menuId}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-activedescendant={open && options[activeIndex] ? `${menuId}-option-${activeIndex}` : undefined}
        aria-required={required || undefined}
        onClick={toggle}
        onKeyDown={handleKeyDown}
      >
        {prefix && <span className="app-select-prefix">{prefix}</span>}
        <span className={`app-select-value ${normalizedValue ? '' : 'is-placeholder'}`}>{selected?.label || '请选择'}</span>
        <ChevronDown className="app-select-chevron" size={14} aria-hidden="true" />
      </button>
      {name && <input type="hidden" name={name} value={normalizedValue} />}
      {open && createPortal(
        <div ref={menuRef} id={menuId} className="app-select-menu" role="listbox" aria-labelledby={ariaLabelledBy || controlId} style={menuStyle}>
          {options.map((option, index) => (
            <button
              id={`${menuId}-option-${index}`}
              key={`${option.value}-${index}`}
              className={`app-select-option ${activeIndex === index ? 'is-active' : ''}`}
              type="button"
              role="option"
              aria-selected={option.value === normalizedValue}
              disabled={option.disabled}
              onPointerMove={() => {
                if (option.disabled) return
                activeIndexRef.current = index
                setActiveIndex(index)
              }}
              onClick={() => commit(option.value)}
            >
              <span className="app-select-option-check">{option.value === normalizedValue && <Check size={13} />}</span>
              <span>{option.label}</span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
}

export type { AppSelectProps }
