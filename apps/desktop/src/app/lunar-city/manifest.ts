import type {
  CameraLandmark,
  MaterialManifestEntry,
  ModelManifestEntry,
  ModelStatistics,
  NavigationManifest,
  ProjectSlotManifestEntry,
  QualityBudget,
  TextureManifestEntry,
  Vec3,
  WorldBounds,
  WorldManifestV2
} from './model'

export const APPROVED_SOURCE_SHA256 = '248e8d40946b08b9f74f4b2ddd0ba17e4f17fd054260189972164c5d6ca70590'
const APPROVED_SOURCE_FILENAME = 'moon-settlement-approved.jpg'
const MODEL_URI = /^models\/[a-z0-9][a-z0-9._-]*\.glb$/i
const TEXTURE_URI = /^textures\/[a-z0-9][a-z0-9._-]*\.(?:jpe?g|ktx2|png|webp)$/i

function record(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${path} must be an object`)
  }

  return value as Record<string, unknown>
}

function array(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${path} must be an array`)
  }

  return value
}

function string(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${path} must be a non-empty string`)
  }

  return value
}

function finite(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${path} must be a finite number`)
  }

  return value
}

function natural(value: unknown, path: string): number {
  const parsed = finite(value, path)

  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`${path} must be a non-negative integer`)
  }

  return parsed
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') {
    throw new Error(`${path} must be a boolean`)
  }

  return value
}

function strings(value: unknown, path: string): readonly string[] {
  return array(value, path).map((item, index) => string(item, `${path}[${index}]`))
}

function vec3(value: unknown, path: string): Vec3 {
  const items = array(value, path)

  if (items.length !== 3) {
    throw new Error(`${path} must contain exactly three values`)
  }

  return {
    x: finite(items[0], `${path}[0]`),
    y: finite(items[1], `${path}[1]`),
    z: finite(items[2], `${path}[2]`)
  }
}

function bounds(value: unknown, path: string): WorldBounds {
  const item = record(value, path)

  return { min: vec3(item.min, `${path}.min`), max: vec3(item.max, `${path}.max`) }
}

function decodedUri(value: string): string {
  let result = value

  for (let index = 0; index < 3; index += 1) {
    try {
      const decoded = decodeURIComponent(result)

      if (decoded === result) {
        break
      }

      result = decoded
    } catch {
      break
    }
  }

  return result
}

function isApprovedSourceUri(value: string): boolean {
  const normalized = decodedUri(value).replaceAll('\\', '/').toLowerCase()
  const pathname = normalized.split(/[?#]/, 1)[0] ?? normalized

  return pathname.endsWith(`/${APPROVED_SOURCE_FILENAME}`) || pathname === APPROVED_SOURCE_FILENAME
}

function scanRuntimeUris(value: unknown, path = 'manifest'): void {
  if (Array.isArray(value)) {
    value.forEach((item, index) => scanRuntimeUris(item, `${path}[${index}]`))

    return
  }

  if (!value || typeof value !== 'object') {
    return
  }

  for (const [key, item] of Object.entries(value)) {
    const itemPath = `${path}.${key}`

    if (key.toLowerCase().endsWith('uri') && typeof item === 'string' && isApprovedSourceUri(item)) {
      throw new Error(`approved source cannot be a runtime asset (${itemPath})`)
    }

    scanRuntimeUris(item, itemPath)
  }
}

function runtimeAssetUri(value: unknown, path: string, kind: 'model' | 'texture'): string {
  const uri = string(value, path)

  if (isApprovedSourceUri(uri)) {
    throw new Error('approved source cannot be a runtime asset')
  }

  const pattern = kind === 'model' ? MODEL_URI : TEXTURE_URI

  if (!pattern.test(uri) || uri.includes('..') || uri.includes('\\') || /^[a-z][a-z\d+.-]*:/i.test(uri)) {
    throw new Error(`${path} must be a relative v2 ${kind} asset`)
  }

  return uri
}

function statistics(value: unknown, path: string): ModelStatistics {
  const item = record(value, path)
  const budget = record(item.budget, `${path}.budget`)
  const extent = array(item.extent, `${path}.extent`)

  if (extent.length !== 3) {
    throw new Error(`${path}.extent must contain exactly three values`)
  }

  return {
    animationClips: strings(item.animationClips, `${path}.animationClips`),
    drawCalls: natural(item.drawCalls, `${path}.drawCalls`),
    materials: natural(item.materials, `${path}.materials`),
    meshes: natural(item.meshes, `${path}.meshes`),
    nodes: natural(item.nodes, `${path}.nodes`),
    textures: natural(item.textures, `${path}.textures`),
    triangles: natural(item.triangles, `${path}.triangles`),
    budget: {
      maxDrawCalls: natural(budget.maxDrawCalls, `${path}.budget.maxDrawCalls`),
      maxGpuMiB: finite(budget.maxGpuMiB, `${path}.budget.maxGpuMiB`),
      maxMaterials: natural(budget.maxMaterials, `${path}.budget.maxMaterials`),
      maxTextures: natural(budget.maxTextures, `${path}.budget.maxTextures`),
      maxTriangles: natural(budget.maxTriangles, `${path}.budget.maxTriangles`)
    },
    bytes: natural(item.bytes, `${path}.bytes`),
    extent: [
      finite(extent[0], `${path}.extent[0]`),
      finite(extent[1], `${path}.extent[1]`),
      finite(extent[2], `${path}.extent[2]`)
    ],
    gpuMiB: finite(item.gpuMiB, `${path}.gpuMiB`),
    sha256: string(item.sha256, `${path}.sha256`)
  }
}

function modelEntry(value: unknown, index: number): ModelManifestEntry {
  const path = `models[${index}]`
  const item = record(value, path)
  const transform = record(item.transform, `${path}.transform`)
  const anchorsValue = record(item.anchors, `${path}.anchors`)

  const anchors = Object.fromEntries(
    Object.entries(anchorsValue).map(([name, anchor]) => [name, vec3(anchor, `${path}.anchors.${name}`)])
  )

  const collision = record(item.collision, `${path}.collision`)
  const instancingValue = item.instancing === undefined ? undefined : record(item.instancing, `${path}.instancing`)

  return {
    id: string(item.id, `${path}.id`),
    uri: runtimeAssetUri(item.uri, `${path}.uri`, 'model'),
    maxTriangles: natural(item.maxTriangles, `${path}.maxTriangles`),
    maxDrawCalls: natural(item.maxDrawCalls, `${path}.maxDrawCalls`),
    maxMaterials: natural(item.maxMaterials, `${path}.maxMaterials`),
    maxTextures: natural(item.maxTextures, `${path}.maxTextures`),
    maxGpuMiB: finite(item.maxGpuMiB, `${path}.maxGpuMiB`),
    requiredNodes: strings(item.requiredNodes, `${path}.requiredNodes`),
    requiredClips: strings(item.requiredClips, `${path}.requiredClips`),
    lods: array(item.lods, `${path}.lods`).map((lod, lodIndex) => {
      const entry = record(lod, `${path}.lods[${lodIndex}]`)

      return {
        distance: finite(entry.distance, `${path}.lods[${lodIndex}].distance`),
        node: string(entry.node, `${path}.lods[${lodIndex}].node`)
      }
    }),
    transform: {
      position: vec3(transform.position, `${path}.transform.position`),
      rotation: vec3(transform.rotation, `${path}.transform.rotation`),
      scale: vec3(transform.scale, `${path}.transform.scale`)
    },
    pivot: vec3(item.pivot, `${path}.pivot`),
    bounds: bounds(item.bounds, `${path}.bounds`),
    anchors,
    cameraAnchor: vec3(item.cameraAnchor, `${path}.cameraAnchor`),
    occlusionGroup: string(item.occlusionGroup, `${path}.occlusionGroup`),
    collision: {
      kind: string(collision.kind, `${path}.collision.kind`),
      navigationArea: string(collision.navigationArea, `${path}.collision.navigationArea`)
    },
    materialSlots: strings(item.materialSlots, `${path}.materialSlots`),
    ...(instancingValue
      ? {
          instancing: {
            eligible: boolean(instancingValue.eligible, `${path}.instancing.eligible`),
            variants: strings(instancingValue.variants, `${path}.instancing.variants`)
          }
        }
      : {}),
    statistics: statistics(item.statistics, `${path}.statistics`)
  }
}

function cameraLandmark(value: unknown, path: string): CameraLandmark {
  const item = record(value, path)

  return {
    id: string(item.id, `${path}.id`),
    alpha: finite(item.alpha, `${path}.alpha`),
    beta: finite(item.beta, `${path}.beta`),
    radius: finite(item.radius, `${path}.radius`),
    target: vec3(item.target, `${path}.target`),
    minBeta: finite(item.minBeta, `${path}.minBeta`),
    maxBeta: finite(item.maxBeta, `${path}.maxBeta`),
    minRadius: finite(item.minRadius, `${path}.minRadius`),
    maxRadius: finite(item.maxRadius, `${path}.maxRadius`)
  }
}

function navigation(value: unknown): NavigationManifest {
  const item = record(value, 'navigation')

  return {
    meshUri: runtimeAssetUri(item.meshUri, 'navigation.meshUri', 'model'),
    areas: strings(item.areas, 'navigation.areas'),
    links: array(item.links, 'navigation.links').map((link, index) => {
      const entry = record(link, `navigation.links[${index}]`)

      return {
        from: vec3(entry.from, `navigation.links[${index}].from`),
        to: vec3(entry.to, `navigation.links[${index}].to`),
        bidirectional: boolean(entry.bidirectional, `navigation.links[${index}].bidirectional`)
      }
    })
  }
}

function texture(value: unknown, index: number): TextureManifestEntry {
  const path = `textures[${index}]`
  const item = record(value, path)

  return {
    bytes: natural(item.bytes, `${path}.bytes`),
    sha256: string(item.sha256, `${path}.sha256`),
    source: string(item.source, `${path}.source`),
    uri: runtimeAssetUri(item.uri, `${path}.uri`, 'texture')
  }
}

function material(value: unknown, index: number): MaterialManifestEntry {
  const path = `materials[${index}]`
  const item = record(value, path)

  return {
    id: string(item.id, `${path}.id`),
    palette: string(item.palette, `${path}.palette`),
    maxTextures: natural(item.maxTextures, `${path}.maxTextures`)
  }
}

function projectSlot(value: unknown, index: number): ProjectSlotManifestEntry {
  const path = `projectSlots[${index}]`
  const item = record(value, path)
  const link = record(item.navigationLink, `${path}.navigationLink`)

  return {
    id: string(item.id, `${path}.id`),
    position: vec3(item.position, `${path}.position`),
    bounds: bounds(item.bounds, `${path}.bounds`),
    navigationLink: {
      from: vec3(link.from, `${path}.navigationLink.from`),
      to: vec3(link.to, `${path}.navigationLink.to`),
      bidirectional: boolean(link.bidirectional, `${path}.navigationLink.bidirectional`)
    }
  }
}

function qualityBudget(value: unknown, path: string): QualityBudget {
  const item = record(value, path)

  return {
    drawCalls: natural(item.drawCalls, `${path}.drawCalls`),
    visibleTriangles: natural(item.visibleTriangles, `${path}.visibleTriangles`),
    gpuMiB: finite(item.gpuMiB, `${path}.gpuMiB`)
  }
}

export function assertWorldManifestRuntimeAssets(
  manifest: Pick<WorldManifestV2, 'models' | 'navigation' | 'textures'>
): void {
  scanRuntimeUris(manifest)
  manifest.models.forEach((model, index) => runtimeAssetUri(model.uri, `models[${index}].uri`, 'model'))
  runtimeAssetUri(manifest.navigation.meshUri, 'navigation.meshUri', 'model')
  manifest.textures.forEach((entry, index) => runtimeAssetUri(entry.uri, `textures[${index}].uri`, 'texture'))
}

export function parseWorldManifest(value: unknown): WorldManifestV2 {
  scanRuntimeUris(value)
  const root = record(value, 'manifest')

  if (root.version !== 2) {
    throw new Error('version must equal 2')
  }

  if (root.assetVersion !== '2.0.0') {
    throw new Error('assetVersion must equal 2.0.0')
  }

  const source = record(root.source, 'source')

  if (source.sha256 !== APPROVED_SOURCE_SHA256) {
    throw new Error('approved source digest mismatch')
  }

  const models = array(root.models, 'models').map(modelEntry)
  const modelIds = new Set<string>()

  for (const model of models) {
    if (modelIds.has(model.id)) {
      throw new Error(`model id ${model.id} is duplicated`)
    }

    modelIds.add(model.id)
  }

  const cameraValue = record(root.camera, 'camera')
  const destinationsValue = record(root.destinations, 'destinations')
  const budgets = record(root.qualityBudgets, 'qualityBudgets')

  const manifest: WorldManifestV2 = {
    version: 2,
    assetVersion: '2.0.0',
    source: { sha256: APPROVED_SOURCE_SHA256 },
    materials: array(root.materials, 'materials').map(material),
    models,
    textures: array(root.textures, 'textures').map(texture),
    camera: {
      overview: cameraLandmark(cameraValue.overview, 'camera.overview'),
      bounds: bounds(cameraValue.bounds, 'camera.bounds'),
      followOffset: vec3(cameraValue.followOffset, 'camera.followOffset')
    },
    navigation: navigation(root.navigation),
    destinations: Object.fromEntries(
      Object.entries(destinationsValue).map(([name, position]) => [name, vec3(position, `destinations.${name}`)])
    ),
    projectSlots: array(root.projectSlots, 'projectSlots').map(projectSlot),
    qualityBudgets: {
      balancedOverview: qualityBudget(budgets.balancedOverview, 'qualityBudgets.balancedOverview'),
      balancedWorkerFocus: qualityBudget(budgets.balancedWorkerFocus, 'qualityBudgets.balancedWorkerFocus')
    },
    generatedAssetPack: { ...record(root.generatedAssetPack, 'generatedAssetPack') }
  }

  assertWorldManifestRuntimeAssets(manifest)

  return manifest
}

export async function loadWorldManifest(url: string, signal?: AbortSignal): Promise<WorldManifestV2> {
  const response = await fetch(url, { signal })

  if (!response.ok) {
    throw new Error(`Lunar City manifest request failed: ${response.status} ${response.statusText}`)
  }

  return parseWorldManifest(await response.json())
}
