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

    await createLunarCityCommandExecutors().session.send(plan)

    expect(requestForSessionProfile).toHaveBeenCalledOnce()
    expect(requestForSessionProfile.mock.calls[0]?.[0]).toEqual({ connectionId: 'source-b', profile: 'builder' })
    expect(requestForSessionProfile.mock.calls[0]?.[2]).toBe('prompt.submit')
    expect(requestForSessionProfile.mock.calls[0]?.[3]).toEqual({ session_id: 'same', text: 'Check.' })
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
    expect(readback).toMatchObject({ effect: plan.readback.expectedEffect, outcome: 'verified' })
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
})
