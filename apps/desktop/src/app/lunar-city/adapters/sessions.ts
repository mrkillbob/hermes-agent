import { sessionOwnerRouteFromRow } from '@/store/session-request-router'
import type { SubagentProgress } from '@/store/subagents'
import type { SessionInfo } from '@/types/hermes'

import { entityKey } from '../identity'
import type { LunarEntity, SourceHealth } from '../model'
import { mapObservedState } from '../state-map'

export interface SessionObservation {
  fresh?: boolean
  legacyConnectionId?: string
  legacySingleBackend?: boolean
  observedAt: number
}

export interface NormalizedSessions {
  entities: readonly LunarEntity[]
  sources: readonly SourceHealth[]
}

export interface NormalizedSubagents {
  entities: readonly LunarEntity[]
}

function isFresh(observation: SessionObservation): boolean {
  return observation.fresh !== false
}

function exactSessionOwner(
  row: SessionInfo,
  observation: SessionObservation
): { connectionId: string; profile: string } | undefined {
  const routed = sessionOwnerRouteFromRow(row)

  if (routed) {
    return routed
  }

  if (!observation.legacySingleBackend || !observation.legacyConnectionId?.trim()) {
    return undefined
  }

  return { connectionId: observation.legacyConnectionId.trim(), profile: row.profile?.trim() || 'default' }
}

function sessionStatus(row: SessionInfo): string {
  if (row.ended_at !== null && row.ended_at !== undefined) {
    return 'completed'
  }

  return row.is_active ? 'running' : 'idle'
}

function sourceRows(entities: readonly LunarEntity[], observation: SessionObservation): readonly SourceHealth[] {
  return [...new Set(entities.map(entity => `session:${entity.identity.connectionId}`))]
    .sort((left, right) => left.localeCompare(right))
    .map(source => ({
      authority: isFresh(observation) ? 'authoritative' : 'stale',
      observedAt: observation.observedAt,
      source
    }))
}

/**
 * Normalizes session rows only when their connection/profile owner is explicit.
 * A legacy connection can be supplied only by a caller that has proven the
 * single-backend topology; active UI state is deliberately never consulted.
 */
export function normalizeSessions(rows: readonly SessionInfo[], observation: SessionObservation): NormalizedSessions {
  const entities = rows
    .flatMap(row => {
      const owner = exactSessionOwner(row, observation)

      if (!owner || !row.id.trim()) {
        return []
      }

      const identity = {
        connectionId: owner.connectionId,
        kind: 'session' as const,
        profile: owner.profile,
        sessionId: row.id
      }

      const state = mapObservedState({ fresh: isFresh(observation), source: 'session', status: sessionStatus(row) })

      return [
        {
          ...state,
          identity,
          key: entityKey(identity),
          observedAt: observation.observedAt,
          ...(row.git_repo_root?.trim() ? { projectId: row.git_repo_root } : {})
        } satisfies LunarEntity
      ]
    })
    .sort((left, right) => left.key.localeCompare(right.key))

  return { entities, sources: sourceRows(entities, observation) }
}

function statusForSubagent(status: SubagentProgress['status'] | string): string {
  if (status === 'complete') {
    return 'completed'
  }

  return status
}

/** Converts one bounded progress row into a presentation entity. */
export function normalizeSubagent(
  row: Pick<SubagentProgress, 'id' | 'status'>,
  owner: Extract<LunarEntity['identity'], { kind: 'session' }>,
  observation: Pick<SessionObservation, 'fresh' | 'observedAt'>
): LunarEntity | undefined {
  if (!row.id.trim()) {
    return undefined
  }

  const identity = {
    connectionId: owner.connectionId,
    kind: 'subagent' as const,
    profile: owner.profile,
    sessionId: owner.sessionId,
    subagentId: row.id
  }

  const state = mapObservedState({
    fresh: observation.fresh !== false,
    source: 'subagent',
    status: statusForSubagent(row.status)
  })

  return { ...state, identity, key: entityKey(identity), observedAt: observation.observedAt }
}

/**
 * The subagent store is keyed only by parent session id.  A parent id that
 * resolves to zero or more than one exact session owner is unrepresentable in
 * the city rather than being guessed onto the active source.
 */
export function normalizeSubagents(
  byParentSession: Readonly<Record<string, readonly SubagentProgress[]>>,
  parentSessions: readonly LunarEntity[],
  observation: Pick<SessionObservation, 'fresh' | 'observedAt'>
): NormalizedSubagents {
  const owners = new Map<string, Extract<LunarEntity['identity'], { kind: 'session' }>[]>()

  for (const entity of parentSessions) {
    if (entity.identity.kind !== 'session') {
      continue
    }

    const candidates = owners.get(entity.identity.sessionId) ?? []
    candidates.push(entity.identity)
    owners.set(entity.identity.sessionId, candidates)
  }

  const entities = Object.entries(byParentSession)
    .flatMap(([parentSessionId, rows]) => {
      const candidates = owners.get(parentSessionId)

      if (candidates?.length !== 1) {
        return []
      }

      const owner = candidates[0]!

      return rows.flatMap(row => {
        if (row.parentId !== null && row.parentId !== parentSessionId) {
          return []
        }

        const entity = normalizeSubagent(row, owner, observation)

        return entity ? [entity] : []
      })
    })
    .sort((left, right) => left.key.localeCompare(right.key))

  return { entities }
}
