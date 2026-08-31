import { afterEach, describe, expect, it, vi } from 'vitest'

// @vitest-environment jsdom
import actualManifest from '../../../../public/lunar-city/v2/world-manifest.v2.json'
import { parseWorldManifest } from '../manifest'
import type { LeaderStateClipMap, LunarCityWorldModules, WorldManifestV2 } from '../model'

import { createLunarCityWorld } from './create-world'

interface FakeNode {
  name: string
  metadata?: Record<string, unknown>
  parent?: unknown
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

function fakeRuntime() {
  const engines: FakeEngine[] = []
  const scenes: FakeScene[] = []
  const loadedUrls: string[] = []
  const roots = new Map<string, FakeNode>()
  const lodNodes = new Map<string, FakeNode>()
  const placements = new Map<string, FakeNode>()

  const frozenMeshes: Array<{
    freezeWorldMatrix: ReturnType<typeof vi.fn>
    metadata?: unknown
    modelId: string
    parent?: unknown
  }> = []

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
    parent?: unknown
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

    if (modelId === 'leaders') {
      for (const leaderId of ['owl', 'fox', 'badger', 'otter', 'bird', 'stag']) {
        transformNodes.push(
          fakeNode(`leader:${leaderId}`, {
            gltf: {
              extras: {
                leaderId,
                stateClips: leaderStateClips(leaderId)
              }
            }
          })
        )
      }
    }

    const mesh = { ...fakeNode('__root__'), freezeWorldMatrix: vi.fn(), modelId }
    const material = { freeze: vi.fn(), modelId }
    frozenMeshes.push(mesh)
    frozenMaterials.push(material)
    scene.materials.push(material)

    return {
      animationGroups: [],
      meshes: [mesh],
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
