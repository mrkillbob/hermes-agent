/** An inventory read over an existing authenticated socket. Never opens a connection. */
export interface InventoryOwner {
  connectionId: string
  profile: string
}
export interface AttachedRoute extends InventoryOwner {
  epoch: number
  gateway: {
    connectionState: string
    request<T>(method: string, params: Record<string, unknown>, timeoutMs: number, signal?: AbortSignal): Promise<T>
  }
}
export interface SessionInventoryEntry extends InventoryOwner {
  sessionId: string
  /** session.list has no activity field. Presence must not imply working or idle. */
  activity: 'unknown'
}
export type SessionInventoryRead =
  | {
      status: 'read'
      entries: readonly SessionInventoryEntry[]
      observedAt: number
      window: 'up-to-100'
      activity: 'unavailable'
    }
  | {
      status: 'unavailable'
      reason: 'not-attached' | 'route-changed' | 'cancelled' | 'invalid-response' | 'read-failed'
      entries: readonly []
    }

const refused = (reason: Extract<SessionInventoryRead, { status: 'unavailable' }>['reason']): SessionInventoryRead => ({
  status: 'unavailable',
  reason,
  entries: []
})

function matches(route: AttachedRoute | null, owner: InventoryOwner): route is AttachedRoute {
  return Boolean(
    route &&
    route.gateway.connectionState === 'open' &&
    route.connectionId === owner.connectionId &&
    route.profile === owner.profile
  )
}

/** Validate the bounded result and retain only owner-scoped IDs, never server display content. */
function inventoryEntries(payload: unknown, owner: InventoryOwner): SessionInventoryEntry[] | null {
  if (
    !payload ||
    typeof payload !== 'object' ||
    !('sessions' in payload) ||
    !Array.isArray(payload.sessions) ||
    payload.sessions.length > 100
  ) {
    return null
  }

  const ids = new Set<string>()
  const entries: SessionInventoryEntry[] = []

  for (const row of payload.sessions) {
    if (
      !row ||
      typeof row !== 'object' ||
      typeof row.id !== 'string' ||
      !row.id.trim() ||
      row.id.length > 2048 ||
      ids.has(row.id)
    ) {
      return null
    }

    // No preview, title, message text, model, URL, credential or backend error is published.
    ids.add(row.id)
    entries.push({ connectionId: owner.connectionId, profile: owner.profile, sessionId: row.id, activity: 'unknown' })
  }

  return entries
}

/** No title lookup (which may recover hidden Bot Chat rows), no events, no polling, no cache writes. */
export async function readAttachedSessionInventory(
  owner: InventoryOwner,
  attached: () => AttachedRoute | null,
  signal?: AbortSignal,
  now: () => number = Date.now
): Promise<SessionInventoryRead> {
  if (signal?.aborted) {
    return refused('cancelled')
  }

  if (!owner.connectionId.trim() || !owner.profile.trim()) {
    return refused('not-attached')
  }

  const before = attached()

  if (!matches(before, owner)) {
    return refused('not-attached')
  }

  try {
    const payload: unknown = await before.gateway.request(
      'session.list',
      { profile: owner.profile, limit: 100, include_hidden: false },
      5000,
      signal
    )

    if (signal?.aborted) {
      return refused('cancelled')
    }

    const after = attached()

    if (!matches(after, owner) || after.gateway !== before.gateway || after.epoch !== before.epoch) {
      return refused('route-changed')
    }

    const entries = inventoryEntries(payload, owner)

    if (!entries) {
      return refused('invalid-response')
    }

    return { status: 'read', entries, observedAt: now(), window: 'up-to-100', activity: 'unavailable' }
  } catch {
    // Raw gateway errors may contain endpoints or server data; never forward or log them.
    return refused(signal?.aborted ? 'cancelled' : 'read-failed')
  }
}
