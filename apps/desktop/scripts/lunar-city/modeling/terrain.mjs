import { beamBetween, box, cone, cylinder, group, prismRailing, torus } from './primitives.mjs'
import { addSign } from './props.mjs'

// A real city plan, not a hand-picked coordinate list: every district sits
// in a zone with a stated identity, walkways are the minimum-spanning-tree
// over actual distances (plus a couple of deliberate redundant links) so
// the network reads as roads rather than an arbitrary edge list, and every
// building faces the plaza it opens onto. This module is the single
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
export const DISTRICTS = Object.freeze({
  archive: Object.freeze({ position: [42, 0.5, 4], zone: 'civic' }),
  'arts-studio': Object.freeze({ position: [34, 0.7, -18], zone: 'civic' }),
  bus: Object.freeze({ position: [0, 0.55, -2], zone: 'plaza' }),
  council: Object.freeze({ position: [18, 0.35, -10], zone: 'civic' }),
  depot: Object.freeze({ position: [-32, 0.45, 6], zone: 'pipeline' }),
  'engineering-workshop': Object.freeze({ position: [-40, 0.6, 26], zone: 'pipeline' }),
  garden: Object.freeze({ position: [-8, 0.25, 34], zone: 'care' }),
  library: Object.freeze({ position: [-20, 0.8, -24], zone: 'civic' }),
  'release-gatehouse': Object.freeze({ position: [-6, 0.5, 10], zone: 'pipeline' }),
  'research-lab': Object.freeze({ position: [18, 1.1, -34], zone: 'civic' }),
  'review-office': Object.freeze({ position: [-22, 0.7, -8], zone: 'pipeline' }),
  triage: Object.freeze({ position: [8, 0.4, 24], zone: 'care' })
})

// Stable render order -- only affects districtPad's cosmetic size variation
// (index % 3 / index % 2) and far-LOD pad iteration, not identity.
export const DISTRICT_IDS = Object.freeze(Object.keys(DISTRICTS).sort())

function districtPoint(id) {
  return DISTRICTS[id].position
}

function distance(a, b) {
  return Math.hypot(a[0] - b[0], a[2] - b[2])
}

// Prim's algorithm over straight-line district distance -- the walkway
// network is derived from where districts actually are, not hand-picked,
// so moving a district in DISTRICTS automatically reroutes its roads.
function minimumSpanningTree(ids) {
  const remaining = new Set(ids.slice(1))
  const connected = new Set([ids[0]])
  const edges = []
  while (remaining.size > 0) {
    let best = null
    for (const from of connected)
      for (const to of remaining) {
        const cost = distance(districtPoint(from), districtPoint(to))
        if (!best || cost < best.cost) best = { cost, from, to }
      }
    edges.push([best.from, best.to])
    connected.add(best.to)
    remaining.delete(best.to)
  }
  return edges
}

// A pure MST reads a little too much like a tree and not enough like a
// city -- these redundant links are deliberate, thematic shortcuts (review
// reports straight to council; the plaza has two ways in) rather than
// anything the distance metric alone would produce.
const REDUNDANT_LINKS = Object.freeze([
  ['review-office', 'council'],
  ['bus', 'release-gatehouse']
])

function walkwayEdges() {
  const mst = minimumSpanningTree(DISTRICT_IDS)
  const extra = REDUNDANT_LINKS.filter(
    ([from, to]) => !mst.some(([a, b]) => (a === from && b === to) || (a === to && b === from))
  )
  return [...mst, ...extra]
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
  cylinder(scene, `terrain:district-pad:${id}`, {
    diameter: 18 - (index % 3),
    height: 2.2 + (index % 2) * 0.5,
    material: 'lunar-rust',
    parent,
    position: [x, y, z],
    tessellation: 10
  })
  cylinder(scene, `terrain:district-deck:${id}`, {
    diameter: 15.2 - (index % 3),
    height: 0.7,
    material: 'charcoal-structure',
    parent,
    position: [x, y + 1.3, z],
    tessellation: 10
  })
}

function walkway(scene, parent, name, from, to, width = 4.2) {
  const route = group(scene, name, parent)
  beamBetween(scene, `${name}:deck`, [from[0], from[1] + 1.65, from[2]], [to[0], to[1] + 1.65, to[2]], {
    height: 0.65,
    material: 'charcoal-structure',
    parent: route,
    width
  })
  beamBetween(
    scene,
    `${name}:signal`,
    [from[0], from[1] + 2.02, from[2] - width * 0.32],
    [to[0], to[1] + 2.02, to[2] - width * 0.32],
    { height: 0.11, material: 'garden-green', parent: route, width: 0.16 }
  )
  return route
}

export function buildTerrain(scene) {
  const root = group(scene, 'terrain:root')
  const near = group(scene, 'terrain:lod:near', root)
  const far = group(scene, 'terrain:lod:far', root)
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
      material: 'garden-green',
      parent: dressing,
      position: [x, y + 2.25, z],
      tessellation: 8
    })
  }
  DISTRICT_IDS.forEach((id, index) => districtPad(scene, near, id, index, districtPoint(id)))

  const walkways = group(scene, 'terrain:walkways', near)
  walkwayEdges().forEach(([from, to]) =>
    walkway(scene, walkways, `terrain:walkway:${from}-${to}`, districtPoint(from), districtPoint(to))
  )

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
    const fromPoint = districtPoint(from)
    const toPoint = districtPoint(to)
    const link = group(scene, `navigation:link:${id}`, walkable)
    beamBetween(
      scene,
      `navigation:link:${id}:mesh`,
      [fromPoint[0], fromPoint[1] + 1.75, fromPoint[2]],
      [toPoint[0], toPoint[1] + 1.75, toPoint[2]],
      {
        height: 0.14,
        material: 'charcoal-structure',
        parent: link,
        width: 3.5
      }
    )
  })
  return root
}
