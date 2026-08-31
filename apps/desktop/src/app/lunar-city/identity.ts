import type { EntityIdentity, EntityKey } from './model'

type FieldName = 'board' | 'connection' | 'kind' | 'profile' | 'run' | 'session' | 'subagent' | 'task' | 'worker'

function required(name: FieldName, value: string): string {
  if (value.trim().length === 0) {
    throw new Error(`Lunar City identity ${name === 'connection' ? 'connectionId' : `${name}Id`} is required`)
  }

  return value
}

function optional(value: string | undefined): string {
  if (value === undefined) {
    return '@absent'
  }

  return value.length === 0 ? '@empty' : value
}

function field(name: FieldName, value: string): string {
  return `${name}=${encodeURIComponent(value)}`
}

/**
 * Builds an opaque, canonical key from typed source identity only.  The field
 * labels and explicit optional-value sentinels make the representation safe
 * even when external IDs contain the delimiter, URL syntax, or Unicode.
 */
export function entityKey(identity: EntityIdentity): EntityKey {
  const common = [field('kind', identity.kind), field('connection', required('connection', identity.connectionId))]

  if (identity.kind === 'profile') {
    return [...common, field('profile', required('profile', identity.profile))].join(':') as EntityKey
  }

  if (identity.kind === 'session') {
    return [
      ...common,
      field('profile', required('profile', identity.profile)),
      field('session', required('session', identity.sessionId))
    ].join(':') as EntityKey
  }

  if (identity.kind === 'subagent') {
    return [
      ...common,
      field('profile', required('profile', identity.profile)),
      field('session', required('session', identity.sessionId)),
      field('subagent', required('subagent', identity.subagentId))
    ].join(':') as EntityKey
  }

  return [
    ...common,
    field('board', required('board', identity.board)),
    field('task', required('task', identity.taskId)),
    field('run', optional(identity.runId)),
    field('worker', optional(identity.workerId))
  ].join(':') as EntityKey
}
