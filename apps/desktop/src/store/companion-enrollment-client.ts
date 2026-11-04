import type { GatewayClientOptions, WebSocketLike } from '@hermes/shared'

import type { createDesktopRouteRegistry, LiveRoute, OwnedRouteObservation } from './companion-enrollment-route'

type SocketFactory = NonNullable<GatewayClientOptions['socketFactory']>
type RouteRegistry = Pick<ReturnType<typeof createDesktopRouteRegistry>, 'source' | 'observeOwnedRoute' | 'retireOwnedRoute'>
type OwnedTarget = Omit<OwnedRouteObservation, 'physicalSocket'>

/** Wrap the existing client's factory at its source-owned construction seam.
 * Construction never dials, and this adapter never closes or reconnects a client.
 * Native target/decision facts still require their privileged owner; observing an
 * open socket supplies neither a Principal nor enrollment/activity authority.
 */
export function createDesktopEnrollmentClientCapture(factory: SocketFactory, registry: RouteRegistry) {
  let current: WebSocketLike | undefined
  let disposed = false, creating = false

  let detach = () => {}
  const seen = new WeakSet<WebSocketLike>()
  const owned = new Map<string, LiveRoute>()

  function retireIfOwned(key: string, record: LiveRoute) {
    if (registry.source.lookup(key) === record) { registry.retireOwnedRoute(key) }
  }

  function invalidate() {
    current = undefined
    detach();

 detach = () => {}
    const records = [...owned]
    owned.clear()

    for (const [key, record] of records) { retireIfOwned(key, record) }
  }

  const socketFactory: SocketFactory = url => {
    if (disposed || creating) { throw new Error('Desktop enrollment socket capture unavailable') }
    creating = true

    try {
      // Invalidate before delegation: even a failed replacement cannot inherit consent.
      invalidate()

      if (disposed) { throw new Error('Desktop enrollment socket capture unavailable') }
      const socket = factory(url)

      if (disposed) { return socket }

      if (seen.has(socket) || socket.readyState > 1) { throw new Error('Desktop enrollment socket capture unavailable') }
      seen.add(socket); current = socket

      const lost = () => { if (current === socket) { invalidate() } }
      socket.addEventListener('close', lost); socket.addEventListener('error', lost)

      detach = () => { socket.removeEventListener('close', lost); socket.removeEventListener('error', lost) }

      return socket
    } finally { creating = false }
  }

  return {
    socketFactory,
    /** Trusted source composition only. No URL, label, ambient session or client metadata lookup. */
    observeOwnedTarget(key: string, target: OwnedTarget) {
      const physicalSocket = current

      if (disposed || creating || !physicalSocket || physicalSocket.readyState !== 1) { return false }
      const existing = registry.source.lookup(key)

      if (existing && existing !== owned.get(key)) { owned.delete(key);

 return false }

      if (!registry.observeOwnedRoute(key, { ...target, physicalSocket })) { owned.delete(key);

 return false }

      const record = registry.source.lookup(key)

      if (current !== physicalSocket || record?.incarnation !== physicalSocket) { if (record?.incarnation === physicalSocket) { retireIfOwned(key, record) }; owned.delete(key);

 return false }

      owned.set(key, record)

      return true
    },
    retireOwnedTarget(key: string) {
      const record = owned.get(key)
      owned.delete(key)

      if (record) { retireIfOwned(key, record) }
    },
    dispose() {
      if (disposed) { return }
      disposed = true
      invalidate()
    }
  }
}
