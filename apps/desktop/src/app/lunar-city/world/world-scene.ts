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
import {
  applyLodSelection,
  createEntityRegistry,
  type EntityPresentationFactory,
  type EntityVisual,
  type InstancedEntityGroup,
  type InstancedEntityMember,
  type LodEntry,
  selectLodIndex
} from './entities'
import { createNavigationController, type NavigationQuery } from './navigation'
import { createOcclusionController, type OcclusionCandidate, type OcclusionSelection } from './occlusion'
import { applyQualitySettings, createQualityController } from './quality'
import { createFrameScheduler } from './scheduler'

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
  setVisible(visible: boolean): void
  setQuality(tier: QualityTier): void
  render(): void
  dispose(): void
}

interface FocusMetadata extends OcclusionSelection {
  focusEntityKey: EntityKey
}

interface PlacedModel {
  focus: FocusMetadata
  lods: readonly LodEntry[]
}

function staticFocusKey(kind: 'leader' | 'model', value: string): EntityKey {
  return `lunar-city:${kind}:${encodeURIComponent(value)}` as EntityKey
}

function navigationPointKey(point: Vec3): string {
  return `${point.x},${point.y},${point.z}`
}

/**
 * A fail-closed traversal boundary for the manifest's declared links. Recast
 * may replace this query when its route-local navmesh is ready, but no worker
 * is ever sent on an inferred straight line in the meantime.
 */
export function createManifestNavigationQuery(
  manifest: Pick<WorldManifestV2, 'navigation'>['navigation']
): NavigationQuery {
  const points = new Map<string, Vec3>()
  const edges = new Map<string, string[]>()

  const addEdge = (from: Vec3, to: Vec3): void => {
    const fromKey = navigationPointKey(from)
    const toKey = navigationPointKey(to)
    points.set(fromKey, { ...from })
    points.set(toKey, { ...to })
    const adjacent = edges.get(fromKey) ?? []
    adjacent.push(toKey)
    edges.set(fromKey, adjacent)
  }

  for (const link of manifest.links) {
    addEdge(link.from, link.to)

    if (link.bidirectional) {
      addEdge(link.to, link.from)
    }
  }

  return {
    computePath(from, to) {
      const start = navigationPointKey(from)
      const destination = navigationPointKey(to)

      if (!points.has(start) || !points.has(destination)) {
        return undefined
      }

      const previous = new Map<string, string | undefined>([[start, undefined]])
      const pending = [start]

      while (pending.length > 0) {
        const current = pending.shift()!

        if (current === destination) {
          break
        }

        for (const adjacent of edges.get(current) ?? []) {
          if (!previous.has(adjacent)) {
            previous.set(adjacent, current)
            pending.push(adjacent)
          }
        }
      }

      if (!previous.has(destination)) {
        return undefined
      }

      const path: Vec3[] = []
      let current: string | undefined = destination

      while (current !== undefined) {
        path.push({ ...points.get(current)! })
        current = previous.get(current)
      }

      return path.reverse()
    }
  }
}

interface BabylonQuaternion {
  w: number
  x: number
  y: number
  z: number
}

interface BabylonAnimationGroupLike {
  clone?(name: string, targetConverter?: (target: unknown) => unknown): BabylonAnimationGroupLike | null
  name: string
  start?(loop?: boolean, speedRatio?: number, from?: number, to?: number): void
  stop?(): void
}

interface BabylonHierarchyNodeLike extends BabylonNodeLike {
  instantiateHierarchy?(
    parent?: BabylonNodeLike | null,
    options?: unknown,
    onNewNodeCreated?: (source: unknown, clone: BabylonNodeLike) => void
  ): BabylonNodeLike | null
}

interface BabylonInstancedMeshLike extends BabylonMeshLike {
  createInstance?(name: string): BabylonNodeLike
}

function manifestRotationQuaternion(rotation: Vec3): BabylonQuaternion {
  // TransformNode.rotation is Babylon's y-x-z Euler convention: yaw(y),
  // pitch(x), and roll(z). Keep this in sync with Vector3.toQuaternion().
  const halfRoll = rotation.z * 0.5
  const halfPitch = rotation.x * 0.5
  const halfYaw = rotation.y * 0.5
  const sinRoll = Math.sin(halfRoll)
  const cosRoll = Math.cos(halfRoll)
  const sinPitch = Math.sin(halfPitch)
  const cosPitch = Math.cos(halfPitch)
  const sinYaw = Math.sin(halfYaw)
  const cosYaw = Math.cos(halfYaw)

  return {
    x: cosYaw * sinPitch * cosRoll + sinYaw * cosPitch * sinRoll,
    y: sinYaw * cosPitch * cosRoll - cosYaw * sinPitch * sinRoll,
    z: cosYaw * cosPitch * sinRoll - sinYaw * sinPitch * cosRoll,
    w: cosYaw * cosPitch * cosRoll + sinYaw * sinPitch * sinRoll
  }
}

export function transformManifestPoint(model: Pick<ModelManifestEntry, 'transform'>, local: Vec3): Vec3 {
  const quaternion = manifestRotationQuaternion(model.transform.rotation)
  const x2 = quaternion.x + quaternion.x
  const y2 = quaternion.y + quaternion.y
  const z2 = quaternion.z + quaternion.z
  const xx = quaternion.x * x2
  const xy = quaternion.x * y2
  const xz = quaternion.x * z2
  const yy = quaternion.y * y2
  const yz = quaternion.y * z2
  const zz = quaternion.z * z2
  const wx = quaternion.w * x2
  const wy = quaternion.w * y2
  const wz = quaternion.w * z2

  const scaled = {
    x: local.x * model.transform.scale.x,
    y: local.y * model.transform.scale.y,
    z: local.z * model.transform.scale.z
  }

  return {
    x: model.transform.position.x + (1 - (yy + zz)) * scaled.x + (xy - wz) * scaled.y + (xz + wy) * scaled.z,
    y: model.transform.position.y + (xy + wz) * scaled.x + (1 - (xx + zz)) * scaled.y + (yz - wx) * scaled.z,
    z: model.transform.position.z + (xz - wy) * scaled.x + (yz + wx) * scaled.y + (1 - (xx + yy)) * scaled.z
  }
}

export function worldBoundsFromModel(model: Pick<ModelManifestEntry, 'bounds' | 'transform'>): WorldBounds {
  const corners = [model.bounds.min.x, model.bounds.max.x].flatMap(x =>
    [model.bounds.min.y, model.bounds.max.y].flatMap(y =>
      [model.bounds.min.z, model.bounds.max.z].map(z => transformManifestPoint(model, { x, y, z }))
    )
  )

  return {
    min: {
      x: Math.min(...corners.map(point => point.x)),
      y: Math.min(...corners.map(point => point.y)),
      z: Math.min(...corners.map(point => point.z))
    },
    max: {
      x: Math.max(...corners.map(point => point.x)),
      y: Math.max(...corners.map(point => point.y)),
      z: Math.max(...corners.map(point => point.z))
    }
  }
}

function worldCameraAnchor(model: ModelManifestEntry): Vec3 {
  return transformManifestPoint(model, model.cameraAnchor)
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
): PlacedModel {
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

  const lods: LodEntry[] = []

  for (const lod of model.lods) {
    const node = findNode(result, lod.node)

    if (!node) {
      throw new Error(`Lunar City model ${model.id} is missing LOD node ${lod.node}`)
    }

    tagNode(node, { distance: lod.distance, kind: 'lod', modelId: model.id })
    lods.push({ distance: lod.distance, node })
  }

  // glTF imports commonly enable both subtrees.  Lunar City never does: one
  // declared representation is active for every model from the first frame.
  applyLodSelection(lods, { distance: 0, lodAdvance: 0, selected: false })

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

  return { focus: { cameraAnchor, focusEntityKey, occlusionGroup: model.occlusionGroup }, lods }
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

function setNodePosition(node: BabylonNodeLike, position: Vec3): void {
  node.position?.set(position.x, position.y, position.z)
}

function workerAnimationGroups(result: BabylonImportResultLike): ReadonlyMap<string, BabylonAnimationGroupLike> {
  const groups = new Map<string, BabylonAnimationGroupLike>()

  for (const candidate of result.animationGroups) {
    if (
      candidate &&
      typeof candidate === 'object' &&
      'name' in candidate &&
      typeof (candidate as { name?: unknown }).name === 'string'
    ) {
      const group = candidate as BabylonAnimationGroupLike
      groups.set(group.name, group)
    }
  }

  return groups
}

function cloneWorkerHierarchy(
  template: BabylonNodeLike,
  parent: BabylonNodeLike,
  sourceGroups: ReadonlyMap<string, BabylonAnimationGroupLike>,
  name: string
): {
  animations: ReadonlyMap<string, BabylonAnimationGroupLike>
  nodeMap: ReadonlyMap<unknown, BabylonNodeLike>
  root: BabylonNodeLike
} {
  const nodeMap = new Map<unknown, BabylonNodeLike>()
  const hierarchy = template as BabylonHierarchyNodeLike

  const root =
    hierarchy.instantiateHierarchy?.(parent, undefined, (source, clone) => nodeMap.set(source, clone)) ??
    template.clone?.(`lunar-city:worker-clone:${name}`, parent) ??
    parent

  const animations = new Map<string, BabylonAnimationGroupLike>()

  for (const [name, group] of sourceGroups) {
    const cloned = group.clone?.(`lunar-city:worker:${name}`, target => nodeMap.get(target) ?? target)

    if (cloned) {
      animations.set(name, cloned)
    }
  }

  return { animations, nodeMap, root }
}

function deterministicWorkerVariant(key: EntityKey, variants: readonly string[]): string | undefined {
  if (variants.length === 0) {
    return undefined
  }

  let hash = 0

  for (const character of key) {
    hash = (hash * 31 + character.codePointAt(0)!) >>> 0
  }

  return variants[hash % variants.length]
}

function createBabylonEntityFactory(
  model: ModelManifestEntry,
  result: BabylonImportResultLike,
  modules: LunarCityWorldModules,
  scene: ConstructorParameters<LunarCityWorldModules['TransformNode']>[1]
): EntityPresentationFactory {
  const template = findNode(result, `${model.id}:root`)
  const sourceGroups = workerAnimationGroups(result)
  const variants = model.instancing?.variants ?? []
  const candidateMeshes = result.meshes.filter(mesh => mesh.name !== '__root__') as readonly BabylonInstancedMeshLike[]
  const sourceLodNodes = model.lods.map(lod => findNode(result, lod.node))

  const meshesForLod = (lodIndex: number): readonly BabylonInstancedMeshLike[] => {
    const lodRoot = sourceLodNodes[lodIndex] ?? sourceLodNodes[0]
    const matching = lodRoot ? candidateMeshes.filter(mesh => belongsToLeader(mesh, lodRoot)) : []

    return matching.length > 0 ? matching : candidateMeshes
  }

  if (!template) {
    throw new Error('Lunar City workers GLB is missing its runtime root')
  }

  // The imported GLB is a template only. Every observable worker below is a
  // clone or hardware instance of its genuine low-poly meshes, never a CSS
  // substitute or a node-name animation.
  template.setEnabled?.(false)

  return {
    createAnimated(entity, declaredVariant): EntityVisual {
      const anchor = new modules.TransformNode(`lunar-city:entity:${entity.key}`, scene)
      const clone = cloneWorkerHierarchy(template, anchor, sourceGroups, entity.key)
      const variant = declaredVariant ?? deterministicWorkerVariant(entity.key, variants)
      anchor.metadata = { ...metadataRecord(anchor.metadata), lunarCityWorkerVariant: variant }
      clone.root.setEnabled?.(true)
      let active: BabylonAnimationGroupLike | undefined

      return {
        dispose() {
          active?.stop?.()
          active = undefined
          clone.root.dispose?.()
          anchor.dispose?.()
        },
        setAnimation(clip) {
          const next = clone.animations.get(clip)

          if (!next) {
            return
          }

          if (active !== next) {
            active?.stop?.()
            active = next
          }

          active.start?.(true)
        },
        setLod(lodIndex) {
          for (const [index, sourceNode] of sourceLodNodes.entries()) {
            const node = sourceNode ? clone.nodeMap.get(sourceNode) : undefined
            node?.setEnabled?.(index === lodIndex)
          }
        },
        setPosition(position) {
          setNodePosition(anchor, position)
        },
        setStaticPose() {
          active?.stop?.()
          active = undefined
        }
      }
    },
    createInstancedGroup(groupKey): InstancedEntityGroup {
      const lodIndex = Number(/:lod:(\d+)$/u.exec(groupKey)?.[1] ?? 0)
      const sourceMeshes = meshesForLod(lodIndex)
      const members = new Map<EntityKey, { instances: BabylonNodeLike[]; root: BabylonNodeLike }>()

      const disposeMember = (member: { instances: BabylonNodeLike[]; root: BabylonNodeLike }): void => {
        for (const instance of member.instances) {
          instance.dispose?.()
        }

        member.root.dispose?.()
      }

      return {
        dispose() {
          for (const member of members.values()) {
            disposeMember(member)
          }

          members.clear()
        },
        sync(nextMembers: readonly InstancedEntityMember[]) {
          const nextKeys = new Set(nextMembers.map(member => member.key))

          for (const [key, member] of members) {
            if (!nextKeys.has(key)) {
              disposeMember(member)
              members.delete(key)
            }
          }

          for (const member of nextMembers) {
            let visual = members.get(member.key)

            if (!visual) {
              const root = new modules.TransformNode(`lunar-city:instance:${groupKey}:${member.key}`, scene)

              const instances = sourceMeshes.flatMap(mesh => {
                const instance = mesh.createInstance?.(`lunar-city:instance-mesh:${member.key}:${mesh.name}`)

                if (instance) {
                  instance.parent = root

                  return [instance]
                }

                return []
              })

              visual = { instances, root }
              members.set(member.key, visual)
            }

            setNodePosition(visual.root, member.position)
          }
        }
      }
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

  const bounds = worldBoundsFromModel(model)

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
    const focusAnchors = new Map<EntityKey, () => Vec3 | undefined>()
    const focusMetadata = new Map<EntityKey, FocusMetadata>()
    const occlusionCandidates: OcclusionCandidate[] = []
    const staticLods: Array<{ focus: FocusMetadata; lods: readonly LodEntry[] }> = []
    let workerAsset: { model: ModelManifestEntry; result: BabylonImportResultLike } | undefined

    const cameraController: CameraController = createCameraController(camera, overview, manifest.camera.bounds, {
      focusAnchors,
      followOffset: manifest.camera.followOffset
    })

    for (const model of manifest.models) {
      const materialStart = scene.materials?.length ?? 0
      const result = await modules.ImportMeshAsync(resolveAssetUrl(model.uri), scene)
      const placed = placeModel(result, model, modules, scene)
      const focus = placed.focus

      focusAnchors.set(focus.focusEntityKey, () => focus.cameraAnchor)
      focusMetadata.set(focus.focusEntityKey, focus)
      staticLods.push({ focus, lods: placed.lods })
      occlusionCandidates.push(...buildOcclusionCandidates(result, model))

      if (model.id === 'leaders') {
        retainLeaderIdentityMetadata(result, leaderStateClips, model, focus.cameraAnchor)

        for (const leaderId of LEADER_IDS) {
          const leaderFocusKey = staticFocusKey('leader', leaderId)
          focusAnchors.set(leaderFocusKey, () => focus.cameraAnchor)
          focusMetadata.set(leaderFocusKey, { ...focus, focusEntityKey: leaderFocusKey })
        }
      }

      if (model.id === 'workers') {
        workerAsset = { model, result }
      }

      freezeStaticResources(result, scene.materials?.slice(materialStart) ?? [], model)
    }

    const occlusion = createOcclusionController(occlusionCandidates)
    const quality = createQualityController('efficient')

    if (!workerAsset) {
      throw new Error('Lunar City manifest has no workers model')
    }

    const workerClipNames = new Set(workerAnimationGroups(workerAsset.result).keys())

    const entityRegistry = createEntityRegistry({
      factory: createBabylonEntityFactory(workerAsset.model, workerAsset.result, modules, scene),
      focusAnchors,
      workerClips: workerClipNames
    })

    const navigation = createNavigationController({
      destinations: manifest.destinations,
      query: createManifestNavigationQuery(manifest.navigation),
      workerClips: workerClipNames
    })

    const destinationByEntity = new Map<EntityKey, string>()
    const activeNavigation = new Set<EntityKey>()

    const applyOcclusion = (): void => {
      const focusedEntityKey = cameraController.getState().focusedEntityKey
      const selection = focusedEntityKey ? focusMetadata.get(focusedEntityKey) : undefined

      occlusion.update({ position: cameraPosition(camera) }, selection)
    }

    const scheduler = createFrameScheduler({
      onFrame(frame) {
        const previousCameraState = cameraController.getState()
        cameraController.update(frame.elapsedMs)
        const cameraState = cameraController.getState()

        if (
          previousCameraState.focusedEntityKey !== cameraState.focusedEntityKey ||
          previousCameraState.following !== cameraState.following
        ) {
          entityRegistry.setSelection(cameraState.focusedEntityKey)
          emit({ kind: 'camera-state', state: cameraState })
        }

        const settings = quality.settings()
        const currentCameraPosition = cameraPosition(camera)

        navigation.tick(frame.elapsedMs)
        entityRegistry.syncMotion()

        for (const key of [...activeNavigation]) {
          if (!navigation.isMoving(key)) {
            activeNavigation.delete(key)
            entityRegistry.setMoving(key, false)
          }
        }

        for (const staticModel of staticLods) {
          applyLodSelection(staticModel.lods, {
            distance: Math.hypot(
              currentCameraPosition.x - staticModel.focus.cameraAnchor.x,
              currentCameraPosition.y - staticModel.focus.cameraAnchor.y,
              currentCameraPosition.z - staticModel.focus.cameraAnchor.z
            ),
            lodAdvance: settings.lodAdvance,
            selected: cameraState.focusedEntityKey === staticModel.focus.focusEntityKey
          })
        }

        entityRegistry.applyLodPolicy((_key, position, isSelected) =>
          selectLodIndex(workerAsset.model.lods, {
            distance: Math.hypot(
              currentCameraPosition.x - position.x,
              currentCameraPosition.y - position.y,
              currentCameraPosition.z - position.z
            ),
            lodAdvance: settings.lodAdvance,
            selected: isSelected
          })
        )

        applyOcclusion()
        const startedAt = typeof performance === 'undefined' ? Date.now() : performance.now()
        scene.render()
        const finishedAt = typeof performance === 'undefined' ? Date.now() : performance.now()

        if (
          quality.noteFrame({ elapsedMs: Math.max(0, finishedAt - startedAt), interactive: frame.targetFps === 30 })
        ) {
          applyQualitySettings(engine, quality.settings())
        }

        return cameraController.isTransitioning()
      },
      renderer: engine
    })

    scheduler.bindRendererPauseState()

    await scene.whenReadyAsync()

    return {
      leaderStateClips,
      applySnapshot(snapshot) {
        if (!disposed) {
          const dynamicEntities = new Map(
            [...snapshot.entities].filter(([, entity]) => entity.identity.kind !== 'profile')
          )

          entityRegistry.reconcile({ ...snapshot, entities: dynamicEntities })

          for (const [key, entity] of dynamicEntities) {
            const previousDestination = destinationByEntity.get(key)
            destinationByEntity.set(key, entity.destination)

            if (previousDestination === entity.destination) {
              continue
            }

            const presentation = entityRegistry.navigationEntity(key)

            if (presentation && navigation.move(presentation, entity.destination)) {
              activeNavigation.add(key)
              entityRegistry.setMoving(key, true)
            } else {
              activeNavigation.delete(key)
              entityRegistry.setMoving(key, false)
            }
          }

          for (const key of destinationByEntity.keys()) {
            if (!dynamicEntities.has(key)) {
              destinationByEntity.delete(key)
              activeNavigation.delete(key)
            }
          }

          scheduler.requestRender()
        }
      },
      dispatchCamera(intent) {
        if (disposed) {
          return
        }

        cameraController.dispatch(intent)
        const cameraState = cameraController.getState()
        entityRegistry.setSelection(cameraState.focusedEntityKey)
        emit({ kind: 'camera-state', state: cameraState })
        scheduler.noteInteraction(typeof performance === 'undefined' ? Date.now() : performance.now())
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
        quality.setTier(tier)
        applyQualitySettings(engine, quality.settings())
        scheduler.requestRender()
      },
      setVisible(visible) {
        scheduler.setVisible(visible)
      },
      render() {
        if (!disposed) {
          scheduler.requestRender()
          scheduler.tick(typeof performance === 'undefined' ? Date.now() : performance.now())
        }
      },
      dispose() {
        if (disposed) {
          return
        }

        disposed = true

        scheduler.dispose()
        navigation.dispose()
        entityRegistry.dispose()
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
