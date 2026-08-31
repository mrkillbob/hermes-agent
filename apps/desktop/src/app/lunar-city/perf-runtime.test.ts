import { describe, expect, it, vi } from 'vitest'

import { entityKey } from './identity'
import type { EntityKey, LunarCitySnapshot, LunarEntity } from './model'
import { createLunarCityPerfRuntime } from './perf-runtime'

const worker = {
  animation: 'work',
  authority: 'authoritative',
  destination: 'lab',
  identity: { connectionId: 'source-a', kind: 'session', profile: 'research', sessionId: 'session-a' },
  key: entityKey({ connectionId: 'source-a', kind: 'session', profile: 'research', sessionId: 'session-a' }),
  observedAt: 100,
  presentation: {
    groups: [],
    metadata: { source: 'profiles:source-a', state: 'fresh' },
    placement: { lodHint: 0, overflow: false, slot: 0 }
  }
} as const

const leader = {
  animation: 'rest',
  authority: 'authoritative',
  destination: 'council',
  identity: { connectionId: 'source-b', kind: 'profile', profile: 'leader' },
  key: entityKey({ connectionId: 'source-b', kind: 'profile', profile: 'leader' }),
  observedAt: 100,
  presentation: {
    groups: [],
    metadata: { source: 'profiles:source-b', state: 'fresh' },
    placement: { lodHint: 1, overflow: false, primaryGroupId: 'leaders', slot: 1 }
  }
} as const

const city = {
  entities: new Map<EntityKey, LunarEntity>([
    [worker.key, worker],
    [leader.key, leader]
  ]),
  observedAt: 100,
  revision: 1,
  sources: []
} as unknown as LunarCitySnapshot

describe('Lunar City route-local packaged metrics runtime', () => {
  it('allocates no listener or sampling state when the acceptance endpoint is absent', () => {
    const endpoint = { onRequest: vi.fn() }
    const runtime = createLunarCityPerfRuntime(undefined)

    expect(runtime.enabled).toBe(false)
    expect(endpoint.onRequest).not.toHaveBeenCalled()
    expect(runtime.registerRoute).toBeUndefined()
  })

  it('reports exact route population, scene, scheduler and Babylon adapter facts', async () => {
    let request!: (action: string, payload: unknown) => Promise<unknown>
    const release = vi.fn()

    const runtime = createLunarCityPerfRuntime({
      onRequest: callback => {
        request = callback

        return release
      }
    })

    const route = runtime.registerRoute!({
      canvas: document.createElement('canvas'),
      getCameraState: () => ({ focusedEntityKey: worker.key, following: true }),
      getCameraPose: () => ({ alpha: 1, beta: 1, radius: 10 }),
      getCitySnapshot: () => city,
      getDialogueState: () => 'idle',
      getInteriorState: () => false,
      getWorldGeneration: () => 4,
      getQuality: () => ({ internalRenderScale: 1, qualityTier: 'balanced' }),
      getWorldMetrics: () => ({
        activeAnimations: 1,
        drawCalls: 17,
        entities: 2,
        frameMs: 9,
        frameTimestampsMs: [100, 109],
        listeners: 4,
        rafs: 1,
        renderFrames: 2,
        targetFps: 30,
        textures: 3,
        timers: 1,
        visibleTriangles: 1200,
        worldUpdateMs: 3,
        worldUpdateTimestampsMs: [101, 110]
      }),
      performLeaderDialogue: vi.fn(),
      setInterior: vi.fn(),
      setQuality: vi.fn(),
      worldAction: vi.fn()
    })

    const snapshot = await request('snapshot', undefined)

    expect(snapshot).toMatchObject({
      activeAnimations: 1,
      activeLeaderAnimations: 0,
      activeWorkerAnimations: 1,
      cameraState: 'worker-focus',
      dialogueState: 'idle',
      drawCalls: 17,
      entities: 2,
      frameMs: 9,
      internalRenderScale: 1,
      population: {
        active: 1,
        lodMix: { far: 0, mid: 1, near: 1 },
        observed: 2,
        source: 'lunar-city-snapshot-v1'
      },
      populationSourceMix: { 'source-a': 1, 'source-b': 1 },
      qualityTier: 'balanced',
      renderFrames: 2,
      sceneMount: { generation: 1 },
      worldGeneration: 4,
      targetFps: 30,
      visibleTriangles: 1200
    })
    expect((snapshot as { sceneMount: { id: string } }).sceneMount.id).toMatch(/^lunar-city-scene:/u)

    route.dispose()
    const disposed = await request('snapshot', undefined)
    expect(disposed).toMatchObject({ lifecycleState: 'disposed', renderFrames: 0 })
    runtime.dispose?.()
    expect(release).toHaveBeenCalledOnce()
  })

  it('executes only bounded scenario actions and returns causal proof counters', async () => {
    let request!: (action: string, payload: unknown) => Promise<unknown>
    let camera = { alpha: 1, beta: 1, focusedEntityKey: undefined as EntityKey | undefined, radius: 10 }
    let inside = false
    let qualityTier: 'balanced' | 'efficient' = 'balanced'

    const worldAction = vi.fn(intent => {
      if (intent.kind === 'orbit') {
        camera = { ...camera, alpha: camera.alpha + intent.deltaAlpha }
      }

      if (intent.kind === 'zoom') {
        camera = { ...camera, radius: camera.radius + intent.delta }
      }

      if (intent.kind === 'focus') {
        camera = { ...camera, focusedEntityKey: intent.entityKey }
      }
    })

    const setQuality = vi.fn(tier => {
      qualityTier = tier
    })

    const performLeaderDialogue = vi.fn(async () => ({ opened: 1, received: 1, sent: 1 }))

    const runtime = createLunarCityPerfRuntime({
      onRequest: callback => {
        request = callback

        return vi.fn()
      }
    })

    runtime.registerRoute!({
      canvas: document.createElement('canvas'),
      getCameraState: () => ({ focusedEntityKey: camera.focusedEntityKey, following: false }),
      getCameraPose: () => ({ alpha: camera.alpha, beta: camera.beta, radius: camera.radius }),
      getCitySnapshot: () => city,
      getDialogueState: () => (performLeaderDialogue.mock.calls.length > 0 ? 'active' : 'idle'),
      getInteriorState: () => inside,
      getQuality: () => ({ internalRenderScale: 1, qualityTier }),
      getWorldGeneration: () => 1,
      getWorldMetrics: () => ({
        activeAnimations: 0,
        drawCalls: 1,
        entities: 2,
        frameMs: 0,
        frameTimestampsMs: [],
        listeners: 0,
        rafs: 0,
        renderFrames: 0,
        targetFps: 15,
        textures: 1,
        timers: 0,
        visibleTriangles: 1,
        worldUpdateMs: 0,
        worldUpdateTimestampsMs: []
      }),
      performLeaderDialogue,
      setInterior: value => {
        inside = value
      },
      setQuality,
      worldAction
    })

    expect(await request('scenario-action', { action: 'quality', payload: { tier: 'efficient' } })).toMatchObject({
      action: 'quality',
      proof: 1
    })
    expect(setQuality).toHaveBeenCalledWith('efficient')
    expect(
      await request('scenario-action', { action: 'orbit', payload: { deltaAlpha: 0.5, deltaBeta: 0.1 } })
    ).toMatchObject({
      action: 'orbit',
      proof: 1
    })
    expect(worldAction).toHaveBeenCalledWith({ kind: 'orbit', deltaAlpha: 0.5, deltaBeta: 0.1 })
    expect(await request('scenario-action', { action: 'zoom', payload: { delta: 2 } })).toMatchObject({ proof: 1 })
    expect(await request('scenario-action', { action: 'focus', payload: { entityKey: worker.key } })).toMatchObject({
      proof: 1
    })
    expect(await request('scenario-action', { action: 'interior', payload: {} })).toMatchObject({ proof: 1 })
    expect(await request('scenario-action', { action: 'leader-dialogue', payload: { leaderId: 'owl' } })).toMatchObject(
      {
        action: 'leader-dialogue',
        proof: 1
      }
    )
    expect(performLeaderDialogue).toHaveBeenCalledWith('owl')
    await expect(
      request('scenario-action', { action: 'orbit', payload: { deltaAlpha: 0, deltaBeta: 0 } })
    ).rejects.toThrow(/nonzero/u)
    await expect(
      request('scenario-action', { action: 'focus', payload: { entityKey: 'source-a::missing' } })
    ).rejects.toThrow(/existing exact worker/u)
    await expect(request('scenario-action', { action: 'send-command', payload: {} })).rejects.toThrow(/unsupported/i)
  })

  it('restores context and removes temporary listeners when context-loss observation times out', async () => {
    let request!: (action: string, payload: unknown) => Promise<unknown>
    const canvas = document.createElement('canvas')
    const loseContext = vi.fn()
    const restoreContext = vi.fn()
    const remove = vi.spyOn(canvas, 'removeEventListener')

    vi.spyOn(canvas, 'getContext').mockReturnValue({ getExtension: () => ({ loseContext, restoreContext }) } as never)

    const runtime = createLunarCityPerfRuntime(
      {
        onRequest: callback => {
          request = callback

          return vi.fn()
        }
      },
      { actionTimeoutMs: 20 }
    )

    runtime.registerRoute!({
      canvas,
      getCameraPose: () => ({ alpha: 1, beta: 1, radius: 10 }),
      getCameraState: () => ({ focusedEntityKey: undefined, following: false }),
      getCitySnapshot: () => city,
      getDialogueState: () => 'idle',
      getInteriorState: () => false,
      getQuality: () => ({ internalRenderScale: 1, qualityTier: 'balanced' }),
      getWorldGeneration: () => 1,
      getWorldMetrics: () => ({
        activeAnimations: 0,
        drawCalls: 0,
        entities: 0,
        frameMs: 0,
        frameTimestampsMs: [],
        listeners: 0,
        rafs: 0,
        renderFrames: 0,
        targetFps: 0,
        textures: 0,
        timers: 0,
        visibleTriangles: 0,
        worldUpdateMs: 0,
        worldUpdateTimestampsMs: []
      }),
      performLeaderDialogue: vi.fn(),
      setInterior: vi.fn(),
      setQuality: vi.fn(),
      worldAction: vi.fn()
    })

    await expect(request('scenario-action', { action: 'context-loss-restore', payload: {} })).rejects.toThrow(
      /context loss event/u
    )
    expect(loseContext).toHaveBeenCalledOnce()
    expect(restoreContext).toHaveBeenCalledOnce()
    expect(remove.mock.calls.map(([type]) => type)).toEqual(
      expect.arrayContaining(['webglcontextlost', 'webglcontextrestored'])
    )
  })
})
