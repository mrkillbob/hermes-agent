import { describe, expect, it } from 'vitest'

import {
  canAcknowledgeHandoffDriverDisposal,
  markerPrecedesAppExit,
  processIdentityExited
} from './handoff-driver-protocol'

describe('handoff driver disposal protocol', () => {
  it('does not let driver termination stand in for original app exit evidence', () => {
    expect(
      canAcknowledgeHandoffDriverDisposal({
        markerAdoptedBeforeAppExit: true,
        originalAppExitReported: false,
        originalAppPidMatches: false,
        originalAppIdentityAbsent: false
      })
    ).toBe(false)
  })

  it('waits for independent marker, exit-event, and original-process checks', () => {
    expect(markerPrecedesAppExit(undefined, 10)).toBe(false)
    expect(markerPrecedesAppExit(11, 10)).toBe(false)
    expect(markerPrecedesAppExit(9, 10)).toBe(true)
    expect(markerPrecedesAppExit(10, 10)).toBe(false)
    expect(processIdentityExited('123', 'R', '123')).toBe(false)
    expect(processIdentityExited('123', 'Z', '123')).toBe(true)
    expect(processIdentityExited('456', 'R', '123')).toBe(true)

    expect(
      canAcknowledgeHandoffDriverDisposal({
        markerAdoptedBeforeAppExit: true,
        originalAppExitReported: true,
        originalAppPidMatches: true,
        originalAppIdentityAbsent: false
      })
    ).toBe(false)

    expect(
      canAcknowledgeHandoffDriverDisposal({
        markerAdoptedBeforeAppExit: false,
        originalAppExitReported: true,
        originalAppPidMatches: true,
        originalAppIdentityAbsent: true
      })
    ).toBe(false)

    expect(
      canAcknowledgeHandoffDriverDisposal({
        markerAdoptedBeforeAppExit: true,
        originalAppExitReported: true,
        originalAppPidMatches: false,
        originalAppIdentityAbsent: true
      })
    ).toBe(false)

    expect(
      canAcknowledgeHandoffDriverDisposal({
        markerAdoptedBeforeAppExit: true,
        originalAppExitReported: true,
        originalAppPidMatches: true,
        originalAppIdentityAbsent: true
      })
    ).toBe(true)
  })
})
