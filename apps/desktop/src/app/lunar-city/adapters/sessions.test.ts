import { describe, expect, it } from 'vitest'

import { normalizeOwnedSubagents, normalizeSessions, normalizeSubagents } from './sessions'

const session = (overrides: Record<string, unknown> = {}) => ({
  archived: false,
  cwd: '/workspace/not-an-identity',
  ended_at: null,
  id: 'session-1',
  input_tokens: 0,
  is_active: true,
  last_active: 20,
  message_count: 1,
  model: null,
  output_tokens: 0,
  preview: null,
  source: null,
  started_at: 10,
  title: 'same display title',
  tool_call_count: 0,
  ...overrides
})

describe('session live adapter', () => {
  it('does not collapse duplicate session ids from distinct connection-tagged owners', () => {
    const normalized = normalizeSessions(
      [
        session({ connection_id: 'local', git_repo_root: '/projects/alpha', profile: 'worker' }),
        session({ connection_id: 'ssh-1', git_repo_root: '/projects/alpha', profile: 'worker' })
      ],
      { observedAt: 42 }
    )

    expect(normalized.entities.map(entity => entity.identity.connectionId)).toEqual(['local', 'ssh-1'])
    expect(new Set(normalized.entities.map(entity => entity.key)).size).toBe(2)
    expect(normalized.entities.map(entity => entity.projectId)).toEqual(['/projects/alpha', '/projects/alpha'])
  })

  it('applies freshness and the last authoritative timestamp per exact connection source', () => {
    const normalized = normalizeSessions(
      [session({ connection_id: 'local', profile: 'worker' }), session({ connection_id: 'ssh-1', profile: 'worker' })],
      {
        observedAt: 999,
        sourceObservations: new Map([
          ['local', { fresh: true, generation: 4, observedAt: 80 }],
          ['ssh-1', { fresh: false, generation: 2, observedAt: 40 }]
        ])
      }
    )

    expect(
      normalized.entities.map(entity => [entity.identity.connectionId, entity.authority, entity.observedAt])
    ).toEqual([
      ['local', 'authoritative', 80],
      ['ssh-1', 'stale', 40]
    ])
    expect(normalized.sources).toEqual([
      { authority: 'authoritative', observedAt: 80, source: 'session:local' },
      { authority: 'stale', observedAt: 40, source: 'session:ssh-1' }
    ])
  })

  it('fails closed for untagged sessions in a registry topology', () => {
    const normalized = normalizeSessions([session({ profile: 'worker' })], { observedAt: 42 })

    expect(normalized.entities).toEqual([])
  })

  it('allows a legacy session only when local ownership was explicitly proven', () => {
    const normalized = normalizeSessions([session({ git_repo_root: null, profile: 'worker' })], {
      legacyConnectionId: 'local',
      legacySingleBackend: true,
      observedAt: 42
    })

    expect(normalized.entities).toHaveLength(1)
    expect(normalized.entities[0]?.identity).toEqual({
      connectionId: 'local',
      kind: 'session',
      profile: 'worker',
      sessionId: 'session-1'
    })
    expect(normalized.entities[0]?.projectId).toBeUndefined()
  })

  it('joins a terminal child only through a unique exact-owner parent session', () => {
    const sessions = normalizeSessions([session({ connection_id: 'local', id: 'parent', profile: 'worker' })], {
      observedAt: 42
    })
    const normalized = normalizeSubagents(
      {
        parent: [
          {
            filesRead: [],
            filesWritten: [],
            goal: 'this must not become identity',
            id: 'sub-1',
            parentId: 'parent',
            startedAt: 10,
            status: 'completed',
            stream: [{ at: 20, kind: 'summary', text: 'secret stream text' }],
            taskCount: 1,
            taskIndex: 0,
            updatedAt: 20
          }
        ]
      },
      sessions.entities,
      { observedAt: 42 }
    )

    expect(normalized.entities).toHaveLength(1)
    expect(normalized.entities[0]).toMatchObject({
      animation: 'done',
      authority: 'authoritative',
      destination: 'project',
      identity: {
        connectionId: 'local',
        kind: 'subagent',
        profile: 'worker',
        sessionId: 'parent',
        subagentId: 'sub-1'
      }
    })
    expect(normalized.entities[0]).not.toHaveProperty('stream')
  })

  it('omits a child when matching parent ids have ambiguous source owners', () => {
    const parents = normalizeSessions(
      [
        session({ connection_id: 'local', id: 'parent', profile: 'worker' }),
        session({ connection_id: 'ssh-1', id: 'parent', profile: 'worker' })
      ],
      { observedAt: 42 }
    )

    const normalized = normalizeSubagents(
      {
        parent: [
          {
            filesRead: [],
            filesWritten: [],
            goal: 'work',
            id: 'sub-1',
            parentId: 'parent',
            startedAt: 10,
            status: 'running',
            stream: [],
            taskCount: 1,
            taskIndex: 0,
            updatedAt: 20
          }
        ]
      },
      parents.entities,
      { observedAt: 42 }
    )

    expect(normalized.entities).toEqual([])
  })

  it('normalizes per-owner child batches without collapsing duplicate parent or child ids', () => {
    const localOwner = {
      connectionId: 'local',
      kind: 'session' as const,
      profile: 'worker',
      sessionId: 'parent'
    }
    const remoteOwner = { ...localOwner, connectionId: 'ssh-1' }
    const row = {
      filesRead: [],
      filesWritten: [],
      goal: 'work',
      id: 'sub-1',
      parentId: 'parent',
      startedAt: 10,
      status: 'running' as const,
      stream: [],
      taskCount: 1,
      taskIndex: 0,
      updatedAt: 20
    }

    const normalized = normalizeOwnedSubagents(
      [
        { owner: localOwner, rows: [row] },
        { owner: remoteOwner, rows: [row] }
      ],
      {
        sourceObservations: new Map([
          ['local', { fresh: true, generation: 3, observedAt: 80 }],
          ['ssh-1', { fresh: false, generation: 1, observedAt: 30 }]
        ])
      }
    )

    expect(
      normalized.entities.map(entity => [entity.identity.connectionId, entity.authority, entity.observedAt])
    ).toEqual([
      ['local', 'authoritative', 80],
      ['ssh-1', 'stale', 30]
    ])
    expect(new Set(normalized.entities.map(entity => entity.key)).size).toBe(2)
  })

  it('fails closed when a subagent row claims a different parent than its map key', () => {
    const parents = normalizeSessions([session({ connection_id: 'local', id: 'parent', profile: 'worker' })], {
      observedAt: 42
    })

    const normalized = normalizeSubagents(
      {
        parent: [
          {
            filesRead: [],
            filesWritten: [],
            goal: 'work',
            id: 'sub-1',
            parentId: 'other-parent',
            startedAt: 10,
            status: 'running',
            stream: [],
            taskCount: 1,
            taskIndex: 0,
            updatedAt: 20
          }
        ]
      },
      parents.entities,
      { observedAt: 42 }
    )

    expect(normalized.entities).toEqual([])
  })
})
