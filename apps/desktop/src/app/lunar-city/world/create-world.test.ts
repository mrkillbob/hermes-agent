import { afterEach, describe, expect, it, vi } from 'vitest'

// @vitest-environment jsdom
import actualManifest from '../../../../public/lunar-city/v2/world-manifest.v2.json'
import { parseWorldManifest } from '../manifest'
import type {
  EntityKey,
  LeaderStateClipMap,
  LunarCityLeaderPickMetadata,
  LunarCitySnapshot,
  LunarCityWorldModules,
  WorldManifestV2
} from '../model'

import { createLunarCityWorld } from './create-world'

interface FakeNode {
  dispose: ReturnType<typeof vi.fn>
  name: string
  metadata?: Record<string, unknown>
  parent?: FakeNode | null
  isPickable?: boolean
  position: { set: ReturnType<typeof vi.fn> }
  rotation: { set: ReturnType<typeof vi.fn> }
  scaling: { set: ReturnType<typeof vi.fn> }
  setEnabled: ReturnType<typeof vi.fn>
  setPivotPoint: ReturnType<typeof vi.fn>
}

interface FakeAnimationGroup {
  advance(): void
  isPlaying: boolean
  loop: boolean
  name: string
  remainingFrames: number
  start(loop?: boolean): void
  stop(): void
}

const leaderStateClips = (leaderId: string): LeaderStateClipMap => ({
  acknowledging: `leader:${leaderId}:acknowledging`,
  idle: `leader:${leaderId}:idle`,
  listening: `leader:${leaderId}:listening`,
  talking: `leader:${leaderId}:talking`,
  thinking: `leader:${leaderId}:thinking`,
  unavailable: `leader:${leaderId}:unavailable`
})

function fakeNode(name: string, metadata?: Record<string, unknown>): FakeNode {
  return {
    dispose: vi.fn(),
    name,
    metadata,
    position: { set: vi.fn() },
    rotation: { set: vi.fn() },
    scaling: { set: vi.fn() },
    setEnabled: vi.fn(),
    setPivotPoint: vi.fn()
  }
}

function fakeRuntime({
  opaqueLeaderNames = false,
  rejectWhenReady = false
}: {
  opaqueLeaderNames?: boolean
  rejectWhenReady?: boolean
} = {}) {
  const engines: FakeEngine[] = []
  const scenes: FakeScene[] = []
  const loadedUrls: string[] = []
  const roots = new Map<string, FakeNode>()
  const lodNodes = new Map<string, FakeNode>()
  const placements = new Map<string, FakeNode>()
  const leaderMeshes = new Map<string, FakeNode>()
  const leaderNodes = new Map<string, FakeNode>()
  const leaderAnimationGroups = new Map<string, FakeAnimationGroup>()
  const lights: FakeDirectionalLight[] = []

  const frozenMeshes: Array<FakeNode & { freezeWorldMatrix: ReturnType<typeof vi.fn>; modelId: string }> = []

  const frozenMaterials: Array<{ freeze: ReturnType<typeof vi.fn>; modelId: string }> = []

  class FakeEngine {
    dispose = vi.fn()
    resize = vi.fn()
    setHardwareScalingLevel = vi.fn()
    stopRenderLoop = vi.fn()
    options: Record<string, unknown>

    constructor(
      public readonly canvas: HTMLCanvasElement,
      public readonly antialias: boolean,
      options: Record<string, unknown>
    ) {
      this.options = options
      engines.push(this)
    }
  }

  class FakeScene {
    activeCamera: unknown
    ambientColor: unknown
    materials: Array<{ freeze: ReturnType<typeof vi.fn>; modelId: string }> = []
    dispose = vi.fn()
    pick = vi.fn()
    render = vi.fn(() => {
      for (const group of leaderAnimationGroups.values()) {
        group.advance()
      }
    })
    whenReadyAsync = vi.fn(async () => {
      if (rejectWhenReady) {
        throw new Error('scene readiness rejected')
      }
    })

    constructor(public readonly engine: FakeEngine) {
      scenes.push(this)
    }
  }

  class FakeVector3 {
    constructor(
      public readonly x: number,
      public readonly y: number,
      public readonly z: number
    ) {}
  }

  class FakeColor3 extends FakeVector3 {}

  class FakeArcRotateCamera {
    metadata?: Record<string, unknown>

    constructor(
      public readonly name: string,
      public readonly alpha: number,
      public readonly beta: number,
      public readonly radius: number,
      public readonly target: FakeVector3,
      public readonly scene: FakeScene
    ) {}
  }

  class FakeDirectionalLight {
    intensity = 1
    shadowEnabled = true

    constructor(
      public readonly name: string,
      public readonly direction: FakeVector3,
      public readonly scene: FakeScene
    ) {
      lights.push(this)
    }
  }

  class FakeTransformNode implements FakeNode {
    dispose = vi.fn()
    metadata?: Record<string, unknown>
    parent?: FakeNode | null
    position = { set: vi.fn() }
    rotation = { set: vi.fn() }
    scaling = { set: vi.fn() }
    setEnabled = vi.fn()
    setPivotPoint = vi.fn()

    constructor(
      public readonly name: string,
      public readonly scene: FakeScene
    ) {
      placements.set(name.replace('lunar-city:placement:', ''), this)
    }
  }

  const ImportMeshAsync = vi.fn(async (url: string, scene: FakeScene) => {
    loadedUrls.push(url)
    const filename = new URL(url).pathname.split('/').pop()!
    const id = filename.replace(/\.glb$/, '')
    const modelId = id === 'research-lab' || id === 'review-office' ? id : id

    if (modelId === 'navigation') {
      return {
        animationGroups: [],
        meshes: [
          {
            dispose: vi.fn(),
            getIndices: () => [0, 1, 2, 1, 3, 2],
            getVerticesData: () => [0, 0, 0, 4, 0, 0, 0, 0, 4, 4, 0, 4],
            name: 'navigation:surface',
            setEnabled: vi.fn()
          }
        ],
        transformNodes: []
      }
    }

    const root = fakeNode(`${modelId}:root`)
    const near = fakeNode(`${modelId}:lod:near`)
    const far = fakeNode(`${modelId}:lod:far`)
    roots.set(modelId, root)
    lodNodes.set(near.name, near)
    lodNodes.set(far.name, far)

    const transformNodes: FakeNode[] = [root, near, far]

    if (modelId === 'garden') {
      const plants = fakeNode('garden:plants', { gltf: { extras: { semantic: 'garden:plants' } } })
      transformNodes.push(plants)
      lodNodes.set(plants.name, plants)
    }

    const leaderPickMeshes: Array<FakeNode & { freezeWorldMatrix: ReturnType<typeof vi.fn>; modelId: string }> = []

    if (modelId === 'leaders') {
      for (const [index, leaderId] of ['owl', 'fox', 'badger', 'otter', 'bird', 'stag'].entries()) {
        const leaderNode = fakeNode(opaqueLeaderNames ? `identity-node-${index}` : `leader:${leaderId}`, {
          gltf: {
            extras: {
              leaderId,
              stateClips: leaderStateClips(leaderId)
            }
          }
        })

        const leaderChild = fakeNode(`render-node-${index}`)

        const leaderMesh = {
          ...fakeNode(`pick-surface-${index}`),
          freezeWorldMatrix: vi.fn(),
          modelId,
          parent: leaderChild
        }

        leaderChild.parent = leaderNode
        leaderMeshes.set(leaderId, leaderMesh)
        leaderNodes.set(leaderId, leaderNode)
        leaderPickMeshes.push(leaderMesh)
        transformNodes.push(leaderNode, leaderChild)

        for (const clip of Object.values(leaderStateClips(leaderId))) {
          const group: FakeAnimationGroup = {
            advance: vi.fn(() => {
              if (group.isPlaying && !group.loop) {
                group.remainingFrames -= 1
                group.isPlaying = group.remainingFrames > 0
              }
            }),
            isPlaying: false,
            loop: false,
            name: clip,
            remainingFrames: 0,
            start: vi.fn((loop = false) => {
              group.isPlaying = true
              group.loop = loop
              group.remainingFrames = loop ? Number.POSITIVE_INFINITY : 2
            }),
            stop: vi.fn(() => {
              group.isPlaying = false
              group.remainingFrames = 0
            })
          }

          leaderAnimationGroups.set(clip, group)
        }
      }
    }

    const mesh = { ...fakeNode('__root__'), freezeWorldMatrix: vi.fn(), modelId }
    const material = { freeze: vi.fn(), modelId }
    frozenMeshes.push(mesh)
    frozenMeshes.push(...leaderPickMeshes)
    frozenMaterials.push(material)
    scene.materials.push(material)

    return {
      animationGroups: modelId === 'leaders' ? [...leaderAnimationGroups.values()] : [],
      meshes: [mesh, ...leaderPickMeshes],
      particleSystems: [],
      skeletons: [],
      transformNodes,
      geometries: [],
      lights: [],
      spriteManagers: []
    }
  })

  const modules = {
    ArcRotateCamera: FakeArcRotateCamera,
    Color3: FakeColor3,
    DirectionalLight: FakeDirectionalLight,
    Engine: FakeEngine,
    ImportMeshAsync,
    Scene: FakeScene,
    TransformNode: FakeTransformNode,
    Vector3: FakeVector3
  } as unknown as LunarCityWorldModules

  return {
    engines,
    frozenMaterials,
    frozenMeshes,
    ImportMeshAsync,
    loadedUrls,
    lodNodes,
    leaderMeshes,
    leaderNodes,
    leaderAnimationGroups,
    lights,
    modules,
    placements,
    roots,
    scenes
  }
}

const manifest = parseWorldManifest(structuredClone(actualManifest))

function workerSnapshot(authority: 'authoritative' | 'stale' = 'authoritative'): LunarCitySnapshot {
  const key = 'session:connection=local:profile=worker:session=s1' as EntityKey

  return {
    entities: new Map([
      [
        key,
        {
          animation: 'walk',
          authority,
          destination: 'bus',
          identity: { kind: 'session', connectionId: 'local', profile: 'worker', sessionId: 's1' },
          key,
          observedAt: 1,
          position: { x: 0, y: 0, z: -1 }
        }
      ]
    ]),
    observedAt: 1,
    revision: 1,
    sources: []
  }
}

function kanbanSnapshot(position: { x: number; y: number; z: number }): LunarCitySnapshot {
  const key = 'kanban:connection=source-a:profile=default:board=main:task=task-1:run=run-1:worker=worker-1' as EntityKey

  return {
    entities: new Map([
      [
        key,
        {
          animation: 'walk',
          authority: 'authoritative',
          destination: 'bus',
          identity: {
            kind: 'kanban',
            board: 'main',
            connectionId: 'source-a',
            profile: 'default',
            runId: 'run-1',
            taskId: 'task-1',
            workerId: 'worker-1'
          },
          key,
          observedAt: 1,
          position,
          projectId: 'project-alpha'
        }
      ]
    ]),
    observedAt: 1,
    revision: 1,
    sources: []
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('createLunarCityWorld', () => {
  it('creates one low-power engine and one static scene for a canvas', async () => {
    const runtime = fakeRuntime()
    const canvas = document.createElement('canvas')

    const handle = await createLunarCityWorld(canvas, manifest, vi.fn(), runtime.modules)

    expect(runtime.engines).toHaveLength(1)
    expect(runtime.scenes).toHaveLength(1)
    expect(runtime.engines[0]).toMatchObject({
      antialias: true,
      canvas,
      options: {
        powerPreference: 'low-power',
        preserveDrawingBuffer: false,
        stencil: false
      }
    })
    expect(runtime.scenes[0]?.whenReadyAsync).toHaveBeenCalledOnce()
    expect(runtime.scenes[0]?.render).toHaveBeenCalledOnce()
    handle.destroy()
  })

  it('loads and places every declared GLB from the v2 manifest without requesting the approved JPG', async () => {
    const runtime = fakeRuntime()

    const handle = await createLunarCityWorld(
      document.createElement('canvas'),
      manifest,
      vi.fn(),
      runtime.modules,
      'https://desktop.test/lunar-city/v2/world-manifest.v2.json'
    )

    expect(runtime.loadedUrls).toHaveLength(manifest.models.length)
    expect(runtime.loadedUrls.every(url => url.startsWith('https://desktop.test/lunar-city/v2/models/'))).toBe(true)
    expect(runtime.loadedUrls.join('\n')).not.toMatch(/moon-settlement-approved\.jpg/i)
    expect(runtime.placements.get('library')?.position.set).toHaveBeenCalledWith(-28, 4, -18)
    expect(runtime.placements.get('library')?.rotation.set).toHaveBeenCalledWith(0, 0.28, 0)
    expect(runtime.placements.get('library')?.scaling.set).toHaveBeenCalledWith(1, 1, 1)
    expect(runtime.placements.get('library')?.setPivotPoint).toHaveBeenCalledWith(
      expect.objectContaining({ x: 0, y: 0, z: 0 })
    )
    expect(runtime.frozenMeshes.find(mesh => mesh.modelId === 'library')?.parent).toBe(
      runtime.placements.get('library')
    )
    expect(runtime.roots.get('library')?.position.set).not.toHaveBeenCalled()
    expect(runtime.lodNodes.get('library:lod:far')?.metadata).toMatchObject({
      lunarCity: { kind: 'lod', modelId: 'library', distance: 48 }
    })
    // Efficient is the default tier, so its aggressive LOD policy may choose
    // far detail; the safety contract is that the two subtrees never coexist.
    expect(runtime.lodNodes.get('library:lod:near')?.setEnabled).toHaveBeenLastCalledWith(false)
    expect(runtime.lodNodes.get('library:lod:far')?.setEnabled).toHaveBeenLastCalledWith(true)
    expect(runtime.frozenMeshes.find(mesh => mesh.modelId === 'terrain')?.freezeWorldMatrix).toHaveBeenCalledOnce()
    expect(runtime.frozenMaterials.find(material => material.modelId === 'terrain')?.freeze).toHaveBeenCalledOnce()
    expect(runtime.frozenMeshes.find(mesh => mesh.modelId === 'leaders')?.freezeWorldMatrix).not.toHaveBeenCalled()
    handle.destroy()
  })

  it('applies quality tiers through hardware scaling without recreating the scene', async () => {
    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)

    handle.setQuality('balanced')

    expect(runtime.engines[0]?.setHardwareScalingLevel).toHaveBeenCalledWith(1 / 0.85)
    expect(runtime.scenes).toHaveLength(1)
    handle.destroy()
  })

  it('applies dynamic-shadow and decorative-node settings to the loaded scene without rebuilding it', async () => {
    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)

    handle.setQuality('detailed')
    expect(runtime.lights[0]?.shadowEnabled).toBe(true)
    expect(runtime.lodNodes.get('garden:plants')?.setEnabled).toHaveBeenLastCalledWith(true)

    handle.setQuality('efficient')
    expect(runtime.lights[0]?.shadowEnabled).toBe(false)
    expect(runtime.lodNodes.get('garden:plants')?.setEnabled).toHaveBeenLastCalledWith(false)
    expect(runtime.scenes).toHaveLength(1)
    handle.destroy()
  })

  it('halts the unified frame authority when the WebGL context is lost', async () => {
    const runtime = fakeRuntime()
    const canvas = document.createElement('canvas')
    const handle = await createLunarCityWorld(canvas, manifest, vi.fn(), runtime.modules)

    canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }))

    expect(runtime.engines[0]?.stopRenderLoop).toHaveBeenCalledOnce()
    handle.destroy()
  })

  it('registers live exact-key worker anchors and rejects stale follow without recreating the city', async () => {
    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    const snapshot = workerSnapshot()
    const key = [...snapshot.entities.keys()][0]!

    handle.applySnapshot(snapshot)
    handle.dispatchCamera({ kind: 'focus', entityKey: key, follow: true })
    expect(handle.getCameraState()).toEqual({ focusedEntityKey: key, following: true })

    handle.applySnapshot(workerSnapshot('stale'))
    handle.dispatchCamera({ kind: 'focus', entityKey: key, follow: true })
    expect(handle.getCameraState()).toEqual({ focusedEntityKey: undefined, following: false })
    expect(runtime.scenes).toHaveLength(1)
    handle.destroy()
  })

  it('retains each Kanban project compound at its declared slot without rebuilding the world', async () => {
    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    const slot = manifest.projectSlots[0]!

    handle.applySnapshot(kanbanSnapshot(slot.position))

    const compound = runtime.placements.get(
      'lunar-city:compound:compound:connection:string:8:source-a:project:string:13:project-alpha'
    )

    expect(compound?.position.set).toHaveBeenCalledWith(slot.position.x, slot.position.y, slot.position.z)
    expect(compound?.metadata).toEqual({
      lunarCity: {
        connectionId: 'source-a',
        key: 'compound:connection:string:8:source-a:project:string:13:project-alpha',
        kind: 'project-compound',
        projectId: 'project-alpha',
        selectable: false
      }
    })
    expect(runtime.scenes).toHaveLength(1)

    handle.applySnapshot({ entities: new Map(), observedAt: 2, revision: 2, sources: [] })

    expect(compound?.dispose).toHaveBeenCalledOnce()
    expect(runtime.scenes).toHaveLength(1)
    handle.destroy()
  })

  it('retains identity-qualified leader state clips from GLB metadata', async () => {
    const runtime = fakeRuntime()

    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)

    expect(handle.leaderStateClips.get('fox')).toEqual(leaderStateClips('fox'))
    expect(handle.leaderStateClips.get('badger')?.talking).toBe('leader:badger:talking')
    expect(handle.leaderStateClips.get('fox')?.talking).not.toBe('talking')
    handle.destroy()
  })

  it('plays only the selected GLB-declared leader state clip without rebuilding the world', async () => {
    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    const foxThinking = runtime.leaderAnimationGroups.get('leader:fox:thinking')!
    const foxTalking = runtime.leaderAnimationGroups.get('leader:fox:talking')!
    const owlThinking = runtime.leaderAnimationGroups.get('leader:owl:thinking')!

    handle.setLeaderAnimation('fox', 'thinking')
    handle.setLeaderAnimation('fox', 'talking')

    expect(foxThinking.start).toHaveBeenCalledWith(true)
    expect(foxThinking.stop).toHaveBeenCalledOnce()
    expect(foxTalking.start).toHaveBeenCalledWith(true)
    expect(owlThinking.start).not.toHaveBeenCalled()
    expect(runtime.scenes).toHaveLength(1)
    handle.destroy()
  })

  it.each(['listening', 'thinking', 'talking'] as const)(
    'keeps the unified scheduler alive while an actual %s leader group is playing and parks after idle',
    async state => {
      vi.useFakeTimers()
      let now = 0
      const requestedFrames: FrameRequestCallback[] = []
      vi.spyOn(performance, 'now').mockImplementation(() => now)
      vi.stubGlobal(
        'requestAnimationFrame',
        vi.fn((callback: FrameRequestCallback) => {
          requestedFrames.push(callback)

          return requestedFrames.length
        })
      )
      vi.stubGlobal('cancelAnimationFrame', vi.fn())

      const runtime = fakeRuntime()
      const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
      const scene = runtime.scenes[0]!
      const active = runtime.leaderAnimationGroups.get(`leader:fox:${state}`)!
      const idle = runtime.leaderAnimationGroups.get('leader:fox:idle')!

      // Drain the one inert callback scheduled by initial world construction.
      requestedFrames.shift()?.(now)
      scene.render.mockClear()

      handle.setLeaderAnimation('fox', state)
      now = 100
      requestedFrames.shift()?.(now)

      for (const next of [167, 234]) {
        now = next
        await vi.advanceTimersByTimeAsync(67)
        requestedFrames.shift()?.(now)
      }

      expect(active.isPlaying).toBe(true)
      expect(scene.render).toHaveBeenCalledTimes(3)

      handle.setLeaderAnimation('fox', 'idle')
      expect(active.stop).toHaveBeenCalledOnce()
      expect(idle.start).not.toHaveBeenCalled()

      now = 301
      await vi.advanceTimersByTimeAsync(67)
      requestedFrames.shift()?.(now)
      const rendersAfterIdle = scene.render.mock.calls.length

      now = 1_000
      await vi.advanceTimersByTimeAsync(1_000)

      while (requestedFrames.length > 0) {
        requestedFrames.shift()?.(now)
      }

      expect(scene.render).toHaveBeenCalledTimes(rendersAfterIdle)
      handle.destroy()
    }
  )

  it('parks the unified scheduler when a finite leader group reports completion', async () => {
    vi.useFakeTimers()
    let now = 0
    const requestedFrames: FrameRequestCallback[] = []
    vi.spyOn(performance, 'now').mockImplementation(() => now)
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => {
        requestedFrames.push(callback)

        return requestedFrames.length
      })
    )

    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    const scene = runtime.scenes[0]!
    const acknowledging = runtime.leaderAnimationGroups.get('leader:fox:acknowledging')!

    requestedFrames.shift()?.(now)
    scene.render.mockClear()
    handle.setLeaderAnimation('fox', 'acknowledging')
    expect(acknowledging.start).toHaveBeenCalledWith(false)

    now = 100
    requestedFrames.shift()?.(now)
    expect(acknowledging.isPlaying).toBe(true)

    now = 167
    await vi.advanceTimersByTimeAsync(67)
    requestedFrames.shift()?.(now)
    expect(acknowledging.isPlaying).toBe(false)
    const rendersAfterCompletion = scene.render.mock.calls.length

    now = 1_000
    await vi.advanceTimersByTimeAsync(1_000)

    while (requestedFrames.length > 0) {
      requestedFrames.shift()?.(now)
    }

    expect(scene.render).toHaveBeenCalledTimes(rendersAfterCompletion)
    handle.destroy()
  })

  it('cancels leader animation scheduler work on route destruction without a late frame', async () => {
    vi.useFakeTimers()
    let now = 0
    const requestedFrames: FrameRequestCallback[] = []
    vi.spyOn(performance, 'now').mockImplementation(() => now)
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => {
        requestedFrames.push(callback)

        return requestedFrames.length
      })
    )
    vi.stubGlobal('cancelAnimationFrame', vi.fn())

    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    const scene = runtime.scenes[0]!
    const thinking = runtime.leaderAnimationGroups.get('leader:fox:thinking')!

    requestedFrames.shift()?.(now)
    scene.render.mockClear()
    handle.setLeaderAnimation('fox', 'thinking')
    handle.destroy()

    now = 100

    while (requestedFrames.length > 0) {
      requestedFrames.shift()?.(now)
    }

    await vi.advanceTimersByTimeAsync(1_000)

    expect(thinking.stop).toHaveBeenCalledOnce()
    expect(scene.render).not.toHaveBeenCalled()
  })

  it('applies camera intents on the existing world scene and restores the exact approved overview', async () => {
    const runtime = fakeRuntime()
    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)

    const camera = runtime.scenes[0]?.activeCamera as {
      alpha: number
      beta: number
      radius: number
      target: { x: number; y: number; z: number }
    }

    handle.dispatchCamera({ kind: 'orbit', deltaAlpha: 99, deltaBeta: 99 })
    handle.dispatchCamera({ kind: 'zoom', delta: -999 })

    expect(camera.beta).toBe(manifest.camera.overview.maxBeta)
    expect(camera.radius).toBe(manifest.camera.overview.minRadius)
    expect(runtime.scenes).toHaveLength(1)

    handle.dispatchCamera({ kind: 'return-to-city' })

    expect(camera).toMatchObject({
      alpha: manifest.camera.overview.alpha,
      beta: manifest.camera.overview.beta,
      radius: manifest.camera.overview.radius,
      target: manifest.camera.overview.target
    })
    expect(runtime.scenes).toHaveLength(1)
    handle.destroy()
  })

  it('uses only typed mesh metadata for picks and clears focus when empty terrain is selected', async () => {
    const runtime = fakeRuntime({ opaqueLeaderNames: true })
    const emit = vi.fn()
    const canvas = document.createElement('canvas')
    canvas.width = 600
    canvas.height = 300
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(new DOMRect(100, 50, 200, 100))
    const handle = await createLunarCityWorld(canvas, manifest, emit, runtime.modules)
    const foxMesh = runtime.leaderMeshes.get('fox')!

    runtime.scenes[0]?.pick.mockReturnValueOnce({ pickedMesh: foxMesh }).mockReturnValueOnce(undefined)
    canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 150, clientY: 75, pointerId: 1 }))
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 150, clientY: 75, pointerId: 1 }))

    expect(foxMesh.name).not.toContain('fox')
    expect(handle.getCameraState()).toEqual({ focusedEntityKey: 'lunar-city:leader:fox', following: true })
    expect(emit).toHaveBeenCalledWith({ kind: 'select-focus', entityKey: 'lunar-city:leader:fox' })
    expect(runtime.scenes[0]?.pick).toHaveBeenCalledWith(50, 25)

    canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 175, clientY: 100, pointerId: 2 }))
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 175, clientY: 100, pointerId: 2 }))

    expect(handle.getCameraState()).toEqual({ focusedEntityKey: undefined, following: false })
    expect(emit).toHaveBeenCalledWith({ kind: 'clear-selection' })
    expect(runtime.scenes[0]?.pick).toHaveBeenLastCalledWith(75, 50)
    handle.destroy()
  })

  it('tags every selectable leader node and mesh with structured identity metadata', async () => {
    const runtime = fakeRuntime({ opaqueLeaderNames: true })

    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)

    for (const leaderId of ['owl', 'fox', 'badger', 'otter', 'bird', 'stag']) {
      const leaderNode = runtime.leaderNodes.get(leaderId)!
      const pickedMesh = runtime.leaderMeshes.get(leaderId)!
      const pickMetadata = (pickedMesh.metadata as { lunarCity?: LunarCityLeaderPickMetadata }).lunarCity

      expect(leaderNode.name).not.toContain(leaderId)
      expect(pickedMesh.name).not.toContain(leaderId)
      expect(leaderNode.metadata?.lunarCity).toMatchObject({
        kind: 'leader',
        leaderId,
        modelId: 'leaders',
        selectable: true,
        stateClips: leaderStateClips(leaderId)
      })
      expect(pickedMesh.isPickable).toBe(true)
      expect(pickMetadata).toMatchObject({
        kind: 'leader',
        leaderId,
        modelId: 'leaders',
        selectable: true,
        stateClips: leaderStateClips(leaderId)
      })
      expect(pickMetadata?.leaderId).toBe(leaderId)
    }

    const sharedLeaderSurface = runtime.frozenMeshes.find(
      mesh => mesh.modelId === 'leaders' && mesh.name === '__root__'
    )!

    expect(sharedLeaderSurface.isPickable).toBe(false)
    expect(sharedLeaderSurface.metadata).toMatchObject({
      lunarCity: { kind: 'leader-shared-surface', modelId: 'leaders', selectable: false }
    })
    handle.destroy()
  })

  it('disposes the scene, engine, resize listener, and observer exactly once', async () => {
    const runtime = fakeRuntime()
    let observerCallback: ResizeObserverCallback | undefined
    const observe = vi.fn()
    const disconnect = vi.fn()
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(callback: ResizeObserverCallback) {
          observerCallback = callback
        }
        observe = observe
        disconnect = disconnect
      }
    )

    const handle = await createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    window.dispatchEvent(new Event('resize'))
    observerCallback?.([], {} as ResizeObserver)
    const resizeCallsBeforeDestroy = runtime.engines[0]!.resize.mock.calls.length

    handle.destroy()
    handle.destroy()
    window.dispatchEvent(new Event('resize'))
    observerCallback?.([], {} as ResizeObserver)

    expect(runtime.scenes[0]?.dispose).toHaveBeenCalledOnce()
    expect(runtime.engines[0]?.dispose).toHaveBeenCalledOnce()
    expect(observe).toHaveBeenCalledOnce()
    expect(disconnect).toHaveBeenCalledOnce()
    expect(runtime.engines[0]?.resize).toHaveBeenCalledTimes(resizeCallsBeforeDestroy)
  })

  it('disposes partial resources when scene construction rejects', async () => {
    const runtime = fakeRuntime()
    runtime.ImportMeshAsync.mockRejectedValueOnce(new Error('broken GLB'))

    await expect(
      createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    ).rejects.toThrow(/broken GLB/)

    expect(runtime.scenes[0]?.dispose).toHaveBeenCalledOnce()
    expect(runtime.engines[0]?.dispose).toHaveBeenCalledOnce()
  })

  it('releases world-stage scheduler, navigation, registry, and occlusion resources if readiness rejects after Recast initializes', async () => {
    const runtime = fakeRuntime({ rejectWhenReady: true })
    const configurationDestroy = vi.fn()
    const navMeshBuild = vi.fn()
    const navMeshCreate = vi.fn()
    const navMeshDestroy = vi.fn()
    const documentAdd = vi.spyOn(document, 'addEventListener')
    const documentRemove = vi.spyOn(document, 'removeEventListener')

    class FakeConfiguration {
      destroy = configurationDestroy
      set_bmax = vi.fn()
      set_bmin = vi.fn()
    }

    class FakeNavMesh {
      build = navMeshBuild
      computePath = vi.fn()
      destroy = navMeshDestroy

      constructor() {
        navMeshCreate()
      }
    }

    class FakeVector3 {
      constructor(
        public readonly x: number,
        public readonly y: number,
        public readonly z: number
      ) {}
    }

    const createRecastNavigation = vi.fn(
      async () =>
        ({
          NavMesh: FakeNavMesh,
          Vec3: FakeVector3,
          rcConfig: FakeConfiguration
        }) as unknown as Awaited<ReturnType<NonNullable<LunarCityWorldModules['createRecastNavigation']>>>
    )

    runtime.modules.createRecastNavigation = createRecastNavigation

    await expect(
      createLunarCityWorld(document.createElement('canvas'), manifest, vi.fn(), runtime.modules)
    ).rejects.toThrow(/scene readiness rejected/)

    const navigationImports = runtime.ImportMeshAsync.mock.calls.filter(([url]) =>
      url.endsWith('/models/navigation.glb')
    )

    const visibilityAdds = documentAdd.mock.calls.filter(([event]) => event === 'visibilitychange')
    const visibilityRemovals = documentRemove.mock.calls.filter(([event]) => event === 'visibilitychange')
    const visibilityListener = visibilityAdds[0]?.[1]

    expect(navigationImports).toHaveLength(1)
    expect(createRecastNavigation).toHaveBeenCalledOnce()
    expect(navMeshCreate).toHaveBeenCalledOnce()
    expect(navMeshBuild).toHaveBeenCalledOnce()
    expect(runtime.scenes[0]?.whenReadyAsync).toHaveBeenCalledOnce()
    expect(createRecastNavigation.mock.invocationCallOrder[0]).toBeLessThan(
      runtime.scenes[0]!.whenReadyAsync.mock.invocationCallOrder[0]!
    )
    expect(navMeshBuild.mock.invocationCallOrder[0]).toBeLessThan(
      runtime.scenes[0]!.whenReadyAsync.mock.invocationCallOrder[0]!
    )
    expect(configurationDestroy).toHaveBeenCalledOnce()
    expect(navMeshDestroy).toHaveBeenCalledOnce()
    expect(visibilityAdds).toHaveLength(1)
    expect(visibilityListener).toEqual(expect.any(Function))
    expect(visibilityRemovals).toEqual([['visibilitychange', visibilityListener]])
    expect(runtime.scenes[0]?.dispose).toHaveBeenCalledOnce()
    expect(runtime.scenes[0]?.render).not.toHaveBeenCalled()
    document.dispatchEvent(new Event('visibilitychange'))
    expect(runtime.scenes[0]?.render).not.toHaveBeenCalled()
  })

  it('rejects a forged runtime manifest even when the caller bypasses the parser', async () => {
    const runtime = fakeRuntime()

    const forged = structuredClone(manifest) as WorldManifestV2

    ;(forged.models[0] as { uri: string }).uri = '../moon-settlement-approved.jpg'

    await expect(
      createLunarCityWorld(document.createElement('canvas'), forged, vi.fn(), runtime.modules)
    ).rejects.toThrow(/approved source cannot be a runtime asset/)

    expect(runtime.loadedUrls).toHaveLength(0)
  })
})
