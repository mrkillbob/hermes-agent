import { describe, expect, it } from 'vitest'
import type { EntityKey, LunarEntity } from './model'
import { resolveCityStatus } from './city-status'

const NOW = 1_700_000_000_000

function entityWith(overrides: Partial<LunarEntity>): LunarEntity {
  return {
    authority: 'authoritative',
    animation: 'rest',
    destination: 'project',
    identity: { kind: 'session', connectionId: 'local', profile: 'default', sessionId: 'session-a' },
    key: 'local/default/session-a' as EntityKey,
    observedAt: NOW,
    ...overrides
  }
}

describe('resolveCityStatus', () => {
  it('uses blocked before working, celebrating, or waiting', () => {
    const entity = entityWith({ sourceState: 'blocked', animation: 'work' })
    expect(resolveCityStatus(entity, NOW).status).toBe('blocked')
    expect(resolveCityStatus(entity, NOW).badge).toBe('!')
  })

  it('does not promote stale or unknown entities into active states', () => {
    const entity = entityWith({ authority: 'stale', sourceState: 'running', animation: 'work' })
    expect(resolveCityStatus(entity, NOW).status).toBe('unavailable')
    expect(resolveCityStatus(entity, NOW).badge).toBeUndefined()
  })

  it('marks inactive authoritative entities as sleeping after three days', () => {
    const entity = entityWith({ observedAt: NOW - 4 * 24 * 60 * 60 * 1000 })
    expect(resolveCityStatus(entity, NOW).status).toBe('sleeping')
  })

  it('keeps explicit waiting visible after active work completes', () => {
    const entity = entityWith({ sourceState: 'waiting', signals: { waiting: true } })
    expect(resolveCityStatus(entity, NOW).status).toBe('waiting')
    expect(resolveCityStatus(entity, NOW).badge).toBe('?')
  })
})
