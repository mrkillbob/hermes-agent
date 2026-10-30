export interface HandoffDriverDisposeEvidence {
  /** The updater's marker-adoption log entry predates the app-process exit event. */
  markerAdoptedBeforeAppExit: boolean
  /** The isolated driver reported the original Electron ChildProcess exit event. */
  originalAppExitReported: boolean
  /** The reported ChildProcess belongs to the exact PID observed by the parent. */
  originalAppPidMatches: boolean
  /** The parent independently confirmed the original PID/start-time identity is gone. */
  originalAppIdentityAbsent: boolean
}

export function markerPrecedesAppExit(markerTimestamp: number | undefined, appExitTimestamp: number): boolean {
  return markerTimestamp !== undefined && markerTimestamp < appExitTimestamp
}

/** A matching Linux PID/start-time is no longer running once procfs marks it zombie. */
export function processIdentityExited(
  currentStartTicks: string | undefined,
  currentState: string | undefined,
  expectedStartTicks: string
): boolean {
  return currentStartTicks !== expectedStartTicks || currentState === 'Z'
}

/** Only the parent may acknowledge driver disposal after all app-side evidence exists. */
export function canAcknowledgeHandoffDriverDisposal(evidence: HandoffDriverDisposeEvidence): boolean {
  return (
    evidence.markerAdoptedBeforeAppExit &&
    evidence.originalAppExitReported &&
    evidence.originalAppPidMatches &&
    evidence.originalAppIdentityAbsent
  )
}

export type HandoffDriverCommand =
  | { type: 'start'; facts: unknown; env: Record<string, string>; targetSha: string }
  | { type: 'identity-ack' }
  | { type: 'click' }
  | { type: 'dispose' }
  | { type: 'abort' }

export type HandoffDriverMessage =
  | { type: 'launched'; pid: number; startTicks?: string }
  | { type: 'ready'; pid: number }
  | { type: 'click-result'; ok: true }
  | { type: 'click-result'; ok: false; error: string }
  | { type: 'app-exit'; pid: number; code: number | null; signal: NodeJS.Signals | null; observedAt: number }
  | { type: 'driver-error'; stage: string; error: string }
  | { type: 'disposed' }
