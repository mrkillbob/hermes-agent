import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'

// pluginSocket must dial the ACTIVE gateway's backend — resolved through the
// same (connectionId, profile) source of truth ensureGatewayProfile /
// ensureGatewayAgent maintain for $connection — not the unscoped primary
// (#73044). Exercises the REAL hermes + store/gateway + store/profile chain:
//  1. A profile switch routes the plugin socket to the pooled profile backend
//     (getConnection(profile), like pluginRest's profileScoped()).
//  2. A registry-agent activation routes it to the agent's SOURCE connection
//     (getConnectionFor), not the local pool — the post-#87600 shape.

vi.mock('@/hermes', async importOriginal => {
  const actual = await importOriginal<Record<string, unknown>>()

  return {
    ...actual,
    // Stub only the socket class so gateway activations don't dial real WS.
    HermesGateway: class {
      connectionState = 'closed'
      connect = async (_wsUrl: string): Promise<void> => {
        this.connectionState = 'open'
      }
      close = (): void => {
        this.connectionState = 'closed'
      }
      onEvent = vi.fn(() => () => {})
      onState = vi.fn(() => () => {})
    }
  }
})
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph: vi.fn() }))

const { pluginSocket, setApiRequestConnection, setApiRequestProfile } = await import('@/hermes')
const { closeSecondaryGateways, configureGatewayRegistry, setPrimaryGateway } = await import('@/store/gateway')
const { $activeGatewayProfile, ensureGatewayAgent, ensureGatewayProfile } = await import('@/store/profile')

// authMode 'oauth' makes pluginSocket stop after resolving the connection
// (polling fallback), so the assertions cover resolution without a WS dial.
const conn = (over: Partial<HermesConnection> = {}): HermesConnection =>
  ({
    authMode: 'oauth',
    baseUrl: 'https://pool.invalid',
    mode: 'remote',
    token: 'fake-test-token',
    wsUrl: 'wss://pool.invalid/api/ws?token=fake-test-token',
    ...over
  }) as HermesConnection

let getConnection: ReturnType<typeof vi.fn>
let getConnectionFor: ReturnType<typeof vi.fn>

beforeEach(() => {
  getConnection = vi.fn(async (profile?: null | string) => conn({ profile: profile ?? 'default' }))
  getConnectionFor = vi.fn(async ({ profile }: { connectionId?: null | string; profile?: null | string }) =>
    conn({ baseUrl: 'https://homelab.invalid', profile: profile ?? 'default' })
  )
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: {
      getConnection,
      getConnectionFor,
      getGatewayWsUrl: vi.fn(async () => 'wss://pool.invalid/api/ws?ticket=fake'),
      getGatewayWsUrlFor: vi.fn(async () => 'wss://homelab.invalid/api/ws?ticket=fake'),
      touchBackend: vi.fn(async () => ({ ok: true }))
    }
  })
  configureGatewayRegistry({ onEvent: vi.fn() })
  setPrimaryGateway({ connectionState: 'open' } as never, 'default')
})

afterEach(() => {
  closeSecondaryGateways()
  $activeGatewayProfile.set('default')
  setApiRequestProfile(null)
  setApiRequestConnection(null)
  Reflect.deleteProperty(window, 'hermesDesktop')
  vi.restoreAllMocks()
})

describe('pluginSocket active-backend scoping (#73044)', () => {
  it('dials the active profile backend after a profile switch', async () => {
    await ensureGatewayProfile('work')
    getConnection.mockClear()
    getConnectionFor.mockClear()

    const dispose = pluginSocket('kanban', '/events', () => {})

    await vi.waitFor(() => expect(getConnection).toHaveBeenCalled())
    expect(getConnection).toHaveBeenCalledWith('work')
    expect(getConnectionFor).not.toHaveBeenCalled()

    dispose()
  })

  it("dials the agent's SOURCE connection after a registry-agent activation", async () => {
    await ensureGatewayAgent('homelab', 'research')
    getConnection.mockClear()
    getConnectionFor.mockClear()

    const dispose = pluginSocket('kanban', '/events', () => {})

    await vi.waitFor(() => expect(getConnectionFor).toHaveBeenCalled())
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'homelab', profile: 'research' })
    expect(getConnection).not.toHaveBeenCalled()

    dispose()
  })

  it('falls back to the primary when no profile or connection is active', async () => {
    const dispose = pluginSocket('kanban', '/events', () => {})

    await vi.waitFor(() => expect(getConnection).toHaveBeenCalled())
    expect(getConnection).toHaveBeenCalledWith(null)

    dispose()
  })
})

class FakePluginWebSocket {
  static instances: FakePluginWebSocket[] = []
  readonly url: string
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  closed = false

  constructor(url: string) {
    this.url = url
    FakePluginWebSocket.instances.push(this)
  }

  close(): void {
    if (this.closed) {
      return
    }

    this.closed = true
    this.onclose?.()
  }

  drop(): void {
    this.onclose?.()
  }

  open(): void {
    this.onopen?.()
  }
}

const scopedConn = (connectionId: string, profile: string, over: Partial<HermesConnection> = {}): HermesConnection =>
  conn({
    authMode: 'token',
    baseUrl: `https://${connectionId}.invalid`,
    connectionId,
    profile,
    registryScoped: true,
    token: 'scoped-token',
    ...over
  })

describe('pluginSocket explicit source scope', () => {
  beforeEach(() => {
    FakePluginWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakePluginWebSocket)
    Object.defineProperty(window, 'WebSocket', { configurable: true, writable: true, value: FakePluginWebSocket })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses only getConnectionFor for an explicit scope, regardless of ambient source', async () => {
    setApiRequestConnection('ambient-a')
    setApiRequestProfile('ambient-profile')
    const source = scopedConn('source-b', 'research')
    getConnectionFor.mockResolvedValue(source)

    const dispose = pluginSocket('kanban', '/events', () => {}, {
      scope: { connectionId: 'source-b', profile: 'research' }
    })

    await vi.waitFor(() => expect(getConnectionFor).toHaveBeenCalled())
    await vi.waitFor(() => expect(FakePluginWebSocket.instances).toHaveLength(1))
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'source-b', profile: 'research' })
    expect(getConnection).not.toHaveBeenCalled()
    expect(FakePluginWebSocket.instances[0]?.url).toBe(
      'wss://source-b.invalid/api/plugins/kanban/events?token=scoped-token'
    )
    dispose()
  })

  it('retains the captured scope across reconnect and ignores caller mutation', async () => {
    const source = scopedConn('source-a', 'default')
    getConnectionFor.mockResolvedValue(source)
    const scope = { connectionId: ' source-a ', profile: ' default ' }
    const dispose = pluginSocket('kanban', '/events', () => {}, { scope })
    scope.connectionId = 'source-b'
    scope.profile = 'other'

    await vi.waitFor(() => expect(FakePluginWebSocket.instances).toHaveLength(1))
    FakePluginWebSocket.instances[0].drop()
    await vi.waitFor(() => expect(getConnectionFor).toHaveBeenCalledTimes(2), { timeout: 2_000 })
    expect(getConnectionFor).toHaveBeenNthCalledWith(1, { connectionId: 'source-a', profile: 'default' })
    expect(getConnectionFor).toHaveBeenNthCalledWith(2, { connectionId: 'source-a', profile: 'default' })
    dispose()
  })

  it('reports only a completed reconnect after the initial scoped socket has opened', async () => {
    const source = scopedConn('source-a', 'default')
    const reconnected = vi.fn()
    getConnectionFor.mockResolvedValue(source)

    const dispose = pluginSocket('kanban', '/events', () => {}, {
      onReconnect: reconnected,
      scope: { connectionId: 'source-a', profile: 'default' }
    })

    await vi.waitFor(() => expect(FakePluginWebSocket.instances).toHaveLength(1))
    FakePluginWebSocket.instances[0]?.open()
    expect(reconnected).not.toHaveBeenCalled()

    FakePluginWebSocket.instances[0]?.drop()
    await vi.waitFor(() => expect(FakePluginWebSocket.instances).toHaveLength(2), { timeout: 2_000 })
    FakePluginWebSocket.instances[1]?.open()

    expect(reconnected).toHaveBeenCalledOnce()
    dispose()
  })

  it('never ambient-dials when getConnectionFor is unavailable or the descriptor mismatches', async () => {
    const desktop = window.hermesDesktop
    Reflect.deleteProperty(desktop, 'getConnectionFor')

    const unavailableDispose = pluginSocket('kanban', '/events', () => {}, {
      scope: { connectionId: 'source-b', profile: 'research' }
    })

    await Promise.resolve()
    expect(getConnection).not.toHaveBeenCalled()
    expect(FakePluginWebSocket.instances).toHaveLength(0)
    unavailableDispose()

    desktop.getConnectionFor = vi.fn().mockResolvedValue(scopedConn('other', 'research'))

    const mismatchDispose = pluginSocket('kanban', '/events', () => {}, {
      scope: { connectionId: 'source-b', profile: 'research' }
    })

    await Promise.resolve()
    await Promise.resolve()
    expect(desktop.getConnectionFor).toHaveBeenCalledWith({ connectionId: 'source-b', profile: 'research' })
    expect(FakePluginWebSocket.instances).toHaveLength(0)
    mismatchDispose()
  })

  it('does not dial or deliver a late result after disposal during resolution', async () => {
    let resolveConnection: (connection: HermesConnection) => void = () => undefined
    getConnectionFor.mockImplementation(
      () =>
        new Promise<HermesConnection>(resolve => {
          resolveConnection = resolve
        })
    )

    const dispose = pluginSocket('kanban', '/events', () => {}, {
      scope: { connectionId: 'source-b', profile: 'research' }
    })

    dispose()
    resolveConnection(scopedConn('source-b', 'research'))
    await Promise.resolve()
    await Promise.resolve()
    expect(FakePluginWebSocket.instances).toHaveLength(0)
  })

  it('keeps OAuth explicit sockets as polling no-ops', async () => {
    getConnectionFor.mockResolvedValue(scopedConn('source-b', 'research', { authMode: 'oauth' }))

    const dispose = pluginSocket('kanban', '/events', () => {}, {
      scope: { connectionId: 'source-b', profile: 'research' }
    })

    await vi.waitFor(() => expect(getConnectionFor).toHaveBeenCalled())
    expect(FakePluginWebSocket.instances).toHaveLength(0)
    dispose()
  })

  it('rejects empty explicit scope before resolving any backend', () => {
    expect(() =>
      pluginSocket('kanban', '/events', () => {}, { scope: { connectionId: ' ', profile: 'research' } })
    ).toThrow('pluginSocket: scope.connectionId must not be empty')
    expect(getConnectionFor).not.toHaveBeenCalled()
  })
})
