export type EntityKey = string & { readonly entityKey: unique symbol }
export type AuthorityState = 'authoritative' | 'partial' | 'stale' | 'unknown'
export type DestinationId =
  | 'bus'
  | 'council'
  | 'depot'
  | 'garden'
  | 'lab'
  | 'library'
  | 'project'
  | 'review'
  | 'triage'
  | 'unavailable'
  | 'unknown'
export type QualityTier = 'efficient' | 'balanced' | 'detailed'

export interface Vec3 {
  x: number
  y: number
  z: number
}

export type EntityIdentity =
  | { kind: 'profile'; connectionId: string; profile: string }
  | { kind: 'session'; connectionId: string; profile: string; sessionId: string }
  | { kind: 'subagent'; connectionId: string; profile: string; sessionId: string; subagentId: string }
  | { kind: 'kanban'; connectionId: string; board: string; taskId: string; runId?: string; workerId?: string }

export interface SourceHealth {
  source: string
  authority: AuthorityState
  observedAt: number
  error?: string
}

export interface CameraLandmark {
  id: string
  alpha: number
  beta: number
  radius: number
  target: Vec3
  minBeta: number
  maxBeta: number
  minRadius: number
  maxRadius: number
}

export interface WorldBounds {
  min: Vec3
  max: Vec3
}

export interface ModelStatistics {
  animationClips: readonly string[]
  drawCalls: number
  materials: number
  meshes: number
  nodes: number
  textures: number
  triangles: number
  budget: {
    maxDrawCalls: number
    maxGpuMiB: number
    maxMaterials: number
    maxTextures: number
    maxTriangles: number
  }
  bytes: number
  extent: readonly [number, number, number]
  gpuMiB: number
  sha256: string
}

export interface ModelManifestEntry {
  id: string
  uri: string
  maxTriangles: number
  maxDrawCalls: number
  maxMaterials: number
  maxTextures: number
  maxGpuMiB: number
  requiredNodes: readonly string[]
  requiredClips: readonly string[]
  lods: readonly { distance: number; node: string }[]
  transform: { position: Vec3; rotation: Vec3; scale: Vec3 }
  pivot: Vec3
  bounds: WorldBounds
  anchors: Readonly<Record<string, Vec3>>
  cameraAnchor: Vec3
  occlusionGroup: string
  collision: { kind: string; navigationArea: string }
  materialSlots: readonly string[]
  instancing?: { eligible: boolean; variants: readonly string[] }
  statistics: ModelStatistics
}

export interface NavigationManifest {
  meshUri: string
  areas: readonly string[]
  links: readonly { from: Vec3; to: Vec3; bidirectional: boolean }[]
}

export interface TextureManifestEntry {
  bytes: number
  sha256: string
  source: string
  uri: string
}

export interface MaterialManifestEntry {
  id: string
  palette: string
  maxTextures: number
}

export interface ProjectSlotManifestEntry {
  id: string
  position: Vec3
  bounds: WorldBounds
  navigationLink: { from: Vec3; to: Vec3; bidirectional: boolean }
}

export interface QualityBudget {
  drawCalls: number
  visibleTriangles: number
  gpuMiB: number
}

export interface WorldManifestV2 {
  version: 2
  assetVersion: '2.0.0'
  source: { sha256: string }
  materials: readonly MaterialManifestEntry[]
  models: readonly ModelManifestEntry[]
  textures: readonly TextureManifestEntry[]
  camera: { overview: CameraLandmark; bounds: WorldBounds; followOffset: Vec3 }
  navigation: NavigationManifest
  destinations: Readonly<Record<string, Vec3>>
  projectSlots: readonly ProjectSlotManifestEntry[]
  qualityBudgets: {
    balancedOverview: QualityBudget
    balancedWorkerFocus: QualityBudget
  }
  generatedAssetPack: Readonly<Record<string, unknown>>
}

export interface LunarEntity {
  key: EntityKey
  identity: EntityIdentity
  observedAt: number
  authority: AuthorityState
  destination: DestinationId
  animation: string
  /** Presentation position only; it never represents work progress. */
  position?: Vec3
  projectId?: string
  /** Declared worker colour/accessory variant, never a display-name identity. */
  variant?: string
}

export interface LunarCitySnapshot {
  revision: number
  observedAt: number
  entities: ReadonlyMap<EntityKey, LunarEntity>
  sources: readonly SourceHealth[]
}

export type CameraIntent =
  | { kind: 'orbit'; deltaAlpha: number; deltaBeta: number }
  | { kind: 'pan'; deltaX: number; deltaZ: number }
  | { kind: 'zoom'; delta: number }
  | { kind: 'focus'; entityKey: EntityKey; follow: boolean }
  | { kind: 'clear-focus' }
  | { kind: 'return-to-city' }

export interface CameraControlState {
  focusedEntityKey: EntityKey | undefined
  following: boolean
}

export type LunarCityIntent =
  | { kind: 'camera-state'; state: CameraControlState }
  | { kind: 'clear-selection' }
  | { kind: 'select-focus'; entityKey: EntityKey }
  | { kind: 'select-landmark'; landmarkId: string }

export type LeaderAnimationState = 'acknowledging' | 'idle' | 'listening' | 'talking' | 'thinking' | 'unavailable'
export type LeaderId = 'owl' | 'fox' | 'badger' | 'otter' | 'bird' | 'stag'

export type LeaderStateClipMap = Readonly<Record<LeaderAnimationState, string>>

export interface LunarCityLandmarkMetadata {
  cameraAnchor: Vec3
  focusEntityKey: EntityKey
  kind: 'landmark' | 'landmark-mesh'
  modelId: string
  occlusionGroup: string
  selectable: boolean
}

export interface LunarCityLodMetadata {
  distance: number
  kind: 'lod'
  modelId: string
}

export interface LunarCityLeaderPickMetadata {
  cameraAnchor: Vec3
  focusEntityKey: EntityKey
  kind: 'leader'
  leaderId: LeaderId
  modelId: 'leaders'
  occlusionGroup: string
  selectable: true
  stateClips: LeaderStateClipMap
}

/** Exact worker identity attached to every clone and hardware instance. */
export interface LunarCityWorkerPickMetadata {
  cameraAnchor: Vec3
  entityKey: EntityKey
  focusEntityKey: EntityKey
  identity: EntityIdentity
  kind: 'worker'
  modelId: 'workers'
  occlusionGroup: string
  selectable: true
  variant?: string
}

export interface LunarCitySharedLeaderSurfaceMetadata {
  cameraAnchor: Vec3
  focusEntityKey: EntityKey
  kind: 'leader-shared-surface'
  modelId: 'leaders'
  occlusionGroup: string
  selectable: false
}

export type LunarCityNodeMetadata =
  | LunarCityLandmarkMetadata
  | LunarCityLeaderPickMetadata
  | LunarCityLodMetadata
  | LunarCitySharedLeaderSurfaceMetadata
  | LunarCityWorkerPickMetadata

export interface LunarCityWorldHandle {
  readonly leaderStateClips: ReadonlyMap<string, LeaderStateClipMap>
  applySnapshot(snapshot: LunarCitySnapshot): void
  dispatchCamera(intent: CameraIntent): void
  getCameraState(): CameraControlState
  setQuality(tier: QualityTier): void
  destroy(): void
}

export interface BabylonVector3Like {
  readonly x: number
  readonly y: number
  readonly z: number
}

export interface BabylonMutableVectorLike {
  x?: number
  y?: number
  z?: number
  set(x: number, y: number, z: number): void
}

export interface BabylonNodeLike {
  name: string
  metadata?: Record<string, unknown> & { lunarCity?: LunarCityNodeMetadata }
  parent?: BabylonNodeLike | null
  position?: BabylonMutableVectorLike
  rotation?: BabylonMutableVectorLike
  scaling?: BabylonMutableVectorLike
  clone?(name: string, parent?: BabylonNodeLike | null, doNotCloneChildren?: boolean): BabylonNodeLike | null
  dispose?(): void
  isEnabled?(): boolean
  setEnabled?(enabled: boolean): void
  setPivotPoint?(point: BabylonVector3Like): void
}

export interface BabylonMeshLike extends BabylonNodeLike {
  freezeWorldMatrix?(): void
  getIndices?(): readonly number[] | null
  getVerticesData?(kind: string): readonly number[] | null
  getWorldMatrix?(): { m: readonly number[] }
  isPickable?: boolean
  material?: {
    alpha?: number
    clone?(name: string): unknown
  } | null
}

export interface BabylonMaterialLike {
  freeze?(): void
}

export interface BabylonEngineLike {
  dispose(): void
  resize(): void
  setHardwareScalingLevel?(level: number): void
  stopRenderLoop?(): void
}

export interface BabylonSceneLike {
  activeCamera?: unknown
  ambientColor?: unknown
  materials?: readonly BabylonMaterialLike[]
  dispose(): void
  render(): void
  whenReadyAsync(): Promise<void>
  pick?(x: number, y: number): { pickedMesh?: BabylonNodeLike } | undefined
}

export interface BabylonImportResultLike {
  meshes: readonly BabylonMeshLike[]
  transformNodes: readonly BabylonNodeLike[]
  animationGroups: readonly unknown[]
}

export interface BabylonLightLike {
  intensity: number
}

export interface RecastPathLike {
  __destroy__?(): void
  delete?(): void
  destroy?(): void
  getPoint(index: number): BabylonVector3Like | undefined
  getPointCount(): number
}

export interface RecastNavMeshLike {
  build(
    positions: Float32Array,
    positionCount: number,
    indices: Uint32Array,
    indexCount: number,
    configuration: unknown
  ): void
  computePath(from: unknown, to: unknown): RecastPathLike
  destroy?(): void
}

export interface RecastWrapperLike {
  __destroy__?(): void
  delete?(): void
  destroy?(): void
}

export interface RecastConfigLike extends Record<string, unknown>, RecastWrapperLike {
  set_bmax?(index: number, value: number): void
  set_bmin?(index: number, value: number): void
}

export interface RecastRuntimeLike {
  NavMesh: new () => RecastNavMeshLike
  Vec3: new (x: number, y: number, z: number) => BabylonVector3Like & RecastWrapperLike
  rcConfig: new () => RecastConfigLike
}

export interface LunarCityWorldModules {
  Engine: new (
    canvas: HTMLCanvasElement,
    antialias: boolean,
    options: { powerPreference: 'low-power'; preserveDrawingBuffer: false; stencil: false }
  ) => BabylonEngineLike
  Scene: new (engine: BabylonEngineLike) => BabylonSceneLike
  Vector3: new (x: number, y: number, z: number) => BabylonVector3Like
  Color3: new (red: number, green: number, blue: number) => unknown
  ArcRotateCamera: new (
    name: string,
    alpha: number,
    beta: number,
    radius: number,
    target: BabylonVector3Like,
    scene: BabylonSceneLike
  ) => unknown
  DirectionalLight: new (name: string, direction: BabylonVector3Like, scene: BabylonSceneLike) => BabylonLightLike
  TransformNode: new (name: string, scene: BabylonSceneLike) => BabylonNodeLike
  ImportMeshAsync(source: string, scene: BabylonSceneLike): Promise<BabylonImportResultLike>
  createRecastNavigation?(): Promise<RecastRuntimeLike>
}
