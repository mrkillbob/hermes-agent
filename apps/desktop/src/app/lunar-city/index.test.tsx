// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LunarCity } from './index'

afterEach(() => cleanup())

describe('LunarCity', () => {
  it('opens the world with buildings, group leaders, and the moon asset', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Lunar City' })).toBeTruthy()
    expect(screen.getByAltText(/isometric lunar settlement/i).getAttribute('src')).toBe(
      './lunar-city/moon-settlement-approved.jpg'
    )
    expect(screen.getByRole('button', { name: /Open Research Lab/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Inspect Fox Scientist' }).getAttribute('data-character-kind')).toBe(
      'leader'
    )
    expect(screen.getByText('SIMULATION')).toBeTruthy()
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

  it('exposes the memory graph escape hatch', () => {
    const onOpenMemoryGraph = vi.fn()
    render(<LunarCity onOpenMemoryGraph={onOpenMemoryGraph} />)

    fireEvent.click(screen.getByRole('button', { name: 'Open memory graph' }))

    expect(onOpenMemoryGraph).toHaveBeenCalledOnce()
  })

  it('shows live task progress and moving workers without a state legend', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    expect(screen.getByText('MISSIONS')).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: 'Survey the archive' })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /Inspect .* worker/ }).length).toBeGreaterThanOrEqual(5)
    expect(screen.getByRole('button', { name: /Inspect Pip worker/ }).getAttribute('data-worker-design')).toBe(
      'orbital'
    )
    expect(screen.queryByText('Worker states')).toBeNull()
    expect(screen.getByTestId('lunar-city-viewport').getAttribute('data-camera')).toBe('isometric')
  })

  it('supports low-cost camera zoom for the isometric world', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    const viewport = screen.getByTestId('lunar-city-viewport')
    const initialZoom = viewport.getAttribute('data-zoom')

    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))

    expect(viewport.getAttribute('data-zoom')).not.toBe(initialZoom)
    expect(screen.getByRole('button', { name: 'Reset camera' })).toBeTruthy()
  })

  it('advances task progress while the simulation is playing', async () => {
    vi.useFakeTimers()

    try {
      render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

      const progress = screen.getByRole('progressbar', { name: 'Survey the archive' })
      const initialProgress = progress.getAttribute('aria-valuenow')

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1200)
      })

      expect(progress.getAttribute('aria-valuenow')).not.toBe(initialProgress)
    } finally {
      vi.useRealTimers()
    }
  })
})
