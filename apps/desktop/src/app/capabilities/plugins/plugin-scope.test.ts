import { afterEach, describe, expect, it, vi } from 'vitest'

import { scopeIsForeignConnection } from './plugin-scope'

const active = vi.hoisted(() => ({ id: 'conn-a' as null | string }))

vi.mock('@/store/gateway', () => ({ activeGatewayConnectionId: () => active.id }))

describe('scopeIsForeignConnection', () => {
  afterEach(() => {
    active.id = 'conn-a'
  })

  it('is false for bare profiles and the active connection', () => {
    expect(scopeIsForeignConnection(null)).toBe(false)
    expect(scopeIsForeignConnection('work')).toBe(false)
    expect(scopeIsForeignConnection({ connectionId: 'conn-a', profile: 'work' })).toBe(false)
  })

  it('is true for another connection, treating a missing active id as local', () => {
    expect(scopeIsForeignConnection({ connectionId: 'conn-b', profile: 'work' })).toBe(true)
    active.id = null
    expect(scopeIsForeignConnection({ connectionId: 'local', profile: 'work' })).toBe(false)
    expect(scopeIsForeignConnection({ connectionId: 'conn-b', profile: 'work' })).toBe(true)
  })
})
