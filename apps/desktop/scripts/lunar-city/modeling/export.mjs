import { createHash } from 'node:crypto'
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { deflateSync } from 'node:zlib'

import { NodeIO } from '@gltf-transform/core'
import { format } from 'prettier'

import { bakeAmbientOcclusion } from './ambient-occlusion.mjs'
import { normalizePaletteId } from './authored.mjs'
import { GLTF2Export } from './babylon.mjs'
import { approvedPaletteBytes } from './palette.mjs'
import { mergeLodMeshes } from './primitives.mjs'

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value
  for (let bit = 0; bit < 8; bit += 1) crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1
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

export function optimizeModelScene(scene, id, budget, { skipVertexAOSubdivision = false } = {}) {
  // Near carries the shading detail; mid gets a coarser share; far is seen
  // from far enough away that neither subdivision nor AO buys anything.
  const shares = { far: 0, mid: 0.18, near: 0.62 }
  // Subdivision is scoped to the terrain deliberately. Vertex AO needs
  // tessellation to have anything to interpolate across, but tessellating a
  // district breaks `build-models.test.mjs`'s silhouette-uniqueness guard:
  // that metric rasterizes triangles into a coarse occupancy grid, so denser
  // meshes fill it more completely and every specialist converges toward a
  // filled box. The guard is measuring something real, so the geometry it
  // watches stays at its authored density; the terrain is not one of its
  // SPECIALIST_IDS and is also where ground-contact shading reads hardest.
  // skipVertexAOSubdivision is set once terrain (or any model) has an
  // authored baked-texture detail pass instead: that texture is baked
  // against a specific, fixed vertex count/UV layout from the authored
  // source file, and this subdivision pass would silently change vertex
  // counts after the fact, breaking injectBakedDetailTexture's by-count
  // primitive matching. The AO *texture* atlas this comment used to call
  // future work is exactly what that hook now provides -- decoupling
  // shading resolution from triangle density without this subdivision.
  const shadeGeometry = id === 'terrain' && !skipVertexAOSubdivision

  // Leaders are exported as authored skinned character nodes. Merging any
  // of their LODs bakes child transforms into vertex space while the glTF
  // inverse-bind matrices still expect the original node spaces, which
  // displaces the characters in Blender. Keep this family topology-preserved
  // until the Blender-authored optimization pass can rebuild weights safely.
  if (id === 'leaders') return

  for (const suffix of ['near', 'mid', 'far']) {
    const lodRoot = scene.getTransformNodeByName(`${id}:lod:${suffix}`)
    if (!lodRoot) continue
    if (id === 'leaders') continue
    mergeLodMeshes(scene, lodRoot, `${id}:${suffix}`)
    // Bake after merging so occlusion is computed once against the final
    // triangle soup for this LOD.
    if (shares[suffix] > 0 && !skipVertexAOSubdivision)
      bakeAmbientOcclusion(scene, lodRoot, {
        maxTriangles: shadeGeometry ? Math.floor((budget?.maxTriangles ?? Infinity) * shares[suffix]) : 0
      })
  }
}

async function inspectWrittenGlb(uri) {
  const root = (await new NodeIO().read(uri)).getRoot()
  const primitives = root.listMeshes().flatMap(mesh => mesh.listPrimitives())
  const triangles = primitives.reduce((total, primitive) => {
    const indices = primitive.getIndices()
    return total + (indices ? indices.getCount() / 3 : (primitive.getAttribute('POSITION')?.getCount() ?? 0) / 3)
  }, 0)
  const accessorBytes = root
    .listAccessors()
    .reduce((total, accessor) => total + (accessor.getArray()?.byteLength ?? 0), 0)
  const textureBytes = root.listTextures().reduce((total, texture) => total + (texture.getImage()?.byteLength ?? 0), 0)
  return {
    accessorBytes,
    animationClips: root
      .listAnimations()
      .map(animation => animation.getName())
      .filter(Boolean)
      .toSorted(),
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

export async function exportModel({ budget, id, outputRoot, scene, skipVertexAOSubdivision = false }) {
  optimizeModelScene(scene, id, budget, { skipVertexAOSubdivision })
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

/**
 * Multiplies a baked (not hand-painted, not third-party) grayscale AO map
 * into specific materials' baseColorTexture on an already-written model
 * GLB. glTF's baseColorTexture is spec-required to multiply against
 * baseColorFactor per channel, so with an unchanged approved-palette
 * baseColorFactor this reads as (approved color x AO) at pixel resolution
 * -- the same math the existing per-vertex AO baker already does, just
 * finer-grained, and it darkens under every light in the rig, not only
 * indirect/IBL contribution (glTF's occlusionTexture channel is spec'd to
 * affect indirect lighting only, so it was tried first here and measured
 * as visually inert under this scene's all-direct lighting rig -- kept as
 * a cautionary note, not a design to repeat).
 *
 * Runs as a post-process on the Babylon-exported file rather than through
 * Babylon/NullEngine itself, for two independent reasons discovered while
 * building this: (1) Babylon's Node-side texture loading expects browser
 * image-decoding APIs that do not exist here, while @gltf-transform/core
 * writes raw image bytes into a glTF texture directly, no decode required;
 * (2) Babylon's own glTF exporter silently drops a mesh's UV0 buffer for
 * any material that has no texture attached *at export time* -- measured
 * directly (a minimal repro mesh with setVerticesData(UVKind, ...) lost
 * its TEXCOORD_0 through GLTF2Export.GLBAsync every time), so setting the
 * texture only after Babylon has already written the file means the UV
 * data required to sample it correctly is already gone. This function
 * therefore also copies TEXCOORD_0 from the authored source file's
 * matching primitive (by approved-palette material name, tolerating
 * Blender's ".NNN" dedup suffix) directly onto the output primitive,
 * verifying vertex counts line up first -- authored.mjs's importer keeps
 * source vertex order/count 1:1 into the Babylon mesh, and mergeLodMeshes
 * never touches a material with only one contributing mesh, so this is a
 * true 1:1 correspondence, not a heuristic match.
 */
export async function injectBakedDetailTexture({
  authoredPath,
  budget,
  extent,
  id,
  imageBytes,
  materialNames,
  outputRoot
}) {
  const uri = join(outputRoot, 'models', `${id}.glb`)
  const [document, authoredDocument] = await Promise.all([new NodeIO().read(uri), new NodeIO().read(authoredPath)])
  const root = document.getRoot()
  const authoredPrimitives = authoredDocument
    .getRoot()
    .listMeshes()
    .flatMap(mesh => mesh.listPrimitives())

  const texture = document.createTexture(`${id}:ao`).setImage(imageBytes).setMimeType('image/png')
  for (const materialName of materialNames) {
    const material = root.listMaterials().find(candidate => candidate.getName() === materialName)
    if (!material) throw new Error(`${id}: no material named "${materialName}" to attach a baked AO texture to`)
    // Several authored primitives can share one approved-palette material
    // name (e.g. far-LOD geometry alongside the UV-unwrapped near-LOD
    // atlas), and the same is true on the output side after LOD-tiered
    // merging -- only the one actually carrying TEXCOORD_0 is the bake
    // target, so pick the source primitive that has UV0 for this material,
    // then match it to the output primitive by exact vertex count -- the
    // one property guaranteed 1:1 between source and output for a
    // single-mesh-per-material group untouched by mergeLodMeshes.
    const sourcePrimitive = authoredPrimitives.find(
      primitive =>
        normalizePaletteId(primitive.getMaterial()?.getName()) === materialName && primitive.getAttribute('TEXCOORD_0')
    )
    if (!sourcePrimitive)
      throw new Error(`${id}: authored source has no UV-mapped primitive for material "${materialName}"`)
    const sourceUV = sourcePrimitive.getAttribute('TEXCOORD_0')
    const sourceCount = sourceUV.getCount()
    const candidates = root
      .listMeshes()
      .flatMap(mesh => mesh.listPrimitives())
      .filter(primitive => primitive.getMaterial() === material)
    const outputPrimitive = candidates.find(primitive => primitive.getAttribute('POSITION').getCount() === sourceCount)
    if (!outputPrimitive)
      throw new Error(
        `${id}: no output "${materialName}" primitive matches authored UV0 vertex count (${sourceCount}); candidates had [${candidates.map(c => c.getAttribute('POSITION').getCount()).join(', ')}]`
      )
    const uvAccessor = document
      .createAccessor(`${materialName}:uv`)
      .setType('VEC2')
      .setArray(Float32Array.from(sourceUV.getArray()))
    outputPrimitive.setAttribute('TEXCOORD_0', uvAccessor)
    material.setBaseColorTexture(texture)
    material.getBaseColorTextureInfo().setTexCoord(0)
  }
  const outBytes = Buffer.from(await new NodeIO().writeBinary(document))
  await writeFile(uri, outBytes)
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
    bytes: outBytes.length,
    extent,
    sha256: sha256(outBytes)
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
