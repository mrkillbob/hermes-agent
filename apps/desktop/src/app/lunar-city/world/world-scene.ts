import type {
  BabylonImportResultLike,
  BabylonNodeLike,
  CameraIntent,
  LeaderAnimationState,
  LeaderId,
  LeaderStateClipMap,
  LunarCityIntent,
  LunarCityNodeMetadata,
  LunarCitySnapshot,
  LunarCityWorldModules,
  ModelManifestEntry,
  QualityTier,
  WorldManifestV2
} from '../model'

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
  setQuality(tier: QualityTier): void
  render(): void
  dispose(): void
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
): void {
  const root = findNode(result, `${model.id}:root`)

  if (!root) {
    throw new Error(`Lunar City model ${model.id} is missing its runtime root`)
  }

  const placement = new modules.TransformNode(`lunar-city:placement:${model.id}`, scene)
  const { position, rotation, scale } = model.transform

  placement.position?.set(position.x, position.y, position.z)
  placement.rotation?.set(rotation.x, rotation.y, rotation.z)
  placement.scaling?.set(scale.x, scale.y, scale.z)
  placement.setPivotPoint?.(new modules.Vector3(model.pivot.x, model.pivot.y, model.pivot.z))

  const loaderConversionRoot = result.meshes.find(node => node.name === '__root__') ?? root

  loaderConversionRoot.parent = placement
  tagNode(placement, {
    cameraAnchor: model.cameraAnchor,
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
      cameraAnchor: model.cameraAnchor,
      kind: 'landmark-mesh',
      modelId: model.id,
      occlusionGroup: model.occlusionGroup,
      selectable: model.id !== 'terrain'
    })
  }
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
  model: ModelManifestEntry
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
          cameraAnchor: model.cameraAnchor,
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
      cameraAnchor: model.cameraAnchor,
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

    for (const model of manifest.models) {
      const materialStart = scene.materials?.length ?? 0
      const result = await modules.ImportMeshAsync(resolveAssetUrl(model.uri), scene)
      placeModel(result, model, modules, scene)

      if (model.id === 'leaders') {
        retainLeaderIdentityMetadata(result, leaderStateClips, model)
      }

      freezeStaticResources(result, scene.materials?.slice(materialStart) ?? [], model)
    }

    void emit
    await scene.whenReadyAsync()

    return {
      leaderStateClips,
      applySnapshot(snapshot) {
        void snapshot
      },
      dispatchCamera(intent) {
        void intent
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
        scene.dispose()
      }
    }
  } catch (error) {
    disposed = true
    scene.dispose()
    throw error
  }
}
