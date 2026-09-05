import { afterEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestConnection, setApiRequestProfile } from '@/hermes'

import { activeConnection, pluginRest } from './plugins'

// desktop.getConnection/getConnectionFor are IPC round-trips into the main
// process with no timeout of their own (#93454). A wedged main-process
// round-trip must reject instead of hanging pluginSocket's connect() forever.
describe('activeConnection connection timeout (#93454)', () => {
  afterEach(() => {
    setApiRequestConnection(null)
    setApiRequestProfile(null)
    Reflect.deleteProperty(window, 'hermesDesktop')
    vi.useRealTimers()
  })

  it('rejects instead of hanging forever when getConnection() wedges', async () => {
    vi.useFakeTimers()
    setApiRequestProfile('coder')
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { getConnection: vi.fn(() => new Promise(() => undefined)) }
    })

    const pending = expect(activeConnection()).rejects.toThrow('Timed out connecting to profile "coder"')

    await vi.advanceTimersByTimeAsync(20_000)
    await pending
  })

  it('rejects instead of hanging forever when getConnectionFor() wedges', async () => {
    vi.useFakeTimers()
    setApiRequestConnection('gw-tailscale')
    setApiRequestProfile('research')
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        getConnection: vi.fn(() => new Promise(() => undefined)),
        getConnectionFor: vi.fn(() => new Promise(() => undefined))
      }
    })

    const pending = expect(activeConnection()).rejects.toThrow('Timed out connecting to profile "research"')

    await vi.advanceTimersByTimeAsync(20_000)
    await pending
  })
})

describe('pluginRest explicit source scope', () => {
  afterEach(() => {
    setApiRequestConnection(null)
    setApiRequestProfile(null)
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  it('routes an explicit scope through the exact capability-scoped REST request', async () => {
    const api = vi.fn().mockResolvedValue({ ok: true })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })

    await expect(
      pluginRest('kanban', '/board', { method: 'GET', scope: { connectionId: 'source-b', profile: 'research' } })
    ).resolves.toEqual({ ok: true })

    expect(api).toHaveBeenCalledWith({
      path: '/api/plugins/kanban/board',
      method: 'GET',
      body: undefined,
      upload: undefined,
      timeoutMs: undefined,
      connectionId: 'source-b',
      profile: 'research'
    })
  })

  it('captures a trimmed frozen copy before the caller can mutate the scope', async () => {
    const api = vi.fn(() => new Promise(resolve => setTimeout(() => resolve({ ok: true }), 0)))
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })
    const scope = { connectionId: ' source-a ', profile: ' default ' }

    const pending = pluginRest('kanban', '/board', { scope })
    scope.connectionId = 'source-b'
    scope.profile = 'other'
    await pending

    expect(api).toHaveBeenCalledWith(expect.objectContaining({ connectionId: 'source-a', profile: 'default' }))
  })

  it('rejects an empty explicit scope instead of falling back to ambient routing', async () => {
    const api = vi.fn().mockResolvedValue({ ok: true })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })

    await expect(
      pluginRest('kanban', '/board', { scope: { connectionId: '  ', profile: 'research' } })
    ).rejects.toThrow('pluginRest: scope.connectionId must not be empty')
    expect(api).not.toHaveBeenCalled()
  })

  it('preserves the ambient request shape when scope is omitted', async () => {
    const api = vi.fn().mockResolvedValue({ ok: true })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api }
    })
    setApiRequestConnection('ambient-a')
    setApiRequestProfile('work')

    await pluginRest('kanban', '/board')

    expect(api).toHaveBeenCalledWith({
      path: '/api/plugins/kanban/board',
      method: undefined,
      body: undefined,
      upload: undefined,
      timeoutMs: undefined,
      connectionId: 'ambient-a',
      profile: 'work'
    })
  })
})
