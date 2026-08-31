import { afterEach, describe, expect, it, vi } from 'vitest'

// @vitest-environment jsdom
import actualManifest from '../../../../public/lunar-city/v2/world-manifest.v2.json'
import { parseWorldManifest } from '../manifest'
import type { LeaderStateClipMap, LunarCityLeaderPickMetadata, LunarCityWorldModules, WorldManifestV2 } from '../model'

import { createLunarCityWorld } from './create-world'

interface FakeNode {
  name: string
  metadata?: Record<string, unknown>
  parent?: FakeNode | null
  isPickable?: boolean
  position: { set: ReturnType<typeof vi.fn> }
  rotation: { set: ReturnType<typeof vi.fn> }
  scaling: { set: ReturnType<typeof vi.fn> }
  setPivotPoint: ReturnType<typeof vi.fn>
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
    name,
    metadata,
    position: { set: vi.fn() },
    rotation: { set: vi.fn() },
    scaling: { set: vi.fn() },
    setPivotPoint: vi.fn()
  }
}

function fakeRuntime({ opaqueLeaderNames = false }: { opaqueLeaderNames?: boolean } = {}) {
  const engines: FakeEngine[] = []
  const scenes: FakeScene[] = []
  const loadedUrls: string[] = []
  const roots = new Map<string, FakeNode>()
  const lodNodes = new Map<string, FakeNode>()
  const placements = new Map<string, FakeNode>()
  const leaderMeshes = new Map<string, FakeNode>()
  const leaderNodes = new Map<string, FakeNode>()

  const frozenMeshes: Array<FakeNode & { freezeWorldMatrix: ReturnType<typeof vi.fn>; modelId: string }> = []

  const frozenMaterials: Array<{ freeze: ReturnType<typeof vi.fn>; modelId: string }> = []

  class FakeEngine {
    dispose = vi.fn()
    resize = vi.fn()
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
    render = vi.fn()
    whenReadyAsync = vi.fn(async () => {})

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

    constructor(
      public readonly name: string,
      public readonly direction: FakeVector3,
      public readonly scene: FakeScene
    ) {}
  }

  class FakeTransformNode implements FakeNode {
    metadata?: Record<string, unknown>
    parent?: FakeNode | null
    position = { set: vi.fn() }
    rotation = { set: vi.fn() }
    scaling = { set: vi.fn() }
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
    const root = fakeNode(`${modelId}:root`)
    const near = fakeNode(`${modelId}:lod:near`)
    const far = fakeNode(`${modelId}:lod:far`)
    roots.set(modelId, root)
    lodNodes.set(near.name, near)
    lodNodes.set(far.name, far)

    const transformNodes: FakeNode[] = [root, near, far]

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
      }
    }

    const mesh = { ...fakeNode('__root__'), freezeWorldMatrix: vi.fn(), modelId }
    const material = { freeze: vi.fn(), modelId }
    frozenMeshes.push(mesh)
    frozenMeshes.push(...leaderPickMeshes)
    frozenMaterials.push(material)
    scene.materials.push(material)

    return {
      animationGroups: [],
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
    modules,
    placements,
    roots,
    scenes
  }
}

const manifest = parseWorldManifest(structuredClone(actualManifest))

afterEach(() => {
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
    expect(runtime.frozenMeshes.find(mesh => mesh.modelId === 'terrain')?.freezeWorldMatrix).toHaveBeenCalledOnce()
    expect(runtime.frozenMaterials.find(material => material.modelId === 'terrain')?.freeze).toHaveBeenCalledOnce()
    expect(runtime.frozenMeshes.find(mesh => mesh.modelId === 'leaders')?.freezeWorldMatrix).not.toHaveBeenCalled()
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
    const handle = await createLunarCityWorld(canvas, manifest, emit, runtime.modules)
    const foxMesh = runtime.leaderMeshes.get('fox')!

    runtime.scenes[0]?.pick.mockReturnValueOnce({ pickedMesh: foxMesh }).mockReturnValueOnce(undefined)
    canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 10, clientY: 10, pointerId: 1 }))
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 10, clientY: 10, pointerId: 1 }))

    expect(foxMesh.name).not.toContain('fox')
    expect(handle.getCameraState()).toEqual({ focusedEntityKey: 'lunar-city:leader:fox', following: true })
    expect(emit).toHaveBeenCalledWith({ kind: 'select-focus', entityKey: 'lunar-city:leader:fox' })

    canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 14, clientY: 14, pointerId: 2 }))
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 14, clientY: 14, pointerId: 2 }))

    expect(handle.getCameraState()).toEqual({ focusedEntityKey: undefined, following: false })
    expect(emit).toHaveBeenCalledWith({ kind: 'clear-selection' })
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
