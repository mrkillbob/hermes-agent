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
    if (deps.sessionStateByRuntimeIdRef.current.get(sessionId)?.heartbeatSettledAt !== settledAt) {
      return
    }

    let consumed = false

    const next = deps.updateSessionState(sessionId, state => {
      // The timer is the grace, so no clock re-check: timers may fire a
      // millisecond early against Date.now() and would strand the prefix.
      if (state.heartbeatSettledAt !== settledAt || !holdsSettledPrefix(state, deps.activeSessionIdRef.current === sessionId)) {
        return state
      }

      consumed = true

      return { ...state, heartbeatSettledAt: null }
    })

    if (consumed) {
      void deps.hydrateFromStoredSession(3, next.storedSessionId, sessionId)
    }
  }, SETTLED_PREFIX_GRACE_MS)
}
