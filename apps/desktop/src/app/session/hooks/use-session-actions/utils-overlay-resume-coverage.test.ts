import { describe, expect, it } from 'vitest'

import { type ChatMessage, chatMessageText } from '@/lib/chat-messages'

import { clearLiveSegmentStart, liveSegmentStart, noteLiveSegmentStart } from '../use-message-stream/segment-identity'

import { overlayConcurrentMessageChanges } from './utils'

const reply = (id: string, text: string) =>
  ({ id, role: 'assistant', parts: [{ type: 'text', text }], pending: true }) as ChatMessage

const merged = (snapshot: string, live: string, coverage: { liveStart: number; snapshotEnd: number }) =>
  overlayConcurrentMessageChanges(
    [reply('assistant-stream-activation', snapshot)],
    [],
    [reply('assistant-stream-runtime', live)],
    coverage
  )

describe('overlayConcurrentMessageChanges resume coverage', () => {
  it('keeps live text that starts at the end of an exclusive snapshot', () => {
    const rows = merged('A2 ', 'answer across a reload', { liveStart: 3, snapshotEnd: 3 })

    expect(rows.map(row => row.id)).toEqual(['assistant-stream-runtime'])
    expect(chatMessageText(rows[0])).toBe('A2 answer across a reload')
  })

  it('does not repeat live text an inclusive snapshot already carries', () => {
    const rows = merged('A2 answer across a reload', 'answer across a reload', { liveStart: 3, snapshotEnd: 25 })

    expect(rows).toHaveLength(1)
    expect(chatMessageText(rows[0])).toBe('A2 answer across a reload')
  })

  it('keeps only the part of the live text beyond a partly covering snapshot', () => {
    const rows = merged('A2 answer', 'answer across a reload', { liveStart: 3, snapshotEnd: 9 })

    expect(chatMessageText(rows[0])).toBe('A2 answer across a reload')
  })
})

describe('live segment start registry', () => {
  it('records the first delta of a segment and keeps it while the segment extends', () => {
    clearLiveSegmentStart('rt')
    noteLiveSegmentStart('rt', undefined, { turnId: 't', segment: 1, offset: 3 })
    noteLiveSegmentStart('rt', { turnId: 't', segment: 1, end: 25 }, { turnId: 't', segment: 1, offset: 25 })

    expect(liveSegmentStart('rt')).toEqual({ turnId: 't', segment: 1, start: 3 })

    noteLiveSegmentStart('rt', { turnId: 't', segment: 1, end: 30 }, { turnId: 't', segment: 2, offset: 0 })

    expect(liveSegmentStart('rt')).toEqual({ turnId: 't', segment: 2, start: 0 })
    clearLiveSegmentStart('rt')
  })
})
