import { beamBetween, box, cone, cylinder, group, prismRailing } from './primitives.mjs'
import { addSign } from './props.mjs'

const DISTRICTS = Object.freeze([
  [-28, 0.8, -18],
  [25, 1.1, -22],
  [-31, 0.45, 12],
  [33, 0.7, 10],
  [4, 0.4, 25],
  [-8, 0.25, 34],
  [27, 0.35, 31],
  [0, 0.55, -1]
])

function districtPad(scene, parent, index, [x, y, z]) {
  cylinder(scene, `terrain:district-pad:${index}`, {
    diameter: 18 - (index % 3),
    height: 2.2 + (index % 2) * 0.5,
    material: 'lunar-rust',
    parent,
    position: [x, y, z],
    tessellation: 10
  })
  cylinder(scene, `terrain:district-deck:${index}`, {
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
  DISTRICTS.forEach((point, index) => districtPad(scene, near, index, point))

  const walkways = group(scene, 'terrain:walkways', near)
  walkway(scene, walkways, 'terrain:walkway:library-research', DISTRICTS[0], DISTRICTS[1], 4.8)
  walkway(scene, walkways, 'terrain:walkway:library-bus', DISTRICTS[0], DISTRICTS[7])
  walkway(scene, walkways, 'terrain:walkway:research-bus', DISTRICTS[1], DISTRICTS[7])
  walkway(scene, walkways, 'terrain:walkway:depot-garden', DISTRICTS[2], DISTRICTS[5])
  walkway(scene, walkways, 'terrain:walkway:review-triage', DISTRICTS[3], DISTRICTS[4])
  walkway(scene, walkways, 'terrain:walkway:triage-garden', DISTRICTS[4], DISTRICTS[5])
  walkway(scene, walkways, 'terrain:walkway:triage-council', DISTRICTS[4], DISTRICTS[6])
  walkway(scene, walkways, 'terrain:walkway:bus-triage', DISTRICTS[7], DISTRICTS[4])

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
  for (const [index, point] of DISTRICTS.entries())
    cylinder(scene, `terrain:far:pad:${index}`, {
      diameter: 14,
      height: 1.2,
      material: 'charcoal-structure',
      parent: far,
      position: [point[0], point[1] + 0.4, point[2]],
      tessellation: 8
    })
  return root
}

export function buildNavigation(scene) {
  const root = group(scene, 'navigation:root')
  const walkable = group(scene, 'navigation:walkable', root)
  DISTRICTS.forEach((point, index) =>
    cylinder(scene, `navigation:area:${index}`, {
      diameter: 13,
      height: 0.16,
      material: 'charcoal-structure',
      parent: walkable,
      position: [point[0], point[1] + 1.7, point[2]],
      tessellation: 8
    })
  )
  const links = [
    ['library-research', DISTRICTS[0], DISTRICTS[1]],
    ['library-bus', DISTRICTS[0], DISTRICTS[7]],
    ['research-bus', DISTRICTS[1], DISTRICTS[7]],
    ['depot-garden', DISTRICTS[2], DISTRICTS[5]],
    ['review-triage', DISTRICTS[3], DISTRICTS[4]],
    ['triage-garden', DISTRICTS[4], DISTRICTS[5]],
    ['triage-council', DISTRICTS[4], DISTRICTS[6]],
    ['bus-triage', DISTRICTS[7], DISTRICTS[4]]
  ]
  links.forEach(([id, from, to]) => {
    const link = group(scene, `navigation:link:${id}`, walkable)
    beamBetween(scene, `navigation:link:${id}:mesh`, [from[0], from[1] + 1.75, from[2]], [to[0], to[1] + 1.75, to[2]], {
      height: 0.14,
      material: 'charcoal-structure',
      parent: link,
      width: 3.5
    })
  })
  return root
}
