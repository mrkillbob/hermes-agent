import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { entityKey } from '../identity'
import { $lunarCitySnapshot, createLunarCitySnapshot, type LunarDelta } from '../store'
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
    const local = { connectionId: 'local', profile: 'worker', sessionId: 'session-a' }
    const remote = { connectionId: 'ssh-1', profile: 'worker', sessionId: 'session-a' }

    expect(reconciler.acceptEvent({ sequence: 5, source: local })).toBe('accepted')
    expect(reconciler.acceptEvent({ sequence: 5, source: remote })).toBe('accepted')
    expect(reconciler.acceptEvent({ sequence: 5, source: local })).toBe('ignored')
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
    reconciler.acceptEvent({ sequence: 3, source: { connectionId: 'local', profile: 'worker', sessionId: 'session-a' } })
    reconciler.acceptEvent({ sequence: 5, source: { connectionId: 'local', profile: 'worker', sessionId: 'session-a' } })
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

  it('stales only expired sources and reschedules the next source deadline', async () => {
    vi.useFakeTimers()
    let now = 0
    const sessionIdentity = { connectionId: 'ssh-1', kind: 'session' as const, profile: 'worker', sessionId: 's-1' }
    const sessionEntity = {
      animation: 'work',
      authority: 'authoritative' as const,
      destination: 'project' as const,
      identity: sessionIdentity,
      key: entityKey(sessionIdentity),
      observedAt: 50
    }
    const reconciler = createLunarCityReconciler({
      freshnessMs: 50,
      now: () => now,
      read: async () => ({
        authoritative: true,
        entities: [profile, sessionEntity],
        sources: [
          { authority: 'authoritative' as const, observedAt: 0, source: 'fleet:local' },
          { authority: 'authoritative' as const, observedAt: 50, source: 'session:ssh-1' }
        ]
      })
    })

    const stop = reconciler.start()
    await flush()
    now = 50
    await vi.advanceTimersByTimeAsync(50)

    expect($lunarCitySnapshot.get().entities.get(profile.key)).toMatchObject({ authority: 'stale' })
    expect($lunarCitySnapshot.get().entities.get(sessionEntity.key)).toMatchObject({ authority: 'authoritative' })
    expect($lunarCitySnapshot.get().sources).toEqual([
      expect.objectContaining({ authority: 'stale', source: 'fleet:local' }),
      expect.objectContaining({ authority: 'authoritative', source: 'session:ssh-1' })
    ])

    now = 100
    await vi.advanceTimersByTimeAsync(50)
    expect($lunarCitySnapshot.get().entities.get(sessionEntity.key)).toMatchObject({ authority: 'stale' })
    stop()
  })

  it('merges source health from a partial read instead of dropping unaffected sources', async () => {
    const sessionIdentity = { connectionId: 'ssh-1', kind: 'session' as const, profile: 'worker', sessionId: 's-1' }
    const sessionEntity = {
      animation: 'work',
      authority: 'authoritative' as const,
      destination: 'project' as const,
      identity: sessionIdentity,
      key: entityKey(sessionIdentity),
      observedAt: 42
    }
    let reads = 0
    const reconciler = createLunarCityReconciler({
      read: async () => {
        reads += 1
        return reads === 1
          ? {
              authoritative: true,
              entities: [profile, sessionEntity],
              sources: [
                { authority: 'authoritative' as const, observedAt: 42, source: 'fleet:local' },
                { authority: 'authoritative' as const, observedAt: 42, source: 'session:ssh-1' }
              ]
            }
          : {
              authoritative: false,
              entities: [{ ...profile, animation: 'unavailable', authority: 'stale' as const, destination: 'unknown' as const }],
              sources: [{ authority: 'stale' as const, error: 'fleet failed', observedAt: 42, source: 'fleet:local' }]
            }
      }
    })

    const stop = reconciler.start()
    await flush()
    reconciler.invalidate('focus')
    await flush()

    expect($lunarCitySnapshot.get().entities.get(sessionEntity.key)).toMatchObject({ authority: 'authoritative' })
    expect($lunarCitySnapshot.get().sources).toEqual([
      expect.objectContaining({ authority: 'stale', source: 'fleet:local' }),
      expect.objectContaining({ authority: 'authoritative', source: 'session:ssh-1' })
    ])
    stop()
  })

  it('freezes the complete delta payload before handing it to a publisher', async () => {
    let published: LunarDelta | undefined
    const reconciler = createLunarCityReconciler({
      publish: delta => {
        published = delta
      },
      read: async () => currentRead()
    })

    const stop = reconciler.start()
    await flush()

    expect(Object.isFrozen(published)).toBe(true)
    expect(Object.isFrozen(published!.upserts)).toBe(true)
    expect(Object.isFrozen(published!.upserts[0]!)).toBe(true)
    expect(Object.isFrozen(published!.upserts[0]!.identity)).toBe(true)
    expect(Object.isFrozen(published!.sources)).toBe(true)
    expect(Object.isFrozen(published!.sources[0]!)).toBe(true)
    expect(() => (published!.upserts as LunarDelta['upserts'] & { push: (value: typeof profile) => void }).push(profile)).toThrow()
    stop()
  })

  it('accepts restarted lower sequence values after an exact-source reset without resetting a colliding source', () => {
    const reconciler = createLunarCityReconciler({ read: async () => currentRead() })
    const restarted = { connectionId: 'a/b', profile: 'c', sessionId: 'd' }
    const untouched = { connectionId: 'a', profile: 'b/c', sessionId: 'd' }

    expect(reconciler.acceptEvent({ sequence: 5, source: restarted })).toBe('accepted')
    expect(reconciler.acceptEvent({ sequence: 5, source: untouched })).toBe('accepted')
    reconciler.resetSequences({ connectionId: 'a/b', profile: 'c' })
    expect(reconciler.acceptEvent({ sequence: 1, source: restarted })).toBe('accepted')
    expect(reconciler.acceptEvent({ sequence: 1, source: untouched })).toBe('ignored')
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

          return { status: 'failed' } as never
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
