import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppSelect } from './AppSelect'

afterEach(cleanup)

describe('AppSelect', () => {
  it('uses a project-owned listbox and reports the selected value', () => {
    const onChange = vi.fn()
    render(
      <label>
        <span>研究档位</span>
        <AppSelect value="medium" onChange={onChange}>
          <option value="lite">快速</option>
          <option value="medium">标准</option>
          <option value="deep">深度</option>
        </AppSelect>
      </label>,
    )

    const trigger = screen.getByRole('combobox', { name: '研究档位' })
    expect(trigger).toHaveValue('medium')
    fireEvent.click(trigger)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: '深度' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0].target.value).toBe('deep')
  })

  it('supports arrow navigation without opening a native menu', () => {
    const onChange = vi.fn()
    render(
      <AppSelect value="lite" aria-label="研究深度" onChange={onChange}>
        <option value="lite">快速</option>
        <option value="medium">标准</option>
      </AppSelect>,
    )

    const trigger = screen.getByRole('combobox', { name: '研究深度' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'Enter' })

    expect(onChange.mock.calls[0][0].target.value).toBe('medium')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
