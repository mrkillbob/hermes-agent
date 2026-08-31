import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { entityKey } from '../identity'
import { $lunarCitySnapshot, createLunarCitySnapshot } from '../store'
import { createLunarCityReconciler, shouldReconcile, startLunarCityReconciler } from './reconciler'

const profileIdentity = { connectionId: 'local', kind: 'profile' as const, profile: 'worker' }
const profile = {
  animation: 'rest',
  authority: 'authoritative' as const,
  destination: 'garden' as const,
  identity: profileIdentity,
  key: entityKey(profileIdentity),
  observedAt: 0
}

const currentRead = () => ({
  authoritative: true,
  entities: [profile],
  sources: [{ authority: 'authoritative' as const, observedAt: 0, source: 'fleet:local' }]
})

async function flush(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
}

afterEach(() => {
  vi.useRealTimers()
  $lunarCitySnapshot.set(createLunarCitySnapshot())
})

describe('Lunar City reconciler', () => {
  it('recognizes only skipped backend revisions as a reconciliation gap', () => {
    expect(shouldReconcile(4, 6)).toBe(true)
    expect(shouldReconcile(4, 5)).toBe(false)
    expect(shouldReconcile(4, 4)).toBe(false)
  })

  it('tracks backend sequence cursors per exact source rather than globally', () => {
    const reconciler = createLunarCityReconciler({ read: async () => currentRead() })

    expect(reconciler.acceptEvent({ sequence: 5, source: 'local/worker/session-a' })).toBe('accepted')
    expect(reconciler.acceptEvent({ sequence: 5, source: 'ssh-1/worker/session-a' })).toBe('accepted')
    expect(reconciler.acceptEvent({ sequence: 5, source: 'local/worker/session-a' })).toBe('ignored')
  })

  it('coalesces gaps, reconnects, and focus invalidations into one queued reread', async () => {
    let readCount = 0
    let resolveFirst!: (value: ReturnType<typeof currentRead>) => void
    const first = new Promise<ReturnType<typeof currentRead>>(resolve => {
      resolveFirst = resolve
    })
    const reconciler = createLunarCityReconciler({
      read: async () => {
        readCount += 1
        return readCount === 1 ? first : currentRead()
      }
    })

    const stop = reconciler.start()
    await flush()
    reconciler.acceptEvent({ sequence: 3, source: 'local/worker/session-a' })
    reconciler.acceptEvent({ sequence: 5, source: 'local/worker/session-a' })
    reconciler.invalidate('gateway.ready')
    reconciler.invalidate('focus')
    resolveFirst(currentRead())
    await flush()
    await flush()

    expect(readCount).toBe(2)
    stop()
  })

  it('marks retained identity stale at the single freshness deadline', async () => {
    vi.useFakeTimers()
    let now = 0
    const reconciler = createLunarCityReconciler({ freshnessMs: 50, now: () => now, read: async () => currentRead() })

    const stop = reconciler.start()
    await flush()
    now = 50
    await vi.advanceTimersByTimeAsync(50)

    expect($lunarCitySnapshot.get().entities.get(profile.key)).toMatchObject({
      animation: 'unavailable',
      authority: 'stale',
      destination: 'unknown'
    })
    expect($lunarCitySnapshot.get().sources[0]).toMatchObject({ authority: 'stale', observedAt: 0 })
    stop()
  })

  it('prevents a late read from publishing after disposal', async () => {
    let resolveRead!: (value: ReturnType<typeof currentRead>) => void
    const delayed = new Promise<ReturnType<typeof currentRead>>(resolve => {
      resolveRead = resolve
    })
    const reconciler = createLunarCityReconciler({ read: async () => delayed })

    const stop = reconciler.start()
    await flush()
    stop()
    resolveRead(currentRead())
    await flush()

    expect($lunarCitySnapshot.get().revision).toBe(0)
  })

  it('forces one fleet read at route mount without stamping retained data fresh after a failed refresh', async () => {
    let now = 42
    const fleet = atom({
      agents: [
        {
          connectionId: 'local',
          connectionKind: 'local' as const,
          connectionLabel: 'this Mac',
          handle: '@worker-local',
          profile: 'worker'
        }
      ],
      sources: [{ connectionId: 'local', kind: 'local' as const, label: 'this Mac', reachable: true }]
    })
    const sessions = atom([])
    const subagents = atom({})
    let refreshes = 0
    let focus!: () => void
    const stop = startLunarCityReconciler({
      now: () => now,
      sources: {
        $fleetRoster: fleet,
        $sessions: sessions,
        $subagentsBySession: subagents,
        legacySingleBackend: () => false,
        onFocus: listener => {
          focus = listener
          return () => undefined
        },
        refreshFleet: async () => {
          refreshes += 1

          if (refreshes === 1) {
            fleet.set({ ...fleet.get() })
            return
          }

          throw new Error('sleeping remote')
        }
      }
    })
    await flush()

    expect($lunarCitySnapshot.get().sources).toEqual([
      { authority: 'authoritative', observedAt: 42, source: 'fleet:local' }
    ])
    now = 100
    focus()
    await flush()

    expect(refreshes).toBe(2)
    expect($lunarCitySnapshot.get().sources[0]).toMatchObject({
      authority: 'stale',
      error: 'Fleet refresh failed',
      observedAt: 42
    })
    expect($lunarCitySnapshot.get().entities.get(profile.key)).toMatchObject({ authority: 'stale' })
    stop()
  })
})
