import { assertWorldManifestRuntimeAssets } from '../manifest'
import type { LunarCityIntent, LunarCityWorldHandle, LunarCityWorldModules, WorldManifestV2 } from '../model'

import { createWorldScene } from './world-scene'

const DEFAULT_MANIFEST_URL = './lunar-city/v2/world-manifest.v2.json'

export async function loadBabylonModules(): Promise<LunarCityWorldModules> {
  const [engine, scene, sceneLoader, camera, vector, color, directionalLight, transformNode] = await Promise.all([
    import('@babylonjs/core/Engines/engine'),
    import('@babylonjs/core/scene'),
    import('@babylonjs/core/Loading/sceneLoader'),
    import('@babylonjs/core/Cameras/arcRotateCamera'),
    import('@babylonjs/core/Maths/math.vector'),
    import('@babylonjs/core/Maths/math.color'),
    import('@babylonjs/core/Lights/directionalLight'),
    import('@babylonjs/core/Meshes/transformNode'),
    import('@babylonjs/loaders/glTF')
  ])

  return {
    ArcRotateCamera: camera.ArcRotateCamera,
    Color3: color.Color3,
    DirectionalLight: directionalLight.DirectionalLight,
    Engine: engine.Engine,
    ImportMeshAsync: sceneLoader.ImportMeshAsync,
    Scene: scene.Scene,
    TransformNode: transformNode.TransformNode,
    Vector3: vector.Vector3
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
  let destroyed = false

  try {
    world = await createWorldScene(engine, manifest, emit, loaded, uri => new URL(uri, resolvedManifestUrl).toString())

    const resize = () => {
      if (destroyed) {
        return
      }

      engine.resize()
      world?.render()
    }

    resizeListener = resize
    window.addEventListener('resize', resize)

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
      setQuality(tier) {
        world?.setQuality(tier)
      },
      destroy() {
        if (destroyed) {
          return
        }

        destroyed = true

        if (resizeListener) {
          window.removeEventListener('resize', resizeListener)
        }

        observer?.disconnect()
        world?.dispose()
        engine.dispose()
        world = undefined
        observer = undefined
        resizeListener = undefined
      }
    }

    return handle
  } catch (error) {
    destroyed = true

    if (resizeListener) {
      window.removeEventListener('resize', resizeListener)
    }

    observer?.disconnect()
    world?.dispose()
    engine.dispose()
    throw error
  }
}
