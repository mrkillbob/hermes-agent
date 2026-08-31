import { describe, expect, it } from 'vitest'

import { entityKey } from './identity'
import type { EntityIdentity } from './model'

describe('entityKey', () => {
  it('includes the complete source identity so similarly named workers on separate connections cannot collide', () => {
    const identities: EntityIdentity[] = [
      { kind: 'profile', connectionId: 'local', profile: 'worker' },
      { kind: 'profile', connectionId: 'ssh-1', profile: 'worker' },
      { kind: 'session', connectionId: 'local', profile: 'worker', sessionId: 'same-session' },
      {
        kind: 'subagent',
        connectionId: 'local',
        profile: 'worker',
        sessionId: 'same-session',
        subagentId: 'child'
      },
      {
        kind: 'kanban',
        connectionId: 'local',
        board: 'default',
        taskId: 'same-session',
        workerId: 'child'
      }
    ]

    expect(new Set(identities.map(entityKey)).size).toBe(identities.length)
  })

  it('uses tagged escaped fields so delimiters, unicode, and optional empty identifiers cannot create a collision', () => {
    const special = entityKey({
      kind: 'kanban',
      connectionId: 'a:b/% ✓',
      board: 'board:one',
      taskId: 'task/one',
      runId: '',
      workerId: ''
    })
    const ambiguous = entityKey({
      kind: 'kanban',
      connectionId: 'a',
      board: 'b/% ✓:board',
      taskId: 'one:task/one',
      runId: undefined,
      workerId: undefined
    })

    expect(special).not.toBe(ambiguous)
    expect(special).toContain('connection=')
    expect(special).toContain('run=%40empty')
    expect(ambiguous).toContain('run=%40absent')
  })

  it('fails closed when a required canonical identity field is missing instead of using a display name', () => {
    expect(() =>
      entityKey({ kind: 'session', connectionId: 'local', profile: 'worker', sessionId: ' ' } as EntityIdentity)
    ).toThrow(/sessionId is required/i)
  })
})
