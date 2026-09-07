import { projectCompoundKey } from '../identity'
import type {
  BabylonGlowLayerLike,
  BabylonImportResultLike,
  BabylonMeshLike,
  BabylonNodeLike,
  BabylonShadowGeneratorLike,
  CameraControlState,
  CameraIntent,
  CharacterAssetManifest,
  EntityKey,
  LeaderAnimationState,
  LeaderId,
  LeaderStateClipMap,
  LunarCityIntent,
  LunarCityNodeMetadata,
  LunarCitySnapshot,
  LunarCityWorkerPickMetadata,
  LunarCityWorldHandle,
  LunarCityWorldModules,
  LunarEntity,
  ModelManifestEntry,
  QualityTier,
  RecastConfigLike,
  RecastRuntimeLike,
  Vec3,
  WorkerCharacterPresentation,
  WorldBounds,
  WorldManifestV2,
  WorldPresetId
} from '../model'
import { lightingFor } from '../world-presets'

import {
  type CameraController,
  type CameraLike,
  type CameraPickTarget,
  createCameraController
} from './camera-controller'
import {
  applyLodSelection,
  createEntityRegistry,
  type EntityFocusMetadata,
  type EntityPresentationFactory,
  type EntityVisual,
  type InstancedEntityGroup,
  type InstancedEntityMember,
  type LodEntry,
  selectLodIndex
} from './entities'
import {
  createNavigationController,
  createRecastNavigationQuery,
  disposeRecastWrapper,
  type NavigationQuery
} from './navigation'
import { createOcclusionController, type OcclusionCandidate, type OcclusionSelection } from './occlusion'
import { createBabylonPerfAdapter } from './perf-adapter'
import { animationDistanceUnits, applyQualitySettings, createQualityController } from './quality'
import { createFrameScheduler } from './scheduler'

const LEADER_STATES: readonly LeaderAnimationState[] = [
  'acknowledging',
  'idle',
  'listening',
  'talking',
  'thinking',
  'unavailable'
]

const CONTINUOUS_LEADER_STATES = new Set<LeaderAnimationState>(['listening', 'talking', 'thinking'])

/** `Scene.FOGMODE_LINEAR`, inlined so the fog needs no extra Babylon import. */
const FOG_MODE_LINEAR = 3
/** `ImageProcessingConfiguration.TONEMAPPING_ACES`, inlined for the same reason. */
const TONE_MAPPING_ACES = 1
/**
 * One 1024² cascade is enough for a settlement that fits inside the camera's
 * bounded 18–120 unit zoom, and keeps the shadow pass to a single low-cost
 * render of the near-LOD casters.
 */
const SHADOW_MAP_SIZE = 1024

const LEADER_IDS = ['owl', 'fox', 'badger', 'otter', 'bird', 'stag'] as const satisfies readonly LeaderId[]

export interface LunarCitySceneHandle {
  readonly leaderStateClips: ReadonlyMap<string, LeaderStateClipMap>
  applySnapshot(snapshot: LunarCitySnapshot): void
  dispatchCamera(intent: CameraIntent): void
  getEntityCameraOrder(): readonly EntityKey[]
  getCameraState(): CameraControlState
  getPerfSnapshot(): NonNullable<ReturnType<NonNullable<LunarCityWorldHandle['getPerfSnapshot']>>>
  pick(clientX: number, clientY: number): CameraPickTarget | undefined
  setLeaderAnimation(leaderId: LeaderId, state: LeaderAnimationState): void
  setVisible(visible: boolean): void
  setQuality(tier: QualityTier): void
  setReducedMotion(reduced: boolean): void
  setTimeOfDay(value: number): void
  setWorldPreset(preset: WorldPresetId): void
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

function samePoint(left: Vec3 | undefined, right: Vec3 | undefined): boolean {
  return left?.x === right?.x && left?.y === right?.y && left?.z === right?.z
}

export interface ProjectCompoundAnchor {
  connectionId: string
  key: string
  position: Vec3
  projectId: string
}

/**
 * Project anchors are derived only from exact Kanban identities with a
 * manifest-assigned position. Overflow and ambiguous input stay unplaced;
 * this helper never invents a city coordinate from a task title or path.
 */
export function projectCompoundsForSnapshot(snapshot: LunarCitySnapshot): readonly ProjectCompoundAnchor[] {
  const compounds = new Map<string, ProjectCompoundAnchor>()
  const conflicted = new Set<string>()

  for (const entity of snapshot.entities.values()) {
    if (entity.identity.kind !== 'kanban' || !entity.position) {
      continue
    }

    const projectId = entity.projectId?.trim()

    if (!projectId) {
      continue
    }

    const key = projectCompoundKey(entity.identity.connectionId, projectId)
    const prior = compounds.get(key)

    if (prior && !samePoint(prior.position, entity.position)) {
      conflicted.add(key)
      compounds.delete(key)

      continue
    }

    if (!prior && !conflicted.has(key)) {
      compounds.set(key, {
        connectionId: entity.identity.connectionId,
        key,
        position: { ...entity.position },
        projectId
      })
    }
  }

  return [...compounds.values()].sort((left, right) => left.key.localeCompare(right.key))
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

interface NavigationGeometry {
  bounds: WorldBounds
  indices: Uint32Array
  positions: Float32Array
}

function transformNavigationVertex(mesh: BabylonMeshLike, x: number, y: number, z: number): Vec3 | undefined {
  const values = mesh.getWorldMatrix?.().m

  if (!values) {
    return { x, y, z }
  }

  if (values.length !== 16 || !values.every(Number.isFinite)) {
    return undefined
  }

  const w = values[3]! * x + values[7]! * y + values[11]! * z + values[15]!

  if (!Number.isFinite(w) || w === 0) {
    return undefined
  }

  return {
    x: (values[0]! * x + values[4]! * y + values[8]! * z + values[12]!) / w,
    y: (values[1]! * x + values[5]! * y + values[9]! * z + values[13]!) / w,
    z: (values[2]! * x + values[6]! * y + values[10]! * z + values[14]!) / w
  }
}

/** Extracts valid, indexed geometry only; malformed navigation assets fail closed. */
function navigationGeometry(result: BabylonImportResultLike): NavigationGeometry | undefined {
  const positions: number[] = []
  const indices: number[] = []

  for (const mesh of result.meshes) {
    const meshPositions = mesh.getVerticesData?.('position')
    const meshIndices = mesh.getIndices?.()

    if (!meshPositions || !meshIndices || meshPositions.length === 0 || meshPositions.length % 3 !== 0) {
      continue
    }

    const vertexCount = meshPositions.length / 3

    if (
      !Array.from(meshPositions).every(Number.isFinite) ||
      !Array.from(meshIndices).every(index => Number.isSafeInteger(index) && index >= 0 && index < vertexCount)
    ) {
      continue
    }

    const transformed = [] as Vec3[]

    for (let index = 0; index < meshPositions.length; index += 3) {
      const point = transformNavigationVertex(
        mesh,
        meshPositions[index]!,
        meshPositions[index + 1]!,
        meshPositions[index + 2]!
      )

      if (!point) {
        transformed.length = 0

        break
      }

      transformed.push(point)
    }

    if (transformed.length !== vertexCount) {
      continue
    }

    const vertexOffset = positions.length / 3

    for (const point of transformed) {
      positions.push(point.x, point.y, point.z)
    }

    indices.push(...Array.from(meshIndices, index => vertexOffset + index))
  }

  if (positions.length === 0 || indices.length === 0) {
    return undefined
  }

  const xs = positions.filter((_value, index) => index % 3 === 0)
  const ys = positions.filter((_value, index) => index % 3 === 1)
  const zs = positions.filter((_value, index) => index % 3 === 2)

  return {
    bounds: {
      min: { x: Math.min(...xs), y: Math.min(...ys), z: Math.min(...zs) },
      max: { x: Math.max(...xs), y: Math.max(...ys), z: Math.max(...zs) }
    },
    indices: Uint32Array.from(indices),
    positions: Float32Array.from(positions)
  }
}

function disposeNavigationImport(result: BabylonImportResultLike): void {
  for (const node of new Set<BabylonNodeLike>([...result.meshes, ...result.transformNodes])) {
    node.setEnabled?.(false)
    node.dispose?.()
  }
}

function recastConfig(Runtime: RecastRuntimeLike, bounds: WorldBounds): RecastConfigLike {
  const config = new Runtime.rcConfig()

  if (!config.set_bmin || !config.set_bmax) {
    throw new Error('Lunar City Recast runtime does not expose indexed bounds setters')
  }

  for (const [index, axis] of ['x', 'y', 'z'].entries() as Iterable<[number, keyof Vec3]>) {
    config.set_bmin(index, bounds.min[axis])
    config.set_bmax(index, bounds.max[axis])
  }

  // These are Recast voxelization inputs, not hand-authored city coordinates;
  // the actual navigable extent is extracted from the declared navigation GLB.
  Object.assign(config, {
    ch: 0.2,
    cs: 0.2,
    detailSampleDist: 6,
    detailSampleMaxError: 1,
    maxEdgeLen: 12,
    maxSimplificationError: 1.3,
    maxVertsPerPoly: 6,
    mergeRegionArea: 20,
    minRegionArea: 8,
    walkableClimb: 1,
    walkableHeight: 2,
    walkableRadius: 0.25,
    walkableSlopeAngle: 45
  })

  return config
}

/**
 * Builds a route-local Recast query from the manifest navigation GLB. The
 * imported geometry is released immediately after the navmesh has been built;
 * declared links remain a fail-closed fallback if Recast/WASM cannot start.
 */
export async function createRouteNavigationQuery(
  navigation: Pick<WorldManifestV2, 'navigation'>['navigation'],
  modules: Pick<LunarCityWorldModules, 'ImportMeshAsync' | 'createRecastNavigation'>,
  scene: Parameters<LunarCityWorldModules['ImportMeshAsync']>[1],
  resolveAssetUrl: (uri: string) => string
): Promise<NavigationQuery> {
  const fallback = createManifestNavigationQuery(navigation)

  if (!modules.createRecastNavigation) {
    return fallback
  }

  let imported: BabylonImportResultLike | undefined
  let releaseNavMesh: (() => void) | undefined

  try {
    imported = await modules.ImportMeshAsync(resolveAssetUrl(navigation.meshUri), scene)
    const geometry = navigationGeometry(imported)

    if (!geometry) {
      return fallback
    }

    const Runtime = await modules.createRecastNavigation()
    const navMesh = new Runtime.NavMesh()
    releaseNavMesh = () => navMesh.destroy?.()
    const configuration = recastConfig(Runtime, geometry.bounds)

    try {
      navMesh.build(
        geometry.positions,
        geometry.positions.length / 3,
        geometry.indices,
        geometry.indices.length,
        configuration
      )
    } finally {
      disposeRecastWrapper(configuration)
    }

    const query = createRecastNavigationQuery(navMesh, (x, y, z) => new Runtime.Vec3(x, y, z))

    // The query now owns navMesh and its idempotent destroy lifecycle.
    releaseNavMesh = undefined

    return query
  } catch {
    releaseNavMesh?.()

    return fallback
  } finally {
    if (imported) {
      disposeNavigationImport(imported)
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
  dispose?(): void
  isPlaying?: boolean
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

function copiedPoint(point: Vec3 | undefined): Vec3 {
  return point ? { x: point.x, y: point.y, z: point.z } : { x: 0, y: 0, z: 0 }
}

function workerPickMetadata(
  entity: { identity: LunarEntity['identity']; key: EntityKey; position?: Vec3; variant?: string },
  model: Pick<ModelManifestEntry, 'occlusionGroup'>,
  variant: string | undefined,
  character?: WorkerCharacterPresentation
): LunarCityWorkerPickMetadata {
  return {
    cameraAnchor: copiedPoint(entity.position),
    ...(character ? { character } : {}),
    entityKey: entity.key,
    focusEntityKey: entity.key,
    identity: entity.identity,
    kind: 'worker',
    modelId: 'workers',
    occlusionGroup: model.occlusionGroup,
    selectable: true,
    variant
  }
}

function tagWorkerNode(node: BabylonNodeLike, metadata: LunarCityWorkerPickMetadata): void {
  tagNode(node, metadata)

  if ('isPickable' in node) {
    ;(node as BabylonMeshLike).isPickable = true
  }
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

/** Decorative authored GLB nodes are optional detail, never a source of city truth. */
function isDecorationNode(node: BabylonNodeLike): boolean {
  const semantic = gltfExtras(node).semantic

  return typeof semantic === 'string' && /:(?:plants|bench|cyan-fixture|wheels|signal)$/u.test(semantic)
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

export function leaderCameraAnchor(node: BabylonNodeLike, fallback: Vec3): Vec3 {
  const matrix = (node as BabylonNodeLike & { getWorldMatrix?(): { m: readonly number[] } }).getWorldMatrix?.().m

  return matrix &&
    [matrix[12], matrix[13], matrix[14]].every(value => typeof value === 'number' && Number.isFinite(value))
    ? { x: matrix[12]!, y: matrix[13]!, z: matrix[14]! }
    : { ...fallback }
}

function retainLeaderIdentityMetadata(
  result: BabylonImportResultLike,
  leaderStateClips: Map<string, LeaderStateClipMap>,
  model: ModelManifestEntry,
  cameraAnchor: Vec3
): ReadonlyMap<LeaderId, Vec3> {
  const leaders = readStructuredLeaders(result)
  const anchors = new Map<LeaderId, Vec3>()

  for (const leader of leaders) {
    leaderStateClips.set(leader.id, leader.stateClips)
    anchors.set(leader.id, leaderCameraAnchor(leader.node, cameraAnchor))
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
      cameraAnchor: anchors.get(leader.id)!,
      focusEntityKey: staticFocusKey('leader', leader.id),
      kind: 'leader',
      leaderId: leader.id,
      modelId: 'leaders',
      occlusionGroup: model.occlusionGroup,
      selectable: true,
      stateClips: leader.stateClips
    })
  }

  return anchors
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

function applyIdentityAccent(node: BabylonNodeLike, accentCode: number): void {
  const low = accentCode & 0x1ff
  const high = (accentCode >>> 9) & 0x1ff
  const rotation = (low / 512) * Math.PI * 2
  const scaleX = 0.75 + high / 1024
  node.rotation?.set(0, 0, rotation)
  node.scaling?.set(scaleX, 1.5 - scaleX, 1)
}

function validActivationScale(value: unknown): value is readonly [number, number, number] {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every(component => typeof component === 'number' && Number.isFinite(component) && component > 0)
  )
}

export function createBabylonEntityFactory(
  model: ModelManifestEntry,
  result: BabylonImportResultLike,
  modules: LunarCityWorldModules,
  scene: ConstructorParameters<LunarCityWorldModules['TransformNode']>[1],
  characterAssets?: CharacterAssetManifest
): EntityPresentationFactory {
  const template = findNode(result, `${model.id}:root`)
  const sourceGroups = workerAnimationGroups(result)
  const variants = model.instancing?.variants ?? []
  const candidateMeshes = result.meshes.filter(mesh => mesh.name !== '__root__') as readonly BabylonInstancedMeshLike[]
  const sourceLodNodes = model.lods.map(lod => findNode(result, lod.node))

  const sourceVariantNodes = new Map(
    variants.flatMap(variant => {
      const node = findNode(result, `worker:variant:${variant}`)

      return node ? [[variant, node] as const] : []
    })
  )

  const sourceGroupKitNodes = new Map(
    allImportedNodes(result).flatMap(node => {
      const match = /^worker:group-kit:([^:]+)$/u.exec(node.name)

      return match?.[1] ? [[match[1], node] as const] : []
    })
  )

  const declaredSignatureNodes = new Map(
    characterAssets
      ? [
          ...Object.entries(characterAssets.physicalVariantRoots.body).map(
            ([id, node]) => [node, `body:${id}`] as const
          ),
          ...Object.entries(characterAssets.physicalVariantRoots.head).map(
            ([id, node]) => [node, `head:${id}`] as const
          ),
          ...Object.entries(characterAssets.physicalVariantRoots.palette).map(
            ([id, node]) => [node, `palette:${id}`] as const
          )
        ]
      : []
  )

  const sourceSignatureNodes = new Map(
    allImportedNodes(result).flatMap(node => {
      const id = declaredSignatureNodes.get(node.name)

      return id ? [[id, node] as const] : []
    })
  )

  const activationScaleFor = (node: BabylonNodeLike): readonly [number, number, number] => {
    const declared = characterAssets?.physicalVariantRoots.activationScale[node.name]
    const fromExtras = gltfExtras(node).activationScale
    const value = declared ? [declared.x, declared.y, declared.z] : fromExtras

    if (!validActivationScale(value)) {
      throw new Error(`Lunar City workers GLB is missing a valid activationScale for ${node.name}`)
    }

    return value
  }

  const sourceActivationScales = new Map<BabylonNodeLike, readonly [number, number, number]>()

  for (const node of [
    ...sourceVariantNodes.values(),
    ...sourceGroupKitNodes.values(),
    ...sourceSignatureNodes.values()
  ]) {
    sourceActivationScales.set(node, activationScaleFor(node))
  }

  if (characterAssets && sourceSignatureNodes.size !== declaredSignatureNodes.size) {
    const found = new Set(allImportedNodes(result).map(node => node.name))
    const missing = [...declaredSignatureNodes.keys()].filter(node => !found.has(node))

    throw new Error(
      `Lunar City workers GLB is missing manifest-declared physical signature root: ${missing.join(', ')}`
    )
  }

  const sourceAccentNodes = new Map(
    allImportedNodes(result).flatMap(node => {
      const suffix = characterAssets?.physicalVariantRoots.groupKit.identityAccentSuffix
      const prefix = 'worker:group-kit:'
      const ending = suffix ? `:${suffix}` : ':identity-accent'

      const kitId =
        node.name.startsWith(prefix) && node.name.endsWith(ending)
          ? node.name.slice(prefix.length, -ending.length)
          : undefined

      return kitId ? [[kitId, node] as const] : []
    })
  )

  if (characterAssets) {
    const allNodes = allImportedNodes(result)
    const suffixes = characterAssets.physicalVariantRoots.groupKit

    const requiredKitNodes = characterAssets.groupKits.flatMap(kit => [
      `worker:group-kit:${kit.kitId}`,
      `worker:group-kit:${kit.kitId}:${suffixes.silhouetteSuffix}`,
      `worker:group-kit:${kit.kitId}:${suffixes.emblemSuffix}`,
      `worker:group-kit:${kit.kitId}:${suffixes.identityAccentSuffix}`
    ])

    const counts = new Map<string, number>()

    for (const node of allNodes) {
      counts.set(node.name, (counts.get(node.name) ?? 0) + 1)
    }

    const invalid = requiredKitNodes.filter(node => counts.get(node) !== 1)

    if (invalid.length > 0) {
      throw new Error(
        `Lunar City workers GLB has missing or duplicate manifest-declared kit roots: ${invalid.join(', ')}`
      )
    }
  }

  const meshesForLod = (lodIndex: number): readonly BabylonInstancedMeshLike[] => {
    const lodRoot = sourceLodNodes[lodIndex] ?? sourceLodNodes[0]
    const matching = lodRoot ? candidateMeshes.filter(mesh => belongsToLeader(mesh, lodRoot)) : []

    return matching.length > 0 ? matching : candidateMeshes
  }

  const meshesForVariant = (meshes: readonly BabylonInstancedMeshLike[], variant: string | undefined) => {
    if (!variant || !sourceVariantNodes.has(variant)) {
      return meshes
    }

    const selectedVariant = sourceVariantNodes.get(variant)!

    return meshes.filter(
      mesh =>
        ![...sourceVariantNodes.values()].some(variantNode => belongsToLeader(mesh, variantNode)) ||
        belongsToLeader(mesh, selectedVariant)
    )
  }

  const meshesForCharacterKit = (meshes: readonly BabylonInstancedMeshLike[], kitId: string | undefined) => {
    if (!kitId || !sourceGroupKitNodes.has(kitId)) {
      return meshes.filter(
        mesh => ![...sourceGroupKitNodes.values()].some(groupKitNode => belongsToLeader(mesh, groupKitNode))
      )
    }

    const selectedKit = sourceGroupKitNodes.get(kitId)!

    return meshes.filter(
      mesh =>
        ![...sourceGroupKitNodes.values()].some(groupKitNode => belongsToLeader(mesh, groupKitNode)) ||
        belongsToLeader(mesh, selectedKit)
    )
  }

  if (!template) {
    throw new Error('Lunar City workers GLB is missing its runtime root')
  }

  // The imported GLB is a template only. Every observable worker below is a
  // clone or hardware instance of its genuine low-poly meshes, never a CSS
  // substitute or a node-name animation.
  template.setEnabled?.(false)

  return {
    createAnimated(entity, declaredVariant, character): EntityVisual {
      const anchor = new modules.TransformNode(`lunar-city:entity:${entity.key}`, scene)
      const clone = cloneWorkerHierarchy(template, anchor, sourceGroups, entity.key)
      const variant = declaredVariant ?? deterministicWorkerVariant(entity.key, variants)
      const metadata = workerPickMetadata(entity, model, variant, character)
      anchor.metadata = {
        ...metadataRecord(anchor.metadata),
        ...(character ? { lunarCityCharacter: character } : {}),
        lunarCityWorkerVariant: variant
      }
      tagWorkerNode(anchor, metadata)
      tagWorkerNode(clone.root, metadata)

      for (const node of new Set(clone.nodeMap.values())) {
        tagWorkerNode(node, metadata)
      }

      clone.root.setEnabled?.(true)

      for (const [variantId, sourceNode] of sourceVariantNodes) {
        const cloneNode = clone.nodeMap.get(sourceNode)
        const scale = sourceActivationScales.get(sourceNode)

        if (cloneNode && scale) {
          cloneNode.scaling?.set(...(variantId === variant ? scale : ([0, 0, 0] as const)))
          cloneNode.setEnabled?.(variantId === variant)
        }
      }

      for (const [kitId, sourceNode] of sourceGroupKitNodes) {
        const cloneNode = clone.nodeMap.get(sourceNode)
        const scale = sourceActivationScales.get(sourceNode)

        if (cloneNode && scale) {
          cloneNode.scaling?.set(...(kitId === character?.kitId ? scale : ([0, 0, 0] as const)))
          cloneNode.setEnabled?.(kitId === character?.kitId)
        }
      }

      const enabledSignatureNodes = new Set(
        character?.signature
          ? [
              `body:${character.signature.body}`,
              `head:${character.signature.head}`,
              `palette:${character.signature.palette}`
            ]
          : []
      )

      for (const [signatureId, sourceNode] of sourceSignatureNodes) {
        const cloneNode = clone.nodeMap.get(sourceNode)
        const scale = sourceActivationScales.get(sourceNode)

        if (cloneNode && scale) {
          const active = enabledSignatureNodes.has(signatureId)
          cloneNode.scaling?.set(...(active ? scale : ([0, 0, 0] as const)))
          cloneNode.setEnabled?.(active)
        }
      }

      for (const [kitId, sourceNode] of sourceAccentNodes) {
        const accent = clone.nodeMap.get(sourceNode)
        const enabled = kitId === character?.kitId
        accent?.setEnabled?.(enabled)

        if (enabled && character?.accentCode !== undefined && accent) {
          applyIdentityAccent(accent, character.accentCode)
        }
      }

      if (!character?.kitId && character?.signature && character.accentCode !== undefined) {
        const neutralAccentSource = sourceSignatureNodes.get(`palette:${character.signature.palette}`)
        const neutralAccent = neutralAccentSource ? clone.nodeMap.get(neutralAccentSource) : undefined

        if (neutralAccent) {
          applyIdentityAccent(neutralAccent, character.accentCode)
        }
      }

      let active: BabylonAnimationGroupLike | undefined

      return {
        dispose() {
          active?.stop?.()
          active = undefined

          for (const group of clone.animations.values()) {
            group.stop?.()
            group.dispose?.()
          }

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
      const variant = variants.find(candidate => groupKey.startsWith(`worker:${candidate}:`))
      const kitId = /:kit:([^:]+)(?::lod:\d+)?$/u.exec(groupKey)?.[1]
      const sourceMeshes = meshesForCharacterKit(meshesForVariant(meshesForLod(lodIndex), variant), kitId)
      const kitNode = kitId ? sourceGroupKitNodes.get(kitId) : undefined
      const accentNode = kitId ? sourceAccentNodes.get(kitId) : undefined

      const midKitMeshes =
        lodIndex === 1 && kitNode ? candidateMeshes.filter(mesh => belongsToLeader(mesh, kitNode)) : []

      const instanceSources = [
        ...sourceMeshes.map(mesh => ({
          accent: !kitId && lodIndex === 1 && /(?:^|:)face$/u.test(mesh.name),
          mesh
        })),
        ...midKitMeshes
          .filter(mesh => !sourceMeshes.includes(mesh))
          .map(mesh => ({ accent: accentNode ? belongsToLeader(mesh, accentNode) : false, mesh }))
      ]

      const members = new Map<
        EntityKey,
        { instances: { accent: boolean; node: BabylonNodeLike }[]; root: BabylonNodeLike }
      >()

      const disposeMember = (member: {
        instances: { accent: boolean; node: BabylonNodeLike }[]
        root: BabylonNodeLike
      }): void => {
        for (const instance of member.instances) {
          instance.node.dispose?.()
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

              const instances = instanceSources.flatMap(({ accent, mesh }) => {
                const instance = mesh.createInstance?.(`lunar-city:instance-mesh:${member.key}:${mesh.name}`)

                if (instance) {
                  instance.parent = root

                  return [{ accent, node: instance }]
                }

                return []
              })

              visual = { instances, root }
              members.set(member.key, visual)
            }

            const metadata = workerPickMetadata(member, model, member.variant ?? variant, member.character)
            tagWorkerNode(visual.root, metadata)

            for (const instance of visual.instances) {
              tagWorkerNode(instance.node, metadata)

              if (instance.accent && member.character?.accentCode !== undefined) {
                applyIdentityAccent(instance.node, member.character.accentCode)
              }
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
  const capturePerf = typeof window !== 'undefined' && window.__LUNAR_CITY_PERF__ !== undefined

  const perfAdapter = createBabylonPerfAdapter(
    scene,
    capturePerf && modules.SceneInstrumentation ? new modules.SceneInstrumentation(scene) : undefined
  )

  let lastFrameMs = 0
  let lastWorldUpdateMs = 0
  const worldUpdateTimestampsMs: number[] | undefined = capturePerf ? [] : undefined

  let disposed = false
  let scheduler: ReturnType<typeof createFrameScheduler> | undefined
  let navigation: ReturnType<typeof createNavigationController> | undefined
  let entityRegistry: ReturnType<typeof createEntityRegistry> | undefined
  let occlusion: ReturnType<typeof createOcclusionController> | undefined
  let glowLayer: BabylonGlowLayerLike | undefined
  let shadowGenerator: BabylonShadowGeneratorLike | undefined
  const projectCompoundNodes = new Map<string, BabylonNodeLike>()
  const activeLeaderAnimations = new Map<LeaderId, BabylonAnimationGroupLike>()
  const desiredLeaderStates = new Map<LeaderId, LeaderAnimationState>()
  const leaderNodes = new Map<LeaderId, BabylonNodeLike>()

  const stopLeaderAnimations = (): void => {
    for (const group of activeLeaderAnimations.values()) {
      group.stop?.()
    }

    activeLeaderAnimations.clear()
    desiredLeaderStates.clear()
  }

  const disposeWorld = (): void => {
    if (disposed) {
      return
    }

    disposed = true
    stopLeaderAnimations()
    scheduler?.dispose()
    entityRegistry?.dispose()
    occlusion?.clear()
    navigation?.dispose()

    for (const node of projectCompoundNodes.values()) {
      node.dispose?.()
    }

    projectCompoundNodes.clear()
    glowLayer?.dispose?.()
    shadowGenerator?.dispose?.()
    perfAdapter.dispose()
    scene.dispose()
  }

  try {
    // The authored rooms use charcoal interiors with cyan/violet accents. A
    // warmer ambient floor keeps those PBR surfaces legible when the efficient
    // tier disables dynamic shadows, without adding another per-frame light.
    scene.ambientColor = new modules.Color3(0.46, 0.32, 0.28)
    // Match the Blender staging skybox's deep-space backdrop. The generated
    // terrain carries the planetary ground; the runtime uses a stable clear
    // color here so the enlarged bowl has a believable horizon without adding
    // another loaded model or texture to every scene.
    if (modules.Color4) {
      scene.clearColor = new modules.Color4(0.005, 0.008, 0.02, 1)
    }
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

    keyLight.intensity = 0.85
    keyLight.diffuse = new modules.Color3(1, 0.86, 0.68)

    // A cool back-left rim light carves the approved silhouettes away from the
    // charcoal interiors, the same StarCraft-style two-tone read as the
    // reference art, without a second shadow map: it never toggles
    // shadowEnabled and stays on across every quality tier.
    const rimLight = new modules.DirectionalLight('lunar-city:rim-light', new modules.Vector3(0.6, -0.25, -0.55), scene)

    rimLight.intensity = 0.28
    rimLight.diffuse = new modules.Color3(0.55, 0.72, 0.95)
    rimLight.shadowEnabled = false

    // A soft top-down/ground-bounce fill keeps unlit faces out of pure black
    // once the key/rim pair adds contrast. HemisphericLight has no shadow map
    // and no specular term here, so it costs one extra ambient term per pixel.
    const fillLight = modules.HemisphericLight
      ? new modules.HemisphericLight('lunar-city:fill-light', new modules.Vector3(0, 1, 0), scene)
      : undefined

    if (fillLight) {
      fillLight.intensity = 0.32
      fillLight.diffuse = new modules.Color3(0.55, 0.64, 0.82)
      fillLight.groundColor = new modules.Color3(0.34, 0.24, 0.22)
      fillLight.specular = new modules.Color3(0, 0, 0)
      fillLight.shadowEnabled = false
    }

    let worldPreset: WorldPresetId = 'luna'
    let timeOfDay = 0.5
    const applyWorldPalette = (): void => {
      const lighting = lightingFor(worldPreset, timeOfDay)
      scene.ambientColor = new modules.Color3(...lighting.ambient)
      keyLight.intensity = lighting.keyIntensity
      keyLight.diffuse = new modules.Color3(...lighting.preset.key)
      rimLight.intensity = lighting.rimIntensity
      rimLight.diffuse = new modules.Color3(...lighting.preset.rim)
      scene.fogColor = new modules.Color3(...lighting.fog)
      if (modules.Color4) {
        scene.clearColor = new modules.Color4(...lighting.clear, 1)
      }
    }

    // A low-resolution glow pass blooms the authored emissive materials
    // (signal-emissive, archive-emissive, the amber/green accent glows) so
    // they read as lit signage instead of flat colored panels. It rides the
    // existing `decorations` quality flag so the efficient tier's most
    // aggressive degradation step turns it off with everything else.
    glowLayer = modules.GlowLayer
      ? new modules.GlowLayer('lunar-city:glow', scene, { mainTextureRatio: 0.5 })
      : undefined

    if (glowLayer) {
      glowLayer.intensity = 0.42
    }

    // Distance haze. Without it the settlement reads as a diorama floating in
    // a void: every district is equally crisp, so the eye gets no depth cue
    // and the far rim of the crater sits visually on top of the near one.
    // Linear fog over the camera's own bounded zoom range costs nothing —
    // it is a per-pixel lerp the fixed-function path already runs.
    scene.fogMode = FOG_MODE_LINEAR
    scene.fogColor = new modules.Color3(0.09, 0.06, 0.08)
    scene.fogStart = overview.maxRadius * 0.7
    scene.fogEnd = overview.maxRadius * 2.4

    const imageProcessing = scene.imageProcessingConfiguration

    if (imageProcessing) {
      // ACES filmic tonemapping. The authored palette drives emissive values
      // above 1.0 for the signage; without a tonemapper those clip to flat
      // white and the whole image reads as untonemapped sRGB — the single
      // clearest "engine default" tell. ACES rolls the highlights off and is
      // the same curve the reference games grade through.
      imageProcessing.toneMappingEnabled = true
      imageProcessing.toneMappingType = TONE_MAPPING_ACES
      imageProcessing.contrast = 1.34
      imageProcessing.exposure = 1.25
      imageProcessing.vignetteEnabled = true
      imageProcessing.vignetteWeight = 1.6
      imageProcessing.vignetteColor = new modules.Color3(0.04, 0.02, 0.04)
    }

    // The quality tiers have always declared `dynamicShadows: 'near'`, but no
    // shadow generator existed, so the flag toggled nothing and every district
    // floated with no contact against the terrain. One soft-filtered map on
    // the key light grounds the whole settlement; it renders only while
    // `keyLight.shadowEnabled` is true, which the efficient tier keeps off.
    shadowGenerator = modules.ShadowGenerator ? new modules.ShadowGenerator(SHADOW_MAP_SIZE, keyLight) : undefined

    if (shadowGenerator) {
      shadowGenerator.usePercentageCloserFiltering = true
      shadowGenerator.filteringQuality = 0
      shadowGenerator.darkness = 0.42
      shadowGenerator.bias = 0.0018
      shadowGenerator.normalBias = 0.012
      shadowGenerator.transparencyShadow = false
    }

    const leaderStateClips = new Map<string, LeaderStateClipMap>()
    const leaderAnimationGroups = new Map<LeaderId, ReadonlyMap<LeaderAnimationState, BabylonAnimationGroupLike>>()
    const camera = scene.activeCamera as CameraLike
    const focusAnchors = new Map<EntityKey, () => Vec3 | undefined>()
    const focusMetadata = new Map<EntityKey, () => EntityFocusMetadata | undefined>()
    const occlusionCandidates: OcclusionCandidate[] = []
    const decorationNodes: BabylonNodeLike[] = []
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
      focusMetadata.set(focus.focusEntityKey, () => focus)
      staticLods.push({ focus, lods: placed.lods })
      occlusionCandidates.push(...buildOcclusionCandidates(result, model))
      decorationNodes.push(...allImportedNodes(result).filter(isDecorationNode))

      if (shadowGenerator) {
        const renderList = shadowGenerator.getShadowMap?.()?.renderList

        for (const mesh of result.meshes) {
          // Every surface receives; only real geometry casts. The terrain is
          // the ground plane the settlement sits on, so it receives without
          // casting a redundant shadow onto itself.
          mesh.receiveShadows = true

          if (model.id !== 'terrain' && (mesh.getTotalVertices?.() ?? 0) > 0) {
            renderList?.push(mesh)
          }
        }
      }

      if (model.id === 'leaders') {
        const leaderCameraAnchors = retainLeaderIdentityMetadata(result, leaderStateClips, model, focus.cameraAnchor)

        for (const leader of readStructuredLeaders(result)) {
          leaderNodes.set(leader.id, leader.node)
        }

        const importedAnimationGroups = workerAnimationGroups(result)

        for (const leaderId of LEADER_IDS) {
          const stateClips = leaderStateClips.get(leaderId)
          const groups = new Map<LeaderAnimationState, BabylonAnimationGroupLike>()

          if (stateClips) {
            for (const state of LEADER_STATES) {
              const group = importedAnimationGroups.get(stateClips[state])

              if (group) {
                groups.set(state, group)
              }
            }
          }

          leaderAnimationGroups.set(leaderId, groups)
        }

        for (const leaderId of LEADER_IDS) {
          const leaderFocusKey = staticFocusKey('leader', leaderId)
          const cameraAnchor = leaderCameraAnchors.get(leaderId) ?? focus.cameraAnchor
          focusAnchors.set(leaderFocusKey, () => cameraAnchor)
          focusMetadata.set(leaderFocusKey, () => ({ ...focus, cameraAnchor, focusEntityKey: leaderFocusKey }))
        }
      }

      if (model.id === 'workers') {
        workerAsset = { model, result }
      }

      freezeStaticResources(result, scene.materials?.slice(materialStart) ?? [], model)
    }

    const occlusionController = createOcclusionController(occlusionCandidates)
    occlusion = occlusionController
    const quality = createQualityController('efficient')

    const applyRuntimeQuality = (): void => {
      const settings = quality.settings()

      applyQualitySettings(engine, settings)
      // Continuous idle camera motion is reserved for balanced/detailed
      // presentation. Efficient mode must be able to park the scheduler when
      // the world has no real activity, which keeps the low-power path honest.
      cameraController.setIdleEnabled(settings.tier !== 'efficient')
      keyLight.shadowEnabled = settings.dynamicShadows === 'near'

      if (glowLayer) {
        glowLayer.intensity = settings.decorations ? 0.42 : 0
      }

      for (const node of decorationNodes) {
        node.setEnabled?.(settings.decorations)
      }
    }

    applyRuntimeQuality()

    if (!workerAsset) {
      throw new Error('Lunar City manifest has no workers model')
    }

    const workerClipNames = new Set(workerAnimationGroups(workerAsset.result).keys())

    const entityRegistryController = createEntityRegistry({
      characterAssets: manifest.characterAssets,
      factory: createBabylonEntityFactory(
        workerAsset.model,
        workerAsset.result,
        modules,
        scene,
        manifest.characterAssets
      ),
      focusAnchors,
      focusMetadata,
      workerClips: workerClipNames
    })

    entityRegistry = entityRegistryController

    const navigationController = createNavigationController({
      destinations: manifest.destinations,
      query: await createRouteNavigationQuery(manifest.navigation, modules, scene, resolveAssetUrl),
      workerClips: workerClipNames
    })

    navigation = navigationController

    let projectCompoundRevision = 0

    const reconcileProjectCompounds = (snapshot: LunarCitySnapshot): void => {
      const anchors = projectCompoundsForSnapshot(snapshot)
      const desired = new Map(anchors.map(anchor => [anchor.key, anchor]))
      let changed = false

      for (const [key, node] of projectCompoundNodes) {
        if (!desired.has(key)) {
          node.dispose?.()
          projectCompoundNodes.delete(key)
          changed = true
        }
      }

      for (const anchor of anchors) {
        const existing = projectCompoundNodes.get(anchor.key)

        if (existing) {
          continue
        }

        const node = new modules.TransformNode(`lunar-city:compound:${anchor.key}`, scene)
        setNodePosition(node, anchor.position)
        tagNode(node, {
          connectionId: anchor.connectionId,
          key: anchor.key,
          kind: 'project-compound',
          projectId: anchor.projectId,
          selectable: false
        })
        projectCompoundNodes.set(anchor.key, node)
        changed = true
      }

      if (changed) {
        projectCompoundRevision += 1
        navigationController.setWalkabilityRevision(projectCompoundRevision)
      }
    }

    const destinationByEntity = new Map<EntityKey, string>()
    const authoritativeOriginByEntity = new Map<EntityKey, Vec3>()
    const activeNavigation = new Set<EntityKey>()
    let currentEntityKeys: readonly EntityKey[] = []
    let reducedMotion = false

    const applyOcclusion = (): void => {
      const focusedEntityKey = cameraController.getState().focusedEntityKey
      const selection = focusedEntityKey ? focusMetadata.get(focusedEntityKey)?.() : undefined

      occlusionController.update({ position: cameraPosition(camera) }, selection)
    }

    const hasActiveLeaderAnimation = (): boolean => {
      let active = false

      for (const [leaderId, group] of activeLeaderAnimations) {
        if (group.isPlaying === true) {
          active = true
        } else {
          activeLeaderAnimations.delete(leaderId)
        }
      }

      return active
    }

    const schedulerController = createFrameScheduler({
      captureMetrics: capturePerf,
      onFrame(frame) {
        const startedAt = typeof performance === 'undefined' ? Date.now() : performance.now()
        const previousCameraState = cameraController.getState()
        cameraController.update(frame.elapsedMs)
        const cameraState = cameraController.getState()

        if (
          previousCameraState.focusedEntityKey !== cameraState.focusedEntityKey ||
          previousCameraState.following !== cameraState.following
        ) {
          entityRegistryController.setSelection(cameraState.focusedEntityKey)
          const focusedLeaderId = cameraState.focusedEntityKey?.match(/^lunar-city:leader:(.+)$/u)?.[1]

          for (const [leaderId, node] of leaderNodes) {
            // A small scale lift is the cheapest per-entity hero treatment: it
            // makes the focused leader read above the shared scene without
            // changing global lighting or allocating another material.
            const emphasis = leaderId === focusedLeaderId ? 1.14 : 1
            node.scaling?.set(emphasis, emphasis, emphasis)
          }

          emit({ kind: 'camera-state', state: cameraState })
        }

        const settings = quality.settings()
        const currentCameraPosition = cameraPosition(camera)

        const navigationActive = navigationController.tick(frame.elapsedMs)
        entityRegistryController.syncMotion()

        for (const key of [...activeNavigation]) {
          if (!navigationController.isMoving(key)) {
            activeNavigation.delete(key)
            entityRegistryController.setMoving(key, false)
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

        entityRegistryController.applyLodPolicy(
          (_key, position, isSelected) =>
            selectLodIndex(workerAsset.model.lods, {
              distance: Math.hypot(
                currentCameraPosition.x - position.x,
                currentCameraPosition.y - position.y,
                currentCameraPosition.z - position.z
              ),
              lodAdvance: settings.lodAdvance,
              selected: isSelected
            }),
          (_key, position, isSelected) =>
            isSelected ||
            Math.hypot(
              currentCameraPosition.x - position.x,
              currentCameraPosition.y - position.y,
              currentCameraPosition.z - position.z
            ) <= animationDistanceUnits(settings.animationDistance)
        )

        applyOcclusion()
        const renderStartedAt = typeof performance === 'undefined' ? Date.now() : performance.now()
        scene.render()
        const leaderAnimationActive = hasActiveLeaderAnimation()
        const finishedAt = typeof performance === 'undefined' ? Date.now() : performance.now()
        lastFrameMs = Math.max(0, finishedAt - startedAt)
        lastWorldUpdateMs = Math.max(0, renderStartedAt - startedAt)
        worldUpdateTimestampsMs?.push(renderStartedAt)

        if (
          quality.noteFrame({ elapsedMs: Math.max(0, finishedAt - startedAt), interactive: frame.targetFps === 30 })
        ) {
          applyRuntimeQuality()
        }

        return (
          navigationActive ||
          activeNavigation.size > 0 ||
          cameraController.isTransitioning() ||
          cameraState.following ||
          cameraController.isIdleActive() ||
          cameraController.isIdlePending() ||
          entityRegistryController.hasActiveAnimations() ||
          leaderAnimationActive
        )
      },
      renderer: engine
    })

    scheduler = schedulerController

    schedulerController.bindRendererPauseState()

    await scene.whenReadyAsync()

    return {
      leaderStateClips,
      applySnapshot(snapshot) {
        if (!disposed) {
          reconcileProjectCompounds(snapshot)

          const dynamicEntities = new Map(
            [...snapshot.entities].filter(([, entity]) => !(entity.identity.kind === 'kanban' && !entity.position))
          )

          currentEntityKeys = [...dynamicEntities.values()]
            .filter(
              entity =>
                entity.position !== undefined &&
                (entity.identity.kind !== 'profile' || entity.presentation?.placement.slot !== undefined)
            )
            .map(entity => entity.key)

          for (const key of destinationByEntity.keys()) {
            if (!dynamicEntities.has(key)) {
              navigationController.cancel(key)
              destinationByEntity.delete(key)
              authoritativeOriginByEntity.delete(key)
              activeNavigation.delete(key)
            }
          }

          entityRegistryController.reconcile({ ...snapshot, entities: dynamicEntities })

          for (const [key, entity] of dynamicEntities) {
            const previousDestination = destinationByEntity.get(key)
            const previousOrigin = authoritativeOriginByEntity.get(key)
            const authoritativeOrigin = entity.authority === 'authoritative' ? entity.position : undefined

            const hasOriginCorrection =
              authoritativeOrigin !== undefined && !samePoint(previousOrigin, authoritativeOrigin)

            destinationByEntity.set(key, entity.destination)

            if (authoritativeOrigin) {
              authoritativeOriginByEntity.set(key, { ...authoritativeOrigin })
            }

            navigationController.updateArrivalAnimation(key, entity.animation)

            if (entity.authority !== 'authoritative') {
              navigationController.cancel(key)
              activeNavigation.delete(key)
              entityRegistryController.setMoving(key, false)

              continue
            }

            const presentation = entityRegistryController.navigationEntity(key)

            if (
              presentation &&
              (previousDestination !== entity.destination || hasOriginCorrection) &&
              navigationController.move(presentation, entity.destination, entity.animation)
            ) {
              activeNavigation.add(key)
              entityRegistryController.setMoving(key, true)
            } else if (previousDestination !== entity.destination || hasOriginCorrection) {
              activeNavigation.delete(key)
              entityRegistryController.setMoving(key, false)
            }
          }

          schedulerController.requestRender()
        }
      },
      dispatchCamera(intent) {
        if (disposed) {
          return
        }

        cameraController.dispatch(intent)
        const cameraState = cameraController.getState()
        entityRegistryController.setSelection(cameraState.focusedEntityKey)
        emit({ kind: 'camera-state', state: cameraState })
        schedulerController.noteInteraction(typeof performance === 'undefined' ? Date.now() : performance.now())
      },
      getCameraState() {
        return cameraController.getState()
      },
      getPerfSnapshot() {
        const schedulerMetrics = schedulerController.getMetrics()
        const babylon = perfAdapter.snapshot()
        const qualitySettings = quality.settings()

        return {
          ...babylon,
          activeAnimations: entityRegistryController.activeAnimationCount() + activeLeaderAnimations.size,
          cameraAlpha: camera.alpha,
          cameraBeta: camera.beta,
          cameraRadius: camera.radius,
          frameMs: lastFrameMs,
          frameTimestampsMs: schedulerMetrics.frameTimestampsMs,
          internalRenderScale: qualitySettings.renderScale,
          listeners: schedulerMetrics.listeners,
          rafs: schedulerMetrics.rafs,
          renderFrames: schedulerMetrics.renderFrames,
          targetFps: schedulerMetrics.targetFps,
          timers: schedulerMetrics.timers,
          qualityTier: qualitySettings.tier,
          worldUpdateMs: lastWorldUpdateMs,
          worldUpdateTimestampsMs: worldUpdateTimestampsMs ? [...worldUpdateTimestampsMs] : []
        }
      },
      getEntityCameraOrder() {
        const position = cameraPosition(camera)

        return [...currentEntityKeys].sort((left, right) => {
          const leftPosition = entityRegistryController.entity(left)?.position
          const rightPosition = entityRegistryController.entity(right)?.position

          const leftDistance = leftPosition
            ? Math.hypot(leftPosition.x - position.x, leftPosition.y - position.y, leftPosition.z - position.z)
            : Number.POSITIVE_INFINITY

          const rightDistance = rightPosition
            ? Math.hypot(rightPosition.x - position.x, rightPosition.y - position.y, rightPosition.z - position.z)
            : Number.POSITIVE_INFINITY

          return leftDistance - rightDistance || left.localeCompare(right)
        })
      },
      setLeaderAnimation(leaderId, state) {
        if (disposed) {
          return
        }

        desiredLeaderStates.set(leaderId, state)
        const active = activeLeaderAnimations.get(leaderId)

        // Idle deliberately parks the world after one dirty render. The GLB's
        // rest pose is retained without spending a continuing frame budget.
        if (state === 'idle') {
          active?.stop?.()
          activeLeaderAnimations.delete(leaderId)
          schedulerController.requestRender()

          return
        }

        // State is selected by the exact profile-owned dialogue, while this
        // map is sourced only from the corresponding GLB stateClips metadata.
        // A missing declared animation is a visual no-op, never a guessed or
        // ambient fallback clip.
        const next = leaderAnimationGroups.get(leaderId)?.get(state)

        if (!next) {
          return
        }

        if (active !== next) {
          active?.stop?.()
          activeLeaderAnimations.set(leaderId, next)
        }

        if (active !== next || next.isPlaying !== true) {
          next.start?.(!reducedMotion && CONTINUOUS_LEADER_STATES.has(state))
        }

        schedulerController.requestRender()
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
        applyRuntimeQuality()
        schedulerController.requestRender()
      },
      setTimeOfDay(value) {
        if (disposed) {
          return
        }
        timeOfDay = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0.5))
        applyWorldPalette()
        schedulerController.requestRender()
      },
      setWorldPreset(preset) {
        if (disposed || (preset !== 'luna' && preset !== 'mars' && preset !== 'terra')) {
          return
        }
        worldPreset = preset
        applyWorldPalette()
        schedulerController.requestRender()
      },
      setReducedMotion(reduced) {
        if (disposed || reducedMotion === reduced) {
          return
        }

        reducedMotion = reduced
        cameraController.setReducedMotion(reduced)
        navigationController.setReducedMotion(reduced)

        if (reduced) {
          for (const [leaderId, group] of [...activeLeaderAnimations]) {
            group.stop?.()
            activeLeaderAnimations.delete(leaderId)
          }

          for (const key of [...activeNavigation]) {
            activeNavigation.delete(key)
            entityRegistryController.setMoving(key, false)
          }

          entityRegistryController.syncMotion()
        } else {
          for (const [leaderId, state] of desiredLeaderStates) {
            if (!CONTINUOUS_LEADER_STATES.has(state)) {
              continue
            }

            const next = leaderAnimationGroups.get(leaderId)?.get(state)

            if (!next) {
              continue
            }

            const active = activeLeaderAnimations.get(leaderId)

            if (active && active !== next) {
              active.stop?.()
            }

            if (next.isPlaying === true) {
              next.stop?.()
            }

            activeLeaderAnimations.set(leaderId, next)
            next.start?.(true)
          }
        }

        schedulerController.requestRender()
      },
      setVisible(visible) {
        schedulerController.setVisible(visible)
      },
      render() {
        if (!disposed) {
          schedulerController.requestRender()
          schedulerController.tick(typeof performance === 'undefined' ? Date.now() : performance.now())
        }
      },
      dispose() {
        disposeWorld()
      }
    }
  } catch (error) {
    disposeWorld()
    throw error
  }
}
