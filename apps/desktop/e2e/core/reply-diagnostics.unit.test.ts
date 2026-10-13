import type { Page } from '@playwright/test'
import { expect, it, vi } from 'vitest'

import type { GatewayEventFrame } from './harness'
import { captureReplyFrames, projectReplyEvents } from './reply-diagnostics'

const frame = (payload: unknown, type = 'message.complete'): GatewayEventFrame => ({
  socket: 1,
  seq: 2,
  sessionId: 'synthetic',
  type,
  payload
})

it('retains synthetic markers and numeric receipt identity without transcript bodies', () => {
  expect(
    projectReplyEvents([
      frame({
        timestamp: 123,
        text: 'A20-example noted the process; private command',
        persisted_turn: { final_assistant_row_id: 7, private: 'omit' },
        command: 'omit'
      })
    ])
  ).toEqual([
    { socket: 1, seq: 2, type: 'message.complete', timestamp: 123, finalAssistantRowId: 7, markers: ['A20-example'] }
  ])
})

it('omits unrecognized event names and malformed receipt fields', () => {
  expect(
    projectReplyEvents([
      frame(
        { timestamp: Infinity, text: { secret: 'omit' }, persisted_turn: { final_assistant_row_id: 'omit' } },
        'unrecognized body'
      )
    ])
  ).toEqual([{ socket: 1, seq: 2, type: 'other', markers: [] }])
})

it('bounds the retained event and marker counts', () => {
  const events = Array.from({ length: 2_010 }, () => frame({ text: 'A20-example '.repeat(30) }))
  const projected = projectReplyEvents(events)
  expect(projected).toHaveLength(2_000)
  expect(projected.every(event => event.markers.length === 16)).toBe(true)
})

it('retains only synthetic session identity for correlation', () => {
  const event = { ...frame({}), sessionId: '20261013_190731_2f5b64' }
  expect(projectReplyEvents([event])[0]!.sessionId).toBe(event.sessionId)
  expect(projectReplyEvents([{ ...event, sessionId: '/private/other' }])[0]!.sessionId).toBeUndefined()
})

it('bounds a frozen renderer capture and clears its timer', async () => {
  vi.useFakeTimers()
  try {
    const page = { evaluate: () => new Promise(() => {}) } as unknown as Page
    const capture = captureReplyFrames(page)
    await vi.advanceTimersByTimeAsync(2_000)
    await expect(capture).resolves.toEqual([])
    expect(vi.getTimerCount()).toBe(0)
  } finally {
    vi.useRealTimers()
  }
})

it('keeps capture rejection from replacing the original test failure', async () => {
  const page = { evaluate: () => Promise.reject(new Error('renderer closed')) } as unknown as Page
  await expect(captureReplyFrames(page)).resolves.toEqual([])
})
