import { createHash } from 'node:crypto'
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { deflateSync } from 'node:zlib'

import { NodeIO } from '@gltf-transform/core'
import { format } from 'prettier'

import { GLTF2Export } from './babylon.mjs'
import { approvedPaletteBytes } from './palette.mjs'
import { mergeLodMeshes } from './primitives.mjs'

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value
  for (let bit = 0; bit < 8; bit += 1) crc = (crc & 1) ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1
  return crc >>> 0
})

export function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function crc32(bytes) {
  let crc = 0xffffffff
  for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function pngChunk(type, data) {
  const typeBytes = Buffer.from(type, 'ascii')
  const size = Buffer.alloc(4)
  size.writeUInt32BE(data.length)
  const checksum = Buffer.alloc(4)
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])))
  return Buffer.concat([size, typeBytes, data, checksum])
}

export function createPalettePng() {
  const colors = approvedPaletteBytes()
  const header = Buffer.alloc(13)
  header.writeUInt32BE(colors.length, 0)
  header.writeUInt32BE(1, 4)
  header[8] = 8
  header[9] = 6
  const row = Buffer.from([0, ...colors.flat()])
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', header),
    pngChunk('IDAT', deflateSync(row, { level: 9 })),
    pngChunk('IEND', Buffer.alloc(0))
  ])
}

function sceneExtent(scene) {
  const min = [Infinity, Infinity, Infinity]
  const max = [-Infinity, -Infinity, -Infinity]
  for (const mesh of scene.meshes) {
    mesh.computeWorldMatrix(true)
    mesh.refreshBoundingInfo()
    const bounds = mesh.getBoundingInfo().boundingBox
    const low = bounds.minimumWorld.asArray()
    const high = bounds.maximumWorld.asArray()
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], low[axis])
      max[axis] = Math.max(max[axis], high[axis])
    }
  }
  return { extent: min.map((value, axis) => Number((max[axis] - value).toFixed(4))), max, min }
}

export function optimizeModelScene(scene, id) {
  for (const suffix of ['near', 'mid', 'far']) {
    const lodRoot = scene.getTransformNodeByName(`${id}:lod:${suffix}`)
    if (lodRoot) mergeLodMeshes(scene, lodRoot, `${id}:${suffix}`)
  }
}

async function inspectWrittenGlb(uri) {
  const root = (await new NodeIO().read(uri)).getRoot()
  const primitives = root.listMeshes().flatMap(mesh => mesh.listPrimitives())
  const triangles = primitives.reduce((total, primitive) => {
    const indices = primitive.getIndices()
    return total + (indices ? indices.getCount() / 3 : (primitive.getAttribute('POSITION')?.getCount() ?? 0) / 3)
  }, 0)
  const accessorBytes = root.listAccessors().reduce((total, accessor) => total + (accessor.getArray()?.byteLength ?? 0), 0)
  const textureBytes = root.listTextures().reduce((total, texture) => total + (texture.getImage()?.byteLength ?? 0), 0)
  return {
    accessorBytes,
    animationClips: root.listAnimations().map(animation => animation.getName()).filter(Boolean).toSorted(),
    drawCalls: primitives.length,
    materials: root.listMaterials().length,
    meshes: root.listMeshes().length,
    nodes: root.listNodes().length,
    gpuBytes: accessorBytes + textureBytes,
    textures: root.listTextures().length,
    triangles,
    gpuMiB: Number(((accessorBytes + textureBytes) / (1024 * 1024)).toFixed(4))
  }
}

function assertBudget(id, stats, budget) {
  const checks = [
    ['triangles', 'maxTriangles'],
    ['drawCalls', 'maxDrawCalls'],
    ['materials', 'maxMaterials'],
    ['textures', 'maxTextures']
  ]
  for (const [actual, maximum] of checks) {
    if (stats[actual] > budget[maximum]) throw new Error(`${id} ${actual} ${stats[actual]} exceeds ${budget[maximum]}`)
  }
  if (stats.gpuMiB > budget.maxGpuMiB) throw new Error(`${id} GPU estimate ${stats.gpuMiB} exceeds ${budget.maxGpuMiB}`)
}

export async function exportModel({ budget, id, outputRoot, scene }) {
  optimizeModelScene(scene, id)
  const bounds = sceneExtent(scene)
  const data = await GLTF2Export.GLBAsync(scene, `${id}.glb`, {
    animationSampleRate: 1 / 30,
    exportWithoutWaitingForScene: true,
    meshCompressionMethod: 'None',
    removeNoopRootNodes: false
  })
  const blob = data.glTFFiles[`${id}.glb`]
  if (!blob) throw new Error(`Babylon exporter did not return ${id}.glb`)
  const bytes = Buffer.from(await blob.arrayBuffer())
  const uri = join(outputRoot, 'models', `${id}.glb`)
  await writeFile(uri, bytes)
  const inspection = await inspectWrittenGlb(uri)
  const stats = {
    ...inspection,
    budget: {
      maxDrawCalls: budget.maxDrawCalls,
      maxGpuMiB: budget.maxGpuMiB,
      maxMaterials: budget.maxMaterials,
      maxTextures: budget.maxTextures,
      maxTriangles: budget.maxTriangles
    },
    bytes: bytes.length,
    extent: bounds.extent,
    sha256: sha256(bytes)
  }
  assertBudget(id, stats, budget)
  return stats
}

export async function writeGeneratedTexture(outputRoot) {
  const bytes = createPalettePng()
  const uri = 'textures/approved-palette.png'
  await writeFile(join(outputRoot, uri), bytes)
  return { bytes: bytes.length, sha256: sha256(bytes), source: 'generated-approved-palette', uri }
}

export async function prepareOutputDirectories(outputRoot) {
  await Promise.all([
    mkdir(join(outputRoot, 'models'), { recursive: true }),
    mkdir(join(outputRoot, 'textures'), { recursive: true })
  ])
}

export async function updateManifestStatistics(outputRoot, receipt) {
  const manifestPath = join(outputRoot, 'world-manifest.v2.json')
  try {
    await stat(manifestPath)
  } catch {
    return false
  }
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  manifest.models = manifest.models.map(model => ({ ...model, statistics: receipt.statistics[model.id] }))
  manifest.textures = receipt.textures
  manifest.generatedAssetPack = {
    builder: 'babylonjs-null-engine',
    engineVersion: '9.21.2',
    navigation: receipt.auxiliary.navigation,
    sourceSha256: receipt.sourceSha256
  }
  await writeFile(
    manifestPath,
    await format(JSON.stringify(manifest), {
      arrowParens: 'avoid',
      bracketSpacing: true,
      endOfLine: 'auto',
      filepath: manifestPath,
      printWidth: 120,
      semi: false,
      singleQuote: true,
      tabWidth: 2,
      trailingComma: 'none',
      useTabs: false
    })
  )
  return true
}
