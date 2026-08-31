import type {
  BabylonImportResultLike,
  BabylonMeshLike,
  BabylonNodeLike,
  CameraControlState,
  CameraIntent,
  EntityKey,
  LeaderAnimationState,
  LeaderId,
  LeaderStateClipMap,
  LunarCityIntent,
  LunarCityNodeMetadata,
  LunarCitySnapshot,
  LunarCityWorldModules,
  ModelManifestEntry,
  QualityTier,
  Vec3,
  WorldBounds,
  WorldManifestV2
} from '../model'

import {
  type CameraController,
  type CameraLike,
  type CameraPickTarget,
  createCameraController
} from './camera-controller'
import { createOcclusionController, type OcclusionCandidate, type OcclusionSelection } from './occlusion'

const LEADER_STATES: readonly LeaderAnimationState[] = [
  'acknowledging',
  'idle',
  'listening',
  'talking',
  'thinking',
  'unavailable'
]

const LEADER_IDS = ['owl', 'fox', 'badger', 'otter', 'bird', 'stag'] as const satisfies readonly LeaderId[]

export interface LunarCitySceneHandle {
  readonly leaderStateClips: ReadonlyMap<string, LeaderStateClipMap>
  applySnapshot(snapshot: LunarCitySnapshot): void
  dispatchCamera(intent: CameraIntent): void
  getCameraState(): CameraControlState
  pick(clientX: number, clientY: number): CameraPickTarget | undefined
  setQuality(tier: QualityTier): void
  render(): void
  dispose(): void
}

interface FocusMetadata extends OcclusionSelection {
  focusEntityKey: EntityKey
}

function staticFocusKey(kind: 'leader' | 'model', value: string): EntityKey {
  return `lunar-city:${kind}:${encodeURIComponent(value)}` as EntityKey
}

function worldCameraAnchor(model: ModelManifestEntry): Vec3 {
  return {
    x: model.transform.position.x + model.cameraAnchor.x,
    y: model.transform.position.y + model.cameraAnchor.y,
    z: model.transform.position.z + model.cameraAnchor.z
  }
}

function metadataRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function tagNode(node: BabylonNodeLike, lunarCity: LunarCityNodeMetadata): void {
  node.metadata = { ...metadataRecord(node.metadata), lunarCity }
}

function allImportedNodes(result: BabylonImportResultLike): readonly BabylonNodeLike[] {
  return [...result.transformNodes, ...result.meshes]
}

function findNode(result: BabylonImportResultLike, name: string): BabylonNodeLike | undefined {
  return allImportedNodes(result).find(node => node.name === name)
}

function placeModel(
  result: BabylonImportResultLike,
  model: ModelManifestEntry,
  modules: LunarCityWorldModules,
  scene: ConstructorParameters<LunarCityWorldModules['TransformNode']>[1]
): FocusMetadata {
  const root = findNode(result, `${model.id}:root`)

  if (!root) {
    throw new Error(`Lunar City model ${model.id} is missing its runtime root`)
  }

  const placement = new modules.TransformNode(`lunar-city:placement:${model.id}`, scene)
  const { position, rotation, scale } = model.transform
  const cameraAnchor = worldCameraAnchor(model)
  const focusEntityKey = staticFocusKey('model', model.id)

  placement.position?.set(position.x, position.y, position.z)
  placement.rotation?.set(rotation.x, rotation.y, rotation.z)
  placement.scaling?.set(scale.x, scale.y, scale.z)
  placement.setPivotPoint?.(new modules.Vector3(model.pivot.x, model.pivot.y, model.pivot.z))

  const loaderConversionRoot = result.meshes.find(node => node.name === '__root__') ?? root

  loaderConversionRoot.parent = placement
  tagNode(placement, {
    cameraAnchor,
    focusEntityKey,
    kind: 'landmark',
    modelId: model.id,
    occlusionGroup: model.occlusionGroup,
    selectable: model.id !== 'terrain'
  })

  for (const lod of model.lods) {
    const node = findNode(result, lod.node)

    if (!node) {
      throw new Error(`Lunar City model ${model.id} is missing LOD node ${lod.node}`)
    }

    tagNode(node, { distance: lod.distance, kind: 'lod', modelId: model.id })
  }

  for (const mesh of result.meshes) {
    tagNode(mesh, {
      cameraAnchor,
      focusEntityKey,
      kind: 'landmark-mesh',
      modelId: model.id,
      occlusionGroup: model.occlusionGroup,
      selectable: model.id !== 'terrain'
    })
  }

  return { cameraAnchor, focusEntityKey, occlusionGroup: model.occlusionGroup }
}

function gltfExtras(node: BabylonNodeLike): Record<string, unknown> {
  const gltf = metadataRecord(metadataRecord(node.metadata).gltf)

  return metadataRecord(gltf.extras)
}

function readLeaderStateClips(node: BabylonNodeLike, leaderId: LeaderId): LeaderStateClipMap {
  const extras = gltfExtras(node)

  if (extras.leaderId !== leaderId) {
    throw new Error(`leader ${leaderId} GLB metadata has a mismatched identity`)
  }

  const clips = metadataRecord(extras.stateClips)

  const entries = LEADER_STATES.map(state => {
    const clip = clips[state]
    const expected = `leader:${leaderId}:${state}`

    if (clip !== expected) {
      throw new Error(`leader ${leaderId} is missing authoritative ${state} clip metadata`)
    }

    return [state, expected]
  })

  return Object.freeze(Object.fromEntries(entries)) as LeaderStateClipMap
}

interface StructuredLeader {
  id: LeaderId
  node: BabylonNodeLike
  stateClips: LeaderStateClipMap
}

function isLeaderId(value: unknown): value is LeaderId {
  return typeof value === 'string' && LEADER_IDS.some(leaderId => leaderId === value)
}

function readStructuredLeaders(result: BabylonImportResultLike): readonly StructuredLeader[] {
  const leaders = new Map<LeaderId, StructuredLeader>()

  for (const node of new Set(allImportedNodes(result))) {
    const extras = gltfExtras(node)

    if (!('leaderId' in extras) && !('stateClips' in extras)) {
      continue
    }

    if (!isLeaderId(extras.leaderId)) {
      throw new Error('leaders GLB contains state metadata without a recognized leaderId')
    }

    if (leaders.has(extras.leaderId)) {
      throw new Error(`leaders GLB contains duplicate structured identity for ${extras.leaderId}`)
    }

    leaders.set(extras.leaderId, {
      id: extras.leaderId,
      node,
      stateClips: readLeaderStateClips(node, extras.leaderId)
    })
  }

  return LEADER_IDS.map(leaderId => {
    const leader = leaders.get(leaderId)

    if (!leader) {
      throw new Error(`leaders GLB is missing structured identity for ${leaderId}`)
    }

    return leader
  })
}

function belongsToLeader(node: BabylonNodeLike, leaderNode: BabylonNodeLike): boolean {
  const visited = new Set<BabylonNodeLike>()
  let current: BabylonNodeLike | null | undefined = node

  while (current && !visited.has(current)) {
    if (current === leaderNode) {
      return true
    }

    visited.add(current)
    current = current.parent
  }

  return false
}

function retainLeaderIdentityMetadata(
  result: BabylonImportResultLike,
  leaderStateClips: Map<string, LeaderStateClipMap>,
  model: ModelManifestEntry,
  cameraAnchor: Vec3
): void {
  const leaders = readStructuredLeaders(result)

  for (const leader of leaders) {
    leaderStateClips.set(leader.id, leader.stateClips)
  }

  for (const node of new Set(allImportedNodes(result))) {
    const leader = leaders.find(entry => belongsToLeader(node, entry.node))
    const mesh = result.meshes.find(candidate => candidate === node)

    if (!leader) {
      if (mesh) {
        mesh.isPickable = false
        tagNode(mesh, {
          cameraAnchor,
          focusEntityKey: staticFocusKey('model', model.id),
          kind: 'leader-shared-surface',
          modelId: 'leaders',
          occlusionGroup: model.occlusionGroup,
          selectable: false
        })
      }

      continue
    }

    if (mesh) {
      mesh.isPickable = true
    }

    tagNode(node, {
      cameraAnchor,
      focusEntityKey: staticFocusKey('leader', leader.id),
      kind: 'leader',
      leaderId: leader.id,
      modelId: 'leaders',
      occlusionGroup: model.occlusionGroup,
      selectable: true,
      stateClips: leader.stateClips
    })
  }
}

function freezeStaticResources(
  result: BabylonImportResultLike,
  materials: readonly { freeze?(): void }[],
  model: ModelManifestEntry
): void {
  if (model.requiredClips.length > 0) {
    return
  }

  for (const mesh of result.meshes) {
    mesh.freezeWorldMatrix?.()
  }

  for (const material of materials) {
    material.freeze?.()
  }
}

function translatedBounds(model: ModelManifestEntry): WorldBounds {
  const { bounds, transform } = model

  return {
    min: {
      x: transform.position.x + bounds.min.x * transform.scale.x,
      y: transform.position.y + bounds.min.y * transform.scale.y,
      z: transform.position.z + bounds.min.z * transform.scale.z
    },
    max: {
      x: transform.position.x + bounds.max.x * transform.scale.x,
      y: transform.position.y + bounds.max.y * transform.scale.y,
      z: transform.position.z + bounds.max.z * transform.scale.z
    }
  }
}

function intersectsBounds(start: Vec3, end: Vec3, bounds: WorldBounds): boolean {
  let minimum = 0
  let maximum = 1

  for (const axis of ['x', 'y', 'z'] as const) {
    const origin = start[axis]
    const direction = end[axis] - origin
    const lower = bounds.min[axis]
    const upper = bounds.max[axis]

    if (direction === 0) {
      if (origin < lower || origin > upper) {
        return false
      }

      continue
    }

    const first = (lower - origin) / direction
    const second = (upper - origin) / direction
    minimum = Math.max(minimum, Math.min(first, second))
    maximum = Math.min(maximum, Math.max(first, second))

    if (minimum > maximum) {
      return false
    }
  }

  return true
}

interface MaterialAdapter {
  readonly source: NonNullable<BabylonMeshLike['material']>
  alpha?: number
  clone?(): MaterialAdapter | undefined
}

function materialAdapter(source: NonNullable<BabylonMeshLike['material']>): MaterialAdapter {
  return {
    source,
    get alpha() {
      return source.alpha
    },
    set alpha(value: number | undefined) {
      source.alpha = value
    },
    clone() {
      const cloned = source.clone?.('lunar-city:occlusion-isolated')

      return cloned && typeof cloned === 'object'
        ? materialAdapter(cloned as NonNullable<BabylonMeshLike['material']>)
        : undefined
    }
  }
}

function buildOcclusionCandidates(
  result: BabylonImportResultLike,
  model: ModelManifestEntry
): readonly OcclusionCandidate[] {
  if (!/(?:roof|wall)/iu.test(model.occlusionGroup)) {
    return []
  }

  const bounds = translatedBounds(model)

  return result.meshes.flatMap(mesh => {
    if (!mesh.material?.clone) {
      return []
    }

    const material = materialAdapter(mesh.material)

    return [
      {
        group: model.occlusionGroup,
        material,
        isolateMaterial: true,
        assignMaterial(isolated) {
          mesh.material = (isolated as MaterialAdapter).source
        },
        intersectsFocusRay(camera, selection) {
          return intersectsBounds(camera.position, selection.cameraAnchor, bounds)
        }
      }
    ]
  })
}

function cameraPosition(camera: CameraLike): Vec3 {
  const positional = camera as CameraLike & { globalPosition?: Vec3; position?: Vec3 }
  const position = positional.globalPosition ?? positional.position

  if (position && Number.isFinite(position.x) && Number.isFinite(position.y) && Number.isFinite(position.z)) {
    return { x: position.x, y: position.y, z: position.z }
  }

  return { x: camera.target.x, y: camera.target.y, z: camera.target.z }
}

function pickedCameraTarget(node: BabylonNodeLike | undefined): CameraPickTarget | undefined {
  const visited = new Set<BabylonNodeLike>()
  let current = node

  while (current && !visited.has(current)) {
    visited.add(current)
    const metadata = current.metadata?.lunarCity

    if (metadata && 'selectable' in metadata && metadata.selectable && 'focusEntityKey' in metadata) {
      return { kind: 'entity', entityKey: metadata.focusEntityKey }
    }

    current = current.parent ?? undefined
  }

  return undefined
}

export async function createWorldScene(
  engine: ConstructorParameters<LunarCityWorldModules['Scene']>[0],
  manifest: WorldManifestV2,
  emit: (intent: LunarCityIntent) => void,
  modules: LunarCityWorldModules,
  resolveAssetUrl: (uri: string) => string
): Promise<LunarCitySceneHandle> {
  const scene = new modules.Scene(engine)
  let disposed = false

  try {
    scene.ambientColor = new modules.Color3(0.32, 0.18, 0.12)
    const overview = manifest.camera.overview
    scene.activeCamera = new modules.ArcRotateCamera(
      'lunar-city:approved-overview',
      overview.alpha,
      overview.beta,
      overview.radius,
      new modules.Vector3(overview.target.x, overview.target.y, overview.target.z),
      scene
    )

    const keyLight = new modules.DirectionalLight('lunar-city:key-light', new modules.Vector3(-0.45, -1, 0.35), scene)

    keyLight.intensity = 0.55

    const leaderStateClips = new Map<string, LeaderStateClipMap>()
    const camera = scene.activeCamera as CameraLike
    const focusAnchors = new Map<EntityKey, () => Vec3>()
    const focusMetadata = new Map<EntityKey, FocusMetadata>()
    const occlusionCandidates: OcclusionCandidate[] = []

    const cameraController: CameraController = createCameraController(camera, overview, manifest.camera.bounds, {
      focusAnchors,
      followOffset: manifest.camera.followOffset
    })

    for (const model of manifest.models) {
      const materialStart = scene.materials?.length ?? 0
      const result = await modules.ImportMeshAsync(resolveAssetUrl(model.uri), scene)
      const focus = placeModel(result, model, modules, scene)

      focusAnchors.set(focus.focusEntityKey, () => focus.cameraAnchor)
      focusMetadata.set(focus.focusEntityKey, focus)
      occlusionCandidates.push(...buildOcclusionCandidates(result, model))

      if (model.id === 'leaders') {
        retainLeaderIdentityMetadata(result, leaderStateClips, model, focus.cameraAnchor)

        for (const leaderId of LEADER_IDS) {
          const leaderFocusKey = staticFocusKey('leader', leaderId)
          focusAnchors.set(leaderFocusKey, () => focus.cameraAnchor)
          focusMetadata.set(leaderFocusKey, { ...focus, focusEntityKey: leaderFocusKey })
        }
      }

      freezeStaticResources(result, scene.materials?.slice(materialStart) ?? [], model)
    }

    const occlusion = createOcclusionController(occlusionCandidates)
    let animationFrame: number | undefined
    let previousAnimationTime: number | undefined

    const applyOcclusion = (): void => {
      const focusedEntityKey = cameraController.getState().focusedEntityKey
      const selection = focusedEntityKey ? focusMetadata.get(focusedEntityKey) : undefined

      occlusion.update({ position: cameraPosition(camera) }, selection)
    }

    const scheduleCameraTransition = (): void => {
      if (
        animationFrame !== undefined ||
        !cameraController.isTransitioning() ||
        typeof requestAnimationFrame === 'undefined'
      ) {
        return
      }

      animationFrame = requestAnimationFrame(now => {
        animationFrame = undefined
        const elapsedMs = previousAnimationTime === undefined ? 16 : now - previousAnimationTime
        previousAnimationTime = now
        cameraController.update(elapsedMs)
        applyOcclusion()

        if (!disposed) {
          scene.render()
        }

        scheduleCameraTransition()
      })
    }

    await scene.whenReadyAsync()

    return {
      leaderStateClips,
      applySnapshot(snapshot) {
        void snapshot
      },
      dispatchCamera(intent) {
        if (disposed) {
          return
        }

        cameraController.dispatch(intent)
        previousAnimationTime = undefined
        applyOcclusion()
        emit({ kind: 'camera-state', state: cameraController.getState() })
        scene.render()
        scheduleCameraTransition()
      },
      getCameraState() {
        return cameraController.getState()
      },
      pick(clientX, clientY) {
        const target = pickedCameraTarget(scene.pick?.(clientX, clientY)?.pickedMesh)

        if (target) {
          emit({ kind: 'select-focus', entityKey: target.entityKey })
        } else {
          emit({ kind: 'clear-selection' })
        }

        return target
      },
      setQuality(tier) {
        void tier
      },
      render() {
        if (!disposed) {
          scene.render()
        }
      },
      dispose() {
        if (disposed) {
          return
        }

        disposed = true

        if (animationFrame !== undefined && typeof cancelAnimationFrame !== 'undefined') {
          cancelAnimationFrame(animationFrame)
        }

        animationFrame = undefined
        occlusion.clear()
        scene.dispose()
      }
    }
  } catch (error) {
    disposed = true
    scene.dispose()
    throw error
  }
}
