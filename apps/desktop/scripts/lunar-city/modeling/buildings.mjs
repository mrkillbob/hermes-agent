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
    material: 'charcoal-structure',
    parent: shell,
    position: [-width / 2, height * 0.41, 0],
    width: 0.7
  })
  box(scene, `${id}:right-wall`, {
    depth,
    height: height * 0.82,
    material: 'charcoal-structure',
    parent: shell,
    position: [width / 2, height * 0.41, 0],
    width: 0.7
  })
  const roof = group(scene, `${id}:roof`, near)
  box(scene, `${id}:roof:center`, {
    depth: depth + 0.3,
    height: 0.48,
    material: 'charcoal-structure',
    parent: roof,
    position: [0, height + 0.25, -0.1],
    width: width + 0.45
  })
  box(scene, `${id}:roof:left-bevel`, {
    depth: depth + 0.9,
    height: 0.42,
    material: 'bone-metal',
    parent: roof,
    position: [-width * 0.37, height - 0.02, -0.05],
    rotation: [0, 0, -0.26],
    width: width * 0.28
  })
  box(scene, `${id}:roof:right-bevel`, {
    depth: depth + 0.9,
    height: 0.42,
    material: 'bone-metal',
    parent: roof,
    position: [width * 0.37, height - 0.02, -0.05],
    rotation: [0, 0, 0.26],
    width: width * 0.28
  })
  for (const side of [-1, 1]) {
    box(scene, `${id}:roof:front-rail:${side}`, {
      depth: 0.28,
      height: 0.4,
      material: side < 0 ? 'bone-metal' : 'lunar-rust',
      parent: roof,
      position: [side * width * 0.25, height + 0.48, depth / 2 + 0.18],
      width: width * 0.48
    })
    box(scene, `${id}:roof:side-rail:${side}`, {
      depth: depth + 0.2,
      height: 0.38,
      material: 'bone-metal',
      parent: roof,
      position: [side * (width / 2 + 0.12), height + 0.36, -0.08],
      width: 0.3
    })
    for (const z of [-depth * 0.34, 0, depth * 0.34])
      box(scene, `${id}:frame-post:${side}:${z}`, {
        depth: 0.52,
        height: height * 0.76,
        material: z === 0 ? 'lunar-rust' : 'bone-metal',
        parent: shell,
        position: [side * (width / 2 + 0.08), height * 0.39, z],
        rotation: [0, 0, side * 0.035],
        width: 0.5
      })
  }
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

function addLayeredRoomDetail(scene, id, parent, { accent, depth, height, width }) {
  const detail = group(scene, `${id}:layered-detail`, parent)
  for (const [sideIndex, side] of [-1, 1].entries()) {
    for (let rib = 0; rib < 4; rib += 1) {
      const z = -depth * 0.36 + rib * ((depth * 0.72) / 3)
      box(scene, `${id}:side-rib:${sideIndex}:${rib}`, {
        depth: 0.48,
        height: height * (0.48 + (rib % 2) * 0.12),
        material: rib % 2 ? accent : 'lunar-rust',
        parent: detail,
        position: [side * (width / 2 + 0.42), height * 0.33, z],
        rotation: [0, 0, side * 0.08],
        width: 0.48
      })
    }
  }
  for (let panel = 0; panel < 9; panel += 1) {
    const x = -width * 0.38 + panel * ((width * 0.76) / 8)
    box(scene, `${id}:back-panel:${panel}`, {
      depth: 0.14,
      height: 1.55 + (panel % 3) * 0.42,
      material: panel % 2 ? accent : 'bone-metal',
      parent: detail,
      position: [x, 2.1 + (panel % 2) * 1.9, -depth / 2 + 0.38],
      width: width * 0.055
    })
  }
  for (let step = 0; step < 3; step += 1)
    box(scene, `${id}:front-step:${step}`, {
      depth: 0.72 + step * 0.5,
      height: 0.22,
      material: step === 1 ? accent : 'charcoal-structure',
      parent: detail,
      position: [0, 0.12 + step * 0.15, depth / 2 + 0.5 + step * 0.24],
      width: width * (0.56 - step * 0.05)
    })
  return detail
}

function addLibraryMassing(scene, building) {
  const towers = group(scene, 'library:corner-archive-towers', building.near)
  for (const side of [-1, 1]) {
    const x = side * 6.55
    cylinder(scene, `library:corner-tower:${side}`, {
      diameter: 2.2,
      height: 8.5,
      material: 'charcoal-structure',
      parent: towers,
      position: [x, 4.4, -4.4],
      tessellation: 8
    })
    for (let floor = 0; floor < 5; floor += 1)
      torus(scene, `library:corner-tower:ring:${side}:${floor}`, {
        diameter: 2.28,
        material: floor % 2 ? 'archive-emissive' : 'bone-metal',
        parent: towers,
        position: [x, 1.2 + floor * 1.55, -4.4],
        rotation: [Math.PI / 2, 0, 0],
        tessellation: 8,
        thickness: 0.1
      })
    cone(scene, `library:corner-tower:cap:${side}`, {
      diameterBottom: 2.6,
      diameterTop: 0.7,
      height: 1.8,
      material: 'bone-metal',
      parent: towers,
      position: [x, 9.1, -4.4],
      tessellation: 8
    })
  }
  const crest = group(scene, 'library:roof-crest', building.near)
  for (let index = 0; index < 5; index += 1)
    box(scene, `library:roof-crest:${index}`, {
      depth: 2.1,
      height: 0.55 + (2 - Math.abs(index - 2)) * 0.22,
      material: index === 2 ? 'archive-emissive' : 'lunar-rust',
      parent: crest,
      position: [(index - 2) * 1.65, 10.65 + (2 - Math.abs(index - 2)) * 0.18, -1.4],
      rotation: [0, 0, (index - 2) * 0.04],
      width: 1.4
    })
}

function addLabMassing(scene, building) {
  const pods = group(scene, 'research-lab:asymmetric-service-pods', building.near)
  for (const [index, side] of [-1, 1].entries()) {
    box(scene, `research-lab:service-pod:${index}`, {
      depth: 5.4 + index * 1.2,
      height: 6.8 - index * 0.8,
      material: 'lunar-rust',
      parent: pods,
      position: [side * 9.55, 3.7, -2.7 + index * 1.2],
      rotation: [0, 0, side * 0.08],
      width: 1.7 + index * 0.35
    })
    for (let cell = 0; cell < 4; cell += 1)
      cylinder(scene, `research-lab:pod-cell:${index}:${cell}`, {
        diameter: 0.68,
        height: 1.7,
        material: cell % 2 ? 'signal-emissive' : 'bone-metal',
        parent: pods,
        position: [side * 9.62, 1.4 + cell * 1.35, -1.2 + index * 0.6],
        tessellation: 8
      })
  }
  const roofArray = group(scene, 'research-lab:roof-array', building.near)
  for (let index = 0; index < 7; index += 1) {
    cylinder(scene, `research-lab:roof-array:cell:${index}`, {
      diameter: 0.72 + (index % 2) * 0.18,
      height: 1.7 + (index % 3) * 0.35,
      material: index % 3 ? 'bone-metal' : 'signal-emissive',
      parent: roofArray,
      position: [-5.5 + index * 1.75, 11.45 + (index % 2) * 0.28, -2.7],
      tessellation: 8
    })
  }
}

function addDepotMassing(scene, building) {
  const cargo = group(scene, 'depot:exterior-cargo-racks', building.near)
  for (const side of [-1, 1]) {
    for (let level = 0; level < 4; level += 1) {
      box(scene, `depot:exterior-cargo:${side}:${level}`, {
        depth: 2.4,
        height: 1.05,
        material: level % 2 ? 'signal-emissive' : 'lunar-rust',
        parent: cargo,
        position: [side * 6.45, 1 + level * 1.22, 2.9 - level * 0.22],
        rotation: [0, side * 0.06 * level, 0],
        width: 2.1 + (level % 2) * 0.4
      })
      for (const brace of [-1, 1])
        box(scene, `depot:cargo-brace:${side}:${level}:${brace}`, {
          depth: 0.2,
          height: 1.18,
          material: 'bone-metal',
          parent: cargo,
          position: [side * 6.45 + brace * 0.65, 1 + level * 1.22, 4.12],
          rotation: [0, 0, brace * 0.55],
          width: 0.16
        })
    }
  }
  for (let vent = 0; vent < 6; vent += 1)
    cylinder(scene, `depot:roof-vent:${vent}`, {
      diameter: 0.75,
      height: 1.2 + (vent % 2) * 0.4,
      material: vent % 2 ? 'charcoal-structure' : 'bone-metal',
      parent: building.near,
      position: [-4.8 + vent * 1.9, 9.3, -2.9],
      tessellation: 8
    })
}

function addReviewMassing(scene, building) {
  const pylons = group(scene, 'review-office:portal-pylons', building.near)
  for (const side of [-1, 1]) {
    for (let tier = 0; tier < 4; tier += 1)
      cylinder(scene, `review-office:pylon:${side}:${tier}`, {
        diameter: 2.1 - tier * 0.25,
        height: 1.7,
        material: tier % 2 ? 'archive-emissive' : 'charcoal-structure',
        parent: pylons,
        position: [side * 6.5, 1.15 + tier * 1.55, -3.7],
        tessellation: 8
      })
    torus(scene, `review-office:pylon-halo:${side}`, {
      diameter: 2.7,
      material: 'archive-emissive',
      parent: pylons,
      position: [side * 6.5, 7.45, -3.7],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 12,
      thickness: 0.16
    })
  }
  for (let column = 0; column < 5; column += 1)
    box(scene, `review-office:verdict-column:${column}`, {
      depth: 0.7,
      height: 2.2 + column * 0.55,
      material: column % 2 ? 'bone-metal' : 'archive-emissive',
      parent: building.near,
      position: [-3 + column * 1.5, 1.1 + column * 0.28, -5.3],
      width: 0.7
    })
}

function addCouncilMassing(scene, building) {
  const chamber = group(scene, 'council:open-chamber-arch', building.near)
  for (const side of [-1, 1]) {
    for (let tier = 0; tier < 4; tier += 1)
      cylinder(scene, `council:column:${side}:${tier}`, {
        diameter: 1.25 - tier * 0.12,
        height: 1.8,
        material: tier % 2 ? 'archive-emissive' : 'bone-metal',
        parent: chamber,
        position: [side * (5.7 - tier * 0.25), 1.25 + tier * 1.5, -2.8],
        tessellation: 8
      })
  }
  for (let ring = 0; ring < 4; ring += 1)
    torus(scene, `council:chamber-ring:${ring}`, {
      diameter: 7.2 - ring * 0.65,
      material: ring % 2 ? 'archive-emissive' : 'charcoal-structure',
      parent: chamber,
      position: [0, 7.2 + ring * 0.38, -3.1],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 14,
      thickness: 0.16
    })
  for (let seat = 0; seat < 9; seat += 1) {
    const angle = -1.15 + seat * 0.29
    cylinder(scene, `council:seat:${seat}`, {
      diameter: 1.05,
      height: 0.82,
      material: seat % 2 ? 'archive-emissive' : 'lunar-rust',
      parent: chamber,
      position: [Math.sin(angle) * 4.5, 1.05, 0.8 + Math.cos(angle) * 2.8],
      tessellation: 8
    })
  }
}

export function buildLibrary(scene) {
  const building = openFrontShell(scene, 'library', { accent: 'archive-emissive', depth: 12, height: 9.8, width: 15 })
  addLayeredRoomDetail(scene, 'library', building.near, {
    accent: 'archive-emissive',
    depth: 12,
    height: 9.8,
    width: 15
  })
  addLibraryMassing(scene, building)
  addSign(scene, 'library:sign', building.near, {
    accent: 'archive-emissive',
    glyph: 'book',
    position: [0, 8.5, 6.25],
    width: 5.2
  })
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
  for (let bay = 0; bay < 7; bay += 1) {
    const x = -5.1 + bay * 1.7
    box(scene, `library:central-stack:${bay}`, {
      depth: 0.65,
      height: 6.9,
      material: 'charcoal-structure',
      parent: stacks,
      position: [x, 4.05, -5.45],
      width: 1.4
    })
    for (let shelf = 0; shelf < 5; shelf += 1)
      box(scene, `library:central-book:${bay}:${shelf}`, {
        depth: 0.24,
        height: 0.42,
        material: shelf % 2 ? 'archive-emissive' : 'lunar-rust',
        parent: stacks,
        position: [x, 1.45 + shelf * 1.15, -5.05],
        width: 1.06
      })
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
  addLayeredRoomDetail(scene, 'research-lab', building.near, {
    accent: 'signal-emissive',
    depth: 14,
    height: 10.5,
    width: 18
  })
  addLabMassing(scene, building)
  addSign(scene, 'research-lab:sign', building.near, {
    accent: 'signal-emissive',
    glyph: 'flask',
    position: [0, 9.25, 7.25],
    width: 6.5
  })
  addConsoleBank(scene, 'research-lab:consoles', building.near, {
    accent: 'signal-emissive',
    count: 5,
    position: [0, 1.25, -6.1],
    width: 1.35
  })
  const displayWall = group(scene, 'research-lab:display-wall', building.near)
  for (let display = 0; display < 7; display += 1) {
    const x = -7.1 + display * 2.35
    box(scene, `research-lab:display:${display}`, {
      depth: 0.18,
      height: 1.45 + (display % 3) * 0.28,
      material: display % 3 === 1 ? 'garden-green' : 'signal-emissive',
      parent: displayWall,
      position: [x, 5.3 + (display % 2) * 1.25, -6.58],
      rotation: [0, 0, (display - 3) * 0.018],
      width: 1.75
    })
    box(scene, `research-lab:display-frame:${display}`, {
      depth: 0.23,
      height: 0.22,
      material: 'bone-metal',
      parent: displayWall,
      position: [x, 4.48 + (display % 2) * 1.25, -6.53],
      width: 1.95
    })
  }
  const workbenches = addWorkbenches(scene, 'research-lab:workbenches', building.near, {
    accent: 'signal-emissive',
    count: 3,
    position: [-2.2, 0.75, 2.4]
  })
  const specimen = group(scene, 'research-lab:specimen', building.near, { position: [5.9, 2.3, -3.3] })
  cylinder(scene, 'research-lab:specimen:tank', {
    diameter: 2.8,
    height: 5.4,
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
    diameter: 3.05,
    height: 0.35,
    material: 'bone-metal',
    parent: specimen,
    position: [0, 2.76, 0],
    tessellation: 10
  })
  addTelescope(scene, building.near)
  scalarClip(scene, 'lights-idle', specimen, 'rotation.y', 0.14, { duration: 48 })
  scalarClip(scene, 'workbench-cycle', workbenches, 'rotation.y', 0.045, { duration: 60 })
  return building.root
}

export function buildDepot(scene) {
  const building = openFrontShell(scene, 'depot', { accent: 'signal-emissive', depth: 11, height: 8.5, width: 14.5 })
  addLayeredRoomDetail(scene, 'depot', building.near, {
    accent: 'signal-emissive',
    depth: 11,
    height: 8.5,
    width: 14.5
  })
  addDepotMassing(scene, building)
  addSign(scene, 'depot:sign', building.near, {
    accent: 'signal-emissive',
    glyph: 'crate',
    position: [0, 7.35, 5.75],
    width: 5.4
  })
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
  addLayeredRoomDetail(scene, 'review-office', building.near, {
    accent: 'archive-emissive',
    depth: 11.5,
    height: 9.2,
    width: 15.5
  })
  addReviewMassing(scene, building)
  addSign(scene, 'review-office:sign', building.near, {
    accent: 'archive-emissive',
    glyph: 'review',
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
  addLayeredRoomDetail(scene, 'council', building.near, {
    accent: 'archive-emissive',
    depth: 10.5,
    height: 8.8,
    width: 14
  })
  addCouncilMassing(scene, building)
  addSign(scene, 'council:sign', building.near, {
    accent: 'archive-emissive',
    glyph: 'council',
    position: [0, 7.65, 5.45],
    width: 4.5
  })
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
