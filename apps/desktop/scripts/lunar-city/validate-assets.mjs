import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { NodeIO } from '@gltf-transform/core'
import { ALL_EXTENSIONS } from '@gltf-transform/extensions'

export const APPROVED_SHA = '248e8d40946b08b9f74f4b2ddd0ba17e4f17fd054260189972164c5d6ca70590'

const TRIANGLES = 4

export function validationResult(errors) {
  return { ok: errors.length === 0, errors: Object.freeze(errors) }
}

function isApprovedSourceUri(uri) {
  return typeof uri === 'string' && /moon-settlement-approved\.jpg$/i.test(uri)
}

function isVec3(value) {
  return Array.isArray(value) && value.length === 3 && value.every(Number.isFinite)
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
    clips: new Set(root.listAnimations().map(animation => animation.getName()).filter(Boolean)),
    nodes: new Set(root.listNodes().map(node => node.getName()).filter(Boolean)),
    triangles: root
      .listMeshes()
      .flatMap(mesh => mesh.listPrimitives())
      .reduce((total, primitive) => total + countTriangles(primitive), 0)
  }
}

export function requireNamedNodes(model, stats, errors) {
  for (const node of model.requiredNodes ?? []) {
    if (!stats.nodes.has(node)) errors.push(`${model.id} missing node ${node}`)
  }
}

export function requireClips(model, stats, errors) {
  for (const clip of model.requiredClips ?? []) {
    if (!stats.clips.has(clip)) errors.push(`${model.id} missing clip ${clip}`)
  }
}

export function requireLods(model, stats, errors) {
  for (const lod of model.lods ?? []) {
    if (!stats.nodes.has(lod.node)) errors.push(`${model.id} missing LOD node ${lod.node}`)
  }
}

function validLandmark(landmark) {
  return (
    landmark &&
    typeof landmark.id === 'string' &&
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

  const bounds = camera?.bounds
  if (
    !isVec3(bounds?.min) ||
    !isVec3(bounds?.max) ||
    bounds.min.some((value, index) => value >= bounds.max[index])
  ) {
    errors.push('camera bounds are invalid')
  }
}

export function validateNavigation(navigation, errors) {
  if (isApprovedSourceUri(navigation?.meshUri)) errors.push('approved source cannot be a runtime asset')
  if (typeof navigation?.meshUri !== 'string' || !navigation.meshUri.endsWith('.glb')) {
    errors.push('navigation mesh URI must be a GLB')
  }
  if (!Array.isArray(navigation?.links) || navigation.links.length === 0) {
    errors.push('navigation requires at least one link')
    return
  }
  for (const link of navigation.links) {
    if (!isVec3(link.from) || !isVec3(link.to) || typeof link.bidirectional !== 'boolean') {
      errors.push('navigation link is invalid')
    }
  }
}

function validateRuntimeTextures(root, errors) {
  for (const texture of root.textures ?? []) {
    if (isApprovedSourceUri(texture.uri)) errors.push('approved source cannot be a runtime asset')
  }
}

function validateModelTextures(model, errors) {
  for (const uri of model.textures ?? []) {
    if (isApprovedSourceUri(uri)) errors.push('approved source cannot be a runtime asset')
  }
}

export async function validateAssetPack(root, io) {
  const errors = []

  if (root?.version !== 2) errors.push('version must equal 2')
  if (root?.source?.sha256 !== APPROVED_SHA) errors.push('approved source digest mismatch')

  for (const model of root?.models ?? []) {
    if (isApprovedSourceUri(model.uri)) {
      errors.push('approved source cannot be a runtime asset')
      continue
    }
    validateModelTextures(model, errors)
    if (typeof model.uri !== 'string' || !model.uri.endsWith('.glb')) {
      errors.push(`${model.id} model URI must be a GLB`)
      continue
    }
    if (!Number.isFinite(model.maxTriangles) || model.maxTriangles < 0) {
      errors.push(`${model.id} requires a triangle budget`)
      continue
    }

    const stats = await inspectGlb(io, model.uri)
    if (stats.triangles > model.maxTriangles) errors.push(`${model.id} exceeds ${model.maxTriangles} triangles`)
    requireNamedNodes(model, stats, errors)
    requireClips(model, stats, errors)
    requireLods(model, stats, errors)
  }

  validateRuntimeTextures(root ?? {}, errors)
  validateCameraLandmarks(root?.camera, errors)
  validateNavigation(root?.navigation, errors)
  return validationResult(errors)
}

async function main() {
  const manifestPath = process.argv[2]
  if (!manifestPath) {
    console.error('Usage: node validate-assets.mjs <world-manifest.v2.json>')
    process.exitCode = 2
    return
  }

  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  const result = await validateAssetPack(manifest, io)
  if (!result.ok) {
    console.error(result.errors.join('\n'))
    process.exitCode = 1
    return
  }

  console.log(`validated ${manifest.models.length} Lunar City GLB models`)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main()
