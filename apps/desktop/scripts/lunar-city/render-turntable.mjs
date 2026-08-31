import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { NodeIO } from '@gltf-transform/core'

const OUTPUT_URL = new URL(
  '../../../../.superpowers/sdd/2026-08-30-lunar-city-playable-world/task-3-turntable.svg',
  import.meta.url
)
const ASSET_ROOT = new URL('../../public/lunar-city/v2/', import.meta.url)
const MANIFEST_URL = new URL('world-manifest.v2.json', ASSET_ROOT)
const WIDTH = 2110
const HEADER_HEIGHT = 54
const ROW_HEIGHT = 206
const VIEW_CENTERS = [505, 1135, 1765]
const VIEW_ANGLES = [-Math.PI / 4, Math.PI / 4, Math.PI]
const PALETTE = Object.freeze({
  'archive-emissive': '#8c55d8',
  'bone-metal': '#dbc9b3',
  'charcoal-structure': '#252532',
  'garden-green': '#74ca6c',
  'lunar-rust': '#ab4724',
  'signal-emissive': '#44daed',
  'triage-amber': '#f2b53a'
})

function isDescendantOf(node, ancestor) {
  for (let current = node; current; current = current.getParentNode()) if (current === ancestor) return true
  return false
}

function transformPoint([x, y, z], matrix) {
  return [
    matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
    matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
    matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
  ]
}

function modelTriangles(root, nearName) {
  const near = root.listNodes().find(node => node.getName() === nearName)
  if (!near) throw new Error(`missing ${nearName}`)
  const triangles = []
  for (const node of root.listNodes()) {
    const mesh = node.getMesh()
    if (!mesh || !isDescendantOf(node, near)) continue
    const world = node.getWorldMatrix()
    for (const primitive of mesh.listPrimitives()) {
      const positions = primitive.getAttribute('POSITION')
      if (!positions) continue
      const values = positions.getArray()
      const indices =
        primitive.getIndices()?.getArray() ?? Uint32Array.from({ length: positions.getCount() }, (_, i) => i)
      const material = primitive.getMaterial()?.getName() ?? 'charcoal-structure'
      for (let index = 0; index + 2 < indices.length; index += 3) {
        triangles.push({
          material,
          points: [indices[index], indices[index + 1], indices[index + 2]].map(vertex =>
            transformPoint([values[vertex * 3], values[vertex * 3 + 1], values[vertex * 3 + 2]], world)
          )
        })
      }
    }
  }
  return triangles
}

function rotatePoint([x, y, z], angle) {
  return [x * Math.cos(angle) - z * Math.sin(angle), y, x * Math.sin(angle) + z * Math.cos(angle)]
}

function projectPoint(point, angle) {
  const [x, y, depth] = rotatePoint(point, angle)
  return { depth, x, y: -y + depth * 0.32 }
}

function triangleNormal([a, b, c], angle) {
  const [ra, rb, rc] = [a, b, c].map(point => rotatePoint(point, angle))
  const ab = [rb[0] - ra[0], rb[1] - ra[1], rb[2] - ra[2]]
  const ac = [rc[0] - ra[0], rc[1] - ra[1], rc[2] - ra[2]]
  const normal = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]]
  const length = Math.hypot(...normal) || 1
  return normal.map(value => value / length)
}

function shade(hex, factor) {
  const value = Number.parseInt(hex.slice(1), 16)
  const channels = [value >> 16, (value >> 8) & 255, value & 255]
  return `#${channels
    .map(channel =>
      Math.min(255, Math.max(0, Math.round(channel * factor)))
        .toString(16)
        .padStart(2, '0')
    )
    .join('')}`
}

function renderView(triangles, angle, centerX, rowTop) {
  const projected = triangles.map(triangle => {
    const points = triangle.points.map(point => projectPoint(point, angle))
    const signedArea =
      (points[1].x - points[0].x) * (points[2].y - points[0].y) -
      (points[1].y - points[0].y) * (points[2].x - points[0].x)
    return {
      ...triangle,
      depth: points.reduce((sum, point) => sum + point.depth, 0) / 3,
      normal: triangleNormal(triangle.points, angle),
      projected: points,
      signedArea
    }
  })
  const visible = projected.filter(triangle => triangle.signedArea < -0.00001)
  const allPoints = projected.flatMap(triangle => triangle.projected)
  const minX = Math.min(...allPoints.map(point => point.x))
  const maxX = Math.max(...allPoints.map(point => point.x))
  const minY = Math.min(...allPoints.map(point => point.y))
  const maxY = Math.max(...allPoints.map(point => point.y))
  const scale = Math.min(520 / Math.max(0.01, maxX - minX), 164 / Math.max(0.01, maxY - minY))
  const offsetX = centerX - ((minX + maxX) / 2) * scale
  const offsetY = rowTop + 108 - ((minY + maxY) / 2) * scale
  const minDepth = Math.min(...visible.map(triangle => triangle.depth))
  const maxDepth = Math.max(...visible.map(triangle => triangle.depth))
  const paths = new Map()
  for (const triangle of visible) {
    const depthBin = Math.floor(((triangle.depth - minDepth) / Math.max(0.001, maxDepth - minDepth)) * 23)
    const light = 0.7 + Math.max(0, triangle.normal[1]) * 0.28 + Math.max(0, -triangle.normal[0]) * 0.1
    const fill = shade(PALETTE[triangle.material] ?? '#9b7b65', Math.round(light * 10) / 10)
    const key = `${depthBin}:${fill}`
    const points = triangle.projected.map(point => [point.x * scale + offsetX, point.y * scale + offsetY])
    const path = `M${points.map(point => `${point[0].toFixed(1)},${point[1].toFixed(1)}`).join('L')}Z`
    if (!paths.has(key)) paths.set(key, [])
    paths.get(key).push(path)
  }
  return [...paths.entries()]
    .toSorted(([left], [right]) => Number(right.split(':')[0]) - Number(left.split(':')[0]))
    .map(([key, paths]) => {
      const fill = key.slice(key.indexOf(':') + 1)
      return `<path d="${paths.join('')}" fill="${fill}" stroke="#16141d" stroke-width="0.28"/>`
    })
    .join('')
}

async function renderTurntable(outputUrl = OUTPUT_URL) {
  const manifest = JSON.parse(await readFile(MANIFEST_URL, 'utf8'))
  const io = new NodeIO()
  const rows = []
  for (const [index, model] of manifest.models.entries()) {
    const document = await io.read(fileURLToPath(new URL(model.uri, ASSET_ROOT)))
    const triangles = modelTriangles(document.getRoot(), model.lods[0].node)
    const rowTop = HEADER_HEIGHT + index * ROW_HEIGHT
    const background = index % 2 ? '#2c2226' : '#33252a'
    rows.push(`<rect x="0" y="${rowTop}" width="${WIDTH}" height="${ROW_HEIGHT}" fill="${background}"/>`)
    rows.push(
      `<text x="18" y="${rowTop + 88}" fill="#d7c5af" font-size="20" font-family="system-ui" font-weight="700">${model.id}</text>`
    )
    rows.push(
      `<text x="18" y="${rowTop + 116}" fill="#d96a31" font-size="13" font-family="system-ui">${triangles.length} triangles</text>`
    )
    VIEW_ANGLES.forEach((angle, viewIndex) => rows.push(renderView(triangles, angle, VIEW_CENTERS[viewIndex], rowTop)))
  }
  const height = HEADER_HEIGHT + manifest.models.length * ROW_HEIGHT
  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${height}" viewBox="0 0 ${WIDTH} ${height}">`,
    '<rect width="100%" height="100%" fill="#242431"/>',
    '<text x="18" y="34" fill="#f2b53a" font-size="23" font-family="system-ui" font-weight="700">Lunar City v2 — actual GLB near-LOD turntable</text>',
    ...rows,
    '</svg>\n'
  ].join('\n')
  await writeFile(outputUrl, svg)
}

if (process.argv[1] === fileURLToPath(import.meta.url))
  await renderTurntable(process.argv[2] ? new URL(`file://${process.argv[2]}`) : OUTPUT_URL)

export { renderTurntable }
