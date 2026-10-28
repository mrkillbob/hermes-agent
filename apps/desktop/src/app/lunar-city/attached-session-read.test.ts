import { describe, expect, it, vi } from 'vitest'

import { type AttachedRoute, readAttachedSessionInventory } from './attached-session-read'
const owner = { connectionId: 'registry-A', profile: 'default' }

function fixture(
  payload: unknown = { sessions: [{ id: 'stored-1', title: 'private', preview: 'private body', status: 'working' }] }
) {
  const request = vi.fn().mockResolvedValue(payload)
  const route: AttachedRoute = { ...owner, epoch: 4, gateway: { connectionState: 'open', request } }

  return { route, request }
}

describe('attached session inventory', () => {
  it('performs exactly one bounded profile read and publishes only inventory identity', async () => {
    const { route, request } = fixture()
    const abort = new AbortController()

    const result = await readAttachedSessionInventory(
      owner,
      () => route,
      abort.signal,
      () => 42
    )

    expect(request.mock.calls).toEqual([
      ['session.list', { profile: 'default', limit: 100, include_hidden: false }, 5000, abort.signal]
    ])
    expect(result).toEqual({
      status: 'read',
      entries: [{ ...owner, sessionId: 'stored-1', activity: 'unknown' }],
      observedAt: 42,
      window: 'up-to-100',
      activity: 'unavailable'
    })
  })
  it('does not call a transport for absent, disconnected, wrong-source or wrong-profile routes', async () => {
    const { route, request } = fixture()

    for (const current of [
      null,
      { ...route, connectionId: 'registry-B' },
      { ...route, profile: 'other' },
      { ...route, gateway: { ...route.gateway, connectionState: 'closed' } }
    ]) {
      expect(await readAttachedSessionInventory(owner, () => current)).toMatchObject({
        status: 'unavailable',
        entries: []
      })
    }

    expect(request).not.toHaveBeenCalled()
  })
  it('rejects responses from a changed epoch even if owner and socket return to their original values', async () => {
    const { route, request } = fixture()
    let current = route
    request.mockImplementation(async () => {
      current = { ...route, epoch: 6 }

      return { sessions: [{ id: 'x' }] }
    })
    expect(await readAttachedSessionInventory(owner, () => current)).toEqual({
      status: 'unavailable',
      reason: 'route-changed',
      entries: []
    })
  })
  it('rejects a replacement gateway with matching owner and epoch', async () => {
    const { route, request } = fixture()
    let current = route
    request.mockImplementation(async () => {
      current = { ...route, gateway: { ...route.gateway } }

      return { sessions: [] }
    })
    expect(await readAttachedSessionInventory(owner, () => current)).toMatchObject({ reason: 'route-changed' })
  })
  it.each([
    null,
    {},
    { sessions: null },
    { sessions: [{}] },
    { sessions: [{ id: ' ' }] },
    { sessions: [{ id: 'x' }, { id: 'x' }] },
    { sessions: Array.from({ length: 101 }, (_, i) => ({ id: String(i) })) }
  ])('refuses malformed or oversized response %#', async payload => {
    const { route } = fixture(payload)
    expect(await readAttachedSessionInventory(owner, () => route)).toEqual({
      status: 'unavailable',
      reason: 'invalid-response',
      entries: []
    })
  })
  it('accepts an empty bounded inventory without inventing idle activity', async () => {
    const { route } = fixture({ sessions: [] })
    expect(await readAttachedSessionInventory(owner, () => route)).toMatchObject({
      status: 'read',
      entries: [],
      activity: 'unavailable'
    })
  })
  it('cancels before and after a read without publishing its response', async () => {
    const { route, request } = fixture()
    const abort = new AbortController()
    abort.abort()
    expect(await readAttachedSessionInventory(owner, () => route, abort.signal)).toMatchObject({ reason: 'cancelled' })
    expect(request).not.toHaveBeenCalled()
    const after = new AbortController()
    request.mockImplementation(async () => {
      after.abort()

      return { sessions: [{ id: 'x' }] }
    })
    expect(await readAttachedSessionInventory(owner, () => route, after.signal)).toMatchObject({
      reason: 'cancelled',
      entries: []
    })
  })
  it('does not leak arbitrary server errors or return cached success on failure', async () => {
    const { route, request } = fixture()
    await readAttachedSessionInventory(owner, () => route)
    request.mockRejectedValue(new Error('https://private.invalid/?token=synthetic-secret'))
    const result = await readAttachedSessionInventory(owner, () => route)
    expect(result).toEqual({ status: 'unavailable', reason: 'read-failed', entries: [] })
    expect(JSON.stringify(result)).not.toContain('private')
  })
})
