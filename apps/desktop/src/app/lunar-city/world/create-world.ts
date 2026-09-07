import { assertWorldManifestRuntimeAssets } from '../manifest'
import type { LunarCityIntent, LunarCityWorldHandle, LunarCityWorldModules, WorldManifestV2 } from '../model'

import { bindCameraInput, type CameraInputRelease } from './camera-controller'
import { createWorldScene } from './world-scene'

const DEFAULT_MANIFEST_URL = './lunar-city/v2/world-manifest.v2.json'

export async function loadBabylonModules(): Promise<LunarCityWorldModules> {
  const [
    engine,
    scene,
    sceneLoader,
    camera,
    vector,
    color,
    directionalLight,
    hemisphericLight,
    glowLayer,
    shadowGenerator,
    transformNode,
    instrumentation,
    ,
    recast
  ] = await Promise.all([
    import('@babylonjs/core/Engines/engine'),
    import('@babylonjs/core/scene'),
    import('@babylonjs/core/Loading/sceneLoader'),
    import('@babylonjs/core/Cameras/arcRotateCamera'),
    import('@babylonjs/core/Maths/math.vector'),
    import('@babylonjs/core/Maths/math.color'),
    import('@babylonjs/core/Lights/directionalLight'),
    import('@babylonjs/core/Lights/hemisphericLight'),
    import('@babylonjs/core/Layers/glowLayer'),
    import('@babylonjs/core/Lights/Shadows/shadowGenerator'),
    import('@babylonjs/core/Meshes/transformNode'),
    import('@babylonjs/core/Instrumentation/sceneInstrumentation'),
    import('@babylonjs/loaders/glTF'),
    import('recast-detour')
  ])

  return {
    ArcRotateCamera: camera.ArcRotateCamera,
    Color3: color.Color3,
    Color4: color.Color4,
    DirectionalLight: directionalLight.DirectionalLight,
    HemisphericLight: hemisphericLight.HemisphericLight,
    GlowLayer: glowLayer.GlowLayer,
    ShadowGenerator: shadowGenerator.ShadowGenerator,
    Engine: engine.Engine,
    ImportMeshAsync: sceneLoader.ImportMeshAsync,
    Scene: scene.Scene,
    SceneInstrumentation: instrumentation.SceneInstrumentation,
    TransformNode: transformNode.TransformNode,
    Vector3: vector.Vector3,
    createRecastNavigation: recast.default
  } as unknown as LunarCityWorldModules
}

function absoluteManifestUrl(manifestUrl: string): URL {
  const documentBase = typeof document === 'undefined' ? 'http://localhost/' : document.baseURI

  try {
    return new URL(manifestUrl, documentBase)
  } catch {
    return new URL(manifestUrl, 'http://localhost/')
  }
}

export async function createLunarCityWorld(
  canvas: HTMLCanvasElement,
  manifest: WorldManifestV2,
  emit: (intent: LunarCityIntent) => void,
  modules?: LunarCityWorldModules,
  manifestUrl = DEFAULT_MANIFEST_URL
): Promise<LunarCityWorldHandle> {
  assertWorldManifestRuntimeAssets(manifest)
  const loaded = modules ?? (await loadBabylonModules())

  const engine = new loaded.Engine(canvas, true, {
    powerPreference: 'low-power',
    preserveDrawingBuffer: false,
    stencil: false
  })

  const resolvedManifestUrl = absoluteManifestUrl(manifestUrl)
  let world: Awaited<ReturnType<typeof createWorldScene>> | undefined
  let observer: ResizeObserver | undefined
  let resizeListener: (() => void) | undefined
  let releaseCameraInput: CameraInputRelease | undefined
  let contextLostListener: ((event: Event) => void) | undefined
  let contextRestoredListener: (() => void) | undefined
  let ownedListenerCount = 0
  let destroyed = false

  try {
    world = await createWorldScene(engine, manifest, emit, loaded, uri => new URL(uri, resolvedManifestUrl).toString())
    releaseCameraInput = bindCameraInput(canvas, {
      dispatch(intent) {
        world?.dispatchCamera(intent)
      },
      pick(clientX, clientY) {
        return world?.pick(clientX, clientY)
      }
    })

    contextLostListener = event => {
      event.preventDefault()
      world?.setVisible(false)
    }

    contextRestoredListener = () => world?.setVisible(true)
    canvas.addEventListener('webglcontextlost', contextLostListener)
    canvas.addEventListener('webglcontextrestored', contextRestoredListener)
    ownedListenerCount += 2

    const resize = () => {
      if (destroyed) {
        return
      }

      engine.resize()
      world?.render()
    }

    resizeListener = resize
    window.addEventListener('resize', resize)
    ownedListenerCount += 1

    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize)
      observer.observe(canvas)
    }

    world.render()

    const handle: LunarCityWorldHandle = {
      leaderStateClips: world.leaderStateClips,
      applySnapshot(snapshot) {
        world?.applySnapshot(snapshot)
      },
      dispatchCamera(intent) {
        world?.dispatchCamera(intent)
      },
      getEntityCameraOrder() {
        return world?.getEntityCameraOrder() ?? []
      },
      getCameraState() {
        return world?.getCameraState() ?? { focusedEntityKey: undefined, following: false }
      },
      getPerfSnapshot() {
        const metrics = world?.getPerfSnapshot()

        return metrics
          ? {
              ...metrics,
              listeners: metrics.listeners + ownedListenerCount + (releaseCameraInput?.activeListenerCount() ?? 0)
            }
          : {
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
            }
      },
      setLeaderAnimation(leaderId, state) {
        world?.setLeaderAnimation(leaderId, state)
      },
      setQuality(tier) {
        world?.setQuality(tier)
      },
      setTimeOfDay(value) {
        world?.setTimeOfDay(value)
      },
      setWorldPreset(preset) {
        world?.setWorldPreset(preset)
      },
      setReducedMotion(reduced) {
        world?.setReducedMotion(reduced)
      },
      destroy() {
        if (destroyed) {
          return
        }

        destroyed = true

        if (resizeListener) {
          window.removeEventListener('resize', resizeListener)
          ownedListenerCount -= 1
        }

        releaseCameraInput?.()

        if (contextLostListener) {
          canvas.removeEventListener('webglcontextlost', contextLostListener)
          ownedListenerCount -= 1
        }

        if (contextRestoredListener) {
          canvas.removeEventListener('webglcontextrestored', contextRestoredListener)
          ownedListenerCount -= 1
        }

        observer?.disconnect()
        world?.dispose()
        engine.dispose()
        world = undefined
        observer = undefined
        resizeListener = undefined
        releaseCameraInput = undefined
        contextLostListener = undefined
        contextRestoredListener = undefined
      }
    }

    return handle
  } catch (error) {
    destroyed = true

    if (resizeListener) {
      window.removeEventListener('resize', resizeListener)
      ownedListenerCount -= 1
    }

    releaseCameraInput?.()

    if (contextLostListener) {
      canvas.removeEventListener('webglcontextlost', contextLostListener)
      ownedListenerCount -= 1
    }

    if (contextRestoredListener) {
      canvas.removeEventListener('webglcontextrestored', contextRestoredListener)
      ownedListenerCount -= 1
    }

    observer?.disconnect()
    world?.dispose()
    engine.dispose()
    throw error
  }
}
