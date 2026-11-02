import { randomUuid } from '@hermes/shared'

import { type CapturedAcknowledgement, sha256, validateCapturedAcknowledgement } from '@/store/companion-enrollment-ack'
import { captureAlreadyOpenRoute, type CapturedRouteLease, identifier, type LiveRegistrySource, type RouteSnapshot } from '@/store/companion-enrollment-route'

export type DecisionStatus = 'unavailable' | 'ready' | 'invalid' | 'pending' | 'candidate' | 'cancelled' | 'uncertain' | 'expired'
export interface DecisionSnapshot { status: DecisionStatus; target?: Readonly<RouteSnapshot>; candidate?: Readonly<CapturedAcknowledgement> }
interface SourceDependencies {
  source?: LiveRegistrySource
  digestChallenge?: (bytes: Uint8Array) => Promise<string>
  newOperationId?: () => string
}

function decodeChallenge(value: string): Uint8Array | undefined {
  if (!/^[A-Za-z0-9_-]{43}$/.test(value)) { return undefined }
  const binary = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '=')

  if (binary.length !== 32 || btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') !== value) { return undefined }

  return Uint8Array.from(binary, char => char.charCodeAt(0))
}

async function digestBytes(bytes: Uint8Array) {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(bytes))

  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
}

/** Internal trusted-source composition only. No real registry/native port is installed.
 * Local candidate/expiry/cancel states never settle A2A or issue activity authority.
 */
export function createDesktopEnrollment(deps: SourceDependencies = {}) {
  let value: Readonly<DecisionSnapshot> = Object.freeze({ status: deps.source ? 'ready' : 'unavailable' })
  let disposed = false, attempted = false, sent = false, epoch = 0
  let selectedKey: string | undefined
  let latestTime = Date.now()
  let lease: CapturedRouteLease | undefined
  let abort: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined

  let detach = () => {}
  const listeners = new Set<() => void>()

  function publish(next: DecisionSnapshot) {
    if (disposed) { return }
    value = Object.freeze(next)

    for (const listener of listeners) { listener() }
  }

  function cleanup() {
    epoch++; clearTimeout(timer); timer = undefined
    detach();

 detach = () => {}; abort?.abort(); abort = undefined
    lease?.release(); lease = undefined; selectedKey = undefined
  }

  function stop(status: DecisionStatus) { cleanup(); publish({ status }) }

  function readClock() {
    const now = Date.now()

    if (!Number.isSafeInteger(now) || now < latestTime) { return undefined }
    latestTime = now;

 return now
  }

  function arm(deadline: number, expire: () => void) {
    clearTimeout(timer)
    const now = readClock()

    if (now === undefined) { stop(sent ? 'uncertain' : 'unavailable');

 return }

    const ticket = epoch
    timer = setTimeout(() => {
      if (disposed || ticket !== epoch) { return }
      const checked = readClock()

      if (checked === undefined) { stop(sent ? 'uncertain' : 'unavailable');

 return }

      if (checked < deadline) { arm(deadline, expire) } else { expire() }
    }, Math.min(2147483647, Math.max(0, deadline - now)))
  }

  function isLive(ticket: number) { return !disposed && ticket === epoch }

  function finishAcknowledgement(raw: unknown, operationId: string, challengeSha256: string, selected: CapturedRouteLease, ticket: number) {
    if (!isLive(ticket)) { return }
    const candidate = validateCapturedAcknowledgement(raw, { operationId, challengeSha256, target: selected.snapshot, captureLease: selected }, Date.now())

    if (!candidate || !selected.isCurrent()) { stop('uncertain');

 return }

    clearTimeout(timer)
    publish({ status: 'candidate', target: selected.snapshot, candidate })

    if (isLive(ticket) && lease === selected && value.status === 'candidate') {
      arm(Math.min(candidate.expiresAtSeconds * 1000, selected.expiresAtMs), () => stop('expired'))
    }
  }

  async function dispatchApproved(challenge: string, bytes: Uint8Array, selected: CapturedRouteLease, ticket: number, signal: AbortSignal) {
    const challengeSha256 = await (deps.digestChallenge ?? digestBytes)(bytes)

    if (!isLive(ticket)) { return }
    const operationId = (deps.newOperationId ?? randomUuid)()

    if (!sha256(challengeSha256) || !identifier(operationId) || !selected.isCurrent()) { stop('unavailable');

 return }

    sent = true
    const raw = await selected.dispatchOnce({ operationId, challenge, challengeSha256 }, signal)
    finishAcknowledgement(raw, operationId, challengeSha256, selected, ticket)
  }

  return {
    snapshot: () => value,
    subscribe(callback: () => void) { if (!disposed) { listeners.add(callback) };

 return () => { listeners.delete(callback) } },
    selectTarget(selectionKey: string) {
      if (disposed || attempted || !deps.source || !['ready', 'invalid'].includes(value.status)) { return false }
      cleanup()

      try { lease = captureAlreadyOpenRoute(deps.source, selectionKey) } catch { stop('unavailable');

 return false }

      if (!lease) { stop('unavailable');

 return false }

      selectedKey = selectionKey
      const selected = lease
      publish({ status: 'ready', target: selected.snapshot })
      detach = selected.onInvalidation(() => { if (lease === selected) { stop(sent ? 'uncertain' : 'unavailable') } })

      if (lease !== selected || !selected.isCurrent()) { return false }
      arm(selected.expiresAtMs, () => stop('unavailable'))

      return true
    },
    async approve(challenge: string, selectionKey: string) {
      if (disposed || attempted || !deps.source || !['ready', 'invalid'].includes(value.status)) { return }
      const bytes = decodeChallenge(challenge)

      if (!bytes) { publish({ status: 'invalid', target: value.target });

 return }

      attempted = true

      if (!lease || selectedKey !== selectionKey || !lease.isCurrent()) { stop('unavailable');

 return }

      const selected = lease, ticket = ++epoch
      abort = new AbortController(); const signal = abort.signal
      publish({ status: 'pending', target: selected.snapshot })

      if (ticket !== epoch || disposed) { return }
      // UI watchdog only: never claims the remote challenge was cancelled or consumed.
      arm(Math.min(Date.now() + 30000, selected.expiresAtMs), () => stop(sent ? 'uncertain' : 'unavailable'))

      try {
        await dispatchApproved(challenge, bytes, selected, ticket, signal)
      } catch {
        if (!disposed && ticket === epoch) { stop(sent ? 'uncertain' : 'unavailable') }
      } finally { bytes.fill(0) }
    },
    cancel() {
      if (!disposed && !['unavailable', 'cancelled', 'uncertain', 'expired'].includes(value.status)) { stop(sent ? 'uncertain' : 'cancelled') }
    },
    dispose() { if (!disposed) { cleanup(); disposed = true; listeners.clear() } }
  }
}

/** Application-facing source entry: no provider/config/argument may enable enrollment. */
export function createUnavailableEnrollment(..._ignored: unknown[]) { return createDesktopEnrollment() }
export type DesktopEnrollmentController = ReturnType<typeof createDesktopEnrollment>
