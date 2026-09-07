import { describe, expect, it } from 'vitest'

import {
  RELAY_DELIVER_BACKEND_CEILING_MS,
  RELAY_DELIVER_SETTLEMENT_MARGIN_MS,
  RELAY_DELIVER_TIMEOUT_MS,
  RELAY_TURN_ATTEMPT_MS,
  RELAY_TURN_LOCK_WAIT_MS,
  RELAY_TURN_MAX_ATTEMPTS
} from './relay'

// #93911 review follow-up: this Vitest guard exercises the Desktop relay budget
// behavior directly. scripts/ci/classify_changes.py routes the backend leaves
// that define the mirrored values into the frontend lane so changing them runs
// this guard before merge instead of only after push CI fails open.

describe('bot_relay.deliver budget mirrors', () => {
  it('models the documented backend ceiling with exported behavior', () => {
    expect(RELAY_TURN_LOCK_WAIT_MS).toBe(120_000)
    expect(RELAY_TURN_ATTEMPT_MS).toBe(600_000)
    expect(RELAY_TURN_MAX_ATTEMPTS).toBe(2)
    expect(RELAY_DELIVER_BACKEND_CEILING_MS).toBe(
      RELAY_TURN_LOCK_WAIT_MS + RELAY_TURN_ATTEMPT_MS * RELAY_TURN_MAX_ATTEMPTS
    )
  })

  it('keeps the client deadline strictly greater than the backend ceiling', () => {
    expect(RELAY_DELIVER_SETTLEMENT_MARGIN_MS).toBeGreaterThan(0)
    expect(RELAY_DELIVER_TIMEOUT_MS).toBe(RELAY_DELIVER_BACKEND_CEILING_MS + RELAY_DELIVER_SETTLEMENT_MARGIN_MS)
    expect(RELAY_DELIVER_TIMEOUT_MS).toBeGreaterThan(RELAY_DELIVER_BACKEND_CEILING_MS)
  })
})
