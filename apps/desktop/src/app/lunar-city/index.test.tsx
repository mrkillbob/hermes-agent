// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LunarCity } from './index'

afterEach(() => cleanup())

describe('LunarCity', () => {
  it('opens the world with buildings, group leaders, and the moon asset', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Lunar City' })).toBeTruthy()
    expect(screen.getByAltText(/isometric lunar settlement/i).getAttribute('src')).toBe('./lunar-city/moon-settlement.png')
    expect(screen.getByRole('button', { name: /Open Research Lab/i })).toBeTruthy()
    expect(screen.getByText('8 groups')).toBeTruthy()
  })

  it('lets the user enter a building and inspect its rooms', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Open Library/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Enter building' }))

    expect(screen.getByText('Inside Library')).toBeTruthy()
    expect(screen.getByText('Owl Librarian is managing this shift')).toBeTruthy()
    expect(screen.getByText('Consultation desk')).toBeTruthy()
    expect(screen.getByText('Quiet reading room')).toBeTruthy()
  })

  it('exposes the memory graph escape hatch and state filter', () => {
    const onOpenMemoryGraph = vi.fn()
    render(<LunarCity onOpenMemoryGraph={onOpenMemoryGraph} />)

    fireEvent.click(screen.getByRole('button', { name: 'Open memory graph' }))
    fireEvent.click(screen.getByRole('button', { name: 'Blocked' }))

    expect(onOpenMemoryGraph).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Blocked' }).getAttribute('aria-pressed')).toBe('true')
  })
})
