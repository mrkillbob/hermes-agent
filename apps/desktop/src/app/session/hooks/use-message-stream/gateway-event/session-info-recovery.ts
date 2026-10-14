import type { ClientSessionState } from '@/app/types'

import type { GatewayEventDeps } from './types'

/** Reorder grace before a retained prefix is trusted to have lost its completion. */
const SETTLED_PREFIX_GRACE_MS = 2000

function holdsSettledPrefix(state: ClientSessionState, active: boolean): boolean {
  return (
    !state.busy &&
    active &&
    Boolean(state.heartbeatSettledStreamId) &&
    typeof state.heartbeatSettledAt === 'number'
  )
}

/** A retained prefix gets a reorder grace before one foreground history read. */
export function needsSettledPrefixCatchup(state: ClientSessionState, active: boolean, now: number): boolean {
  return holdsSettledPrefix(state, active) && now - state.heartbeatSettledAt! >= SETTLED_PREFIX_GRACE_MS
}

/** One read per settle at a time; a failed read releases it so a later idle heartbeat can retry. */
const inFlight = new Map<string, number>()

/**
 * One bounded history read for a retained prefix. The marker is consumed only
 * once an authoritative read succeeded: when every attempt fails (the same
 * connection gap that lost the completion can keep REST down too) it stays set,
 * and the next idle foreground heartbeat tries again.
 */
export async function recoverSettledPrefix(deps: GatewayEventDeps, sessionId: string, settledAt: number) {
  const state = deps.sessionStateByRuntimeIdRef.current.get(sessionId)

  if (inFlight.get(sessionId) === settledAt || !state || state.heartbeatSettledAt !== settledAt) {
    return
  }

  inFlight.set(sessionId, settledAt)

  try {
    if (!(await deps.hydrateFromStoredSession(3, state.storedSessionId, sessionId))) {
      return
    }

    deps.updateSessionState(sessionId, current =>
      current.heartbeatSettledAt === settledAt ? { ...current, heartbeatSettledAt: null } : current
    )
  } finally {
    if (inFlight.get(sessionId) === settledAt) {
      inFlight.delete(sessionId)
    }
  }
}

/**
 * The terminal transition owns its recovery: a settled session may never emit
 * another event (heartbeats are RPC replies, not session.info), so waiting for
 * a second one would strand the prefix. One timer per settle; the marker is the
 * token, so a late complete, a new turn, or an earlier consumption retires it.
 * A session that is in the background at fire time keeps its marker for the
 * first idle heartbeat after the user returns.
 */
export function scheduleSettledPrefixCatchup(deps: GatewayEventDeps, sessionId: string, settledAt: number) {
  setTimeout(() => {
    // The timer is the grace, so no clock re-check: timers may fire a
    // millisecond early against Date.now() and would strand the prefix.
    const state = deps.sessionStateByRuntimeIdRef.current.get(sessionId)

    if (state?.heartbeatSettledAt === settledAt && holdsSettledPrefix(state, deps.activeSessionIdRef.current === sessionId)) {
      void recoverSettledPrefix(deps, sessionId, settledAt)
    }
  }, SETTLED_PREFIX_GRACE_MS)
}
