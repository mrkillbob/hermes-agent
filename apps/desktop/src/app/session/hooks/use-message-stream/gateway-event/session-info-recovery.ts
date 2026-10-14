import type { ClientSessionState } from '@/app/types'

import type { GatewayEventDeps } from './types'

/** Reorder grace before a retained prefix is trusted to have lost its completion. */
const SETTLED_PREFIX_GRACE_MS = 2000

function holdsSettledPrefix(state: ClientSessionState, active: boolean): boolean {
  return (
    !state.busy && active && Boolean(state.heartbeatSettledStreamId) && typeof state.heartbeatSettledAt === 'number'
  )
}

/**
 * The marker of a retained prefix that has outlived its reorder grace and
 * is due one foreground history read, else null.
 */
export function idleCatchupMarker(state: ClientSessionState, active: boolean, now: number): null | number {
  return holdsSettledPrefix(state, active) && now - state.heartbeatSettledAt! >= SETTLED_PREFIX_GRACE_MS
    ? state.heartbeatSettledAt!
    : null
}

/** The marker a settle stamps: set only when a streamed bubble was kept. */
export function settledPrefixStamp(heartbeatSettledStreamId: null | string | undefined): null | number {
  return heartbeatSettledStreamId ? Date.now() : null
}

/** One run per settle at a time. */
const inFlight = new Map<string, number>()

/** Waits between reads while the durable answer has not replaced the prefix yet. */
const RETRY_DELAYS_MS = [1000, 2000, 4000]

function prefixStillRetained(deps: GatewayEventDeps, sessionId: string, settledAt: number): boolean {
  const state = deps.sessionStateByRuntimeIdRef.current.get(sessionId)

  return state?.heartbeatSettledAt === settledAt && state.messages.some(m => m.id === state.heartbeatSettledStreamId)
}

/**
 * Bounded history reads for a retained prefix. The marker is consumed only once
 * a read actually replaced the prefix: a failed read (the connection gap that
 * lost the completion can keep REST down too) or one that lands before the
 * gateway commits the answer retries a few times, and past that the marker
 * stays set for the next idle foreground heartbeat.
 */
export async function recoverSettledPrefix(deps: GatewayEventDeps, sessionId: string, settledAt: null | number) {
  if (settledAt === null || inFlight.get(sessionId) === settledAt) {
    return
  }

  inFlight.set(sessionId, settledAt)

  try {
    for (let attempt = 0; prefixStillRetained(deps, sessionId, settledAt); attempt += 1) {
      const state = deps.sessionStateByRuntimeIdRef.current.get(sessionId)!

      await deps.hydrateFromStoredSession(3, state.storedSessionId, sessionId)

      if (!prefixStillRetained(deps, sessionId, settledAt)) {
        break
      }

      if (attempt >= RETRY_DELAYS_MS.length) {
        return
      }

      await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS_MS[attempt]))
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
export function scheduleSettledPrefixCatchup(
  deps: GatewayEventDeps,
  sessionId: string,
  settledAt: null | number | undefined,
  active: boolean
) {
  if (!active || typeof settledAt !== 'number') {
    return
  }

  setTimeout(() => {
    // The timer is the grace, so no clock re-check: timers may fire a
    // millisecond early against Date.now() and would strand the prefix.
    const state = deps.sessionStateByRuntimeIdRef.current.get(sessionId)

    if (
      state?.heartbeatSettledAt === settledAt &&
      holdsSettledPrefix(state, deps.activeSessionIdRef.current === sessionId)
    ) {
      void recoverSettledPrefix(deps, sessionId, settledAt)
    }
  }, SETTLED_PREFIX_GRACE_MS)
}
