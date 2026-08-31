import { describe, expect, it, vi } from 'vitest'

import {
  type CommandExecutors,
  type CommandIntent,
  type CommandPlanningSnapshot,
  type CommandReadback,
  CommandRejectedError,
  type CommandTargetState,
  CommandTimeoutError,
  executeCommand,
  planCommand
} from './command-broker'
import { entityKey } from './identity'
import type { EntityIdentity, LunarCitySnapshot, LunarEntity, SourceHealth } from './model'

const OWNER = { connectionId: 'connection-a', profile: 'worker' }

const SESSION_IDENTITY: EntityIdentity = {
  connectionId: OWNER.connectionId,
  kind: 'session',
  profile: OWNER.profile,
  sessionId: 'session-1'
}

const SUBAGENT_IDENTITY: EntityIdentity = {
  connectionId: OWNER.connectionId,
  kind: 'subagent',
  profile: OWNER.profile,
  sessionId: 'session-1',
  subagentId: 'child-1'
}

const KANBAN_IDENTITY: EntityIdentity = {
  board: 'primary',
  connectionId: OWNER.connectionId,
  kind: 'kanban',
  profile: OWNER.profile,
  runId: 'run-7',
  taskId: 'task-7',
  workerId: 'worker-7'
}

const SOURCE: SourceHealth = {
  authority: 'authoritative',
  observedAt: 2_000,
  source: 'connection-a/sessions'
}

function entity(identity: EntityIdentity, authority: LunarEntity['authority'] = 'authoritative'): LunarEntity {
  return {
    animation: 'work',
    authority,
    destination: 'project',
    identity,
    key: entityKey(identity),
    observedAt: 2_000
  }
}

function target(identity: EntityIdentity, overrides: Partial<CommandTargetState> = {}): CommandTargetState {
  return {
    availableOperations: [
      'change-task-state',
      'dispatch-task',
      'inspect-evidence',
      'interrupt-session',
      'interrupt-subagent',
      'open-session',
      'reassign-task',
      'reclaim-task',
      'retry-task',
      'send-guidance',
      'terminate-run'
    ],
    canonicalProjectId: 'project-1',
    currentState: 'running',
    entity: entity(identity),
    ownerCandidates: [OWNER],
    readbackCapabilities: ['kanban-run', 'kanban-task', 'session', 'subagent'],
    repositoryId: 'repo/hermes',
    source: SOURCE,
    ...overrides
  }
}

function snapshot(
  identity: EntityIdentity,
  overrides: Partial<CommandTargetState> = {},
  sources: readonly SourceHealth[] = [SOURCE]
): CommandPlanningSnapshot {
  const value = target(identity, overrides)

  const city: LunarCitySnapshot = {
    entities: new Map([[value.entity.key, value.entity]]),
    observedAt: 2_000,
    revision: 3,
    sources
  }

  return { city, targets: new Map([[value.entity.key, value]]) }
}

function intent(kind: CommandIntent['kind'], identity: EntityIdentity = SESSION_IDENTITY): CommandIntent {
  const key = entityKey(identity)

  switch (kind) {
    case 'send-guidance':
      return { entityKey: key, kind, text: 'Please inspect the failing test.' }

    case 'reassign-task':
      return { assignee: 'reviewer', entityKey: key, kind }

    case 'change-task-state':
      return { entityKey: key, kind, state: 'review' }

    case 'inspect-evidence':
      return { entityKey: key, evidence: 'diagnostics', kind }

    default:
      return { entityKey: key, kind }
  }
}

function executor(readback: CommandReadback | null): {
  calls: ReturnType<typeof vi.fn>
  executors: CommandExecutors
  reads: ReturnType<typeof vi.fn>
} {
  const calls = vi.fn().mockResolvedValue({ accepted: true })
  const reads = vi.fn().mockResolvedValue(readback)
  const bridge = { readback: reads, send: calls }

  return {
    calls,
    executors: { kanbanRun: bridge, kanbanTask: bridge, session: bridge, subagent: bridge },
    reads
  }
}

function verifiedReadback(
  identity: EntityIdentity,
  operation: CommandIntent['kind'],
  overrides: Partial<CommandReadback> = {}
): CommandReadback {
  return {
    authority: 'authoritative',
    identity,
    observedAt: 2_100,
    operation,
    outcome: 'verified',
    owner: OWNER,
    state: operation === 'change-task-state' ? 'review' : undefined,
    ...overrides
  }
}

describe('LunarCityCommandBroker planning', () => {
  it.each([
    ['open-session', false, SESSION_IDENTITY],
    ['inspect-evidence', false, SESSION_IDENTITY],
    ['send-guidance', false, SESSION_IDENTITY],
    ['interrupt-session', true, SESSION_IDENTITY],
    ['interrupt-subagent', true, SUBAGENT_IDENTITY],
    ['terminate-run', true, KANBAN_IDENTITY],
    ['retry-task', true, KANBAN_IDENTITY],
    ['reclaim-task', true, KANBAN_IDENTITY],
    ['reassign-task', true, KANBAN_IDENTITY],
    ['dispatch-task', true, KANBAN_IDENTITY],
    ['change-task-state', true, KANBAN_IDENTITY]
  ] as const)('classifies %s confirmation as %s', (kind, confirmation, identity) => {
    const plan = planCommand(intent(kind, identity), snapshot(identity))

    expect(plan.confirmation).toBe(confirmation)
  })

  it('preserves complete identity, exact owner, typed params, consequence, context, and exact readback', () => {
    const plan = planCommand(intent('reassign-task', KANBAN_IDENTITY), snapshot(KANBAN_IDENTITY))

    expect(plan).toMatchObject({
      confirmation: true,
      context: {
        canonicalProjectId: 'project-1',
        currentState: 'running',
        repositoryId: 'repo/hermes'
      },
      entityKey: entityKey(KANBAN_IDENTITY),
      identity: KANBAN_IDENTITY,
      method: 'kanban.task.reassign',
      operation: 'reassign-task',
      owner: OWNER,
      params: {
        assignee: 'reviewer',
        board: 'primary',
        run_id: 'run-7',
        task_id: 'task-7',
        worker_id: 'worker-7'
      },
      readback: { id: 'task-7', kind: 'kanban-task' }
    })
    expect(plan.consequence).toContain('reviewer')
  })

  it.each([
    [
      'duplicate display-name owners',
      { ownerCandidates: [OWNER, { connectionId: 'connection-b', profile: 'worker' }] }
    ],
    ['mismatched owner', { ownerCandidates: [{ connectionId: 'connection-b', profile: 'worker' }] }],
    ['missing owner', { ownerCandidates: [] }]
  ] as const)('fails closed with owner-is-ambiguous for %s', (_label, overrides) => {
    expect(() => planCommand(intent('open-session'), snapshot(SESSION_IDENTITY, overrides))).toThrow(
      /owner-is-ambiguous/
    )
  })

  it('fails closed for unsupported, stale, partial, unavailable Kanban, and unreadable mutations', () => {
    expect(() =>
      planCommand(intent('interrupt-session'), snapshot(SESSION_IDENTITY, { availableOperations: [] }))
    ).toThrow(/unsupported-command/)
    expect(() =>
      planCommand(
        intent('interrupt-session'),
        snapshot(SESSION_IDENTITY, { entity: entity(SESSION_IDENTITY, 'stale') })
      )
    ).toThrow(/target-is-stale/)
    expect(() =>
      planCommand(
        intent('interrupt-session'),
        snapshot(SESSION_IDENTITY, { entity: entity(SESSION_IDENTITY, 'partial') })
      )
    ).toThrow(/target-is-partial/)
    expect(() => planCommand(intent('retry-task', KANBAN_IDENTITY), snapshot(KANBAN_IDENTITY, {}, []))).toThrow(
      /kanban-source-unavailable/
    )
    expect(() =>
      planCommand(intent('interrupt-session'), snapshot(SESSION_IDENTITY, { readbackCapabilities: ['subagent'] }))
    ).toThrow(/readback-unavailable/)
  })

  it('keeps stale read-only evidence inspectable with its source timestamp', () => {
    const plan = planCommand(
      intent('inspect-evidence'),
      snapshot(
        SESSION_IDENTITY,
        {
          entity: entity(SESSION_IDENTITY, 'stale'),
          source: { ...SOURCE, authority: 'stale' }
        },
        [{ ...SOURCE, authority: 'stale' }]
      )
    )

    expect(plan.confirmation).toBe(false)
    expect(plan.context.source).toEqual({ ...SOURCE, authority: 'stale' })
  })
})

describe('LunarCityCommandBroker execution', () => {
  it.each([
    ['interrupt-session', SESSION_IDENTITY, 'session'],
    ['interrupt-subagent', SUBAGENT_IDENTITY, 'subagent'],
    ['retry-task', KANBAN_IDENTITY, 'kanbanTask'],
    ['terminate-run', KANBAN_IDENTITY, 'kanbanRun']
  ] as const)(
    'uses the exact %s route once and verifies only matching readback',
    async (kind, identity, executorKey) => {
      const plan = planCommand(intent(kind, identity), snapshot(identity))
      const matching = verifiedReadback(identity, kind)
      const selected = executor(matching)
      const unused = executor(matching)

      const executors = {
        kanbanRun: unused.executors.kanbanRun,
        kanbanTask: unused.executors.kanbanTask,
        session: unused.executors.session,
        subagent: unused.executors.subagent,
        [executorKey]: selected.executors[executorKey]
      }

      const receipt = await executeCommand(plan, executors, { confirmed: true })

      expect(selected.calls).toHaveBeenCalledTimes(1)
      expect(selected.calls).toHaveBeenCalledWith(plan)
      expect(selected.reads).toHaveBeenCalledTimes(1)
      expect(receipt).toMatchObject({ identity, verification: 'verified' })
    }
  )

  it('returns rejected and unsent confirmation refusal without using an ambient route', async () => {
    const plan = planCommand(intent('interrupt-session'), snapshot(SESSION_IDENTITY))
    const selected = executor(verifiedReadback(SESSION_IDENTITY, 'interrupt-session'))

    const receipt = await executeCommand(plan, selected.executors, { confirmed: false })

    expect(receipt.verification).toBe('rejected')
    expect(selected.calls).not.toHaveBeenCalled()
    expect(selected.reads).not.toHaveBeenCalled()
  })

  it.each([
    ['foreign readback', { readback: { id: 'foreign-session', kind: 'session' } }],
    ['non-allowlisted method', { method: 'shell.exec' }],
    ['mismatched owner', { owner: { connectionId: 'connection-b', profile: 'worker' } }]
  ] as const)('rejects a forged plan with %s before any send', async (_label, overrides) => {
    const valid = planCommand(intent('interrupt-session'), snapshot(SESSION_IDENTITY))
    const forged = { ...valid, ...overrides } as typeof valid
    const selected = executor(verifiedReadback(SESSION_IDENTITY, 'interrupt-session'))

    const receipt = await executeCommand(forged, selected.executors, { confirmed: true })

    expect(receipt.verification).toBe('rejected')
    expect(receipt.error).toContain('Invalid command plan')
    expect(selected.calls).not.toHaveBeenCalled()
    expect(selected.reads).not.toHaveBeenCalled()
  })

  it('distinguishes definite rejection, pre-send timeout, and possibly-applied timeout without retrying', async () => {
    const plan = planCommand(intent('interrupt-session'), snapshot(SESSION_IDENTITY))

    for (const [error, expected] of [
      [new CommandRejectedError('backend refused'), 'rejected'],
      [new CommandTimeoutError('connection unavailable', false), 'timed_out'],
      [new CommandTimeoutError('ACK timed out', true), 'verification_required']
    ] as const) {
      const selected = executor(null)
      selected.calls.mockRejectedValueOnce(error)

      const receipt = await executeCommand(plan, selected.executors, { confirmed: true })

      expect(receipt.verification).toBe(expected)
      expect(selected.calls).toHaveBeenCalledTimes(1)
      expect(selected.reads).not.toHaveBeenCalled()
    }
  })

  it.each([
    ['missing', null],
    ['stale', verifiedReadback(SESSION_IDENTITY, 'interrupt-session', { observedAt: 1_999 })],
    [
      'mismatched identity',
      verifiedReadback({ ...SESSION_IDENTITY, sessionId: 'foreign-session' }, 'interrupt-session')
    ],
    [
      'mismatched owner',
      verifiedReadback(SESSION_IDENTITY, 'interrupt-session', {
        owner: { connectionId: 'connection-b', profile: 'worker' }
      })
    ],
    ['unexpected operation', verifiedReadback(SESSION_IDENTITY, 'send-guidance')],
    ['unverified outcome', verifiedReadback(SESSION_IDENTITY, 'interrupt-session', { outcome: 'unknown' })]
  ] as const)('does not verify a success-shaped ACK with %s readback', async (_label, readback) => {
    const plan = planCommand(intent('interrupt-session'), snapshot(SESSION_IDENTITY))
    const selected = executor(readback)

    const receipt = await executeCommand(plan, selected.executors, { confirmed: true })

    expect(selected.calls).toHaveBeenCalledTimes(1)
    expect(receipt.verification).toBe('verification_required')
  })

  it('uses expected task state as part of authoritative readback instead of moving a worker on ACK', async () => {
    const plan = planCommand(intent('change-task-state', KANBAN_IDENTITY), snapshot(KANBAN_IDENTITY))
    const selected = executor(verifiedReadback(KANBAN_IDENTITY, 'change-task-state', { state: 'running' }))

    const receipt = await executeCommand(plan, selected.executors, { confirmed: true })

    expect(receipt.verification).toBe('verification_required')
  })
})
