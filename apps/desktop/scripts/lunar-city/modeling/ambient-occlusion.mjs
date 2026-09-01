/**
 * Build-time ambient-occlusion baker.
 *
 * Flat-shaded untextured boxes read as programmer art because every face of
 * every box receives identical light: nothing darkens where surfaces meet, so
 * the eye gets no cue that the geometry is solid. Baking AO into COLOR_0 is
 * the cheapest fix in the entire pipeline — it costs 4 bytes per vertex, zero
 * shader work, and zero runtime cost, because glTF multiplies COLOR_0 into
 * base color for free. It is the same trade a pre-rendered background makes:
 * spend unlimited time offline so the runtime spends nothing.
 *
 * Determinism is a hard requirement (the asset build is hash-checked), so ray
 * directions come from a Hammersley sequence rather than Math.random.
 */
import { VertexBuffer } from './babylon.mjs'

const EPSILON = 1e-6

/** Radical inverse (van der Corput), the second Hammersley coordinate. */
function radicalInverse(bits) {
  let value = bits
  value = ((value << 16) | (value >>> 16)) >>> 0
  value = (((value & 0x55555555) << 1) | ((value & 0xaaaaaaaa) >>> 1)) >>> 0
  value = (((value & 0x33333333) << 2) | ((value & 0xcccccccc) >>> 2)) >>> 0
  value = (((value & 0x0f0f0f0f) << 4) | ((value & 0xf0f0f0f0) >>> 4)) >>> 0
  value = (((value & 0x00ff00ff) << 8) | ((value & 0xff00ff00) >>> 8)) >>> 0
  return value * 2.3283064365386963e-10
}

/** Cosine-weighted hemisphere directions in tangent space, deterministic. */
function hemisphereSamples(count) {
  const samples = []
  for (let index = 0; index < count; index += 1) {
    const u = (index + 0.5) / count
    const v = radicalInverse(index)
    const radius = Math.sqrt(u)
    const phi = 2 * Math.PI * v
    samples.push([radius * Math.cos(phi), radius * Math.sin(phi), Math.sqrt(Math.max(0, 1 - u))])
  }
  return samples
}

function buildBasis(normal) {
  const [nx, ny, nz] = normal
  const sign = nz >= 0 ? 1 : -1
  const a = -1 / (sign + nz)
  const b = nx * ny * a
  return [
    [1 + sign * nx * nx * a, sign * b, -sign * nx],
    [b, sign + ny * ny * a, -ny]
  ]
}

/**
 * Bounding-volume hierarchy over the triangle soup. Brute force is O(rays ×
 * triangles) and a single district is ~6k triangles × ~3k vertices × 24 rays,
 * which is far too slow; a median-split BVH brings a full 15-model bake down
 * to seconds.
 */
function buildBvh(triangles) {
  const bounds = triangles.map(triangle => {
    const min = [Infinity, Infinity, Infinity]
    const max = [-Infinity, -Infinity, -Infinity]
    for (const point of triangle) {
      for (let axis = 0; axis < 3; axis += 1) {
        min[axis] = Math.min(min[axis], point[axis])
        max[axis] = Math.max(max[axis], point[axis])
      }
    }
    return { center: min.map((value, axis) => (value + max[axis]) / 2), max, min }
  })

  const nodes = []

  const build = indices => {
    const min = [Infinity, Infinity, Infinity]
    const max = [-Infinity, -Infinity, -Infinity]
    for (const index of indices) {
      for (let axis = 0; axis < 3; axis += 1) {
        min[axis] = Math.min(min[axis], bounds[index].min[axis])
        max[axis] = Math.max(max[axis], bounds[index].max[axis])
      }
    }

    const node = { left: -1, max, min, right: -1, triangles: undefined }
    const self = nodes.push(node) - 1

    if (indices.length <= 4) {
      node.triangles = indices
      return self
    }

    const extent = max.map((value, axis) => value - min[axis])
    const axis = extent.indexOf(Math.max(...extent))
    const sorted = [...indices].sort((left, right) => bounds[left].center[axis] - bounds[right].center[axis])
    const middle = Math.floor(sorted.length / 2)

    node.left = build(sorted.slice(0, middle))
    node.right = build(sorted.slice(middle))
    return self
  }

  build(triangles.map((_, index) => index))
  return nodes
}

function intersectsBox(node, origin, inverseDirection, maxDistance) {
  let near = 0
  let far = maxDistance
  for (let axis = 0; axis < 3; axis += 1) {
    const t0 = (node.min[axis] - origin[axis]) * inverseDirection[axis]
    const t1 = (node.max[axis] - origin[axis]) * inverseDirection[axis]
    near = Math.max(near, Math.min(t0, t1))
    far = Math.min(far, Math.max(t0, t1))
    if (far < near) return false
  }
  return true
}

/** Möller–Trumbore. Returns hit distance or Infinity. */
function intersectsTriangle([a, b, c], origin, direction, maxDistance) {
  const edge1 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const edge2 = [c[0] - a[0], c[1] - a[1], c[2] - a[2]]
  const pvec = [
    direction[1] * edge2[2] - direction[2] * edge2[1],
    direction[2] * edge2[0] - direction[0] * edge2[2],
    direction[0] * edge2[1] - direction[1] * edge2[0]
  ]
  const determinant = edge1[0] * pvec[0] + edge1[1] * pvec[1] + edge1[2] * pvec[2]
  if (Math.abs(determinant) < EPSILON) return Infinity

  const inverseDeterminant = 1 / determinant
  const tvec = [origin[0] - a[0], origin[1] - a[1], origin[2] - a[2]]
  const u = (tvec[0] * pvec[0] + tvec[1] * pvec[1] + tvec[2] * pvec[2]) * inverseDeterminant
  if (u < 0 || u > 1) return Infinity

  const qvec = [
    tvec[1] * edge1[2] - tvec[2] * edge1[1],
    tvec[2] * edge1[0] - tvec[0] * edge1[2],
    tvec[0] * edge1[1] - tvec[1] * edge1[0]
  ]
  const v = (direction[0] * qvec[0] + direction[1] * qvec[1] + direction[2] * qvec[2]) * inverseDeterminant
  if (v < 0 || u + v > 1) return Infinity

  const distance = (edge2[0] * qvec[0] + edge2[1] * qvec[1] + edge2[2] * qvec[2]) * inverseDeterminant
  return distance > EPSILON && distance < maxDistance ? distance : Infinity
}

function occludes(nodes, triangles, origin, direction, maxDistance) {
  if (nodes.length === 0) return Infinity
  const inverseDirection = direction.map(
    value => 1 / (Math.abs(value) < EPSILON ? EPSILON * Math.sign(value || 1) : value)
  )
  const stack = [0]
  let nearest = Infinity

  while (stack.length > 0) {
    const node = nodes[stack.pop()]
    if (!intersectsBox(node, origin, inverseDirection, Math.min(nearest, maxDistance))) continue

    if (node.triangles) {
      for (const index of node.triangles) {
        const distance = intersectsTriangle(triangles[index], origin, direction, maxDistance)
        if (distance < nearest) nearest = distance
      }
      continue
    }

    stack.push(node.left, node.right)
  }

  return nearest
}

function worldTriangles(meshes) {
  const triangles = []
  for (const mesh of meshes) {
    const positions = mesh.getVerticesData(VertexBuffer.PositionKind)
    const indices = mesh.getIndices()
    if (!positions || !indices) continue
    mesh.computeWorldMatrix(true)
    const matrix = mesh.getWorldMatrix().m
    const transformed = []
    for (let vertex = 0; vertex < positions.length; vertex += 3) {
      const [x, y, z] = [positions[vertex], positions[vertex + 1], positions[vertex + 2]]
      transformed.push([
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
      ])
    }
    for (let index = 0; index + 2 < indices.length; index += 3)
      triangles.push([transformed[indices[index]], transformed[indices[index + 1]], transformed[indices[index + 2]]])
  }
  return triangles
}

/**
 * Splits large triangles so per-vertex AO has somewhere to interpolate.
 *
 * This is the step that makes vertex-baked AO work at all on box geometry. A
 * wall face is two triangles with four corner vertices; if all four corners
 * are equally occluded the whole face just shifts to a flat darker colour,
 * which reads as a different flat colour rather than as shading. Splitting the
 * face into a grid gives the occlusion an actual gradient to describe, which
 * is what makes an untextured surface read as solid geometry.
 *
 * Subdivision is uniform per triangle (a barycentric k×k grid) rather than
 * longest-edge bisection, so the two triangles of a quad always split
 * identically and no T-junction appears along the seam. Vertices are
 * duplicated per triangle, matching how the source geometry is already
 * flat-shaded.
 */
function subdivideForVertexShading(mesh, { maxTriangles, target }) {
  const positions = mesh.getVerticesData(VertexBuffer.PositionKind)
  const normals = mesh.getVerticesData(VertexBuffer.NormalKind)
  const indices = mesh.getIndices()
  if (!positions || !normals || !indices) return 0

  const triangleCount = indices.length / 3
  const plans = []
  let projected = 0

  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    const corners = [indices[triangle * 3], indices[triangle * 3 + 1], indices[triangle * 3 + 2]]
    const points = corners.map(index => [positions[index * 3], positions[index * 3 + 1], positions[index * 3 + 2]])
    const longest = Math.max(
      Math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1], points[1][2] - points[0][2]),
      Math.hypot(points[2][0] - points[1][0], points[2][1] - points[1][1], points[2][2] - points[1][2]),
      Math.hypot(points[0][0] - points[2][0], points[0][1] - points[2][1], points[0][2] - points[2][2])
    )
    const divisions = Math.min(6, Math.max(1, Math.ceil(longest / target)))
    plans.push({ corners, divisions, points })
    projected += divisions * divisions
  }

  // Uniformly relax the target until the mesh fits its share of the budget,
  // so a dense district degrades to coarser shading instead of failing the
  // build's triangle contract.
  let scale = 1
  while (projected > maxTriangles && scale < 8) {
    scale *= 2
    projected = 0
    for (const plan of plans) {
      plan.divisions = Math.min(6, Math.max(1, Math.ceil(plan.divisions / 2)))
      projected += plan.divisions * plan.divisions
    }
  }

  if (plans.every(plan => plan.divisions === 1)) return triangleCount

  const nextPositions = []
  const nextNormals = []
  const nextIndices = []

  for (const { corners, divisions, points } of plans) {
    const [a, b, c] = points
    const normal = corners.map(index => [normals[index * 3], normals[index * 3 + 1], normals[index * 3 + 2]])
    const at = (u, v) => [
      a[0] + (b[0] - a[0]) * u + (c[0] - a[0]) * v,
      a[1] + (b[1] - a[1]) * u + (c[1] - a[1]) * v,
      a[2] + (b[2] - a[2]) * u + (c[2] - a[2]) * v
    ]

    for (let row = 0; row < divisions; row += 1) {
      for (let column = 0; column + row < divisions; column += 1) {
        const u = column / divisions
        const v = row / divisions
        const step = 1 / divisions
        const quads = [
          [
            [u, v],
            [u + step, v],
            [u, v + step]
          ]
        ]
        if (column + row + 1 < divisions)
          quads.push([
            [u + step, v],
            [u + step, v + step],
            [u, v + step]
          ])

        for (const corner of quads) {
          const base = nextPositions.length / 3
          for (const [cu, cv] of corner) {
            nextPositions.push(...at(cu, cv))
            nextNormals.push(...normal[0])
          }
          nextIndices.push(base, base + 1, base + 2)
        }
      }
    }
  }

  mesh.setVerticesData(VertexBuffer.PositionKind, nextPositions, false)
  mesh.setVerticesData(VertexBuffer.NormalKind, nextNormals, false)
  mesh.setIndices(nextIndices)
  return nextIndices.length / 3
}

/**
 * Bakes per-vertex ambient occlusion into COLOR_0 for every mesh under a LOD
 * root. Occluders are every mesh in the same LOD group, so a building's own
 * walls, floors, roof, and interior props shade each other.
 *
 * `floor` keeps fully occluded interiors readable rather than black — the
 * approved districts are open-front rooms whose interiors must stay legible
 * from the city view.
 */
export function bakeAmbientOcclusion(
  scene,
  lodRoot,
  { floor = 0.32, maxTriangles = Infinity, rays = 24, radius = 5.5, shadingTarget = 2.2 } = {}
) {
  const meshes = scene.meshes.filter(mesh => {
    for (let cursor = mesh.parent; cursor; cursor = cursor.parent) if (cursor === lodRoot) return true
    return false
  })
  if (meshes.length === 0) return 0

  // Shared budget: every mesh under this LOD root competes for the model's
  // triangle allowance, so a district with one huge floor slab and a hundred
  // small props spends its subdivision where the large surfaces are.
  const share = Number.isFinite(maxTriangles) ? Math.floor(maxTriangles / meshes.length) : Infinity
  for (const mesh of meshes) subdivideForVertexShading(mesh, { maxTriangles: share, target: shadingTarget })

  const triangles = worldTriangles(meshes)
  if (triangles.length === 0) return 0

  const nodes = buildBvh(triangles)
  const samples = hemisphereSamples(rays)
  let shaded = 0

  for (const mesh of meshes) {
    const positions = mesh.getVerticesData(VertexBuffer.PositionKind)
    const normals = mesh.getVerticesData(VertexBuffer.NormalKind)
    if (!positions || !normals) continue

    mesh.computeWorldMatrix(true)
    const matrix = mesh.getWorldMatrix().m
    const colors = new Float32Array((positions.length / 3) * 4)

    for (let vertex = 0; vertex < positions.length; vertex += 3) {
      const [lx, ly, lz] = [positions[vertex], positions[vertex + 1], positions[vertex + 2]]
      const [nx, ny, nz] = [normals[vertex], normals[vertex + 1], normals[vertex + 2]]
      const normal = [
        matrix[0] * nx + matrix[4] * ny + matrix[8] * nz,
        matrix[1] * nx + matrix[5] * ny + matrix[9] * nz,
        matrix[2] * nx + matrix[6] * ny + matrix[10] * nz
      ]
      const length = Math.hypot(...normal) || 1
      const unit = normal.map(value => value / length)
      const origin = [
        matrix[0] * lx + matrix[4] * ly + matrix[8] * lz + matrix[12] + unit[0] * 0.02,
        matrix[1] * lx + matrix[5] * ly + matrix[9] * lz + matrix[13] + unit[1] * 0.02,
        matrix[2] * lx + matrix[6] * ly + matrix[10] * lz + matrix[14] + unit[2] * 0.02
      ]

      const [tangent, bitangent] = buildBasis(unit)
      let occlusion = 0

      for (const [sx, sy, sz] of samples) {
        const direction = [
          tangent[0] * sx + bitangent[0] * sy + unit[0] * sz,
          tangent[1] * sx + bitangent[1] * sy + unit[1] * sz,
          tangent[2] * sx + bitangent[2] * sy + unit[2] * sz
        ]
        const distance = occludes(nodes, triangles, origin, direction, radius)
        // Near hits darken more than distant ones, so a wall two metres away
        // reads as a soft gradient instead of a hard band.
        if (distance < radius) occlusion += 1 - distance / radius
      }

      const ambient = Math.max(floor, 1 - occlusion / samples.length)
      const target = (vertex / 3) * 4
      colors[target] = ambient
      colors[target + 1] = ambient
      colors[target + 2] = ambient
      colors[target + 3] = 1
    }

    mesh.setVerticesData(VertexBuffer.ColorKind, colors, false)
    mesh.hasVertexAlpha = false
    shaded += 1
  }

  return shaded
}
