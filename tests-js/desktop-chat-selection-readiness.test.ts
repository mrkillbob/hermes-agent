import { expect, test } from 'vitest'

import { waitForMockSelection } from './scripts/desktop-chat-smoke.ts'
import type { ComposerSelection, SelectionCapture } from './scripts/desktop-selection-diagnostics.ts'

const captureWith = (readSelection: () => Promise<ComposerSelection | null>): SelectionCapture => ({
  checkpoint: async () => {},
  readSelection,
  stop: async () => {},
})

test('waits for both seeded composer values before allowing the smoke to continue', async () => {
  const selections: ComposerSelection[] = [
    { model: null, provider: null, source: 'default' },
    { model: 'mock-model', provider: null, source: 'default' },
    { model: 'mock-model', provider: 'custom', source: 'default' },
  ]

  let reads = 0

  const ready = await waitForMockSelection(captureWith(async () => {
    reads += 1

    return selections.shift() ?? null
  }), { timeoutMs: 1_000, intervalMs: 1 })

  expect(ready).toEqual({ model: 'mock-model', provider: 'custom', source: 'default' })
  expect(reads).toBe(3)
})

test('fails with fixed selection states when the seeded route never becomes ready', async () => {
  await expect(waitForMockSelection(captureWith(async () => ({ model: null, provider: null, source: 'default' })),
    { timeoutMs: 1, intervalMs: 1 })).rejects.toThrow(
    'Seeded mock selection did not become ready; chat was not submitted (model=missing provider=missing)')
})

test('does not trust non-fixture values as the seeded provider route', async () => {
  await expect(waitForMockSelection(captureWith(async () => ({
    model: 'non-fixture', provider: 'non-fixture', source: 'default',
  })), { timeoutMs: 1, intervalMs: 1 })).rejects.toThrow(
    'Seeded mock selection did not become ready; chat was not submitted (model=non-fixture provider=non-fixture)')
})
