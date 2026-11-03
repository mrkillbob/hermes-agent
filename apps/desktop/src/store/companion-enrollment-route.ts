import { randomUuid } from '@hermes/shared'
/** Proposed private desktop source port, not a native RPC/wire contract or authority issuer. */
export interface DesktopRouteFence { desktopSocketIncarnationId: string; desktopRouteRevision: string }
export interface RouteSnapshot extends DesktopRouteFence {
  connectionId: string; profile: string; targetProfile: string
  installationId: string; nativeSubject: string; nativeProfileId: string; nativeInstanceId: string
  storedSessionId: string; runtimeSessionId: string
  authorityEpoch: number; bindingGeneration: number
}
export interface DecisionIntent { operationId: string; challenge: string; challengeSha256: string }
export interface DecisionSocket {
  isOpen(): boolean
  requestDecision(intent: Readonly<DecisionIntent>, selected: Readonly<RouteSnapshot>, signal: AbortSignal): Promise<unknown>
}
export interface LiveRoute extends RouteSnapshot { socket: DecisionSocket; incarnation: object; validUntilMs: number }
export interface LiveRegistrySource {
  /** Must only read an already-open route and Task4-qualified live target. Never dial/restore. */
  lookup(selectionKey: string): LiveRoute | undefined
  watch(selectionKey: string, changed: () => void): () => void
}
export const routeStrings = ['desktopSocketIncarnationId','connectionId','profile','targetProfile','installationId','nativeSubject','nativeProfileId','nativeInstanceId','storedSessionId','runtimeSessionId'] as const
export const identifier = (value: unknown): value is string => typeof value === 'string' && value === value.trim() && value.length > 0 &&
  new TextEncoder().encode(value).length <= 256 && !/\p{C}/u.test(value)
export const positiveInteger = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0

/** Only for trusted source composition. An actual registry adapter is not installed. */
export function captureAlreadyOpenRoute(source: LiveRegistrySource, selectionKey: string) {
  const captured = source.lookup(selectionKey)

  if (!captured || !routeStrings.every(key => identifier(captured[key])) || !positiveInteger(captured.authorityEpoch) ||
    !positiveInteger(captured.bindingGeneration) || !isDesktopRouteRevision(captured.desktopRouteRevision) || captured.targetProfile !== captured.nativeProfileId ||
    !Number.isSafeInteger(captured.validUntilMs) || captured.validUntilMs <= Date.now() || !captured.incarnation || typeof captured.incarnation !== 'object' || !captured.socket?.isOpen()) { return undefined }

  const snapshot: Readonly<RouteSnapshot> = Object.freeze(Object.fromEntries(
    [...routeStrings, 'desktopRouteRevision', 'authorityEpoch', 'bindingGeneration'].map(key => [key, captured[key as keyof RouteSnapshot]])
  ) as unknown as RouteSnapshot)

  const socket = captured.socket, incarnation = captured.incarnation, expiresAtMs = captured.validUntilMs, capturedAt = Date.now()
  let invalid = false, used = false, latestTime = capturedAt

  let unsubscribe = () => {}
  const listeners = new Set<() => void>()

  function matches() {
    const now = Date.now()

    if (!Number.isSafeInteger(now) || now < latestTime) { return false }
    latestTime = now
    const current = source.lookup(selectionKey)

    return now < expiresAtMs && current?.validUntilMs === expiresAtMs && current.socket === socket && current.incarnation === incarnation && socket.isOpen() &&
      [...routeStrings, 'desktopRouteRevision', 'authorityEpoch', 'bindingGeneration'].every(key => current[key as keyof RouteSnapshot] === snapshot[key as keyof RouteSnapshot])
  }

  function invalidate() {
    if (invalid) { return }
    invalid = true; unsubscribe()

    for (const listener of listeners) { listener() }
    listeners.clear()
  }

  function isCurrent() {
    if (!invalid && !matches()) { invalidate() }

    return !invalid
  }

  unsubscribe = source.watch(selectionKey, () => { if (!matches()) { invalidate() } })

  if (!isCurrent()) { unsubscribe();

 return undefined }

  const captureIdentity = Object.freeze({})

  const lease = {
    snapshot,
    expiresAtMs,
    isCurrent,
    onInvalidation(callback: () => void) {
      if (!isCurrent()) { callback();

 return () => {} }

      listeners.add(callback);

 return () => { listeners.delete(callback) }
    },
    async dispatchOnce(intent: DecisionIntent, signal: AbortSignal) {
      if (used || signal.aborted || !isCurrent()) { throw new Error('Exact route unavailable or decision already dispatched') }
      used = true

      let onAbort = () => {}

      try {
        const result = await new Promise<unknown>((resolve, reject) => {
          onAbort = () => reject(new Error('Local decision cancelled; remote outcome may be uncertain'))
          signal.addEventListener('abort', onAbort, { once: true })

          if (signal.aborted) { onAbort();

 return }

          try { socket.requestDecision(Object.freeze({ ...intent }), snapshot, signal).then(resolve, reject) }
          catch (error) { reject(error) }
        })

        if (signal.aborted || !isCurrent()) { throw new Error('Exact route changed before acknowledgement') }

        return capturedDelivery(result, { desktopSocketIncarnationId: snapshot.desktopSocketIncarnationId, desktopRouteRevision: snapshot.desktopRouteRevision }, snapshot, captureIdentity)
      } finally { signal.removeEventListener('abort', onAbort) }
    },
    release() { invalid = true; unsubscribe(); listeners.clear() }
  }

  captureIdentities.set(lease, captureIdentity)

  return lease
}

export type CapturedRouteLease = NonNullable<ReturnType<typeof captureAlreadyOpenRoute>>


const maxRevision = 9223372036854775807n

export function isDesktopRouteRevision(value: unknown): value is string {
  return typeof value === 'string' && /^[1-9][0-9]{0,18}$/.test(value) && BigInt(value) <= maxRevision
}

interface CapturedDelivery { readonly nativeAcknowledgement: unknown; readonly desktopFence: Readonly<DesktopRouteFence>; readonly target: Readonly<RouteSnapshot> }
interface StoredDelivery { view: Readonly<CapturedDelivery>; captureIdentity: object }
const deliveries = new WeakMap<object, StoredDelivery>()
const captureIdentities = new WeakMap<object, object>()

function snapshotAcknowledgement(value: unknown): unknown {
  if (value === null || typeof value !== 'object') { return value }

  if (Reflect.ownKeys(value).some(key => typeof key !== 'string')) { return undefined }
  const descriptors = Object.getOwnPropertyDescriptors(value)

  if (!Object.values(descriptors).every(descriptor => 'value' in descriptor)) { return undefined }

  return Object.freeze(Object.fromEntries(Object.entries(descriptors).map(([key, descriptor]) => [key, descriptor.value])))
}

function capturedDelivery(nativeAcknowledgement: unknown, desktopFence: DesktopRouteFence, target: Readonly<RouteSnapshot>, captureIdentity: object) {
  const token = Object.freeze({})
  const view = Object.freeze({ nativeAcknowledgement: snapshotAcknowledgement(nativeAcknowledgement), desktopFence: Object.freeze({ ...desktopFence }), target })
  deliveries.set(token, { view, captureIdentity })

  return token
}

/** Local provenance only; the optional genuine lease also checks capture ownership. */
export function readCapturedDecisionDelivery(value: unknown, expectedLease?: object): Readonly<CapturedDelivery> | undefined {
  const stored = value !== null && typeof value === 'object' ? deliveries.get(value) : undefined

  if (!stored || expectedLease && captureIdentities.get(expectedLease) !== stored.captureIdentity) { return undefined }

  return stored.view
}

interface PhysicalSocket {
  readonly readyState: number
  addEventListener(type: 'close' | 'error', listener: () => void): void
  removeEventListener(type: 'close' | 'error', listener: () => void): void
}
export interface OwnedRouteObservation extends Omit<RouteSnapshot, keyof DesktopRouteFence> {
  physicalSocket: PhysicalSocket
  socket: DecisionSocket
  validUntilMs: number
}
interface TransportState { id: string; retired: boolean; physical: PhysicalSocket; detach(): void }

/** Desktop-owned observations only. Generates local fences, never an identity/grant/receipt.
 * A real privileged owner must supply live native facts and the actual physical socket.
 * This registry is not installed into the application-facing unavailable factory.
 */
export function createDesktopRouteRegistry() {
  const records = new Map<string, LiveRoute>()
  const revisions = new Map<string, bigint>()
  const physicalStates = new WeakMap<PhysicalSocket, TransportState>()
  const transports = new Set<TransportState>()
  const watchers = new Map<string, Set<() => void>>()
  let disposed = false

  function notify(key: string) { for (const callback of [...(watchers.get(key) ?? [])]) { callback() } }

  function advance(key: string) {
    const next = (revisions.get(key) ?? 0n) + 1n

    if (next > maxRevision) { return undefined }
    revisions.set(key, next);

 return next.toString()
  }

  function retireOwnedRoute(key: string) {
    if (!records.delete(key)) { return }
    advance(key); notify(key)
  }

  function transportFor(physical: PhysicalSocket) {
    const existing = physicalStates.get(physical)

    if (existing) { return existing.retired ? undefined : existing }
    const state: TransportState = { id: randomUuid(), retired: false, physical, detach() {} }

    const retire = () => {
      state.retired = true; state.detach(); transports.delete(state)

      for (const key of [...records.keys()]) { if (records.get(key)?.incarnation === physical) { retireOwnedRoute(key) } }
    }

    state.detach = () => {
      physical.removeEventListener('close', retire); physical.removeEventListener('error', retire)
    }

    physical.addEventListener('close', retire); physical.addEventListener('error', retire)
    physicalStates.set(physical, state); transports.add(state)

    return state
  }

  const source: LiveRegistrySource = {
    lookup(key) {
      const record = records.get(key)

      if (record && (!record.socket.isOpen() || Date.now() >= record.validUntilMs || (record.incarnation as PhysicalSocket).readyState !== 1)) { retireOwnedRoute(key);

 return undefined }

      return record
    },
    watch(key, callback) {
      if (disposed) { callback();

 return () => {} }

      const listeners = watchers.get(key) ?? new Set<() => void>()
      watchers.set(key, listeners); listeners.add(callback)

      return () => { listeners.delete(callback);

 if (!listeners.size) { watchers.delete(key) } }
    }
  }

  return {
    source,
    observeOwnedRoute(key: string, observed: OwnedRouteObservation) {
      if (disposed) { return false }
      const nativeStrings = routeStrings.filter(field => field !== 'desktopSocketIncarnationId')

      if (!identifier(key) || !nativeStrings.every(field => identifier(observed[field])) || !positiveInteger(observed.authorityEpoch) ||
        !positiveInteger(observed.bindingGeneration) || observed.targetProfile !== observed.nativeProfileId ||
        !Number.isSafeInteger(observed.validUntilMs) || observed.validUntilMs <= Date.now() ||
        observed.physicalSocket.readyState !== 1 || !observed.socket.isOpen()) { retireOwnedRoute(key);

 return false }

      const physical = transportFor(observed.physicalSocket)

      if (!physical) { retireOwnedRoute(key);

 return false }

      const previous = records.get(key)
      const scalarKeys = [...nativeStrings, 'authorityEpoch', 'bindingGeneration', 'validUntilMs'] as const

      if (previous && previous.incarnation === observed.physicalSocket && previous.socket === observed.socket && scalarKeys.every(field => previous[field] === observed[field])) { return true }
      const revision = advance(key)

      if (!revision) { retireOwnedRoute(key);

 return false }

      const scalars = Object.fromEntries(scalarKeys.map(field => [field, observed[field]]))

      const next = Object.freeze({ ...scalars, socket: observed.socket, incarnation: observed.physicalSocket,
        desktopSocketIncarnationId: physical.id, desktopRouteRevision: revision }) as unknown as LiveRoute

      records.set(key, next); notify(key);

 return records.get(key) === next
    },
    retireOwnedRoute,
    dispose() {
      if (disposed) { return }
      disposed = true

      for (const key of [...records.keys()]) { retireOwnedRoute(key) }

      for (const state of transports) { state.retired = true; state.detach() }
      transports.clear(); watchers.clear()
    }
  }
}
