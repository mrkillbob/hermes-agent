import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { basename, resolve } from 'node:path'
import { NodeIO } from '@gltf-transform/core'

function finiteVector(value, fallback = [0, 0, 0]) {
  return Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every(Number.isFinite)
    ? value.slice(0, 3)
    : fallback
}

function accessorBounds(accessor) {
  const low = [0, 0, 0]
  const high = [0, 0, 0]
  try {
    accessor.getMin?.(low)
    accessor.getMax?.(high)
    return { low, high }
  } catch {
    return undefined
  }
}

function boundsFor(root) {
  const min = [Infinity, Infinity, Infinity]
  const max = [-Infinity, -Infinity, -Infinity]
  let found = false
  for (const mesh of root.listMeshes()) {
    for (const primitive of mesh.listPrimitives()) {
      const position = primitive.getAttribute?.('POSITION')
      if (!position) continue
      const measured = accessorBounds(position)
      if (!measured) continue
      const low = finiteVector(measured.low)
      const high = finiteVector(measured.high)
      for (let axis = 0; axis < 3; axis += 1) {
        min[axis] = Math.min(min[axis], low[axis])
        max[axis] = Math.max(max[axis], high[axis])
      }
      found = true
    }
  }
  if (!found) return { min: [0, 0, 0], max: [0, 0, 0], extent: [0, 0, 0] }
  return { min, max, extent: max.map((value, axis) => Number((value - min[axis]).toFixed(4))) }
}

function triangleCount(root) {
  return root
    .listMeshes()
    .flatMap(mesh => mesh.listPrimitives())
    .reduce((total, primitive) => {
      const indices = primitive.getIndices?.()
      const position = primitive.getAttribute?.('POSITION')
      return total + (indices ? indices.getCount() / 3 : position ? position.getCount() / 3 : 0)
    }, 0)
}

function accessorMax(accessor) {
  const values = []
  try {
    accessor?.getMax?.(values)
  } catch {
    return []
  }
  return values
}

function animationSummary(root) {
  return root
    .listAnimations()
    .map(animation => {
      const duration =
        animation.listChannels?.().reduce((max, channel) => {
          const input = channel.getSampler?.()?.getInput?.()
          const values = accessorMax(input)
          return Math.max(max, ...values.filter(Number.isFinite))
        }, 0) ?? 0
      return { duration: Number(duration.toFixed(4)), name: animation.getName() }
    })
    .sort((left, right) => left.name.localeCompare(right.name))
}

export async function auditLunarCityModel(filePath, provenance = 'reference-only') {
  const absolutePath = resolve(filePath)
  const bytes = await readFile(absolutePath)
  const root = (await new NodeIO().read(absolutePath)).getRoot()
  const animations = animationSummary(root)
  const bounds = boundsFor(root)
  return {
    animations,
    bounds,
    bytes: bytes.byteLength,
    file: basename(absolutePath),
    materials: root.listMaterials().length,
    meshes: root.listMeshes().length,
    nodes: root.listNodes().length,
    provenance,
    sha256: createHash('sha256').update(bytes).digest('hex'),
    skins: root.listSkins().length,
    textures: root.listTextures().length,
    triangles: Math.round(triangleCount(root)),
    version: 1
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const paths = process.argv.slice(2).filter(argument => !argument.startsWith('--'))
  if (paths.length === 0) {
    console.error('Usage: npm run audit:lunar-city-model -- <file.glb> [...]')
    process.exitCode = 2
  } else {
    const assets = []
    for (const path of paths) assets.push(await auditLunarCityModel(path))
    console.log(JSON.stringify({ assets }, null, 2))
  }
}
