import { beforeEach, describe, expect, it, vi } from 'vitest'

import { type CommandPlanningSnapshot, CommandRejectedError, planCommand } from './command-broker'
import { createLunarCityCommandExecutors } from './command-executors'
import { entityKey } from './identity'
import type { EntityIdentity, LunarCitySnapshot, LunarEntity, SourceHealth } from './model'

const { pluginRest, requestForSessionProfile } = vi.hoisted(() => ({
  pluginRest: vi.fn(),
  requestForSessionProfile: vi.fn()
}))

vi.mock('@/api/plugins', () => ({ pluginRest }))
vi.mock('@/store/session-request-router', () => ({ requestForSessionProfile }))

function frozenMap<K, V>(entries: readonly (readonly [K, V])[]): ReadonlyMap<K, V> {
  return new Map(entries)
}

function planning(identity: EntityIdentity, state = 'working'): CommandPlanningSnapshot {
  const key = entityKey(identity)
  const observedAt = 100

  const source: SourceHealth = {
    authority: 'authoritative',
    observedAt,
    source:
      identity.kind === 'kanban'
        ? `kanban:${encodeURIComponent(identity.connectionId)}:${encodeURIComponent(identity.profile)}`
        : `session:${identity.connectionId}`
  }

  const entity: LunarEntity = {
    animation: state,
    authority: 'authoritative',
    destination: 'project',
    identity,
    key,
    observedAt
  }

  const city: LunarCitySnapshot = {
    entities: frozenMap([[key, entity]]),
    observedAt,
    revision: 4,
    sources: [source]
  }

  const owner = { connectionId: identity.connectionId, profile: identity.profile }

  return {
    city,
    targets: frozenMap([
      [
        key,
        {
          availableOperations:
            identity.kind === 'session'
              ? (['send-guidance'] as const)
              : (['inspect-evidence', 'reclaim-task', 'terminate-run', 'change-task-state'] as const),
          entity,
          observedState: {
            animation: state,
            authority: 'authoritative' as const,
            destination: 'project' as const,
            observedAt,
            source: source.source,
            value: state
          },
          ownerCandidates: [owner],
          readbackCapabilities:
            identity.kind === 'kanban' ? (['kanban-task', 'kanban-run'] as const) : (['session'] as const),
          source,
          sourceOwner: owner
        }
      ]
    ])
  }
}

beforeEach(() => vi.clearAllMocks())

describe('createLunarCityCommandExecutors', () => {
  it('sends session guidance only through the immutable exact owner route', async () => {
    requestForSessionProfile.mockResolvedValue({ status: 'queued' })
    const identity = { connectionId: 'source-b', kind: 'session', profile: 'builder', sessionId: 'same' } as const
    const snapshot = planning(identity)
    const plan = planCommand({ entityKey: entityKey(identity), kind: 'send-guidance', text: 'Check.' }, snapshot)

    await createLunarCityCommandExecutors({
      resolveLiveRuntime: (owner, storedId) =>
        owner.connectionId === 'source-b' && storedId === 'same' ? 'runtime-b' : undefined
    }).session.send(plan)

    expect(requestForSessionProfile).toHaveBeenCalledOnce()
    expect(requestForSessionProfile.mock.calls[0]?.[0]).toEqual({ connectionId: 'source-b', profile: 'builder' })
    expect(requestForSessionProfile.mock.calls[0]?.[2]).toBe('prompt.submit')
    expect(requestForSessionProfile.mock.calls[0]?.[3]).toEqual({ session_id: 'runtime-b', text: 'Check.' })
  })

  it('does not send a stored DB id when no exact-owner live runtime is authoritative', async () => {
    const identity = {
      connectionId: 'source-b',
      kind: 'session',
      profile: 'builder',
      sessionId: 'stored-only'
    } as const

    const snapshot = planning(identity)
    const plan = planCommand({ entityKey: entityKey(identity), kind: 'send-guidance', text: 'Check.' }, snapshot)

    await expect(
      createLunarCityCommandExecutors({ resolveLiveRuntime: () => undefined }).session.send(plan)
    ).rejects.toThrow(/live runtime.*unavailable/i)
    expect(requestForSessionProfile).not.toHaveBeenCalled()
  })

  it('uses only exact scoped Kanban endpoints and verifies from authoritative task readback', async () => {
    pluginRest
      .mockResolvedValueOnce({ ok: true, task_id: 'T-9' })
      .mockResolvedValueOnce({ task: { current_run_id: null, id: 'T-9', status: 'ready' } })

    const identity = {
      board: 'alpha/beta',
      connectionId: 'source-b',
      kind: 'kanban',
      profile: 'builder',
      runId: '7',
      taskId: 'T-9'
    } as const

    const snapshot = planning(identity, 'running')
    const plan = planCommand({ entityKey: entityKey(identity), kind: 'reclaim-task' }, snapshot)
    const executor = createLunarCityCommandExecutors().kanbanTask

    await executor.send(plan)
    const readback = await executor.readback(plan)

    expect(pluginRest.mock.calls).toEqual([
      [
        'kanban',
        '/tasks/T-9/reclaim?board=alpha%2Fbeta',
        {
          body: { reason: 'Operator confirmed in Lunar City' },
          method: 'POST',
          scope: { connectionId: 'source-b', profile: 'builder' }
        }
      ],
      [
        'kanban',
        '/tasks/T-9?board=alpha%2Fbeta',
        { method: 'GET', scope: { connectionId: 'source-b', profile: 'builder' } }
      ]
    ])
    expect(readback).toMatchObject({ effect: plan.readback.expectedEffect, outcome: 'verified' })
  })

  it('does not retry an ambiguous Kanban mutation failure', async () => {
    pluginRest.mockRejectedValue(new Error('request timed out'))

    const identity = {
      board: 'main',
      connectionId: 'source-a',
      kind: 'kanban',
      profile: 'default',
      taskId: 'T-2'
    } as const

    const snapshot = planning(identity, 'running')
    const plan = planCommand({ entityKey: entityKey(identity), kind: 'reclaim-task' }, snapshot)

    await expect(createLunarCityCommandExecutors().kanbanTask.send(plan)).rejects.toThrow('request timed out')
    expect(pluginRest).toHaveBeenCalledOnce()
  })

  it('reads Kanban evidence through the exact task endpoint without issuing a write', async () => {
    pluginRest.mockResolvedValue({ task: { id: 'T-4', status: 'blocked' } })

    const identity = {
      board: 'main',
      connectionId: 'source-a',
      kind: 'kanban',
      profile: 'default',
      taskId: 'T-4'
    } as const

    const snapshot = planning(identity, 'blocked')

    const plan = planCommand(
      { entityKey: entityKey(identity), evidence: 'diagnostics', kind: 'inspect-evidence' },
      snapshot
    )

    const executor = createLunarCityCommandExecutors().kanbanTask

    await executor.send(plan)
    const readback = await executor.readback(plan)

    expect(pluginRest).toHaveBeenCalledOnce()
    expect(pluginRest).toHaveBeenCalledWith('kanban', '/tasks/T-4?board=main', {
      method: 'GET',
      scope: { connectionId: 'source-a', profile: 'default' }
    })
    expect(readback).toBeNull()
  })

  it('does not verify or replace malformed exact-source evidence', async () => {
    pluginRest.mockResolvedValue({ task: { diagnostics: ['wrong'], id: 'T-other', status: 'blocked' } })

    const identity = {
      board: 'main',
      connectionId: 'source-a',
      kind: 'kanban',
      profile: 'default',
      taskId: 'T-4'
    } as const

    const snapshot = planning(identity, 'blocked')

    const plan = planCommand(
      { entityKey: entityKey(identity), evidence: 'diagnostics', kind: 'inspect-evidence' },
      snapshot
    )

    const executor = createLunarCityCommandExecutors().kanbanTask

    await expect(executor.send(plan)).rejects.toThrow(/exact-task evidence.*unavailable/i)
    await expect(executor.readback(plan)).resolves.toBeNull()
  })

  it('classifies an authoritative Kanban conflict as rejected instead of ambiguous', async () => {
    pluginRest.mockRejectedValue({ message: 'already ended', status: 409 })

    const identity = {
      board: 'main',
      connectionId: 'source-a',
      kind: 'kanban',
      profile: 'default',
      taskId: 'T-5'
    } as const

    const snapshot = planning(identity, 'work')
    const plan = planCommand({ entityKey: entityKey(identity), kind: 'reclaim-task' }, snapshot)

    await expect(createLunarCityCommandExecutors().kanbanTask.send(plan)).rejects.toBeInstanceOf(CommandRejectedError)
    expect(pluginRest).toHaveBeenCalledOnce()
  })

  it.each([
    ['empty payload', {}],
    ['missing task id', { task: { current_run_id: null, status: 'ready' } }],
    ['mismatched task id', { task: { current_run_id: null, id: 'T-other', status: 'ready' } }],
    ['missing terminal run field', { task: { id: 'T-6', status: 'ready' } }],
    ['wrong terminal status type', { task: { current_run_id: null, id: 'T-6', status: 7 } }]
  ])('does not verify reclaim from %s', async (_label, malformed) => {
    pluginRest.mockResolvedValueOnce({ ok: true, task_id: 'T-6' }).mockResolvedValueOnce(malformed)

    const identity = {
      board: 'main',
      connectionId: 'source-a',
      kind: 'kanban',
      profile: 'default',
      runId: '8',
      taskId: 'T-6'
    } as const

    const snapshot = planning(identity, 'work')
    const plan = planCommand({ entityKey: entityKey(identity), kind: 'reclaim-task' }, snapshot)
    const executor = createLunarCityCommandExecutors().kanbanTask

    await executor.send(plan)
    const readback = await executor.readback(plan)

    expect(readback).not.toMatchObject({ effect: plan.readback.expectedEffect, outcome: 'verified' })
  })

  it.each([
    ['mismatched run id', { run: { ended_at: 200, id: 99, outcome: 'reclaimed', task_id: 'T-7' } }],
    ['mismatched task id', { run: { ended_at: 200, id: 9, outcome: 'reclaimed', task_id: 'T-other' } }],
    ['missing terminal timestamp', { run: { id: 9, outcome: 'reclaimed', task_id: 'T-7' } }],
    ['wrong terminal outcome', { run: { ended_at: 200, id: 9, outcome: 'running', task_id: 'T-7' } }]
  ])('does not verify terminate-run from %s', async (_label, malformed) => {
    pluginRest.mockResolvedValueOnce({ ok: true, run_id: 9 }).mockResolvedValueOnce(malformed)

    const identity = {
      board: 'main',
      connectionId: 'source-a',
      kind: 'kanban',
      profile: 'default',
      runId: '9',
      taskId: 'T-7'
    } as const

    const snapshot = planning(identity, 'work')
    const plan = planCommand({ entityKey: entityKey(identity), kind: 'terminate-run' }, snapshot)
    const executor = createLunarCityCommandExecutors().kanbanRun

    await executor.send(plan)
    const readback = await executor.readback(plan)

    expect(readback).not.toMatchObject({ effect: plan.readback.expectedEffect, outcome: 'verified' })
  })

  it('does not manufacture verified session evidence without an authoritative evidence seam', async () => {
    const identity = { connectionId: 'source-b', kind: 'session', profile: 'builder', sessionId: 'stored-8' } as const
    const snapshot = planning(identity)

    const plan = planCommand(
      { entityKey: entityKey(identity), evidence: 'diagnostics', kind: 'inspect-evidence' },
      {
        ...snapshot,
        targets: new Map([
          [
            entityKey(identity),
            { ...snapshot.targets.get(entityKey(identity))!, availableOperations: ['inspect-evidence'] }
          ]
        ])
      }
    )

    const executor = createLunarCityCommandExecutors().session

    await expect(executor.send(plan)).rejects.toThrow(/authoritative session evidence.*unavailable/i)
    await expect(executor.readback(plan)).resolves.toBeNull()
  })
})
