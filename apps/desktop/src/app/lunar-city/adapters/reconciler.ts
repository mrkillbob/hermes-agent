import { onGatewayEvent } from '@/contrib/events'
import type { DesktopAgentRoster } from '@/global'
import { $connectionsRegistry, hasRegistryTopology } from '@/store/connection-registry-state'
import { $fleetRoster, refreshFleetRoster } from '@/store/fleet-roster'
import { $connection, $cronSessions, $messagingSessions, $sessions, ownerLookupSessionRows } from '@/store/session'
import { $subagentsBySession, type SubagentProgress } from '@/store/subagents'
import type { SessionInfo } from '@/types/hermes'

import type { LunarEntity, SourceHealth } from '../model'
import { $lunarCitySnapshot, applyLunarDelta, type LunarDelta } from '../store'

import { normalizeRoster } from './fleet'
import { normalizeSessions, normalizeSubagents } from './sessions'

export interface ReconcileReadResult {
  /** False means the read is partial and therefore cannot remove prior rows. */
  authoritative?: boolean
  entities: readonly LunarEntity[]
  sources: readonly SourceHealth[]
}

export interface ReconcilerEvent {
  sequence?: number
  source: string
}

export interface LunarCityReconcilerOptions {
  freshnessMs?: number
  now?: () => number
  publish?: (delta: LunarDelta) => unknown
  read: () => Promise<ReconcileReadResult>
}

interface ReadableAtom<T> {
  get(): T
  listen(listener: (value: T) => void): () => void
}

export interface LunarCityLiveSources {
  $fleetRoster: ReadableAtom<DesktopAgentRoster | null>
  $sessions?: ReadableAtom<readonly SessionInfo[]>
  $subagentsBySession: ReadableAtom<Readonly<Record<string, readonly SubagentProgress[]>>>
  legacyConnectionId?: () => string | undefined
  legacySingleBackend: () => boolean
  onEvent?: (listener: (event: unknown) => void) => () => void
  onFocus?: (listener: () => void) => () => void
  onRegistryChange?: (listener: () => void) => () => void
  refreshFleet: (options: { force: boolean }) => Promise<void>
  sessionRows?: () => readonly SessionInfo[]
  sessionStores?: readonly ReadableAtom<readonly SessionInfo[]>[]
}

export interface StartLunarCityReconcilerOptions {
  freshnessMs?: number
  now?: () => number
  sources?: LunarCityLiveSources
}

export type ReconcileEventResult = 'accepted' | 'ignored'

/** A backend gap, not a duplicate or ordinary next event, needs a reread. */
export function shouldReconcile(currentRevision: number, incomingRevision: number): boolean {
  return incomingRevision > currentRevision + 1
}

function sameEntity(left: LunarEntity, right: LunarEntity): boolean {
  return (
    left.key === right.key &&
    left.observedAt === right.observedAt &&
    left.authority === right.authority &&
    left.destination === right.destination &&
    left.animation === right.animation &&
    left.projectId === right.projectId &&
    left.variant === right.variant &&
    left.identity.kind === right.identity.kind &&
    left.identity.connectionId === right.identity.connectionId &&
    JSON.stringify(left.identity) === JSON.stringify(right.identity) &&
    JSON.stringify(left.position) === JSON.stringify(right.position)
  )
}

function sameSources(left: readonly SourceHealth[], right: readonly SourceHealth[]): boolean {
  if (left.length !== right.length) {
    return false
  }

  return left.every((source, index) => {
    const other = right[index]

    return (
      other?.source === source.source &&
      other.authority === source.authority &&
      other.observedAt === source.observedAt &&
      other.error === source.error
    )
  })
}

function sortedSources(sources: readonly SourceHealth[]): readonly SourceHealth[] {
  return [...sources].sort((left, right) => left.source.localeCompare(right.source))
}

function staleEntity(entity: LunarEntity): LunarEntity {
  if (entity.authority === 'stale') {
    return entity
  }

  return { ...entity, animation: 'unavailable', authority: 'stale', destination: 'unknown' }
}

/**
 * Event-first bounded reconciliation.  It has no timer or frame polling; the
 * sole timer is the earliest source freshness deadline, and it is disposed
 * together with every late-publication guard.
 */
export class LunarCityReconciler {
  private active = false
  private freshnessTimer: ReturnType<typeof setTimeout> | undefined
  private inFlight = false
  private queued = false
  private readonly sequences = new Map<string, number>()

  constructor(private readonly options: LunarCityReconcilerOptions) {}

  start(): () => void {
    if (!this.active) {
      this.active = true
      this.invalidate('mount')
    }

    return () => this.stop()
  }

  stop(): void {
    this.active = false
    this.queued = false

    if (this.freshnessTimer !== undefined) {
      clearTimeout(this.freshnessTimer)
      this.freshnessTimer = undefined
    }
  }

  acceptEvent(event: ReconcilerEvent): ReconcileEventResult {
    const sequence = event.sequence

    if (sequence === undefined || !Number.isFinite(sequence)) {
      this.invalidate('unsequenced-event')

      return 'accepted'
    }

    const prior = this.sequences.get(event.source)

    if (prior !== undefined && sequence <= prior) {
      return 'ignored'
    }

    this.sequences.set(event.source, sequence)
    this.invalidate(prior !== undefined && shouldReconcile(prior, sequence) ? 'sequence-gap' : 'event')

    return 'accepted'
  }

  invalidate(_reason: string): void {
    if (!this.active) {
      return
    }

    if (this.inFlight) {
      this.queued = true

      return
    }

    void this.readOnce()
  }

  private async readOnce(): Promise<void> {
    if (!this.active || this.inFlight) {
      return
    }

    this.inFlight = true

    try {
      const result = await this.options.read()

      if (!this.active) {
        return
      }

      this.publishRead(result)
    } catch {
      if (this.active) {
        this.publishStale()
      }
    } finally {
      this.inFlight = false

      if (this.active && this.queued) {
        this.queued = false
        void this.readOnce()
      }
    }
  }

  private publishRead(result: ReconcileReadResult): void {
    const previous = $lunarCitySnapshot.get()
    const candidates = result.authoritative === false ? new Map(previous.entities) : new Map<string, LunarEntity>()

    for (const entity of result.entities) {
      candidates.set(entity.key, entity)
    }

    this.publishCandidate([...candidates.values()], sortedSources(result.sources))
  }

  private publishStale(): void {
    const previous = $lunarCitySnapshot.get()

    this.publishCandidate(
      [...previous.entities.values()].map(staleEntity),
      previous.sources.map(source => ({ ...source, authority: 'stale' as const }))
    )
  }

  private publishCandidate(candidates: readonly LunarEntity[], sources: readonly SourceHealth[]): void {
    const previous = $lunarCitySnapshot.get()
    const next = new Map(candidates.map(entity => [entity.key, entity]))
    const removals = [...previous.entities.keys()].filter(key => !next.has(key))

    const upserts = [...next.values()].filter(entity => {
      const prior = previous.entities.get(entity.key)

      return prior === undefined || !sameEntity(prior, entity)
    })

    const sourcesChanged = !sameSources(previous.sources, sources)

    if (removals.length === 0 && upserts.length === 0 && !sourcesChanged) {
      this.scheduleFreshness(sources)

      return
    }

    const observedAt = Math.max(
      this.options.now?.() ?? Date.now(),
      ...sources.map(source => source.observedAt),
      ...candidates.map(entity => entity.observedAt)
    )

    this.options.publish?.({
      observedAt,
      removals,
      revision: previous.revision + 1,
      sources,
      upserts
    }) ?? applyLunarDelta({ observedAt, removals, revision: previous.revision + 1, sources, upserts })
    this.scheduleFreshness(sources)
  }

  private scheduleFreshness(sources: readonly SourceHealth[]): void {
    if (this.freshnessTimer !== undefined) {
      clearTimeout(this.freshnessTimer)
      this.freshnessTimer = undefined
    }

    const freshnessMs = this.options.freshnessMs ?? 60_000

    const deadline = Math.min(
      ...sources.filter(source => source.authority === 'authoritative').map(source => source.observedAt + freshnessMs)
    )

    if (!Number.isFinite(deadline) || !this.active) {
      return
    }

    const delay = Math.max(0, deadline - (this.options.now?.() ?? Date.now()))
    this.freshnessTimer = setTimeout(() => {
      this.freshnessTimer = undefined

      if (this.active) {
        this.publishStale()
      }
    }, delay)
  }
}

export function createLunarCityReconciler(options: LunarCityReconcilerOptions): LunarCityReconciler {
  return new LunarCityReconciler(options)
}

function defaultLiveSources(): LunarCityLiveSources {
  return {
    $fleetRoster,
    $sessions,
    $subagentsBySession,
    legacyConnectionId: () => $connection.get()?.connectionId,
    legacySingleBackend: () => !hasRegistryTopology() && $connection.get()?.connectionId === 'local',
    onEvent: listener => onGatewayEvent('*', listener as never),
    onFocus: listener => {
      const onVisibility = () => {
        if (document.visibilityState === 'visible') {
          listener()
        }
      }

      window.addEventListener('focus', listener)
      document.addEventListener('visibilitychange', onVisibility)

      return () => {
        window.removeEventListener('focus', listener)
        document.removeEventListener('visibilitychange', onVisibility)
      }
    },
    onRegistryChange: listener => {
      const offAtom = $connectionsRegistry.listen(listener)
      const offBridge = window.hermesDesktop?.connections?.onChanged?.(listener)

      return () => {
        offAtom()
        offBridge?.()
      }
    },
    refreshFleet: refreshFleetRoster,
    sessionRows: ownerLookupSessionRows,
    sessionStores: [$sessions, $cronSessions, $messagingSessions]
  }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function eventCursor(event: unknown): { sequence?: number; source: string } {
  const value = record(event)
  const payload = record(value.payload)
  const connectionId = optionalString(value.connectionId)
  const profile = optionalString(value.profile)
  const sessionId = optionalString(value.session_id)
  const rawSequence = value.seq ?? payload.seq
  const sequence = typeof rawSequence === 'number' && Number.isFinite(rawSequence) ? rawSequence : undefined

  // A backend sequence has meaning only alongside every owner component it
  // scopes.  An unscoped event remains a reread hint, never a shared cursor.
  if (!connectionId || !profile || !sessionId || sequence === undefined) {
    return { source: 'unscoped' }
  }

  return { sequence, source: `${connectionId}/${profile}/${sessionId}` }
}

/**
 * Starts the sole live writer for Lunar City.  Native events are hints; all
 * displayed data still comes from the bounded store reread.  The returned
 * disposer stops reconciliation before releasing every subscription.
 */
export function startLunarCityReconciler(options: StartLunarCityReconcilerOptions = {}): () => void {
  const sources = options.sources ?? defaultLiveSources()
  const now = options.now ?? Date.now
  const sessionRows = sources.sessionRows ?? (() => sources.$sessions?.get() ?? [])
  const sessionStores = sources.sessionStores ?? (sources.$sessions ? [sources.$sessions] : [])
  let fleetReference = sources.$fleetRoster.get()
  let fleetObservedAt = 0
  let forceFleetRefresh = true
  let disposed = false

  const reconciler = createLunarCityReconciler({
    freshnessMs: options.freshnessMs,
    now,
    read: async () => {
      const force = forceFleetRefresh
      forceFleetRefresh = false
      let fleetReadFailed = false

      try {
        await sources.refreshFleet({ force })
      } catch {
        // The existing roster remains valuable evidence, but it must retain
        // its last successful observation timestamp and become stale below.
        fleetReadFailed = true
      }

      const roster = sources.$fleetRoster.get()

      const fleet = roster
        ? normalizeRoster(roster, { fresh: !fleetReadFailed && fleetObservedAt > 0, observedAt: fleetObservedAt })
        : { entities: [], sources: [] as readonly SourceHealth[] }

      const sessions = normalizeSessions(sessionRows(), {
        legacyConnectionId: sources.legacyConnectionId?.(),
        legacySingleBackend: sources.legacySingleBackend(),
        observedAt: now()
      })

      const subagents = normalizeSubagents(sources.$subagentsBySession.get(), sessions.entities, { observedAt: now() })

      return {
        authoritative: !fleetReadFailed,
        entities: [...fleet.entities, ...sessions.entities, ...subagents.entities],
        sources: [
          ...fleet.sources.map(source => (fleetReadFailed ? { ...source, error: 'Fleet refresh failed' } : source)),
          ...sessions.sources
        ]
      }
    }
  })

  const disposers: (() => void)[] = []
  disposers.push(
    sources.$fleetRoster.listen(roster => {
      if (roster !== fleetReference) {
        fleetReference = roster
        fleetObservedAt = now()
      }
    })
  )
  disposers.push(sources.$subagentsBySession.listen(() => reconciler.invalidate('subagents')))

  for (const store of sessionStores) {
    disposers.push(store.listen(() => reconciler.invalidate('sessions')))
  }

  if (sources.onFocus) {
    disposers.push(sources.onFocus(() => reconciler.invalidate('focus')))
  }

  if (sources.onRegistryChange) {
    disposers.push(
      sources.onRegistryChange(() => {
        forceFleetRefresh = true
        reconciler.invalidate('registry')
      })
    )
  }

  if (sources.onEvent) {
    disposers.push(
      sources.onEvent(event => {
        const type = optionalString(record(event).type)

        if (type === 'gateway.ready') {
          forceFleetRefresh = true
        }

        reconciler.acceptEvent(eventCursor(event))
      })
    )
  }

  const stop = reconciler.start()

  return () => {
    if (disposed) {
      return
    }

    disposed = true
    stop()

    for (const dispose of disposers.splice(0)) {
      dispose()
    }
  }
}
