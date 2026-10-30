import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  activeGateway,
  activeGatewayConnectionId,
  activeGatewayProfileKey,
  gatewayActivationEpoch
} from '@/store/gateway'

import { currentlyAttachedSessionRoute, readCurrentAttachedSessions } from './attached-session-source'
vi.mock('@/store/gateway', () => ({
  activeGateway: vi.fn(),
  activeGatewayConnectionId: vi.fn(),
  activeGatewayProfileKey: vi.fn(),
  gatewayActivationEpoch: vi.fn()
}))
const owner = { connectionId: 'registry-a', profile: 'default' }
beforeEach(() => vi.resetAllMocks())
describe('current gateway binding', () => {
  it('returns unavailable without a transport when the window has no open gateway', async () => {
    vi.mocked(activeGateway).mockReturnValue(null)
    vi.mocked(activeGatewayConnectionId).mockReturnValue(owner.connectionId)
    vi.mocked(activeGatewayProfileKey).mockReturnValue(owner.profile)
    expect(currentlyAttachedSessionRoute()).toBeNull()
    expect(await readCurrentAttachedSessions(owner)).toMatchObject({ reason: 'not-attached', entries: [] })
  })
  it('binds to the existing socket and one exact registry owner', async () => {
    const request = vi.fn().mockResolvedValue({ sessions: [{ id: 'stored' }] })
    vi.mocked(activeGateway).mockReturnValue({ connectionState: 'open', request } as unknown as NonNullable<
      ReturnType<typeof activeGateway>
    >)
    vi.mocked(activeGatewayConnectionId).mockReturnValue(owner.connectionId)
    vi.mocked(activeGatewayProfileKey).mockReturnValue(owner.profile)
    vi.mocked(gatewayActivationEpoch).mockReturnValue(8)
    expect(await readCurrentAttachedSessions({ ...owner, connectionId: 'registry-b' })).toMatchObject({
      reason: 'not-attached'
    })
    expect(request).not.toHaveBeenCalled()
    expect(await readCurrentAttachedSessions(owner)).toMatchObject({
      status: 'read',
      entries: [{ ...owner, sessionId: 'stored', activity: 'unknown' }]
    })
    expect(request).toHaveBeenCalledTimes(1)
  })
})
