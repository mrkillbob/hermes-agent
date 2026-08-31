import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { NodeIO } from '@gltf-transform/core'
import { ALL_EXTENSIONS } from '@gltf-transform/extensions'

export const APPROVED_SHA = '248e8d40946b08b9f74f4b2ddd0ba17e4f17fd054260189972164c5d6ca70590'
export const APPROVED_DIMENSIONS = Object.freeze({ width: 1280, height: 910 })
export const APPROVED_SOURCE_URI = '../moon-settlement-approved.jpg'
export const ASSET_VERSION = '2.0.0'

const TRIANGLES = 4
const HERMES_GROUPS = Object.freeze([
  'Acceptance & Release',
  'Archive and Acquisition',
  'Arts Studio',
  'CI Repair Triage',
  'Community Intake',
  'Content Studio',
  'Control Plane Incidents',
  'Core Runtime & UX Repairs',
  'Data & Performance Repairs',
  'Editorial Desk',
  'Engineering Guild',
  'Federation Council',
  'Knowledge Commons',
  'Memory Stewardship',
  'Operations and Release',
  'PR Merge Train',
  'Research Lab',
  'Research Review Board',
  'Upstream Hermes Maintenance'
])
const WORKER_SIGNATURE_FIELDS = Object.freeze([
  ['body', 'bodies'],
  ['head', 'heads'],
  ['silhouetteAccessory', 'silhouetteAccessories'],
  ['palette', 'palettes'],
  ['emblem', 'emblems']
])
const QUALITY_CEILINGS = Object.freeze({
  balancedOverview: { drawCalls: 180, gpuMiB: 256, visibleTriangles: 1_500_000 },
  balancedWorkerFocus: { drawCalls: 220, gpuMiB: 256, visibleTriangles: 2_000_000 }
})
const MODEL_BUDGETS = Object.freeze([
  ['maxTriangles', 'triangle'],
  ['maxDrawCalls', 'draw-call'],
  ['maxMaterials', 'material'],
  ['maxTextures', 'texture'],
  ['maxGpuMiB', 'GPU MiB']
])

export function validationResult(errors) {
  return { ok: errors.length === 0, errors: Object.freeze(errors) }
}

function isApprovedSourceUri(uri) {
  return typeof uri === 'string' && /moon-settlement-approved\.jpg$/i.test(uri)
}

function isNonEmptyString(value) {
  return typeof value === 'string' && value.length > 0
}

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isVec3(value) {
  return Array.isArray(value) && value.length === 3 && value.every(Number.isFinite)
}

function isBounds(value) {
  return (
    value &&
    isVec3(value.min) &&
    isVec3(value.max) &&
    value.min.every((coordinate, index) => coordinate < value.max[index])
  )
}

function sameVec3(left, right) {
  return isVec3(left) && isVec3(right) && left.every((coordinate, index) => coordinate === right[index])
}

function linkEquals(left, right) {
  return (
    left?.bidirectional === right?.bidirectional && sameVec3(left?.from, right?.from) && sameVec3(left?.to, right?.to)
  )
}

function countTriangles(primitive) {
  if (primitive.getMode?.() !== TRIANGLES) return 0
  const indices = primitive.getIndices?.()
  if (indices) return indices.getCount() / 3
  return primitive.getAttribute?.('POSITION')?.getCount?.() / 3 || 0
}

export async function inspectGlb(io, uri) {
  const root = (await io.read(uri)).getRoot()
  return {
    clips: new Set(
      root
        .listAnimations()
        .map(animation => animation.getName())
        .filter(Boolean)
    ),
    nodes: new Set(
      root
        .listNodes()
        .map(node => node.getName())
        .filter(Boolean)
    ),
    textureUris: new Set(
      root
        .listTextures()
        .map(texture => texture.getURI?.())
        .filter(Boolean)
    ),
    triangles: root
      .listMeshes()
      .flatMap(mesh => mesh.listPrimitives())
      .reduce((total, primitive) => total + countTriangles(primitive), 0)
  }
}

export function requireNamedNodes(model, stats, errors) {
  for (const node of model.requiredNodes ?? [])
    if (!stats.nodes.has(node)) errors.push(`${model.id} missing node ${node}`)
}

export function requireClips(model, stats, errors) {
  for (const clip of model.requiredClips ?? [])
    if (!stats.clips.has(clip)) errors.push(`${model.id} missing clip ${clip}`)
}

export function requireLods(model, stats, errors) {
  for (const lod of model.lods ?? [])
    if (!stats.nodes.has(lod.node)) errors.push(`${model.id} missing LOD node ${lod.node}`)
}

function validateModelShape(model, root, errors) {
  const id = isNonEmptyString(model?.id) ? model.id : 'model'
  for (const [key, label] of MODEL_BUDGETS) {
    if (!Number.isFinite(model?.[key]) || model[key] < 0) errors.push(`${id} requires a ${label} budget`)
  }
  if (
    !Array.isArray(model?.requiredNodes) ||
    model.requiredNodes.length === 0 ||
    !model.requiredNodes.every(isNonEmptyString)
  )
    errors.push(`${id} requires named nodes`)
  if (!Array.isArray(model?.requiredClips) || !model.requiredClips.every(isNonEmptyString))
    errors.push(`${id} clips are invalid`)
  if (!Array.isArray(model?.lods) || model.lods.length === 0) {
    errors.push(`${id} requires at least one LOD`)
  } else {
    let previousDistance = -Infinity
    for (const lod of model.lods) {
      if (!Number.isFinite(lod?.distance) || lod.distance < 0 || !isNonEmptyString(lod.node)) {
        errors.push(`${id} LOD is invalid`)
        continue
      }
      if (lod.distance <= previousDistance) errors.push(`${id} LOD distances must be strictly increasing`)
      previousDistance = lod.distance
    }
  }
  if (
    !isVec3(model?.transform?.position) ||
    !isVec3(model.transform.rotation) ||
    !isVec3(model.transform.scale) ||
    model.transform.scale.some(value => value <= 0)
  )
    errors.push(`${id} transform is invalid`)
  if (!isVec3(model?.pivot)) errors.push(`${id} pivot is invalid`)
  if (!isBounds(model?.bounds)) errors.push(`${id} bounds are invalid`)
  if (!isVec3(model?.anchors?.foot) || !isVec3(model.anchors.camera)) errors.push(`${id} semantic anchors are invalid`)
  if (!isVec3(model?.cameraAnchor) || !sameVec3(model.cameraAnchor, model?.anchors?.camera))
    errors.push(`${id} camera anchor is invalid`)
  if (!isNonEmptyString(model?.occlusionGroup)) errors.push(`${id} occlusion group is required`)
  if (!isNonEmptyString(model?.collision?.kind) || !isNonEmptyString(model.collision.navigationArea)) {
    errors.push(`${id} collision is invalid`)
  } else if (!(root.navigation?.areas ?? []).includes(model.collision.navigationArea)) {
    errors.push(`${id} navigation area ${model.collision.navigationArea} is not declared`)
  }
  if (!Array.isArray(model?.materialSlots) || model.materialSlots.length === 0) {
    errors.push(`${id} requires material slots`)
  } else {
    const materialIds = new Set((Array.isArray(root.materials) ? root.materials : []).map(material => material.id))
    for (const materialId of model.materialSlots)
      if (!materialIds.has(materialId)) errors.push(`${id} material slot ${materialId} is not declared`)
  }
}

function validLandmark(landmark) {
  return (
    landmark &&
    isNonEmptyString(landmark.id) &&
    ['alpha', 'beta', 'radius', 'minBeta', 'maxBeta', 'minRadius', 'maxRadius'].every(key =>
      Number.isFinite(landmark[key])
    ) &&
    landmark.minBeta <= landmark.beta &&
    landmark.beta <= landmark.maxBeta &&
    landmark.minRadius <= landmark.radius &&
    landmark.radius <= landmark.maxRadius &&
    isVec3(landmark.target)
  )
}

export function validateCameraLandmarks(camera, errors) {
  if (!validLandmark(camera?.overview)) errors.push('camera overview landmark is invalid')
  if (!isBounds(camera?.bounds)) {
    errors.push('camera bounds are invalid')
  } else if (
    validLandmark(camera.overview) &&
    camera.overview.target.some((value, index) => value < camera.bounds.min[index] || value > camera.bounds.max[index])
  ) {
    errors.push('camera overview target falls outside bounds')
  }
}

export function validateNavigation(navigation, errors) {
  if (isApprovedSourceUri(navigation?.meshUri)) errors.push('approved source cannot be a runtime asset')
  if (typeof navigation?.meshUri !== 'string' || !navigation.meshUri.endsWith('.glb'))
    errors.push('navigation mesh URI must be a GLB')
  if (!Array.isArray(navigation?.areas) || navigation.areas.length === 0 || !navigation.areas.every(isNonEmptyString))
    errors.push('navigation requires declared areas')
  if (!Array.isArray(navigation?.links) || navigation.links.length === 0) {
    errors.push('navigation requires at least one link')
    return
  }
  for (const link of navigation.links)
    if (!isVec3(link.from) || !isVec3(link.to) || typeof link.bidirectional !== 'boolean')
      errors.push('navigation link is invalid')
}

function validateMaterials(root, errors) {
  const materials = Array.isArray(root?.materials) ? root.materials : []
  const ids = materials.map(material => material?.id)
  if (
    !Array.isArray(root?.materials) ||
    ids.length === 0 ||
    !ids.every(isNonEmptyString) ||
    new Set(ids).size !== ids.length
  )
    errors.push('materials are invalid')
}

function validateModelIds(root, errors) {
  if (!Array.isArray(root?.models)) {
    errors.push('models must be an array')
    return []
  }
  if (root.models.length === 0) errors.push('at least one runtime model is required')
  const ids = new Set()
  for (const model of root.models) {
    if (!isNonEmptyString(model?.id)) {
      errors.push('model id is required')
      continue
    }
    if (ids.has(model.id)) errors.push(`duplicate model id ${model.id}`)
    ids.add(model.id)
  }
  return root.models
}

function validateDestinations(root, errors) {
  const entries = Object.entries(root.destinations ?? {})
  if (entries.length === 0) errors.push('destinations are required')
  for (const [id, point] of entries)
    if (!isNonEmptyString(id) || !isVec3(point)) errors.push(`destination ${id} is invalid`)
}

function validateProjectSlots(root, errors) {
  if (!Array.isArray(root.projectSlots) || root.projectSlots.length === 0) {
    errors.push('project slots are required')
    return
  }
  const ids = new Set()
  for (const slot of root.projectSlots) {
    const valid =
      isNonEmptyString(slot?.id) &&
      !ids.has(slot.id) &&
      isVec3(slot.position) &&
      isBounds(slot.bounds) &&
      root.navigation?.links?.some(link => linkEquals(link, slot.navigationLink))
    if (!valid) errors.push(`project slot ${slot?.id ?? 'unknown'} is invalid`)
    ids.add(slot?.id)
  }
}

function validateQualityBudgets(root, errors) {
  for (const [id, limits] of Object.entries(QUALITY_CEILINGS)) {
    const budget = root.qualityBudgets?.[id]
    const label = id === 'balancedOverview' ? 'balanced overview' : 'balanced worker focus'
    for (const [key, limit] of Object.entries(limits)) {
      if (!Number.isFinite(budget?.[key]) || budget[key] < 0) {
        errors.push(`${label} quality budget is invalid`)
      } else if (budget[key] > limit) {
        const unit = key === 'drawCalls' ? 'draw calls' : key === 'gpuMiB' ? 'MiB GPU memory' : 'visible triangles'
        errors.push(`${label} exceeds ${limit} ${unit}`)
      }
    }
  }
}

function validateUniqueLeaderIdentities(leaders, errors) {
  if (!Array.isArray(leaders) || leaders.length !== 6) {
    errors.push('character assets require exactly six leaders')
    return
  }
  for (const field of ['species', 'silhouetteId', 'visualId']) {
    const seen = new Set()
    for (const leader of leaders) {
      const value = leader?.[field]
      if (!isNonEmptyString(leader?.id) || !isNonEmptyString(value)) {
        errors.push(`leader ${field} is invalid`)
        continue
      }
      if (seen.has(value))
        errors.push(
          `leader ${field === 'silhouetteId' ? 'silhouette' : field === 'visualId' ? 'visual' : field} ${value} is reused`
        )
      seen.add(value)
    }
  }
}

function validateWorkerVocabulary(characterAssets, errors) {
  const vocabulary = characterAssets?.workerVocabulary
  let capacity = 1
  for (const [, collection] of WORKER_SIGNATURE_FIELDS) {
    const values = vocabulary?.[collection]
    if (
      !Array.isArray(values) ||
      values.length === 0 ||
      !values.every(isNonEmptyString) ||
      new Set(values).size !== values.length
    ) {
      errors.push(`worker ${collection} vocabulary is invalid`)
      capacity = 0
      continue
    }
    capacity *= values.length
  }
  const floor = characterAssets?.fleetIdentityFloor
  if (!Number.isInteger(floor) || floor < 1) {
    errors.push('worker fleet identity floor is invalid')
  } else if (capacity < floor) {
    errors.push(`worker signature vocabulary capacity ${capacity} is below fleet floor ${floor}`)
  }
  return vocabulary
}

function signatureKey(signature) {
  return WORKER_SIGNATURE_FIELDS.map(([field]) => signature?.[field]).join('|')
}

function validateWorkerKits(characterAssets, vocabulary, errors) {
  const kits = characterAssets?.groupKits
  if (!Array.isArray(kits)) {
    errors.push('worker kits must cover exactly 19 Hermes groups')
    for (const groupName of HERMES_GROUPS) errors.push(`missing worker kit for ${groupName}`)
    return
  }
  if (kits.length !== HERMES_GROUPS.length) errors.push('worker kits must cover exactly 19 Hermes groups')
  const expectedGroups = new Set(HERMES_GROUPS)
  const seenGroups = new Set()
  const seenKits = new Set()
  const signatures = new Map()
  for (const kit of kits) {
    const groupName = kit?.group
    if (!expectedGroups.has(groupName) || seenGroups.has(groupName)) {
      errors.push(`worker kit group ${groupName ?? 'unknown'} is invalid or reused`)
    } else {
      seenGroups.add(groupName)
    }
    if (!isNonEmptyString(kit?.kitId)) {
      errors.push(`${groupName ?? 'unknown'} worker kit id is invalid`)
    } else if (seenKits.has(kit.kitId)) {
      errors.push(`worker kit ${kit.kitId} is reused`)
    } else {
      seenKits.add(kit.kitId)
    }
    for (const [field, collection] of WORKER_SIGNATURE_FIELDS) {
      const value = kit?.signature?.[field]
      if (!vocabulary?.[collection]?.includes(value)) {
        errors.push(`${groupName ?? 'unknown'} worker signature ${field} ${value ?? 'missing'} is not declared`)
      }
    }
    const key = signatureKey(kit?.signature)
    if (signatures.has(key)) {
      errors.push(`worker signature collision between ${signatures.get(key)} and ${groupName ?? 'unknown'}`)
    } else {
      signatures.set(key, groupName ?? 'unknown')
    }
  }
  for (const groupName of HERMES_GROUPS)
    if (!seenGroups.has(groupName)) errors.push(`missing worker kit for ${groupName}`)
}

function validateSharedCharacterResources(characterAssets, errors) {
  const strategy = characterAssets?.sharedResourceStrategy
  const sharedFields = ['rig', 'gpuBuffers', 'animationClips', 'materials', 'textureAtlas']
  if (!sharedFields.every(field => isNonEmptyString(strategy?.[field]))) {
    errors.push('character shared resource strategy is invalid')
  }
  for (const resource of ['skeletons', 'meshes', 'materials', 'textures']) {
    if (strategy?.perProfile?.[resource] !== 0) errors.push(`per-profile ${resource} must be zero`)
  }
}

function validateCharacterLods(characterAssets, errors) {
  const lods = characterAssets?.lodRepresentations
  const byId = new Map(Array.isArray(lods) ? lods.map(lod => [lod?.id, lod]) : [])
  if (!Array.isArray(lods) || lods.length !== 3 || ['near', 'mid', 'far'].some(id => !byId.has(id))) {
    errors.push('character LODs must declare near, mid, and far representations')
  }
  if (byId.get('near')?.representation !== 'full' || byId.get('near')?.animated !== true) {
    errors.push('near character LOD must be animated full geometry')
  }
  if (byId.get('mid')?.representation !== 'reduced' || byId.get('mid')?.animated !== false) {
    errors.push('mid character LOD must be static reduced geometry')
  }
  if (byId.get('far')?.representation !== 'static-or-aggregate' || byId.get('far')?.animated !== false) {
    errors.push('far character LOD must be static-or-aggregate')
  }
}

export function validateCharacterAssets(characterAssets, errors) {
  if (!isRecord(characterAssets)) {
    errors.push('character asset contract is required')
    return
  }
  validateUniqueLeaderIdentities(characterAssets.leaders, errors)
  const vocabulary = validateWorkerVocabulary(characterAssets, errors)
  validateWorkerKits(characterAssets, vocabulary, errors)
  validateSharedCharacterResources(characterAssets, errors)
  validateCharacterLods(characterAssets, errors)
}

function validateRuntimeTextures(root, errors) {
  for (const texture of Array.isArray(root?.textures) ? root.textures : [])
    if (isApprovedSourceUri(texture?.uri)) errors.push('approved source cannot be a runtime asset')
}

function validateModelTextures(model, stats, errors) {
  for (const uri of [...(model.textures ?? []), ...stats.textureUris])
    if (isApprovedSourceUri(uri)) errors.push('approved source cannot be a runtime asset')
}

export async function validateAssetPack(root, io) {
  const errors = []
  if (root?.version !== 2) errors.push('version must equal 2')
  if (root?.assetVersion !== ASSET_VERSION) errors.push(`asset version must equal ${ASSET_VERSION}`)
  if (root?.source?.sha256 !== APPROVED_SHA) errors.push('approved source digest mismatch')
  validateMaterials(root ?? {}, errors)
  validateCameraLandmarks(root?.camera, errors)
  validateNavigation(root?.navigation, errors)
  validateDestinations(root ?? {}, errors)
  validateProjectSlots(root ?? {}, errors)
  validateQualityBudgets(root ?? {}, errors)
  validateCharacterAssets(root?.characterAssets, errors)
  const models = validateModelIds(root, errors)
  for (const model of models) {
    const id = model?.id ?? 'model'
    validateModelShape(model, root ?? {}, errors)
    if (isApprovedSourceUri(model?.uri)) {
      errors.push('approved source cannot be a runtime asset')
      continue
    }
    if (typeof model?.uri !== 'string' || !model.uri.endsWith('.glb')) {
      errors.push(`${id} model URI must be a GLB`)
      continue
    }
    const stats = await inspectGlb(io, model.uri)
    validateModelTextures(model, stats, errors)
    if (Number.isFinite(model.maxTriangles) && stats.triangles > model.maxTriangles)
      errors.push(`${id} exceeds ${model.maxTriangles} triangles`)
    requireNamedNodes(model, stats, errors)
    requireClips(model, stats, errors)
    requireLods(model, stats, errors)
  }
  validateRuntimeTextures(root ?? {}, errors)
  return validationResult(errors)
}

export function inspectJpegDimensions(bytes) {
  if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error('approved source is not a JPEG')
  let offset = 2
  while (offset < bytes.length) {
    while (bytes[offset] === 0xff) offset += 1
    const marker = bytes[offset]
    offset += 1
    if (marker === 0xd9 || marker === 0xda) break
    const length = bytes.readUInt16BE(offset)
    if (length < 2 || offset + length > bytes.length) break
    if (
      (marker >= 0xc0 && marker <= 0xc3) ||
      (marker >= 0xc5 && marker <= 0xc7) ||
      (marker >= 0xc9 && marker <= 0xcb) ||
      (marker >= 0xcd && marker <= 0xcf)
    )
      return { height: bytes.readUInt16BE(offset + 3), width: bytes.readUInt16BE(offset + 5) }
    offset += length
  }
  throw new Error('approved source dimensions are unavailable')
}

function validateSourceReferenceDeclaration(sourceReference, errors) {
  if (sourceReference?.version !== 2) errors.push('source reference version must equal 2')
  if (sourceReference?.source?.uri !== APPROVED_SOURCE_URI) errors.push('approved source URI mismatch')
  if (sourceReference?.source?.sha256 !== APPROVED_SHA) errors.push('approved source digest mismatch')
  if (
    sourceReference?.source?.dimensions?.width !== APPROVED_DIMENSIONS.width ||
    sourceReference?.source?.dimensions?.height !== APPROVED_DIMENSIONS.height
  )
    errors.push('approved source dimensions mismatch')
}

export function validateSourceReference(sourceReference, sourceBytes, errors) {
  validateSourceReferenceDeclaration(sourceReference, errors)
  if (createHash('sha256').update(sourceBytes).digest('hex') !== APPROVED_SHA)
    errors.push('approved source file digest mismatch')
  try {
    const dimensions = inspectJpegDimensions(sourceBytes)
    if (dimensions.width !== APPROVED_DIMENSIONS.width || dimensions.height !== APPROVED_DIMENSIONS.height)
      errors.push('approved source file dimensions mismatch')
  } catch (error) {
    errors.push(error.message)
  }
}

export async function validateAssetContract({ root, io, sourcePath, sourceReference }) {
  const assetResult = await validateAssetPack(root, io)
  const errors = [...assetResult.errors]
  try {
    validateSourceReference(sourceReference, await readFile(sourcePath), errors)
  } catch {
    validateSourceReferenceDeclaration(sourceReference, errors)
    errors.push('approved source file cannot be read')
  }
  if (root?.source?.sha256 !== sourceReference?.source?.sha256)
    errors.push('manifest and source reference digest mismatch')
  return validationResult(errors)
}

export async function validateAssetContractFiles(manifestPath, io = new NodeIO().registerExtensions(ALL_EXTENSIONS)) {
  const absoluteManifestPath = resolve(manifestPath)
  const root = JSON.parse(await readFile(absoluteManifestPath, 'utf8'))
  const sourceReferencePath = resolve(dirname(absoluteManifestPath), 'source-reference.v2.json')
  const sourceReference = JSON.parse(await readFile(sourceReferencePath, 'utf8'))
  const baseDirectory = dirname(absoluteManifestPath)
  const resolvedModels = Array.isArray(root?.models)
    ? root.models.map(model =>
        isRecord(model) && isNonEmptyString(model.uri) ? { ...model, uri: resolve(baseDirectory, model.uri) } : model
      )
    : root?.models
  const resolvedNavigation =
    isRecord(root?.navigation) && isNonEmptyString(root.navigation.meshUri)
      ? { ...root.navigation, meshUri: resolve(baseDirectory, root.navigation.meshUri) }
      : root?.navigation
  const resolvedRoot = isRecord(root) ? { ...root, models: resolvedModels, navigation: resolvedNavigation } : root
  const sourceUri = sourceReference?.source?.uri
  const sourcePath = isNonEmptyString(sourceUri) ? resolve(dirname(sourceReferencePath), sourceUri) : undefined
  return validateAssetContract({ root: resolvedRoot, io, sourcePath, sourceReference })
}

async function main() {
  const manifestPath = process.argv[2]
  if (!manifestPath) {
    console.error('Usage: node validate-assets.mjs <world-manifest.v2.json>')
    process.exitCode = 2
    return
  }
  const result = await validateAssetContractFiles(manifestPath)
  if (!result.ok) {
    console.error(result.errors.join('\n'))
    process.exitCode = 1
    return
  }
  console.log('validated Lunar City source reference and GLB asset contract')
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main()
