import { atom } from 'nanostores'

import type { DesktopAgentRoster } from '@/global'

// The union agent roster — every profile on every registered gateway — as
// Electron enumerates it over REST/SSH (`hermes:agents:roster`). Bot Mode and
// the Capabilities scope selector already read it; the Sessions profile rail
// is its third consumer. Fetched on demand only (mount / focus / registry
// change), never on a timer: the multi-connection docs rule out periodic
// fleet polling from the sidebar.
export const $fleetRoster = atom<DesktopAgentRoster | null>(null)

const FLEET_ROSTER_STALE_MS = 60_000

/**
 * The roster remains available after an enumeration failure, but consumers
 * that need authority (such as Lunar City) must not mistake that retained
 * cache for a fresh read.  Existing fire-and-forget callers can continue to
 * ignore this additive result.
 */
export interface FleetRosterRefreshOutcome {
  error?: string
  observedAt?: number
  status: 'failed' | 'refreshed' | 'retained'
}

let fetchedAt = 0
let inflight: null | Promise<FleetRosterRefreshOutcome> = null

export async function refreshFleetRoster(options: { force?: boolean } = {}): Promise<FleetRosterRefreshOutcome> {
  const bridge = window.hermesDesktop?.getAgentRoster

  if (!bridge) {
    return { error: 'Fleet roster bridge unavailable', observedAt: fetchedAt || undefined, status: 'failed' }
  }

  if (!options.force && $fleetRoster.get() && Date.now() - fetchedAt < FLEET_ROSTER_STALE_MS) {
    return { observedAt: fetchedAt, status: 'retained' }
  }

  if (inflight) {
    return inflight
  }

  inflight = bridge()
    .then(roster => {
      $fleetRoster.set(roster)
      fetchedAt = Date.now()

      return { observedAt: fetchedAt, status: 'refreshed' as const }
    })
    .catch((error: unknown) => {
      // A failed enumeration keeps the last roster: the rail should not lose
      // a machine's squares because one refresh hit a sleeping box.  Surface
      // an explicit failed outcome so authority-sensitive consumers do not
      // re-stamp retained rows as a fresh successful read.
      console.warn('[fleet-roster] enumeration failed; keeping the previous roster', error)

      return { error: 'Fleet roster refresh failed', observedAt: fetchedAt || undefined, status: 'failed' as const }
    })
    .finally(() => {
      inflight = null
    })

  return inflight
}

/** @internal */
export function _resetFleetRosterForTests(): void {
  $fleetRoster.set(null)
  fetchedAt = 0
  inflight = null
}
