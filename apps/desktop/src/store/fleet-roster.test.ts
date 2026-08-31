import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DesktopAgentRoster } from '@/global'

import { $fleetRoster, _resetFleetRosterForTests, refreshFleetRoster } from './fleet-roster'

const roster: DesktopAgentRoster = {
  agents: [],
  sources: [{ connectionId: 'local', kind: 'local', label: 'this Mac', reachable: true }]
}

const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }
const initialHermesDesktop = desktopWindow.hermesDesktop

afterEach(() => {
  _resetFleetRosterForTests()
  vi.restoreAllMocks()

  if (initialHermesDesktop) {
    desktopWindow.hermesDesktop = initialHermesDesktop
  } else {
    delete desktopWindow.hermesDesktop
  }
})

describe('fleet roster refresh', () => {
  it('reports cached retention and an enumeration failure without replacing the last successful roster', async () => {
    const getAgentRoster = vi
      .fn<() => Promise<DesktopAgentRoster>>()
      .mockResolvedValueOnce(roster)
      .mockRejectedValueOnce(new Error('the remote gateway is asleep'))
    desktopWindow.hermesDesktop = { getAgentRoster } as unknown as Window['hermesDesktop']

    await expect(refreshFleetRoster({ force: true })).resolves.toMatchObject({ status: 'refreshed' })
    await expect(refreshFleetRoster()).resolves.toMatchObject({ status: 'retained' })
    await expect(refreshFleetRoster({ force: true })).resolves.toMatchObject({
      error: 'Fleet roster refresh failed',
      status: 'failed'
    })

    expect(getAgentRoster).toHaveBeenCalledTimes(2)
    expect($fleetRoster.get()).toBe(roster)
  })
})
