import type { DesktopAgentRoster } from '@/global'

import { entityKey } from '../identity'
import type { LunarEntity, SourceHealth } from '../model'

export interface NormalizedFleet {
  entities: readonly LunarEntity[]
  sources: readonly SourceHealth[]
}

export interface FleetObservation {
  fresh?: boolean
  observedAt: number
}

function sourceHealth(source: DesktopAgentRoster['sources'][number], observedAt: number): SourceHealth {
  return {
    authority: source.reachable ? 'authoritative' : 'stale',
    ...(source.error ? { error: source.error } : {}),
    observedAt,
    source: `fleet:${source.connectionId}`
  }
}

/**
 * Converts Electron's fleet roster into presentation entities without using
 * the active connection or a profile display name as an ownership fallback.
 */
export function normalizeRoster(roster: DesktopAgentRoster, observation: FleetObservation): NormalizedFleet {
  const fresh = observation.fresh !== false
  const healthByConnection = new Map(roster.sources.map(source => [source.connectionId, source]))

  const sources = [...roster.sources]
    .sort((left, right) => left.connectionId.localeCompare(right.connectionId))
    .map(source => ({
      ...sourceHealth(source, observation.observedAt),
      ...(fresh ? {} : { authority: 'stale' as const })
    }))

  const entities = [...roster.agents]
    .sort(
      (left, right) => left.connectionId.localeCompare(right.connectionId) || left.profile.localeCompare(right.profile)
    )
    .map(agent => {
      const source = healthByConnection.get(agent.connectionId)
      const available = fresh && source?.reachable === true
      const identity = { kind: 'profile' as const, connectionId: agent.connectionId, profile: agent.profile }

      return {
        animation: available ? 'rest' : 'unavailable',
        authority: fresh ? (available ? 'authoritative' : source ? 'stale' : 'unknown') : 'stale',
        destination: available ? 'garden' : fresh && source?.reachable === false ? 'unavailable' : 'unknown',
        identity,
        key: entityKey(identity),
        observedAt: observation.observedAt
      } satisfies LunarEntity
    })

  return { entities, sources }
}
