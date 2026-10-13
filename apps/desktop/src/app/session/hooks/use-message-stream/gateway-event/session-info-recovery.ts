import type { ClientSessionState } from '@/app/types'

/** A retained prefix gets a reorder grace before one foreground history read. */
export function needsSettledPrefixCatchup(state: ClientSessionState, active: boolean, now: number): boolean {
  return (
    !state.busy &&
    active &&
    Boolean(state.heartbeatSettledStreamId) &&
    typeof state.heartbeatSettledAt === 'number' &&
    now - state.heartbeatSettledAt >= 2000
  )
}
