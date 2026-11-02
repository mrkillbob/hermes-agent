import type { LiveRoute } from './companion-enrollment-route'

export function fixture() {
  const listeners = new Set<() => void>()
  let open = true
  const calls: unknown[] = []

  const socket = { isOpen: () => open, requestDecision: async (intent: unknown, _target: unknown, _signal: AbortSignal): Promise<unknown> => { calls.push(intent);

 return undefined } }

  let live: LiveRoute = { connectionId: 'connection-a', profile: 'desktop-a', targetProfile: 'native-a', installationId: 'installation-a', nativeSubject: 'user-a', nativeProfileId: 'native-a', nativeInstanceId: 'instance-a', storedSessionId: 'stored-a', runtimeSessionId: 'runtime-a', authorityEpoch: 3, bindingGeneration: 7, socket, incarnation: {}, desktopSocketIncarnationId: 'desktop-socket-a', desktopRouteRevision: '1', validUntilMs: Number.MAX_SAFE_INTEGER }

  const source = { lookup: (key: string) => key === 'exact-target' ? live : undefined, watch: (_key: string, callback: () => void) => { listeners.add(callback);

 return () => { listeners.delete(callback) } } }

  return { source, socket, calls, get live() { return live }, set live(value) { live = value }, listeners,
    close() { open = false; listeners.forEach(fn => fn()) }, change() { listeners.forEach(fn => fn()) } }
}

export const challenge = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
export const digest = 'a'.repeat(64)
export const operationId = 'operation-a'

export function ack(overrides = {}) {
  return { operationId, challengeSha256: digest, approvalId: 'approval-a', installationId: 'installation-a', nativeSubject: 'user-a', nativeProfileId: 'native-a', nativeInstanceId: 'instance-a', storedSessionId: 'stored-a', runtimeSessionId: 'runtime-a', authorityEpoch: 3, bindingGeneration: 7, receiptSha256: 'b'.repeat(64), issuedAtSeconds: 100, expiresAtSeconds: 150, ...overrides }
}

export const enrollmentCopy = {
 title: 'One-time companion setup', description: 'Explicit personal-installation setup; not OpenAI conversation attestation.',
 legend: 'Explicit enrollment decision', challengeLabel: 'One-time challenge', sessionLabel: 'Exact live session', chooseSession: 'Choose a session',
 connectionLabel: 'Connection / desktop profile', nativeLabel: 'Native profile / instance', sessionIdsLabel: 'Stored / live session', approve: 'Approve once', cancel: 'Cancel setup',
 messages: {
 unavailable: 'Setup unavailable. Your dot remains disconnected.', ready: 'Choose the exact live session. Your dot remains disconnected.',
 invalid: 'Invalid challenge. Your dot remains disconnected.', pending: 'Native acknowledgement pending. Your dot remains disconnected.',
 candidate: 'Native candidate; join pending. Your dot remains disconnected.', cancelled: 'Closed locally. Your dot remains disconnected.',
 uncertain: 'Remote outcome uncertain. Your dot remains disconnected.', expired: 'Local candidate expired. Your dot remains disconnected.'
 }
}
