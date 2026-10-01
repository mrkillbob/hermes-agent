/** Regression coverage for #77429: native picker options must paint a readable surface. */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { QuickEntryStatePush } from '@/store/quick-entry'

import { createQuickEntrySubmitRelay } from '../../../electron/quick-entry'

import { QuickEntryApp } from './quick-entry-app'

const initialHermesDesktop = window.hermesDesktop

describe('QuickEntryApp', () => {
  let pushState: ((payload: QuickEntryStatePush) => void) | undefined

  beforeEach(() => {
    pushState = undefined
    window.hermesDesktop = {
      quickEntry: {
        dismiss: vi.fn(),
        onShown: vi.fn(() => vi.fn()),
        onState: vi.fn(callback => {
          pushState = callback

          return vi.fn()
        }),
        onLateResult: vi.fn(() => vi.fn()),
        submit: vi.fn()
      }
    } as never
  })

  afterEach(() => {
    cleanup()
    window.hermesDesktop = initialHermesDesktop
    vi.restoreAllMocks()
  })

  it('reconciles only the timed-out generation and keeps non-retryable late outcomes blocked', async () => {
    vi.useFakeTimers()
    try {
      let onLate: ((payload: unknown) => void) | undefined
      const correlations: string[] = []
      const relay = createQuickEntrySubmitRelay({
        onSuccess: vi.fn(),
        timeoutMs: 10,
        onLateResult: (correlationId, result) => onLate?.({ correlationId, result })
      })
      const api = window.hermesDesktop.quickEntry
      vi.mocked(api.onLateResult).mockImplementation(callback => {
        onLate = callback as never
        return vi.fn()
      })
      vi.mocked(api.submit).mockImplementation(() => relay.begin(id => correlations.push(id)) as never)
      render(<QuickEntryApp />)
      act(() => pushState?.({ connected: true, sessions: [] }))
      const input = screen.getByRole('textbox') as HTMLInputElement
      fireEvent.change(input, { target: { value: 'older prompt' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      fireEvent.change(input, { target: { value: 'newer prompt' } })
      fireEvent.keyDown(input, { key: 'Enter' })
      expect(correlations).toHaveLength(2)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10)
      })
      act(() => relay.acknowledge(correlations[0], { ok: true }))
      expect(input.value).toBe('newer prompt')
      fireEvent.keyDown(input, { key: 'Enter' })
      expect(correlations).toHaveLength(2)
      act(() => relay.acknowledge(correlations[1], { ok: false, retryable: false, message: 'acceptance unknown' }))
      fireEvent.keyDown(input, { key: 'Enter' })
      expect(correlations).toHaveLength(2)
      expect(input.value).toBe('newer prompt')
    } finally {
      vi.useRealTimers()
    }
  })

  it('paints every native target option with matching theme foreground and background tokens', () => {
    render(<QuickEntryApp />)

    act(() => {
      pushState?.({
        connected: true,
        sessions: [{ id: 'session-1', title: 'A recent session' }]
      })
    })

    const options = screen.getAllByRole('option') as HTMLOptionElement[]
    expect(options).toHaveLength(3)

    for (const option of options) {
      expect(option.style.color).toBe('var(--ui-text-primary, var(--foreground))')
      expect(option.style.backgroundColor).toBe('var(--ui-bg-elevated, var(--background))')
    }
  })
})
