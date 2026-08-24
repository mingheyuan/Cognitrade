import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResearchComposer } from './ResearchComposer'

afterEach(cleanup)

describe('ResearchComposer', () => {
  it('requires and forwards an explicit UZI profile for stock research', () => {
    const onSubmit = vi.fn()
    render(<ResearchComposer onSubmit={onSubmit} running={false} />)

    fireEvent.click(screen.getByRole('combobox', { name: '选择研究意图，当前为自动识别' }))
    fireEvent.click(screen.getByRole('option', { name: '个股体检' }))
    fireEvent.change(screen.getByRole('textbox', { name: '研究问题' }), {
      target: { value: '分析贵州茅台' },
    })

    expect(screen.getByRole('button', { name: '开始研究' })).toBeDisabled()
    fireEvent.change(screen.getByRole('combobox', { name: '选择研究深度' }), {
      target: { value: 'deep' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))

    expect(onSubmit).toHaveBeenCalledWith(
      '分析贵州茅台',
      'stock_check',
      { research_profile: 'deep' },
    )
  })

  it('submits UZI quick scan without a research profile', () => {
    const onSubmit = vi.fn()
    render(<ResearchComposer onSubmit={onSubmit} running={false} />)

    fireEvent.click(screen.getByRole('combobox', { name: '选择研究意图，当前为自动识别' }))
    fireEvent.click(screen.getByRole('option', { name: 'UZI 股票速判' }))
    fireEvent.change(screen.getByRole('textbox', { name: '研究问题' }), {
      target: { value: '速判贵州茅台' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始研究' }))

    expect(onSubmit).toHaveBeenCalledWith('速判贵州茅台', 'quick_scan', undefined)
  })
})
