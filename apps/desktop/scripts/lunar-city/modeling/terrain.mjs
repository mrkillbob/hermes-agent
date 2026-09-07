import { Mesh, VertexData } from './babylon.mjs'
import { beamBetween, box, cone, cylinder, group, prismRailing, sphere, torus } from './primitives.mjs'
import { addSign } from './props.mjs'

// A real city plan, not a hand-picked coordinate list: every district sits
// in a zone with a stated identity, and the walkway plan is a set of
// deliberately routed civic, pipeline, and care spines so the network reads
// as roads rather than arbitrary straight lines. Every building faces the
// plaza it opens onto. This module is the single
// source of truth for layout -- terrain.mjs consumes it to place pads and
// walkways, and `node scripts/lunar-city/sync-district-layout.mjs`
// regenerates world-manifest.v2.json's per-model transforms and
// destinations from it, so the three previously-independent hand-kept
// copies of these coordinates can't drift out of sync again.
//
// Zones, by identity:
// - plaza: the front door. bus stays where it always was.
// - pipeline: the literal build -> review -> ship chain (engineering
//   workshop -> depot -> review office -> release gatehouse), arranged as
//   a path that gets closer to the plaza as work moves toward release.
// - civic: leadership, knowledge, culture, and record-keeping. Council
//   sits nearest the plaza (leadership overlooks the front door); library,
//   research lab, arts studio, and archive spread out from there.
// - care: triage and garden, a small quiet cluster apart from the
//   industrial and civic halves.
// Positions below are the output of scripts/lunar-city/layout-solver.mjs,
// not hand-picked: every pair is checked against the sum of both
// buildings' actual footprint radii (post-shellScale width/depth from
// buildings.mjs and primitives.mjs's wireframeShell profile tables) plus a
// walkway/breathing-room margin, and against camera.bounds in
// world-manifest.v2.json (note the bounds are asymmetric -- Z only goes to
// +36 vs +60 on every other side -- which is why civic, the largest zone,
// sits toward -Z). The previous hand-adjusted values here still had real
// overlaps once wireframeShell's per-building width multipliers (e.g.
// council x1.42) were accounted for, and placed garden at Z=54, outside
// the camera bounds entirely. Re-run the solver and paste its output here
// if any building's footprint size or a shellScale entry changes.
export const DISTRICTS = Object.freeze({
  archive: Object.freeze({ position: [28.93, 0.5, -44.95], zone: 'civic' }),
  'arts-studio': Object.freeze({ position: [1.65, 0.7, -47.3], zone: 'civic' }),
  bus: Object.freeze({ position: [0, 0.55, 0], zone: 'plaza' }),
  council: Object.freeze({ position: [14.13, 0.35, -21.84], zone: 'civic' }),
  depot: Object.freeze({ position: [-8.44, 0.45, 23.2], zone: 'pipeline' }),
  'engineering-workshop': Object.freeze({ position: [-49.5, 0.6, 10.86], zone: 'pipeline' }),
  garden: Object.freeze({ position: [20.11, 0.25, 26.5], zone: 'care' }),
  library: Object.freeze({ position: [-14.97, 0.8, -22.54], zone: 'civic' }),
  'release-gatehouse': Object.freeze({ position: [-23.88, 0.5, 2.26], zone: 'pipeline' }),
  'research-lab': Object.freeze({ position: [-29.24, 1.1, -47.3], zone: 'civic' }),
  'review-office': Object.freeze({ position: [-32.79, 0.7, 28.51], zone: 'pipeline' }),
  triage: Object.freeze({ position: [20.49, 0.4, 3.48], zone: 'care' })
})

// Stable render order -- only affects districtPad's cosmetic size variation
// (index % 3 / index % 2) and far-LOD pad iteration, not identity.
export const DISTRICT_IDS = Object.freeze(Object.keys(DISTRICTS).sort())

function districtPoint(id) {
  return DISTRICTS[id].position
}

// Roads are planned as a small number of architectural spines. Each bend is
// a public planning point in the same coordinate system as DISTRICTS: it is
// where the road eases around a building cluster instead of cutting across
// another district or stacking on top of a neighboring link.
// Bend points are each route's midpoint nudged perpendicular by ~12% of
// the segment length (capped at 6 units) -- a gentle curve, not a hand-
// placed detour, recomputed directly from the current DISTRICTS positions
// above. Re-run this same computation (or layout-solver.mjs's positions)
// if DISTRICTS changes; a stale bend from a previous layout can route a
// road through an unrelated building.
export const WALKWAY_ROUTES = Object.freeze([
  Object.freeze({ from: 'archive', to: 'arts-studio', bend: [15.57, -49.4] }),
  Object.freeze({ from: 'arts-studio', to: 'council', bend: [4.83, -33.07] }),
  Object.freeze({ from: 'arts-studio', to: 'research-lab', bend: [-13.79, -51.01] }),
  Object.freeze({ from: 'bus', to: 'council', bend: [9.69, -9.22] }),
  Object.freeze({ from: 'bus', to: 'release-gatehouse', bend: [-12.21, -1.74] }),
  Object.freeze({ from: 'bus', to: 'review-office', bend: [-19.82, 10.32] }),
  Object.freeze({ from: 'depot', to: 'engineering-workshop', bend: [-27.49, 12.1] }),
  Object.freeze({ from: 'release-gatehouse', to: 'triage', bend: [-1.84, 8.19] }),
  Object.freeze({ from: 'review-office', to: 'depot', bend: [-19.98, 28.78] }),
  Object.freeze({ from: 'review-office', to: 'library', bend: [-18.22, 4.96] }),
  Object.freeze({ from: 'triage', to: 'garden', bend: [17.54, 14.94] })
])

const ROAD_SURFACE_Y = 0.42
const ROAD_HEIGHT = 0.32
const ROAD_SAMPLES = 8
const ROAD_ENTRY_OFFSET = 7.8

function routeSpec(from, to) {
  return WALKWAY_ROUTES.find(
    route => (route.from === from && route.to === to) || (route.from === to && route.to === from)
  )
}

export function roadRoutePoints(from, to, elevation = ROAD_SURFACE_Y) {
  const route = routeSpec(from, to)
  if (!route) throw new Error(`missing planned walkway route ${from}-${to}`)
  const fromPoint = districtPoint(from)
  const toPoint = districtPoint(to)
  const distanceBetween = Math.hypot(toPoint[0] - fromPoint[0], toPoint[2] - fromPoint[2])
  const directionX = (toPoint[0] - fromPoint[0]) / distanceBetween
  const directionZ = (toPoint[2] - fromPoint[2]) / distanceBetween
  // Long links can reach the pad perimeter directly, but short civic links
  // must retain a real road body. Without this clamp, subtracting the entry
  // offset twice reverses the endpoints and creates tiny overlapping beams.
  const entryOffset = Math.min(ROAD_ENTRY_OFFSET, distanceBetween * 0.24)
  const start = [fromPoint[0] + directionX * entryOffset, fromPoint[2] + directionZ * entryOffset]
  const end = [toPoint[0] - directionX * entryOffset, toPoint[2] - directionZ * entryOffset]
  const bend = route.bend
  const points = []
  for (let index = 0; index <= ROAD_SAMPLES; index += 1) {
    const t = index / ROAD_SAMPLES
    const inverse = 1 - t
    points.push([
      inverse * inverse * start[0] + 2 * inverse * t * bend[0] + t * t * end[0],
      elevation,
      inverse * inverse * start[1] + 2 * inverse * t * bend[1] + t * t * end[1]
    ])
  }
  return points
}

function walkwayEdges() {
  return WALKWAY_ROUTES.map(({ from, to }) => [from, to])
}

// Every building faces the plaza it opens onto -- replaces the old
// per-model hand-jittered rotation with a real yaw-to-target computed from
// actual position, matching the same [0, 4] plaza anchor the camera
// overview target and bus/release-gatehouse already use.
const PLAZA_TARGET = Object.freeze([0, 4])

export function facingRotationY(id) {
  const [x, , z] = districtPoint(id)
  return Math.atan2(PLAZA_TARGET[0] - x, PLAZA_TARGET[1] - z)
}

function districtPad(scene, parent, id, index, [x, y, z]) {
  const pad = cylinder(scene, `terrain:district-pad:${id}`, {
    diameter: 18 - (index % 3),
    height: 2.2 + (index % 2) * 0.5,
    material: 'lunar-rust',
    parent,
    position: [x, y, z],
    tessellation: 6
  })
  const deck = cylinder(scene, `terrain:district-deck:${id}`, {
    diameter: 15.2 - (index % 3),
    height: 0.7,
    material: 'charcoal-structure',
    parent,
    position: [x, y + 1.3, z],
    tessellation: 6
  })
  return [pad, deck]
}

function districtUtilityPods(scene, parent, id, index, [x, y, z]) {
  // Two compact service modules make each hex plate read as an inhabited
  // colony tile instead of a single isolated landmark. Keep the meshes
  // direct children of the near LOD so the normal material merge still folds
  // these low-poly details into the existing terrain draw calls.
  const meshes = []
  for (const slot of [0, 1]) {
    const angle = index * 0.47 + slot * Math.PI
    const offsetX = Math.cos(angle) * 4.9
    const offsetZ = Math.sin(angle) * 4.9
    const body = cylinder(scene, `terrain:utility-pod:${id}:${slot}`, {
      diameter: 1.8 - slot * 0.2,
      height: 0.9,
      material: 'bone-metal',
      parent,
      position: [x + offsetX, y + 2.05, z + offsetZ],
      tessellation: 6
    })
    meshes.push(body)
    meshes.push(
      sphere(scene, `terrain:utility-cap:${id}:${slot}`, {
        diameter: 1.45 - slot * 0.15,
        material: 'lunar-rust',
        parent,
        position: [x + offsetX, y + 2.55, z + offsetZ],
        scale: [1, 0.35, 1],
        segments: 6
      })
    )
    meshes.push(
      box(scene, `terrain:utility-signal:${id}:${slot}`, {
        depth: 0.08,
        height: 0.12,
        material: 'signal-emissive',
        parent,
        position: [x + offsetX, y + 2.18, z + offsetZ - 0.82],
        width: 0.72
      })
    )
  }
  return meshes
}

/**
 * Build the low-cost planetary ground outside the authored settlement island.
 * The center is lower than the rim, so the existing island reads as a colony
 * sitting in a shallow impact basin instead of a rock floating in space.
 * Keeping this in its own semantic group lets the runtime and Blender stage
 * preserve the settlement's current LOD and navigation geometry unchanged.
 */
function concaveWorldSurface(scene, name, parent, { radius, centerY, rimRise, rings, segments }) {
  const positions = [0, centerY, 0]
  const indices = []
  for (let ring = 1; ring <= rings; ring += 1) {
    const t = ring / rings
    const ringRadius = radius * t
    const y = centerY + rimRise * t * t
    for (let segment = 0; segment < segments; segment += 1) {
      const angle = (segment / segments) * Math.PI * 2
      positions.push(Math.cos(angle) * ringRadius, y, Math.sin(angle) * ringRadius)
    }
  }
  for (let segment = 0; segment < segments; segment += 1) {
    const next = (segment + 1) % segments
    indices.push(0, 1 + next, 1 + segment)
  }
  for (let ring = 1; ring < rings; ring += 1) {
    const current = 1 + (ring - 1) * segments
    const next = current + segments
    for (let segment = 0; segment < segments; segment += 1) {
      const nextSegment = (segment + 1) % segments
      indices.push(current + segment, next + segment, next + nextSegment)
      indices.push(current + segment, next + nextSegment, current + nextSegment)
    }
  }
  const mesh = new Mesh(name, scene)
  const vertexData = new VertexData()
  vertexData.positions = positions
  vertexData.indices = indices
  VertexData.ComputeNormals(positions, indices, (vertexData.normals = []))
  vertexData.applyToMesh(mesh)
  mesh.material = scene.getMaterialByName('lunar-rust')
  mesh.parent = parent
  mesh.isPickable = false
  mesh.metadata = {
    keepSeparate: true,
    gltf: { extras: { semantic: name, role: 'planetary-ground' } }
  }
  return mesh
}

export function addPlanetaryGround(scene, root) {
  if (!root || root.name !== 'terrain:root') throw new Error('planetary ground requires terrain:root')
  const near = scene.getTransformNodeByName('terrain:lod:near')
  const far = scene.getTransformNodeByName('terrain:lod:far')
  if (!near || !far) throw new Error('planetary ground requires near and far terrain LOD roots')
  const worldSurface = group(scene, 'terrain:world-surface', near, { position: [0, 0, 3] })
  concaveWorldSurface(scene, 'terrain:world-surface:mesh', worldSurface, {
    radius: 180,
    centerY: -5.8,
    rimRise: 8,
    rings: 16,
    segments: 64
  })
  const farWorldSurface = group(scene, 'terrain:far:world-surface', far, { position: [0, 0, 3] })
  concaveWorldSurface(scene, 'terrain:far:world-surface:mesh', farWorldSurface, {
    radius: 180,
    centerY: -5.8,
    rimRise: 8,
    rings: 8,
    segments: 32
  })
}

function mergeBaselineMeshes(scene, parent, meshes) {
  const byMaterial = new Map()
  for (const mesh of meshes) {
    const materialId = mesh.material?.uniqueId
    if (materialId === undefined) continue
    if (!byMaterial.has(materialId)) byMaterial.set(materialId, [])
    byMaterial.get(materialId).push(mesh)
  }
  let index = 0
  for (const parts of byMaterial.values()) {
    const merged = Mesh.MergeMeshes(parts, true, true, undefined, false, true)
    if (!merged) throw new Error('unable to merge colony-builder baseline geometry')
    merged.name = `terrain:reference-baseline:${index}`
    merged.parent = parent
    merged.isPickable = false
    merged.metadata = {
      keepSeparate: true,
      gltf: { extras: { role: 'reference-baseline', semantic: merged.name } }
    }
    index += 1
  }
}

export function addColonyBuilderBaseline(scene, root, { mergeMeshes = false } = {}) {
  if (!root || root.name !== 'terrain:root') throw new Error('colony-builder baseline requires terrain:root')
  const near = scene.getTransformNodeByName('terrain:lod:near')
  if (!near) throw new Error('colony-builder baseline requires terrain:lod:near')
  const marker =
    scene.getTransformNodeByName('terrain:reference-baseline') ?? group(scene, 'terrain:reference-baseline', near)
  marker.metadata = {
    gltf: {
      extras: {
        density: 'two-utility-pods-per-district',
        districtPads: 'hexagonal',
        semantic: 'terrain:reference-baseline'
      }
    }
  }
  if (marker.metadata.baselineApplied) return marker
  const baselineMeshes = []
  DISTRICT_IDS.forEach((id, index) => {
    const point = districtPoint(id)
    baselineMeshes.push(...districtPad(scene, near, id, index, point))
    baselineMeshes.push(...districtUtilityPods(scene, near, id, index, point))
  })
  if (mergeMeshes) mergeBaselineMeshes(scene, marker, baselineMeshes)
  marker.metadata.baselineApplied = true
  return marker
}

function walkway(scene, parent, name, from, to, roadMeshes, width = 4.2) {
  const route = group(scene, name, parent)
  const points = roadRoutePoints(from, to)
  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index]
    const end = points[index + 1]
    const deck = beamBetween(scene, `${name}:segment:${index}:deck`, start, end, {
      height: ROAD_HEIGHT,
      material: 'lunar-rust',
      parent: route,
      width
    })
    roadMeshes.deck.push(deck)
    const dx = end[0] - start[0]
    const dz = end[2] - start[2]
    const length = Math.hypot(dx, dz)
    const sideX = (-dz / length) * width * 0.32
    const sideZ = (dx / length) * width * 0.32
    const signal = beamBetween(
      scene,
      `${name}:segment:${index}:signal`,
      [start[0] + sideX, start[1] + ROAD_HEIGHT * 0.7, start[2] + sideZ],
      [end[0] + sideX, end[1] + ROAD_HEIGHT * 0.7, end[2] + sideZ],
      { height: 0.14, material: 'signal-emissive', parent: route, width: 0.44 }
    )
    roadMeshes.signal.push(signal)
  }
  return route
}

function mergeRoadMeshes(meshes, parent, name) {
  if (meshes.length === 0) return
  const merged = Mesh.MergeMeshes(meshes, true, true, undefined, false, true)
  if (!merged) throw new Error(`unable to merge planned road geometry ${name}`)
  merged.name = name
  merged.parent = parent
  merged.isPickable = false
  merged.metadata = { keepSeparate: true, gltf: { extras: { semantic: name, role: 'planned-road' } } }
}

export function addPlannedWalkways(scene, root) {
  if (!root || root.name !== 'terrain:root') throw new Error('planned walkways require terrain:root')
  const walkways = scene.getTransformNodeByName('terrain:walkways')
  if (!walkways) throw new Error('planned walkways require terrain:walkways')
  for (const child of walkways.getChildren().slice()) child.dispose(false)
  const roadMeshes = { deck: [], signal: [] }
  for (const { from, to } of WALKWAY_ROUTES)
    walkway(scene, walkways, `terrain:walkway:${from}-${to}`, from, to, roadMeshes)
  mergeRoadMeshes(roadMeshes.deck, walkways, 'terrain:walkways:deck')
  mergeRoadMeshes(roadMeshes.signal, walkways, 'terrain:walkways:signals')
  return walkways
}

export function buildTerrain(scene) {
  const root = group(scene, 'terrain:root')
  const near = group(scene, 'terrain:lod:near', root)
  const far = group(scene, 'terrain:lod:far', root)
  addPlanetaryGround(scene, root)
  const cliffs = group(scene, 'terrain:cliffs', near)
  cylinder(scene, 'terrain:base:island', {
    diameter: 92,
    height: 5.5,
    material: 'lunar-rust',
    parent: cliffs,
    position: [0, -2.6, 3],
    scale: [1, 1, 0.82],
    tessellation: 14
  })
  cylinder(scene, 'terrain:base:upper', {
    diameter: 76,
    height: 2.8,
    material: 'lunar-rust',
    parent: cliffs,
    position: [-2, -0.5, 2],
    scale: [1, 1, 0.8],
    tessellation: 13
  })
  const cliffSpurs = [
    [-42, -2, -25],
    [43, -2.2, -27],
    [-45, -2.4, 24],
    [42, -2, 26],
    [-17, -2.7, 43],
    [20, -2.5, 44]
  ]
  cliffSpurs.forEach(([x, y, z], index) =>
    cone(scene, `terrain:cliff-spur:${index}`, {
      diameterBottom: 14,
      diameterTop: 9,
      height: 7 + (index % 3),
      material: 'lunar-rust',
      parent: cliffs,
      position: [x, y, z],
      rotation: [0, index * 0.31, 0],
      tessellation: 7
    })
  )
  const craters = group(scene, 'terrain:craters', near)
  ;[
    [-34, 0.4, 20, 5.2],
    [18, 0.45, 39, 4.4],
    [38, 0.72, -8, 3.6],
    [-16, 0.65, -31, 3.1],
    [9, 0.52, 8, 2.5]
  ].forEach(([x, y, z, diameter], index) => {
    torus(scene, `terrain:crater:${index}`, {
      diameter,
      material: 'charcoal-structure',
      parent: craters,
      position: [x, y + 1.18, z],
      rotation: [Math.PI / 2, 0, index * 0.24],
      tessellation: 10,
      thickness: 0.22
    })
  })
  const dressing = group(scene, 'terrain:regolith-dressing', near)
  ;[
    [-39, 0.6, -9, 1.2],
    [-35, 0.8, 8, 0.9],
    [-20, 0.9, 39, 1.1],
    [12, 0.7, 40, 0.8],
    [39, 0.9, 25, 1.3],
    [41, 0.8, -18, 0.95],
    [2, 0.9, -31, 1],
    [-12, 0.75, -26, 0.72]
  ].forEach(([x, y, z, size], index) => {
    cone(scene, `terrain:regolith-rock:${index}`, {
      diameterBottom: size * 1.7,
      diameterTop: size * 0.45,
      height: size * 1.25,
      material: index % 2 ? 'charcoal-structure' : 'lunar-rust',
      parent: dressing,
      position: [x, y + size * 0.55, z],
      rotation: [0, index * 0.37, 0],
      tessellation: 6
    })
  })
  for (const [index, [x, y, z]] of [
    [0, [-1, 0.5, 12]],
    [1, [17, 0.7, -3]],
    [2, [-17, 0.6, 8]]
  ]) {
    cylinder(scene, `terrain:activity-beacon:${index}`, {
      diameter: 0.38,
      height: 2.2,
      material: 'bone-metal',
      parent: dressing,
      position: [x, y + 1.1, z],
      tessellation: 8
    })
    cylinder(scene, `terrain:activity-beacon:signal:${index}`, {
      diameter: 0.72,
      height: 0.16,
      material: 'signal-emissive',
      parent: dressing,
      position: [x, y + 2.25, z],
      tessellation: 8
    })
  }
  addColonyBuilderBaseline(scene, root)

  group(scene, 'terrain:walkways', near)
  addPlannedWalkways(scene, root)

  const busStop = group(scene, 'terrain:bus-stop', near, { position: [0, 2.1, -1] })
  box(scene, 'terrain:bus-stop:platform', {
    depth: 8.2,
    height: 0.55,
    material: 'charcoal-structure',
    parent: busStop,
    width: 12
  })
  box(scene, 'terrain:bus-stop:canopy', {
    depth: 4.5,
    height: 0.45,
    material: 'bone-metal',
    parent: busStop,
    position: [0, 3.1, 0],
    width: 8.7
  })
  for (const x of [-3.8, 3.8])
    box(scene, `terrain:bus-stop:post:${x}`, {
      depth: 0.42,
      height: 3.2,
      material: 'bone-metal',
      parent: busStop,
      position: [x, 1.5, 0],
      width: 0.42
    })
  box(scene, 'terrain:bus-stop:bench', {
    depth: 1.25,
    height: 0.65,
    material: 'lunar-rust',
    parent: busStop,
    position: [0, 0.65, 1.2],
    width: 5.6
  })
  addSign(scene, 'terrain:bus-stop:sign', busStop, { accent: 'garden-green', position: [0, 2.15, -1.75], width: 4.8 })
  prismRailing(scene, 'terrain:bus-stop:railing', busStop, 0, 0, 11.4, 7.7, 'bone-metal')

  cylinder(scene, 'terrain:far:island', {
    diameter: 91,
    height: 5.2,
    material: 'lunar-rust',
    parent: far,
    position: [0, -2.7, 3],
    scale: [1, 1, 0.82],
    tessellation: 12
  })
  const farWorldSurface = group(scene, 'terrain:far:world-surface', far, { position: [0, 0, 3] })
  concaveWorldSurface(scene, 'terrain:far:world-surface:mesh', farWorldSurface, {
    radius: 180,
    centerY: -5.8,
    rimRise: 8,
    rings: 8,
    segments: 32
  })
  for (const id of DISTRICT_IDS) {
    const point = districtPoint(id)
    cylinder(scene, `terrain:far:pad:${id}`, {
      diameter: 14,
      height: 1.2,
      material: 'charcoal-structure',
      parent: far,
      position: [point[0], point[1] + 0.4, point[2]],
      tessellation: 8
    })
  }
  return root
}

export function buildNavigation(scene) {
  const root = group(scene, 'navigation:root')
  const walkable = group(scene, 'navigation:walkable', root)
  DISTRICT_IDS.forEach(id => {
    const point = districtPoint(id)
    cylinder(scene, `navigation:area:${id}`, {
      diameter: 13,
      height: 0.16,
      material: 'charcoal-structure',
      parent: walkable,
      position: [point[0], point[1] + 1.7, point[2]],
      tessellation: 8
    })
  })
  walkwayEdges().forEach(([from, to]) => {
    const id = `${from}-${to}`
    const link = group(scene, `navigation:link:${id}`, walkable)
    const points = roadRoutePoints(from, to, 1.75)
    for (let index = 0; index < points.length - 1; index += 1)
      beamBetween(scene, `navigation:link:${id}:segment:${index}`, points[index], points[index + 1], {
        height: 0.14,
        material: 'charcoal-structure',
        parent: link,
        width: 3.5
      })
  })
  return root
}
