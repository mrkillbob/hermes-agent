import { scalarClip } from './animation.mjs'
import { box, cone, cylinder, group, prismRailing, sphere, torus } from './primitives.mjs'
import { addConsoleBank, addPlants, addPortal, addSign, addTelescope, addWorkbenches } from './props.mjs'

function openFrontShell(scene, id, { accent, depth, height, width }) {
  const root = group(scene, `${id}:root`)
  const near = group(scene, `${id}:lod:near`, root)
  const far = group(scene, `${id}:lod:far`, root)
  const shell = group(scene, `${id}:shell`, near)
  box(scene, `${id}:floor`, {
    depth,
    height: 0.7,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, 0.35, 0],
    width
  })
  box(scene, `${id}:back-wall`, {
    depth: 0.6,
    height,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, height / 2, -depth / 2],
    width
  })
  box(scene, `${id}:left-wall`, {
    depth,
    height: height * 0.82,
    material: 'bone-metal',
    parent: shell,
    position: [-width / 2, height * 0.41, 0],
    width: 0.7
  })
  box(scene, `${id}:right-wall`, {
    depth,
    height: height * 0.82,
    material: 'bone-metal',
    parent: shell,
    position: [width / 2, height * 0.41, 0],
    width: 0.7
  })
  const roof = group(scene, `${id}:roof`, near)
  box(scene, `${id}:roof:center`, {
    depth: depth + 0.8,
    height: 0.65,
    material: 'bone-metal',
    parent: roof,
    position: [0, height + 0.25, -0.1],
    width: width + 1.2
  })
  box(scene, `${id}:roof:left-bevel`, {
    depth: depth + 0.9,
    height: 0.55,
    material: 'charcoal-structure',
    parent: roof,
    position: [-width * 0.37, height - 0.02, -0.05],
    rotation: [0, 0, -0.26],
    width: width * 0.28
  })
  box(scene, `${id}:roof:right-bevel`, {
    depth: depth + 0.9,
    height: 0.55,
    material: 'charcoal-structure',
    parent: roof,
    position: [width * 0.37, height - 0.02, -0.05],
    rotation: [0, 0, 0.26],
    width: width * 0.28
  })
  const entrance = group(scene, `${id}:entrance`, near, { position: [0, 1.9, depth / 2 + 0.14] })
  box(scene, `${id}:entrance:left`, {
    depth: 0.35,
    height: 3.8,
    material: 'bone-metal',
    parent: entrance,
    position: [-width * 0.29, 0, 0],
    width: 0.55
  })
  box(scene, `${id}:entrance:right`, {
    depth: 0.35,
    height: 3.8,
    material: 'bone-metal',
    parent: entrance,
    position: [width * 0.29, 0, 0],
    width: 0.55
  })
  box(scene, `${id}:entrance:header`, {
    depth: 0.38,
    height: 0.5,
    material: accent,
    parent: entrance,
    position: [0, 1.65, 0],
    width: width * 0.62
  })
  group(scene, `${id}:camera`, root, { position: [0, height * 0.58, depth + 5] })
  box(scene, `${id}:far:shell`, {
    depth,
    height: height * 0.86,
    material: 'charcoal-structure',
    parent: far,
    position: [0, height * 0.43, -0.25],
    width
  })
  box(scene, `${id}:far:frame`, {
    depth: depth + 0.3,
    height: 0.5,
    material: 'bone-metal',
    parent: far,
    position: [0, height * 0.88, -0.2],
    width: width + 0.5
  })
  box(scene, `${id}:far:signal`, {
    depth: 0.08,
    height: 0.28,
    material: accent,
    parent: far,
    position: [0, height * 0.7, depth / 2 + 0.08],
    width: width * 0.45
  })
  return { entrance, far, near, roof, root, shell }
}

export function buildLibrary(scene) {
  const building = openFrontShell(scene, 'library', { accent: 'archive-emissive', depth: 12, height: 9.8, width: 15 })
  addSign(scene, 'library:sign', building.near, { accent: 'archive-emissive', position: [0, 8.5, 6.25], width: 5.2 })
  const stacks = group(scene, 'library:archive-stacks', building.near)
  for (const side of [-1, 1]) {
    for (let column = 0; column < 3; column += 1) {
      const x = side * (4.1 + column * 1.35)
      box(scene, `library:shelf:${side}:${column}`, {
        depth: 0.85,
        height: 6.6,
        material: 'charcoal-structure',
        parent: stacks,
        position: [x, 4.15, -5.25],
        width: 1.05
      })
      for (let row = 0; row < 5; row += 1)
        box(scene, `library:book:${side}:${column}:${row}`, {
          depth: 0.3,
          height: 0.55,
          material: 'archive-emissive',
          parent: stacks,
          position: [x, 1.55 + row * 1.05, -4.77],
          width: 0.72
        })
    }
  }
  box(scene, 'library:desk', {
    depth: 2.8,
    height: 1.2,
    material: 'bone-metal',
    parent: building.near,
    position: [0, 1.25, 0.8],
    width: 5
  })
  const orb = group(scene, 'library:violet-orb', building.near, { position: [2.8, 2.45, -1] })
  sphere(scene, 'library:violet-orb:core', { diameter: 1.35, material: 'archive-emissive', parent: orb, segments: 8 })
  torus(scene, 'library:violet-orb:ring', {
    diameter: 1.8,
    material: 'archive-emissive',
    parent: orb,
    rotation: [0.9, 0.2, 0],
    tessellation: 10,
    thickness: 0.1
  })
  group(scene, 'library:leader-anchor', building.near, { position: [-2.1, 1, 0.7] })
  scalarClip(scene, 'lights-idle', orb, 'rotation.y', Math.PI * 2, { duration: 100 })
  return building.root
}

export function buildResearchLab(scene) {
  const building = openFrontShell(scene, 'research-lab', {
    accent: 'signal-emissive',
    depth: 14,
    height: 10.5,
    width: 18
  })
  addSign(scene, 'research-lab:sign', building.near, {
    accent: 'signal-emissive',
    position: [0, 9.25, 7.25],
    width: 6.5
  })
  addConsoleBank(scene, 'research-lab:consoles', building.near, {
    accent: 'signal-emissive',
    count: 5,
    position: [0, 1.25, -6.1],
    width: 1.35
  })
  const workbenches = addWorkbenches(scene, 'research-lab:workbenches', building.near, {
    accent: 'signal-emissive',
    count: 3,
    position: [-2.2, 0.75, 2.4]
  })
  const specimen = group(scene, 'research-lab:specimen', building.near, { position: [5.9, 2.3, -3.3] })
  cylinder(scene, 'research-lab:specimen:tank', {
    diameter: 2.1,
    height: 4.2,
    material: 'signal-emissive',
    parent: specimen,
    tessellation: 10
  })
  sphere(scene, 'research-lab:specimen:sample', {
    diameter: 0.68,
    material: 'garden-green',
    parent: specimen,
    segments: 7
  })
  cylinder(scene, 'research-lab:specimen:cap', {
    diameter: 2.35,
    height: 0.35,
    material: 'bone-metal',
    parent: specimen,
    position: [0, 2.15, 0],
    tessellation: 10
  })
  addTelescope(scene, building.near)
  scalarClip(scene, 'lights-idle', specimen, 'rotation.y', 0.14, { duration: 48 })
  scalarClip(scene, 'workbench-cycle', workbenches, 'rotation.y', 0.045, { duration: 60 })
  return building.root
}

export function buildDepot(scene) {
  const building = openFrontShell(scene, 'depot', { accent: 'signal-emissive', depth: 11, height: 8.5, width: 14.5 })
  addSign(scene, 'depot:sign', building.near, { accent: 'signal-emissive', position: [0, 7.35, 5.75], width: 5.4 })
  const shelves = group(scene, 'depot:stocked-shelves', building.near)
  for (const side of [-1, 1]) {
    box(scene, `depot:shelf:${side}`, {
      depth: 1.15,
      height: 5.8,
      material: 'charcoal-structure',
      parent: shelves,
      position: [side * 5.15, 3.25, -4.65],
      width: 2.5
    })
    for (let row = 0; row < 4; row += 1) {
      box(scene, `depot:stock:${side}:${row}`, {
        depth: 0.66,
        height: 0.75,
        material: row % 2 ? 'signal-emissive' : 'bone-metal',
        parent: shelves,
        position: [side * 5.15, 1.2 + row * 1.25, -3.98],
        width: 1.82
      })
    }
  }
  const crates = group(scene, 'depot:crates', building.near)
  for (let index = 0; index < 8; index += 1)
    box(scene, `depot:crate:${index}`, {
      depth: 1.2,
      height: 1.15,
      material: index % 3 ? 'bone-metal' : 'signal-emissive',
      parent: crates,
      position: [-2.8 + (index % 4) * 1.75, 0.9 + Math.floor(index / 4) * 1.15, 1.7],
      rotation: [0, (index % 2) * 0.12, 0],
      width: 1.45
    })
  const workbench = addWorkbenches(scene, 'depot:workbench', building.near, {
    accent: 'signal-emissive',
    count: 2,
    position: [0, 0.7, -1.2]
  })
  scalarClip(scene, 'lights-idle', workbench, 'rotation.y', 0.035, { duration: 54 })
  scalarClip(scene, 'workbench-cycle', workbench, 'rotation.y', -0.055, { duration: 72 })
  return building.root
}

export function buildReviewOffice(scene) {
  const building = openFrontShell(scene, 'review-office', {
    accent: 'archive-emissive',
    depth: 11.5,
    height: 9.2,
    width: 15.5
  })
  addSign(scene, 'review-office:sign', building.near, {
    accent: 'archive-emissive',
    position: [0, 8.05, 5.95],
    width: 5.8
  })
  addPortal(scene, building.near)
  addConsoleBank(scene, 'review-office:consoles', building.near, {
    accent: 'archive-emissive',
    count: 3,
    position: [3.7, 1.15, -4.85],
    width: 1.15
  })
  const dais = group(scene, 'review-office:verifier-dais', building.near, { position: [3.8, 0.85, 1.1] })
  cylinder(scene, 'review-office:verifier-dais:base', {
    diameter: 3.1,
    height: 0.6,
    material: 'archive-emissive',
    parent: dais,
    tessellation: 10
  })
  box(scene, 'review-office:verifier-dais:desk', {
    depth: 1.45,
    height: 1.2,
    material: 'bone-metal',
    parent: dais,
    position: [0, 0.7, 0],
    width: 2.7
  })
  scalarClip(scene, 'lights-idle', dais, 'rotation.y', 0.06, { duration: 66 })
  return building.root
}

export function buildGarden(scene) {
  const root = group(scene, 'garden:root')
  const near = group(scene, 'garden:lod:near', root)
  const far = group(scene, 'garden:lod:far', root)
  cylinder(scene, 'garden:basin', {
    diameter: 19,
    height: 1.05,
    material: 'lunar-rust',
    parent: near,
    position: [0, 0.1, 0],
    tessellation: 12
  })
  cylinder(scene, 'garden:soil', {
    diameter: 16.8,
    height: 0.52,
    material: 'lunar-rust',
    parent: near,
    position: [0, 0.66, 0],
    tessellation: 12
  })
  const plants = addPlants(scene, near)
  const bench = group(scene, 'garden:bench', near, { position: [0.4, 1, 1.1], rotation: [0, -0.25, 0] })
  box(scene, 'garden:bench:seat', { depth: 1.5, height: 0.3, material: 'bone-metal', parent: bench, width: 4.2 })
  box(scene, 'garden:bench:back', {
    depth: 0.32,
    height: 1.5,
    material: 'bone-metal',
    parent: bench,
    position: [0, 0.75, -0.58],
    rotation: [-0.2, 0, 0],
    width: 4.2
  })
  const fixture = group(scene, 'garden:cyan-fixture', near, { position: [-0.8, 1.5, -1.7] })
  cylinder(scene, 'garden:cyan-fixture:base', {
    diameter: 1.4,
    height: 0.75,
    material: 'bone-metal',
    parent: fixture,
    tessellation: 8
  })
  sphere(scene, 'garden:cyan-fixture:glow', {
    diameter: 0.82,
    material: 'signal-emissive',
    parent: fixture,
    position: [0, 0.72, 0],
    segments: 7
  })
  prismRailing(scene, 'garden:railing', near, 0, 0, 18, 18, 'bone-metal')
  group(scene, 'garden:camera', root, { position: [0, 6, 15] })
  cylinder(scene, 'garden:far:basin', {
    diameter: 18,
    height: 1,
    material: 'lunar-rust',
    parent: far,
    position: [0, 0.2, 0],
    tessellation: 10
  })
  cylinder(scene, 'garden:far:green', {
    diameter: 14.5,
    height: 0.45,
    material: 'garden-green',
    parent: far,
    position: [0, 0.85, 0],
    tessellation: 10
  })
  scalarClip(scene, 'garden-idle', plants, 'rotation.y', 0.09, { duration: 80 })
  return root
}

export function buildCouncil(scene) {
  const building = openFrontShell(scene, 'council', { accent: 'archive-emissive', depth: 10.5, height: 8.8, width: 14 })
  addSign(scene, 'council:sign', building.near, { accent: 'archive-emissive', position: [0, 7.65, 5.45], width: 4.5 })
  const dais = group(scene, 'council:dais', building.near, { position: [0, 0.9, -1.2] })
  cylinder(scene, 'council:dais:base', {
    diameter: 6.6,
    height: 0.82,
    material: 'archive-emissive',
    parent: dais,
    tessellation: 10
  })
  box(scene, 'council:dais:table', {
    depth: 2.3,
    height: 1.2,
    material: 'bone-metal',
    parent: dais,
    position: [0, 1, 0],
    width: 5.2
  })
  const roost = group(scene, 'council:roost', building.near, { position: [0, 2.2, -2] })
  cone(scene, 'council:roost:spire', {
    diameterBottom: 2,
    diameterTop: 1.2,
    height: 2.8,
    material: 'charcoal-structure',
    parent: roost,
    tessellation: 8
  })
  torus(scene, 'council:roost:halo', {
    diameter: 2.5,
    material: 'archive-emissive',
    parent: roost,
    position: [0, 1.35, 0],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: 0.14
  })
  const consoles = addConsoleBank(scene, 'council:console', building.near, {
    accent: 'archive-emissive',
    count: 2,
    position: [4.1, 1.15, -4],
    width: 1.1
  })
  scalarClip(scene, 'lights-idle', consoles, 'rotation.y', 0.045, { duration: 52 })
  return building.root
}
