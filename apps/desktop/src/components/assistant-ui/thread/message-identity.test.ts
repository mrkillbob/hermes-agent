import { describe, expect, it } from 'vitest'

import { createTranscriptMessageKeyCache, transcriptMessageKeys } from './message-identity'

describe('transcript message identity', () => {
  it('follows the durable row when equal-timestamp renderer ids are reused by a shifted page', () => {
    const cache = createTranscriptMessageKeyCache()

    const before = transcriptMessageKeys(
      [
        { id: '7-0-assistant', rowId: 11 },
        { id: '7-1-assistant', rowId: 14 }
      ],
      cache
    )

    const after = transcriptMessageKeys(
      [
        { id: '7-0-assistant', rowId: 14 },
        { id: '7-1-assistant', rowId: 16 }
      ],
      cache
    )

    expect(after[0]).toBe(before[1])
    expect(before).not.toContain(after[1])
    expect(new Set(after).size).toBe(2)
  })

  it('keeps an existing split-row fragment distinct from a newly inserted fragment', () => {
    const cache = createTranscriptMessageKeyCache()
    const [streamKey] = transcriptMessageKeys([{ id: 'fragment-1' }], cache)
    transcriptMessageKeys([{ id: 'fragment-1', rowId: 41 }], cache)

    const keys = transcriptMessageKeys(
      [
        { id: 'fragment-0', rowId: 41 },
        { id: 'fragment-1', rowId: 41 }
      ],
      cache
    )

    expect(new Set(keys).size).toBe(2)
    expect(keys[1]).toBe(streamKey)
    expect(keys[0]).not.toBe(streamKey)

    const reordered = transcriptMessageKeys(
      [
        { id: 'fragment-1', rowId: 41 },
        { id: 'fragment-0', rowId: 41 }
      ],
      cache
    )

    expect(reordered).toEqual([keys[1], keys[0]])
    expect(transcriptMessageKeys([{ id: 'fragment-0', rowId: 41 }], cache)).toEqual([keys[0]])
  })

  it('retains only bounded active-window identity records while preserving overlapping rows', () => {
    const cache = createTranscriptMessageKeyCache()
    let previous: string[] = []

    for (let page = 0; page < 64; page++) {
      const rows = Array.from({ length: 20 }, (_, index) => ({
        id: `page-${page}-slot-${index}`,
        rowId: page * 10 + index
      }))

      const keys = transcriptMessageKeys(rows, cache)

      if (page > 0) {
        expect(keys.slice(0, 10)).toEqual(previous.slice(10))
      }

      expect(new Set(keys).size).toBe(20)
      previous = keys
    }

    // A fixed twenty-row display must not retain every retired page or alias.
    expect(cache.byMessageId.size).toBeLessThanOrEqual(40)
    expect(cache.byRowId.size).toBeLessThanOrEqual(20)
  })
})
