import { scalarClip } from './animation.mjs'
import { box, cone, cylinder, facadeFins, group, prismRailing, sphere, torus, trimmedBox, wireframeShell } from './primitives.mjs'
import { addConsoleBank, addPlants, addPortal, addSign, addTelescope, addWorkbenches } from './props.mjs'

function buildingNodes(scene, id) {
  const root = group(scene, `${id}:root`)
  const near = group(scene, `${id}:lod:near`, root)
  const far = group(scene, `${id}:lod:far`, root)
  const shell = group(scene, `${id}:shell`, near)
  const roof = group(scene, `${id}:roof`, near)
  const entrance = group(scene, `${id}:entrance`, near)
  group(scene, `${id}:camera`, root, { position: [0, 6, 18] })
  return { entrance, far, near, roof, root, shell }
}

function keepIdentity(mesh) {
  mesh.metadata = { ...(mesh.metadata ?? {}), keepSeparate: true }
  return mesh
}

function installWireframeEnvelope(scene, building, id, options) {
  for (const legacy of [building.shell, building.roof, building.entrance]) {
    for (const mesh of legacy.getChildMeshes()) mesh.dispose()
  }
  const envelope = wireframeShell(scene, `${id}:wireframe-envelope`, building.near, options)
  building.near.metadata = { ...(building.near.metadata ?? {}), construction: 'wireframe-with-skin' }
  building.entrance = envelope.entrance
  building.roof = envelope.roof
  building.shell = envelope.root
  building.wireframe = envelope
  return building
}

function addLibraryFrame(scene, { accent, depth, height, width }) {
  const building = buildingNodes(scene, 'library')
  const { entrance, far, near, roof, shell } = building
  box(scene, 'library:floor', {
    depth: depth + 1.2,
    height: 0.7,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, 0.35, 0],
    width: width + 1
  })
  for (const [index, [x, panelHeight, panelWidth]] of [
    [-width * 0.27, height * 0.68, width * 0.22],
    [0, height * 0.96, width * 0.28],
    [width * 0.27, height * 0.68, width * 0.22]
  ].entries())
    box(scene, `library:archive-back:${index}`, {
      depth: 0.75,
      height: panelHeight,
      material: 'charcoal-structure',
      parent: shell,
      position: [x, panelHeight / 2, -depth / 2],
      width: panelWidth
    })
  // A full-width backing wall behind the tiered archive-back panels above.
  // Those panels alone leave gaps a viewer can see straight through from
  // outside; this closes the envelope without losing their stepped massing.
  box(scene, 'library:back-wall', {
    depth: 0.4,
    height: height * 0.86,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, height * 0.43, -depth / 2 - 0.22],
    width: width * 0.98
  })
  for (const side of [-1, 1]) {
    // A solid side wall so the library reads as an enclosed volume from a
    // 3/4 overview angle instead of the freestanding wing/tower armature
    // alone. It stops short of the entrance-facing front edge so the
    // approved open-front interior view is untouched.
    box(scene, `library:side-wall:${side}`, {
      depth: depth * 0.92,
      height: height * 0.82,
      material: 'charcoal-structure',
      parent: shell,
      position: [side * (width / 2 + 0.32), height * 0.41, -depth * 0.06],
      width: 0.62
    })
    for (let window = 0; window < 3; window += 1)
      box(scene, `library:window:${side}:${window}`, {
        depth: 1.35,
        height: 1.5,
        material: accent,
        parent: shell,
        position: [
          side * (width / 2 + 0.32),
          height * 0.3 + (window % 2) * 1.9,
          -depth * 0.32 + window * (depth * 0.34)
        ],
        width: 0.2
      })
    box(scene, `library:archive-wing:${side}`, {
      depth: depth * 0.76,
      height: height * 0.7,
      material: 'charcoal-structure',
      parent: shell,
      position: [side * width * 0.43, height * 0.35, -depth * 0.11],
      rotation: [0, side * 0.06, 0],
      width: 1.1
    })
    box(scene, `library:roof:spire:${side}`, {
      depth: depth * 0.3,
      height: 0.62,
      material: 'bone-metal',
      parent: roof,
      position: [side * width * 0.35, height * 0.86, -depth * 0.34],
      rotation: [0, 0, side * 0.32],
      width: width * 0.26
    })
  }
  box(scene, 'library:roof:cap', {
    depth: depth * 0.94,
    height: 0.5,
    material: 'lunar-rust',
    parent: roof,
    position: [0, height * 0.84, -depth * 0.04],
    width: width * 0.96
  })
  for (let tier = 0; tier < 3; tier += 1)
    box(scene, `library:roof:stepped-gable:${tier}`, {
      depth: 1.25 + tier * 0.25,
      height: 0.65,
      material: tier === 1 ? accent : 'lunar-rust',
      parent: roof,
      position: [0, height * 0.82 + tier * 0.74, -depth * 0.38],
      width: width * (0.62 - tier * 0.14)
    })
  entrance.position.set(0, 2.1, depth / 2 + 0.5)
  for (const side of [-1, 1])
    box(scene, `library:entrance:tower:${side}`, {
      depth: 1,
      height: 4.2,
      material: 'lunar-rust',
      parent: entrance,
      position: [side * width * 0.33, 0, 0],
      width: 1.25
    })
  // Thick ivory portal members are the visual shorthand for the reference's
  // enclosed moon-base architecture.  Keep them on the library only so the
  // landmark gains a strong hero silhouette without making every district
  // share one generic frame.
  for (const side of [-1, 1])
    box(scene, `library:entrance:hull-pillar:${side}`, {
      depth: 0.7,
      height: height * 0.82,
      material: 'bone-metal',
      parent: entrance,
      position: [side * width * 0.47, height * 0.4, 0.08],
      width: 0.62
    })
  box(scene, 'library:entrance:hull-lintel', {
    depth: 0.76,
    height: 0.58,
    material: 'bone-metal',
    parent: entrance,
    position: [0, height * 0.81, 0.08],
    width: width * 0.9
  })
  const identity = group(scene, 'library:city-identity', near)
  keepIdentity(
    box(scene, 'library:city-identity:great-book', {
      depth: 0.52,
      height: 1.65,
      material: 'lunar-rust',
      parent: identity,
      position: [0, height * 0.74, depth / 2 + 0.2],
      rotation: [0, 0, -0.04],
      width: width * 0.52
    })
  )
  for (const side of [-1, 1])
    box(scene, `library:far:tower:${side}`, {
      depth: depth * 0.52,
      height: height * 0.82,
      material: 'bone-metal',
      parent: far,
      position: [side * width * 0.39, height * 0.41, -depth * 0.18],
      width: width * 0.2
    })
  box(scene, 'library:far:archive', {
    depth: depth * 0.34,
    height,
    material: 'bone-metal',
    parent: far,
    position: [0, height / 2, -depth * 0.36],
    width: width * 0.52
  })
  return building
}

function addLabFrame(scene, { accent, depth, height, width }) {
  const building = buildingNodes(scene, 'research-lab')
  const { entrance, far, near, roof, shell } = building
  box(scene, 'research-lab:floor', {
    depth,
    height: 0.7,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, 0.35, 0],
    width
  })
  box(scene, 'research-lab:west-wing', {
    depth: 0.8,
    height: height * 0.9,
    material: 'charcoal-structure',
    parent: shell,
    position: [-width * 0.2, height * 0.45, -depth / 2],
    rotation: [0, 0, -0.035],
    width: width * 0.6
  })
  box(scene, 'research-lab:east-wing', {
    depth: 0.9,
    height: height * 0.62,
    material: 'charcoal-structure',
    parent: shell,
    position: [width * 0.37, height * 0.31, -depth / 2],
    rotation: [0, 0, 0.06],
    width: width * 0.25
  })
  box(scene, 'research-lab:side-return', {
    depth: depth * 0.72,
    height: height * 0.52,
    material: 'lunar-rust',
    parent: shell,
    position: [width / 2, height * 0.26, -depth * 0.12],
    width: 1.4
  })
  box(scene, 'research-lab:shell:rear-band', {
    depth: 0.54,
    height: 0.5,
    material: 'bone-metal',
    parent: shell,
    position: [0, height * 0.84, -depth / 2 - 0.08],
    width: width * 0.9
  })
  // Window glow on the existing east side-return and the west service
  // stack, rather than a dedicated new wall/backing panel, so the lit-window
  // read lands on the lab's own asymmetric massing instead of adding more
  // dominant flat wall area that converges its silhouette on the library's.
  for (let window = 0; window < 2; window += 1)
    box(scene, `research-lab:window:east:${window}`, {
      depth: depth * 0.18,
      height: 1.3,
      material: accent,
      parent: shell,
      position: [width / 2, height * 0.22 + window * 1.7, -depth * 0.28 + window * depth * 0.32],
      width: 0.22
    })
  box(scene, 'research-lab:window:west', {
    depth: 1.3,
    height: 1.2,
    material: accent,
    parent: shell,
    position: [-width * 0.46, height * 0.4, -depth * 0.24],
    width: 0.22
  })
  box(scene, 'research-lab:service-stack', {
    depth: 2.2,
    height: height * 1.14,
    material: 'lunar-rust',
    parent: shell,
    position: [-width * 0.46, height * 0.57, -depth * 0.24],
    rotation: [0, 0, -0.04],
    width: 1.9
  })
  box(scene, 'research-lab:roof:west', {
    depth: depth * 0.44,
    height: 0.62,
    material: 'bone-metal',
    parent: roof,
    position: [-width * 0.2, height * 0.92, -depth * 0.3],
    rotation: [0, 0, -0.14],
    width: width * 0.58
  })
  box(scene, 'research-lab:roof:east', {
    depth: depth * 0.32,
    height: 0.5,
    material: accent,
    parent: roof,
    position: [width * 0.34, height * 0.66, -depth * 0.36],
    rotation: [0, 0, 0.16],
    width: width * 0.28
  })
  entrance.position.set(-width * 0.36, 2, depth / 2 + 0.3)
  box(scene, 'research-lab:entrance:airlock', {
    depth: 1,
    height: 4,
    material: 'lunar-rust',
    parent: entrance,
    width: 2.2
  })
  // Give the observatory a bespoke silver pressure-shell mouth. The shared
  // entry kit adds small trim, but the reference landmarks have a thick,
  // readable enclosure around the occupied room. Keeping this frame local to
  // the lab preserves specialist silhouette diversity and reuses the existing
  // bone-metal material, so there is no texture or lighting cost.
  for (const side of [-1, 1])
    box(scene, `research-lab:entrance:hull-pillar:${side}`, {
      depth: 0.72,
      height: 4.7,
      material: 'bone-metal',
      parent: entrance,
      position: [side * 1.65, 2.2, 0.08],
      width: 0.62
    })
  box(scene, 'research-lab:entrance:hull-lintel', {
    depth: 0.76,
    height: 0.62,
    material: 'bone-metal',
    parent: entrance,
    position: [0, 4.48, 0.08],
    width: 4.55
  })
  const identity = group(scene, 'research-lab:city-identity', near)
  keepIdentity(
    torus(scene, 'research-lab:city-identity:reactor-cradle', {
      diameter: 5.6,
      material: 'lunar-rust',
      parent: identity,
      position: [width * 0.31, 4.2, -depth / 2 + 0.85],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 14,
      thickness: 0.42
    })
  )
  // The cradle alone is a thin horizontal ring and disappears at the
  // overview scale.  Give the lab a compact vertical reactor core so its
  // warm identity occupies a readable fraction of the landmark silhouette.
  keepIdentity(
    cylinder(scene, 'research-lab:city-identity:reactor-core', {
      diameter: 4.8,
      height: 7.2,
      material: 'bone-metal',
      parent: identity,
      position: [width * 0.31, 4.2, -depth / 2 + 0.85],
      tessellation: 12
    })
  )
  box(scene, 'research-lab:far:west-wing', {
    depth: depth * 0.46,
    height: height * 0.9,
    material: 'bone-metal',
    parent: far,
    position: [-width * 0.2, height * 0.45, -depth * 0.3],
    width: width * 0.58
  })
  box(scene, 'research-lab:far:east-wing', {
    depth: depth * 0.38,
    height: height * 0.58,
    material: 'bone-metal',
    parent: far,
    position: [width * 0.36, height * 0.29, -depth * 0.32],
    width: width * 0.26
  })
  return building
}

function addDepotFrame(scene, { accent, depth, height, width }) {
  const building = buildingNodes(scene, 'depot')
  const { entrance, far, near, roof, shell } = building
  box(scene, 'depot:floor', {
    depth: depth + 1,
    height: 0.62,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, 0.31, 0],
    width: width + 2.5
  })
  box(scene, 'depot:warehouse-back', {
    depth: 0.72,
    height: height * 0.62,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, height * 0.31, -depth / 2],
    width: width + 1.5
  })
  box(scene, 'depot:shell:rear-band', {
    depth: 0.5,
    height: 0.48,
    material: 'bone-metal',
    parent: shell,
    position: [0, height * 0.62, -depth / 2 - 0.08],
    width: width * 0.92
  })
  for (const side of [-1, 1]) {
    box(scene, `depot:side-wall:${side}`, {
      depth: depth * 0.82,
      height: height * 0.58,
      material: 'charcoal-structure',
      parent: shell,
      position: [side * (width / 2 + 0.22), height * 0.29, -depth * 0.09],
      width: 0.62
    })
    for (let window = 0; window < 2; window += 1)
      box(scene, `depot:side-window:${side}:${window}`, {
        depth: 0.18,
        height: 0.76,
        material: accent,
        parent: shell,
        position: [side * (width / 2 + 0.55), height * 0.28 + window * 1.5, -depth * 0.25 + window * depth * 0.27],
        width: 0.18
      })
  }
  for (let bay = 0; bay < 4; bay += 1) {
    const x = -width * 0.36 + bay * width * 0.24
    box(scene, `depot:roof:sawtooth:${bay}`, {
      depth: depth * 0.42,
      height: 0.55,
      material: bay % 2 ? accent : 'bone-metal',
      parent: roof,
      position: [x, height * (0.62 + (bay % 2) * 0.08), -depth * 0.28],
      rotation: [0, 0, bay % 2 ? 0.18 : -0.18],
      width: width * 0.28
    })
  }
  entrance.position.set(-width * 0.38, 1.7, depth / 2 + 0.4)
  box(scene, 'depot:entrance:loading-gate', {
    depth: 0.8,
    height: 3.4,
    material: 'bone-metal',
    parent: entrance,
    width: 3.6
  })
  const identity = group(scene, 'depot:city-identity', near)
  keepIdentity(
    box(scene, 'depot:city-identity:cargo-crane', {
      depth: 0.72,
      height: 0.72,
      material: 'lunar-rust',
      parent: identity,
      position: [0.7, height * 0.86, depth * 0.12],
      rotation: [0, 0, -0.09],
      width: width * 0.78
    })
  )
  for (const side of [-1, 1])
    box(scene, `depot:gantry-post:${side}`, {
      depth: 0.6,
      height: height * 0.72,
      material: 'lunar-rust',
      parent: near,
      position: [side * width * 0.31, height * 0.36, depth * 0.12],
      width: 0.65
    })
  box(scene, 'depot:cargo-lift-mast', {
    depth: 1.2,
    height: height * 1.34,
    material: 'lunar-rust',
    parent: near,
    position: [width * 0.42, height * 0.67, depth * 0.12],
    rotation: [0, 0, 0.035],
    width: 1.15
  })
  box(scene, 'depot:far:warehouse', {
    depth: depth * 0.62,
    height: height * 0.6,
    material: 'bone-metal',
    parent: far,
    position: [0, height * 0.3, -depth * 0.18],
    width: width + 1.8
  })
  box(scene, 'depot:far:gantry', {
    depth: 0.7,
    height: 0.7,
    material: 'bone-metal',
    parent: far,
    position: [0.7, height * 0.88, depth * 0.1],
    rotation: [0, 0, -0.09],
    width: width * 0.8
  })
  return building
}

function addReviewFrame(scene, { depth, height, width }) {
  const building = buildingNodes(scene, 'review-office')
  const { entrance, far, near, roof, shell } = building
  cylinder(scene, 'review-office:octagonal-floor', {
    diameter: width + 1.5,
    height: 0.9,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, 0.45, -0.4],
    tessellation: 8
  })
  box(scene, 'review-office:chamber-back-wall', {
    depth: 0.58,
    height: height * 0.42,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, height * 0.21, -depth * 0.46],
    width: width * 0.52
  })
  box(scene, 'review-office:shell:rear-band', {
    depth: 0.46,
    height: 0.44,
    material: 'bone-metal',
    parent: shell,
    position: [0, height * 0.45, -depth * 0.49],
    width: width * 0.72
  })
  for (const side of [-1, 1])
    box(scene, `review-office:chamber-side-wall:${side}`, {
      depth: depth * 0.52,
      height: height * 0.38,
      material: 'charcoal-structure',
      parent: shell,
      position: [side * (width * 0.45), height * 0.19, -depth * 0.18],
      rotation: [0, 0, side * 0.12],
      width: 0.58
    })
  for (const side of [-1, 1]) {
    cylinder(scene, `review-office:judgement-pylon:${side}`, {
      diameter: 2.35,
      height: height * 0.95,
      material: 'charcoal-structure',
      parent: shell,
      position: [side * width * 0.34, height * 0.48, -depth * 0.36],
      tessellation: 8
    })
    cone(scene, `review-office:roof:pylon-cap:${side}`, {
      diameterBottom: 2.9,
      diameterTop: 0.7,
      height: 2.2,
      material: 'lunar-rust',
      parent: roof,
      position: [side * width * 0.34, height + 0.6, -depth * 0.36],
      tessellation: 8
    })
  }
  entrance.position.set(0, 0.6, depth / 2 + 0.5)
  for (let step = 0; step < 4; step += 1)
    cylinder(scene, `review-office:entrance:step:${step}`, {
      diameter: width * (0.6 - step * 0.07),
      height: 0.24,
      material: step % 2 ? 'archive-emissive' : 'bone-metal',
      parent: entrance,
      position: [0, step * 0.2, -step * 0.38],
      tessellation: 8
    })
  const identity = group(scene, 'review-office:city-identity', near)
  keepIdentity(
    torus(scene, 'review-office:city-identity:verdict-halo', {
      diameter: 7.1,
      material: 'bone-metal',
      parent: identity,
      position: [0, height * 0.68, -depth * 0.42],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 16,
      thickness: 0.5
    })
  )
  cylinder(scene, 'review-office:far:floor', {
    diameter: width + 1,
    height: 0.9,
    material: 'bone-metal',
    parent: far,
    position: [0, 0.45, -0.4],
    tessellation: 8
  })
  for (const side of [-1, 1])
    cylinder(scene, `review-office:far:pylon:${side}`, {
      diameter: 2.4,
      height: height,
      material: 'bone-metal',
      parent: far,
      position: [side * width * 0.34, height / 2, -depth * 0.36],
      tessellation: 8
    })
  return building
}

function addCouncilFrame(scene, { depth, height, width }) {
  const building = buildingNodes(scene, 'council')
  const { entrance, far, near, roof, shell } = building
  cylinder(scene, 'council:amphitheater-floor', {
    diameter: width + 2.4,
    height: 0.85,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, 0.42, -0.5],
    tessellation: 10
  })
  box(scene, 'council:chamber-back-wall', {
    depth: 0.62,
    height: height * 0.38,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, height * 0.19, -depth * 0.46],
    width: width * 0.58
  })
  box(scene, 'council:shell:rear-band', {
    depth: 0.5,
    height: 0.44,
    material: 'bone-metal',
    parent: shell,
    position: [0, height * 0.42, -depth * 0.49],
    width: width * 0.78
  })
  for (const side of [-1, 1]) {
    box(scene, `council:chamber-side-wall:${side}`, {
      depth: depth * 0.5,
      height: height * 0.36,
      material: 'charcoal-structure',
      parent: shell,
      position: [side * (width * 0.47), height * 0.18, -depth * 0.2],
      rotation: [0, 0, side * 0.1],
      width: 0.62
    })
    box(scene, `council:chamber-window:${side}`, {
      depth: 0.18,
      height: 1.1,
      material: 'archive-emissive',
      parent: shell,
      position: [side * (width * 0.47), height * 0.35, -depth * 0.24],
      rotation: [0, 0, side * 0.1],
      width: 0.2
    })
  }
  for (let column = 0; column < 7; column += 1) {
    const angle = -1.22 + column * 0.407
    cylinder(scene, `council:amphitheater-column:${column}`, {
      diameter: 1.15,
      height: height * (0.48 + (3 - Math.abs(column - 3)) * 0.07),
      material: column % 2 ? 'archive-emissive' : 'bone-metal',
      parent: shell,
      position: [Math.sin(angle) * width * 0.48, height * 0.34, -depth * 0.1 + Math.cos(angle) * depth * 0.38],
      tessellation: 6
    })
  }
  torus(scene, 'council:roof:open-ring', {
    diameter: width * 0.86,
    material: 'archive-emissive',
    parent: roof,
    position: [0, height * 0.7, -depth * 0.28],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: 0.32
  })
  entrance.position.set(0, 0.65, depth / 2 + 0.4)
  torus(scene, 'council:entrance:threshold', {
    diameter: width * 0.48,
    material: 'archive-emissive',
    parent: entrance,
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: 0.34
  })
  const identity = group(scene, 'council:city-identity', near)
  keepIdentity(
    torus(scene, 'council:city-identity:assembly-halo', {
      diameter: width * 0.72,
      material: 'lunar-rust',
      parent: identity,
      position: [0, height * 0.66, -depth * 0.35],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 10,
      thickness: 0.48
    })
  )
  cylinder(scene, 'council:far:amphitheater', {
    diameter: width + 1.8,
    height: 0.9,
    material: 'bone-metal',
    parent: far,
    position: [0, 0.45, -0.5],
    tessellation: 10
  })
  torus(scene, 'council:far:ring', {
    diameter: width * 0.82,
    material: 'bone-metal',
    parent: far,
    position: [0, height * 0.64, -depth * 0.32],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: 0.5
  })
  return building
}

function specialistFrame(scene, id, options) {
  const building =
    id === 'library'
      ? addLibraryFrame(scene, options)
      : id === 'research-lab'
        ? addLabFrame(scene, options)
        : id === 'depot'
          ? addDepotFrame(scene, options)
          : id === 'review-office'
            ? addReviewFrame(scene, options)
            : id === 'council'
              ? addCouncilFrame(scene, options)
              : undefined

  if (building) {
    installWireframeEnvelope(scene, building, id, options)
    addEntranceWings(scene, id, building.near, options)
    addEntryArch(scene, id, building.entrance, options)
    addWindowGrid(scene, id, building.near, options)
    addRoofDome(scene, id, building.roof, options)
    addRoofEquipment(scene, id, building.roof, options)
    return building
  }

  throw new Error(`unsupported specialist frame ${id}`)
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

/**
 * Shared facade language: recessed light bays, a structural lintel and a
 * visible service spine.  This is deliberately built from the existing
 * palette rather than shipping third-party meshes, so the asset-neutral
 * package still has a strong authored look when no optional kit is present.
 */
function addFacadeKit(scene, id, parent, { accent, depth, height, width }) {
  const facade = group(scene, `${id}:facade-kit`, parent)
  // Sit just inside the room mouth so the kit enriches the façade without
  // changing the landmark's authored city-scale footprint or silhouette.
  const frontZ = depth / 2 - 0.32
  for (let bay = 0; bay < 5; bay += 1) {
    const x = -width * 0.34 + bay * (width * 0.17)
    trimmedBox(scene, `${id}:facade:bay:${bay}`, {
      depth: 0.18,
      height: height * (0.18 + (bay % 2) * 0.04),
      material: accent,
      parent: facade,
      position: [x, height * 0.42 + (bay % 2) * 0.18, frontZ],
      trimMaterial: 'bone-metal',
      width: width * 0.12
    })
  }
  // Wrap a second, orthogonal run around each side wall. The default camera
  // sees the districts from a diagonal, so a front-only façade leaves most
  // of the visible mass as a blank slab. These shallow service panels add the
  // layered sci-fi silhouette of the reference without new materials.
  for (const side of [-1, 1]) {
    for (let bay = 0; bay < 4; bay += 1) {
      const z = -depth * 0.28 + bay * (depth * 0.18)
      trimmedBox(scene, `${id}:facade:side-bay:${side}:${bay}`, {
        depth: 0.18,
        height: height * (0.14 + (bay % 2) * 0.04),
        material: bay % 2 ? 'bone-metal' : accent,
        parent: facade,
        position: [side * (width * 0.47 + 0.06), height * 0.38 + (bay % 2) * 0.22, z],
        rotation: [0, Math.PI / 2, 0],
        trimMaterial: 'bone-metal',
        width: depth * 0.12
      })
    }
  }
  trimmedBox(scene, `${id}:facade:lintel`, {
    depth: 0.28,
    height: 0.34,
    material: 'charcoal-structure',
    parent: facade,
    position: [0, height * 0.7, frontZ + 0.04],
    trimMaterial: 'bone-metal',
    width: width * 0.82
  })
  // Keep the shared kit inside the authored footprint.  Silhouette variety
  // comes from each specialist's own roof/tower massing; common fins would
  // flatten those city-scale differences when viewed from above.
  if (id === 'arts-studio' || id === 'engineering-workshop' || id === 'release-gatehouse' || id === 'archive')
    facadeFins(scene, `${id}:facade:roof-fins`, facade, {
      accent,
      count: 7,
      depth: 0.16,
      height: Math.max(1.1, height * 0.22),
      y: height * 0.9,
      z: -depth * 0.16,
      width: width * 0.72
    })
  return facade
}

/**
 * A vertical entry arch is the cheapest way to give a room a built silhouette
 * at overview distance.  It uses the same trim/glow vocabulary as the rest of
 * the city, but reads as a manufactured portal instead of another rectangular
 * debug block.
 */
function addEntryArch(scene, id, parent, { accent, height, width }) {
  const arch = group(scene, `${id}:entry-arch`, parent, { position: [0, height * 0.46, 0.08] })
  torus(scene, `${id}:entry-arch:ring`, {
    diameter: Math.min(width * 0.72, 7.2),
    material: 'bone-metal',
    parent: arch,
    rotation: [Math.PI / 2, 0, 0],
    scale: [1, 1.12, 1],
    tessellation: 14,
    thickness: id === 'research-lab' || id === 'library' ? 0.46 : 0.34
  })
  box(scene, `${id}:entry-arch:glow`, {
    depth: 0.08,
    height: 0.22,
    material: accent,
    parent: arch,
    position: [0, 0.04, -0.24],
    width: Math.min(width * 0.42, 4.4)
  })
  for (const side of [-1, 1])
    box(scene, `${id}:entry-arch:light:${side}`, {
      depth: 0.1,
      height: 0.5,
      material: accent,
      parent: arch,
      position: [side * Math.min(width * 0.29, 2.2), -0.35, -0.2],
      width: 0.16
    })
  return arch
}

function addEntranceWings(scene, id, parent, { accent, depth, height, width }) {
  const wings = group(scene, `${id}:entrance-wings`, parent)
  for (const side of [-1, 1]) {
    const x = side * width * 0.34
    trimmedBox(scene, `${id}:entrance-wing:${side}`, {
      depth: 0.58,
      height: height * 0.68,
      material: 'charcoal-structure',
      parent: wings,
      position: [x, height * 0.34, depth / 2 + 0.14],
      trimMaterial: 'bone-metal',
      width: width * 0.2
    })
    box(scene, `${id}:entrance-wing:light:${side}`, {
      depth: 0.08,
      height: height * 0.32,
      material: accent,
      parent: wings,
      position: [x, height * 0.43, depth / 2 + 0.46],
      width: 0.12
    })
  }
  return wings
}

/**
 * A compact window grid gives every district a believable occupied interior.
 * The panels are deliberately shallow and merge by material, so they add
 * façade depth without turning each window into a runtime draw call.
 */
function addWindowGrid(scene, id, parent, { accent, depth, height, width }) {
  const windows = group(scene, `${id}:window-grid`, parent)
  const frontZ = depth / 2 + 0.06
  const columns = width >= 14 ? 4 : 3
  const rows = 2
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const x = -width * 0.31 + column * ((width * 0.62) / Math.max(1, columns - 1))
      const y = height * 0.28 + row * height * 0.2
      trimmedBox(scene, `${id}:window-grid:${row}:${column}`, {
        depth: 0.16,
        height: height * 0.105,
        // Keep the window grid on the building's existing material palette;
        // some specialist manifests are capped at four materials.
        material: accent,
        parent: windows,
        position: [x, y, frontZ],
        trimMaterial: 'bone-metal',
        width: width * (columns === 4 ? 0.105 : 0.14)
      })
    }
  }
  box(scene, `${id}:window-grid:header`, {
    depth: 0.2,
    height: 0.26,
    material: 'bone-metal',
    parent: windows,
    position: [0, height * 0.56, frontZ + 0.02],
    width: width * 0.78
  })
  return windows
}

/**
 * Fine facade greebles make the shared shell read as a built module at the
 * overview distance.  They are deliberately shallow and all reuse existing
 * materials, so mergeLodMeshes folds them into the same material surfaces
 * instead of creating one draw call per panel.
 */
function addFacadeGreebles(scene, id, parent, { accent, depth, height, width }) {
  const detail = group(scene, `${id}:facade-greebles`, parent)
  const frontZ = depth / 2 + 0.28

  // Armored corner returns and a cap break the large wall plane into the
  // stepped, pressure-shell language visible in the approved reference.
  for (const side of [-1, 1])
    trimmedBox(scene, `${id}:greeble:corner:${side}`, {
      depth: 0.3,
      height: height * 0.58,
      material: 'bone-metal',
      parent: detail,
      position: [side * width * 0.43, height * 0.32, frontZ],
      trimMaterial: accent,
      width: 0.36
    })
  trimmedBox(scene, `${id}:greeble:cap`, {
    depth: 0.38,
    height: 0.22,
    material: 'bone-metal',
    parent: detail,
    position: [0, height * 0.68, frontZ + 0.04],
    trimMaterial: accent,
    width: width * 0.72
  })

  // Three broad lit cells are more legible than the tiny overview grid and
  // suggest occupied rooms without adding a texture atlas or per-cell light.
  for (let cell = 0; cell < 3; cell += 1)
    trimmedBox(scene, `${id}:greeble:window:${cell}`, {
      depth: 0.14,
      height: Math.max(0.46, height * 0.12),
      material: accent,
      parent: detail,
      position: [-width * 0.23 + cell * width * 0.23, height * 0.35, frontZ + 0.12],
      trimMaterial: 'bone-metal',
      width: width * 0.14
    })

  // A low service rail and two vertical signal bars add the layered facade
  // rhythm without changing collision or the authored open-front interior.
  box(scene, `${id}:greeble:service-rail`, {
    depth: 0.2,
    height: 0.12,
    material: 'bone-metal',
    parent: detail,
    position: [0, height * 0.18, frontZ + 0.08],
    width: width * 0.76
  })
  for (const side of [-1, 1])
    box(scene, `${id}:greeble:signal-bar:${side}`, {
      depth: 0.12,
      height: height * 0.24,
      material: accent,
      parent: detail,
      position: [side * width * 0.32, height * 0.51, frontZ + 0.12],
      width: 0.16
    })

  return detail
}

function addRearIdentityPlaque(scene, id, parent, { accent, depth, height, label, width }) {
  addSign(scene, `${id}:rear-identity`, parent, {
    accent,
    label,
    position: [0, height * 0.62, -depth / 2 - 0.52],
    rotation: [0, Math.PI, 0],
    width: Math.min(width * 0.58, 5.8)
  })
}

/** A low-poly dome and collar break up the flat roofline at overview scale. */
function addRoofDome(scene, id, parent, { accent, depth, height, width }) {
  const dome = group(scene, `${id}:roof-dome`, parent)
  const diameter = Math.min(width * 0.28, 3.8)
  cylinder(scene, `${id}:roof-dome:collar`, {
    diameter: diameter * 0.86,
    height: 0.22,
    material: 'bone-metal',
    parent: dome,
    position: [0, height * 0.95, -depth * 0.12],
    tessellation: 12
  })
  sphere(scene, `${id}:roof-dome:cap`, {
    diameter,
    material: 'bone-metal',
    parent: dome,
    position: [0, height * 1.08, -depth * 0.12],
    scale: [1, 0.48, 1],
    segments: 10
  })
  torus(scene, `${id}:roof-dome:ring`, {
    diameter: diameter * 0.9,
    material: 'bone-metal',
    parent: dome,
    position: [0, height * 1.07, -depth * 0.12],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 12,
    thickness: 0.1
  })
  cylinder(scene, `${id}:roof-dome:beacon`, {
    diameter: 0.14,
    height: 0.9,
    material: 'bone-metal',
    parent: dome,
    position: [0, height * 1.38, -depth * 0.12],
    tessellation: 6
  })
  sphere(scene, `${id}:roof-dome:signal`, {
    diameter: 0.3,
    material: 'lunar-rust',
    parent: dome,
    position: [0, height * 1.84, -depth * 0.12],
    segments: 6
  })
  return dome
}

/** Roof equipment gives each district a lived-in skyline without textures. */
function addRoofEquipment(scene, id, parent, { accent, depth, height, width }) {
  const equipment = group(scene, `${id}:roof-equipment`, parent)
  for (let index = 0; index < 3; index += 1) {
    const x = (index - 1) * width * 0.28
    const tank = group(scene, `${id}:roof-equipment:tank:${index}`, equipment, {
      position: [x, height * 0.96 + (index % 2) * 0.32, -depth * 0.18]
    })
    cylinder(scene, `${id}:roof-equipment:tank:${index}:body`, {
      diameter: 0.72 + (index % 2) * 0.16,
      height: 1.35 + (index % 2) * 0.3,
      material: 'bone-metal',
      parent: tank,
      tessellation: 8
    })
    sphere(scene, `${id}:roof-equipment:tank:${index}:cap`, {
      diameter: 0.78 + (index % 2) * 0.14,
      material: accent,
      parent: tank,
      position: [0, 0.78 + (index % 2) * 0.16, 0],
      scale: [1, 0.4, 1],
      segments: 7
    })
    torus(scene, `${id}:roof-equipment:tank:${index}:ring`, {
      diameter: 0.92 + (index % 2) * 0.16,
      material: 'lunar-rust',
      parent: tank,
      position: [0, -0.28, 0],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 8,
      thickness: 0.08
    })
  }
  cylinder(scene, `${id}:roof-equipment:mast`, {
    diameter: 0.16,
    height: 2.5,
    material: 'bone-metal',
    parent: equipment,
    position: [width * 0.4, height * 1.08, -depth * 0.12],
    tessellation: 6
  })
  sphere(scene, `${id}:roof-equipment:beacon`, {
    diameter: 0.38,
    material: accent,
    parent: equipment,
    position: [width * 0.4, height * 1.08 + 1.32, -depth * 0.12],
    segments: 6
  })
  return equipment
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
      position: [-5.5 + index * 1.75, 13.45 + (index % 2) * 0.28, -2.7],
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

// The four secondary districts share the same low-poly structural language as
// the primary landmarks, but keep their silhouettes and identity props
// separate so the overview can distinguish every approved room at a glance.
function secondaryDistrictFrame(scene, id, { accent, depth, height, width }) {
  const root = group(scene, `${id}:root`)
  const near = group(scene, `${id}:lod:near`, root)
  const far = group(scene, `${id}:lod:far`, root)
  const shell = group(scene, `${id}:shell`, near)
  const roof = group(scene, `${id}:roof`, near)
  const entrance = group(scene, `${id}:entrance`, near, { position: [0, 1.8, depth / 2 + 0.35] })
  group(scene, `${id}:camera`, root, { position: [0, 6, depth + 4] })

  box(scene, `${id}:floor`, {
    depth: depth + 1.2,
    height: 0.62,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, 0.31, 0],
    width: width + 1.4
  })
  box(scene, `${id}:back-wall`, {
    depth: 0.72,
    height: height * 0.72,
    material: 'charcoal-structure',
    parent: shell,
    position: [0, height * 0.36, -depth / 2],
    width: width
  })
  // A shallow silver pressure-shell plate behind the inset facade gives
  // secondary rooms the framed, modular silhouette of the reference city.
  // It sits outside the existing wall so the authored interior windows stay
  // visible and does not change the navigation footprint.
  box(scene, `${id}:back-shell-panel`, {
    depth: 0.32,
    height: height * 0.8,
    material: 'bone-metal',
    parent: shell,
    position: [0, height * 0.4, -depth / 2 - 0.42],
    width: width + 0.9
  })
  // Shared facade construction kit: shallow inset windows and a lintel break
  // up secondary shells at overview distance without changing their footprint
  // or consuming new materials.
  for (let panel = 0; panel < 3; panel += 1) {
    box(scene, `${id}:back-window:${panel}`, {
      depth: 0.12,
      height: height * 0.18,
      material: accent,
      parent: shell,
      position: [-width * 0.3 + panel * width * 0.3, height * 0.46, -depth / 2 - 0.38],
      width: width * 0.16
    })
  }
  box(scene, `${id}:back-lintel`, {
    depth: 0.24,
    height: 0.3,
    material: 'bone-metal',
    parent: shell,
    position: [0, height * 0.66, -depth / 2 - 0.42],
    width: width * 0.76
  })
  for (const side of [-1, 1]) {
    box(scene, `${id}:side-wall:${side}`, {
      depth: depth * 0.7,
      height: height * 0.58,
      material: 'charcoal-structure',
      parent: shell,
      position: [side * width * 0.46, height * 0.29, -depth * 0.12],
      width: 0.9
    })
    box(scene, `${id}:entrance-post:${side}`, {
      depth: 1.08,
      height: height * 0.82,
      material: 'bone-metal',
      parent: entrance,
      position: [side * width * 0.35, 0, 0],
      width: 1.12
    })
    box(scene, `${id}:roof-shell:${side}`, {
      depth: depth * 0.52,
      height: 0.7,
      material: side < 0 ? 'bone-metal' : accent,
      parent: roof,
      position: [side * width * 0.28, height * 0.76, -depth * 0.25],
      rotation: [0, 0, side * 0.11],
      width: width * 0.48
    })
  }
  // Complete the open-front pressure shell with a heavy front lintel. The
  // reference rooms read as enclosed modules rather than freestanding slabs;
  // this single merged trim member makes that silhouette legible from the
  // overview while preserving the doorway opening beneath it.
  box(scene, `${id}:entrance-shell-lintel`, {
    depth: 1.05,
    height: 0.78,
    material: 'bone-metal',
    parent: entrance,
    position: [0, height * 0.78, 0.08],
    width: width * 0.76
  })
  box(scene, `${id}:roof-beam`, {
    depth: 0.9,
    height: 0.72,
    material: 'bone-metal',
    parent: roof,
    position: [0, height * 0.82, -depth * 0.2],
    width: width * 0.7
  })
  for (let skylight = 0; skylight < 2; skylight += 1)
    box(scene, `${id}:roof-skylight:${skylight}`, {
      depth: depth * 0.18,
      height: 0.22,
      material: accent,
      parent: roof,
      position: [-width * 0.18 + skylight * width * 0.36, height * 0.88, -depth * 0.18],
      rotation: [0, 0, skylight ? 0.06 : -0.06],
      width: width * 0.2
    })
  for (let step = 0; step < 3; step += 1)
    box(scene, `${id}:entrance:step:${step}`, {
      depth: 0.62 + step * 0.35,
      height: 0.18,
      material: step === 1 ? accent : 'lunar-rust',
      parent: entrance,
      position: [0, -1.45 + step * 0.18, 0.45 + step * 0.2],
      width: width * (0.5 - step * 0.05)
    })

  const building = installWireframeEnvelope(scene, { entrance, far, near, roof, root, shell }, id, {
    accent,
    depth,
    height,
    width
  })
  addFacadeKit(scene, id, near, { accent, depth, height, width })
  addEntranceWings(scene, id, near, { accent, depth, height, width })
  addEntryArch(scene, id, building.entrance, { accent, height, width })
  addWindowGrid(scene, id, near, { accent, depth, height, width })
  addFacadeGreebles(scene, id, near, { accent, depth, height, width })
  addRoofDome(scene, id, building.roof, { accent, depth, height, width })
  addRoofEquipment(scene, id, building.roof, { accent, depth, height, width })

  // The overview camera can see either side of a district as the player
  // orbits. A compact rear identity plaque keeps the city legible from the
  // outside-facing shell as well as from the open room front.
  const rearLabel = {
    'arts-studio': 'ARTS',
    'engineering-workshop': 'ENGINEER',
    'release-gatehouse': 'RELEASE',
    archive: 'ARCHIVE'
  }[id]
  if (rearLabel)
    addSign(scene, `${id}:rear-sign`, near, {
      accent,
      label: rearLabel,
      position: [0, height * 0.62, -depth / 2 - 0.52],
      rotation: [0, Math.PI, 0],
      width: Math.min(width * 0.58, 5.8)
    })

  box(scene, `${id}:far:floor`, {
    depth: depth + 0.8,
    height: 0.58,
    material: 'bone-metal',
    parent: far,
    position: [0, 0.28, 0],
    width: width + 1
  })
  box(scene, `${id}:far:back-wall`, {
    depth: 0.6,
    height: height * 0.68,
    material: 'bone-metal',
    parent: far,
    position: [0, height * 0.34, -depth * 0.42],
    width: width * 0.9
  })
  return building
}

export function buildArtsStudio(scene) {
  const building = secondaryDistrictFrame(scene, 'arts-studio', {
    accent: 'archive-emissive',
    depth: 10.5,
    height: 8.4,
    width: 14
  })
  const { near } = building
  // A stepped clerestory gives the studio a distinct roofline and enclosed
  // workshop read while keeping the front visually open. Existing materials
  // are reused so the building remains within its capped draw/material budget.
  const clerestory = group(scene, 'arts-studio:clerestory', near)
  for (let bay = 0; bay < 3; bay += 1) {
    box(scene, `arts-studio:clerestory:panel:${bay}`, {
      depth: 1.4,
      height: 1.35 + bay * 0.45,
      material: bay % 2 ? 'archive-emissive' : 'bone-metal',
      parent: clerestory,
      position: [-3.2 + bay * 3.2, 7.35 + bay * 0.35, -3.9],
      rotation: [0, 0, bay === 1 ? 0.08 : -0.06],
      width: 2.5
    })
  }
  addSign(scene, 'arts-studio:sign', near, {
    accent: 'archive-emissive',
    label: 'ARTS',
    position: [0, 7.3, 5.5],
    width: 5.4
  })
  const gallery = group(scene, 'arts-studio:gallery', near)
  for (let panel = 0; panel < 5; panel += 1) {
    const x = -5.1 + panel * 2.55
    box(scene, `arts-studio:canvas:${panel}`, {
      depth: 0.2,
      height: 2.2 + (panel % 2) * 0.6,
      material: panel % 2 ? 'sunset-orange' : 'archive-emissive',
      parent: gallery,
      position: [x, 3.4 + (panel % 2) * 0.5, -5.02],
      rotation: [0, 0, (panel - 2) * 0.04],
      width: 1.7
    })
    box(scene, `arts-studio:canvas-frame:${panel}`, {
      depth: 0.24,
      height: 0.14,
      material: 'bone-metal',
      parent: gallery,
      position: [x, 2.12 + (panel % 2) * 0.5, -4.9],
      width: 1.9
    })
  }
  const easels = group(scene, 'arts-studio:easels', near)
  for (const [index, x] of [-3.4, 3.4].entries()) {
    box(scene, `arts-studio:easel:${index}:board`, {
      depth: 0.35,
      height: 2.6,
      material: index ? 'sunset-orange' : 'garden-green',
      parent: easels,
      position: [x, 2.2, 1.5],
      rotation: [0, index ? -0.12 : 0.12, 0],
      width: 2.1
    })
    for (const leg of [-1, 1])
      box(scene, `arts-studio:easel:${index}:leg:${leg}`, {
        depth: 0.3,
        height: 2.4,
        material: 'bone-metal',
        parent: easels,
        position: [x + leg * 0.55, 1.1, 1.7],
        rotation: [0, leg * 0.09, leg * 0.08],
        width: 0.22
      })
  }
  const palette = group(scene, 'arts-studio:palette', near, { position: [0, 2.1, 0.4] })
  torus(scene, 'arts-studio:palette:rim', {
    diameter: 2.2,
    material: 'lunar-rust',
    parent: palette,
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: 0.34
  })
  for (const [index, material] of ['archive-emissive', 'sunset-orange', 'garden-green'].entries())
    sphere(scene, `arts-studio:palette:paint:${index}`, {
      diameter: 0.46,
      material,
      parent: palette,
      position: [-0.55 + index * 0.55, 0.2, 0],
      segments: 6
    })
  const identity = group(scene, 'arts-studio:city-identity', near)
  keepIdentity(
    torus(scene, 'arts-studio:city-identity:studio-star', {
      diameter: 6.8,
      material: 'lunar-rust',
      parent: identity,
      position: [0, 7.2, -5.2],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 8,
      thickness: 0.38
    })
  )
  group(scene, 'arts-studio:leader-anchor', near, { position: [-2.4, 1, 0.6] })
  scalarClip(scene, 'lights-idle', palette, 'rotation.y', 0.18, { duration: 72 })
  return building.root
}

export function buildEngineeringWorkshop(scene) {
  const building = secondaryDistrictFrame(scene, 'engineering-workshop', {
    accent: 'signal-emissive',
    depth: 12,
    height: 8.8,
    width: 15.5
  })
  const { near } = building
  addSign(scene, 'engineering-workshop:sign', near, {
    accent: 'signal-emissive',
    label: 'ENGINEER',
    position: [0, 7.65, 6.25],
    width: 6.2
  })
  const workbenches = addWorkbenches(scene, 'engineering-workshop:workbenches', near, {
    accent: 'signal-emissive',
    count: 3,
    position: [0, 0.7, 1.5]
  })
  const gantry = group(scene, 'engineering-workshop:gantry', near, { position: [0, 0.4, -1.8] })
  for (const side of [-1, 1])
    box(scene, `engineering-workshop:gantry-post:${side}`, {
      depth: 0.62,
      height: 7.3,
      material: 'lunar-rust',
      parent: gantry,
      position: [side * 5.5, 3.65, 0],
      width: 0.62
    })
  box(scene, 'engineering-workshop:gantry-beam', {
    depth: 0.62,
    height: 0.65,
    material: 'bone-metal',
    parent: gantry,
    position: [0, 7.1, 0],
    width: 11.6
  })
  box(scene, 'engineering-workshop:gantry-hook', {
    depth: 0.38,
    height: 2.2,
    material: 'signal-emissive',
    parent: gantry,
    position: [1.7, 5.9, 0],
    width: 0.34
  })
  const gear = group(scene, 'engineering-workshop:gear', near, { position: [-4.1, 3, -3.7] })
  torus(scene, 'engineering-workshop:gear:outer', {
    diameter: 4.6,
    material: 'lunar-rust',
    parent: gear,
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 12,
    thickness: 0.55
  })
  cylinder(scene, 'engineering-workshop:gear:hub', {
    diameter: 1.1,
    height: 0.6,
    material: 'signal-emissive',
    parent: gear,
    tessellation: 8
  })
  for (let tooth = 0; tooth < 8; tooth += 1) {
    const angle = tooth * (Math.PI / 4)
    box(scene, `engineering-workshop:gear:tooth:${tooth}`, {
      depth: 0.48,
      height: 0.62,
      material: 'bone-metal',
      parent: gear,
      position: [Math.sin(angle) * 2.25, 0, Math.cos(angle) * 2.25],
      rotation: [0, angle, 0],
      width: 0.7
    })
  }
  const identity = group(scene, 'engineering-workshop:city-identity', near)
  keepIdentity(
    box(scene, 'engineering-workshop:city-identity:bridge', {
      depth: 0.7,
      height: 1.6,
      material: 'lunar-rust',
      parent: identity,
      position: [0, 7.4, -5.5],
      rotation: [0, 0, -0.12],
      width: 8.2
    })
  )
  group(scene, 'engineering-workshop:leader-anchor', near, { position: [3.1, 1, 0.6] })
  scalarClip(scene, 'workbench-cycle', workbenches, 'rotation.y', 0.04, { duration: 64 })
  scalarClip(scene, 'gantry-idle', gantry, 'rotation.y', 0.06, { duration: 80 })
  return building.root
}

export function buildReleaseGatehouse(scene) {
  const building = secondaryDistrictFrame(scene, 'release-gatehouse', {
    accent: 'triage-amber',
    depth: 9.5,
    height: 7.8,
    width: 12.5
  })
  const { near, entrance } = building
  addSign(scene, 'release-gatehouse:sign', near, {
    accent: 'triage-amber',
    label: 'RELEASE',
    position: [0, 6.8, 5.1],
    width: 6.5
  })
  const gate = group(scene, 'release-gatehouse:release-gate', near, { position: [0, 1.4, 0] })
  for (const side of [-1, 1])
    box(scene, `release-gatehouse:gate-post:${side}`, {
      depth: 0.72,
      height: 6.1,
      material: 'bone-metal',
      parent: gate,
      position: [side * 4.5, 3.05, -0.3],
      width: 0.72
    })
  box(scene, 'release-gatehouse:gate-lintel', {
    depth: 0.72,
    height: 0.8,
    material: 'triage-amber',
    parent: gate,
    position: [0, 5.8, -0.3],
    width: 9.8
  })
  box(scene, 'release-gatehouse:gate-panel', {
    depth: 0.28,
    height: 3.7,
    material: 'charcoal-structure',
    parent: gate,
    position: [0, 2.3, -0.35],
    width: 7.4
  })
  for (let stripe = 0; stripe < 4; stripe += 1)
    box(scene, `release-gatehouse:gate-signal:${stripe}`, {
      depth: 0.08,
      height: 0.22,
      material: stripe % 2 ? 'triage-amber' : 'garden-green',
      parent: gate,
      position: [-2.7 + stripe * 1.8, 4.4, -0.55],
      width: 1.2
    })
  const beacon = group(scene, 'release-gatehouse:beacon', near, { position: [0, 7.6, -3.7] })
  cylinder(scene, 'release-gatehouse:beacon:stem', {
    diameter: 0.5,
    height: 2.2,
    material: 'bone-metal',
    parent: beacon,
    tessellation: 8
  })
  sphere(scene, 'release-gatehouse:beacon:light', {
    diameter: 1.35,
    material: 'triage-amber',
    parent: beacon,
    position: [0, 1.3, 0],
    segments: 7
  })
  const identity = group(scene, 'release-gatehouse:city-identity', near)
  keepIdentity(
    torus(scene, 'release-gatehouse:city-identity:release-ring', {
      diameter: 6.2,
      material: 'lunar-rust',
      parent: identity,
      position: [0, 5.9, -5.1],
      rotation: [Math.PI / 2, 0, 0],
      tessellation: 12,
      thickness: 0.42
    })
  )
  group(scene, 'release-gatehouse:leader-anchor', near, { position: [0, 1, 1.2] })
  scalarClip(scene, 'gatehouse-beacon', beacon, 'rotation.y', 0.16, { duration: 56 })
  scalarClip(scene, 'gatehouse-gate', entrance, 'rotation.y', 0.025, { duration: 80 })
  return building.root
}

export function buildArchive(scene) {
  const building = secondaryDistrictFrame(scene, 'archive', {
    accent: 'archive-emissive',
    depth: 11,
    height: 8.7,
    width: 13.5
  })
  const { near } = building
  addSign(scene, 'archive:sign', near, {
    accent: 'archive-emissive',
    label: 'ARCHIVE',
    position: [0, 7.55, 5.75],
    width: 4.8
  })
  const stacks = group(scene, 'archive:stacks', near)
  for (const side of [-1, 1]) {
    for (let row = 0; row < 3; row += 1) {
      const x = side * (3.9 + row * 1.25)
      box(scene, `archive:shelf:${side}:${row}`, {
        depth: 0.85,
        height: 5.7,
        material: 'charcoal-structure',
        parent: stacks,
        position: [x, 3.2, -4.95],
        width: 0.92
      })
      for (let level = 0; level < 5; level += 1)
        box(scene, `archive:record:${side}:${row}:${level}`, {
          depth: 0.32,
          height: 0.42,
          material: level % 2 ? 'archive-emissive' : 'lunar-rust',
          parent: stacks,
          position: [x, 1.25 + level * 1.03, -4.48],
          width: 0.7
        })
    }
  }
  const vault = group(scene, 'archive:vault', near, { position: [0, 2, 0.5] })
  cylinder(scene, 'archive:vault:body', {
    diameter: 3.7,
    height: 3.1,
    material: 'bone-metal',
    parent: vault,
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10
  })
  torus(scene, 'archive:vault:seal', {
    diameter: 2.5,
    material: 'archive-emissive',
    parent: vault,
    position: [0, 0, 1.9],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: 0.25
  })
  const identity = group(scene, 'archive:city-identity', near)
  keepIdentity(
    box(scene, 'archive:city-identity:sealed-volume', {
      depth: 0.55,
      height: 1.8,
      material: 'lunar-rust',
      parent: identity,
      position: [0, 7.2, -5.4],
      rotation: [0, 0, -0.08],
      width: 6.4
    })
  )
  group(scene, 'archive:leader-anchor', near, { position: [-2.5, 1, 0.5] })
  scalarClip(scene, 'archive-seal-idle', vault, 'rotation.y', 0.12, { duration: 96 })
  return building.root
}

export function buildLibrary(scene) {
  const building = specialistFrame(scene, 'library', {
    accent: 'archive-emissive',
    depth: 12,
    height: 9.8,
    width: 15
  })
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
    label: 'LIBRARY',
    position: [0, 8.5, 6.25],
    width: 5.2
  })
  addRearIdentityPlaque(scene, 'library', building.near, {
    accent: 'archive-emissive',
    depth: 12,
    height: 9.8,
    label: 'LIBRARY',
    width: 15
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
  const building = specialistFrame(scene, 'research-lab', {
    accent: 'signal-emissive',
    depth: 14,
    // Give the lab an observatory-like skyline so it cannot collapse into
    // the library's broad stepped silhouette in the city overview.
    height: 12.6,
    width: 18
  })
  addLayeredRoomDetail(scene, 'research-lab', building.near, {
    accent: 'signal-emissive',
    depth: 14,
    height: 12.6,
    width: 18
  })
  addLabMassing(scene, building)
  // A tall asymmetric sensor mast is the lab's skyline signature.  It keeps
  // the research landmark visually distinct from the library at overview
  // distance while remaining a handful of low-tessellation primitives.
  const mast = group(scene, 'research-lab:sensor-mast', building.near, { position: [-6.9, 12.8, -2.4] })
  cylinder(scene, 'research-lab:sensor-mast:shaft', {
    diameter: 0.38,
    height: 4.8,
    material: 'bone-metal',
    parent: mast,
    position: [0, 2.3, 0],
    tessellation: 6
  })
  sphere(scene, 'research-lab:sensor-mast:orb', {
    diameter: 0.95,
    material: 'signal-emissive',
    parent: mast,
    position: [0, 4.85, 0],
    segments: 6
  })
  torus(scene, 'research-lab:sensor-mast:ring', {
    diameter: 1.45,
    material: 'signal-emissive',
    parent: mast,
    position: [0, 4.85, 0],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 8,
    thickness: 0.09
  })
  addSign(scene, 'research-lab:sign', building.near, {
    accent: 'signal-emissive',
    glyph: 'flask',
    label: 'RESEARCH',
    position: [0, 11.2, 7.25],
    width: 6.5
  })
  addRearIdentityPlaque(scene, 'research-lab', building.near, {
    accent: 'signal-emissive',
    depth: 14,
    height: 12.6,
    label: 'RESEARCH',
    width: 18
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
  const building = specialistFrame(scene, 'depot', {
    accent: 'signal-emissive',
    depth: 11,
    height: 8.5,
    width: 14.5
  })
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
    label: 'DEPOT',
    position: [0, 7.35, 5.75],
    width: 5.4
  })
  addRearIdentityPlaque(scene, 'depot', building.near, {
    accent: 'signal-emissive',
    depth: 11,
    height: 8.5,
    label: 'DEPOT',
    width: 14.5
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
  const building = specialistFrame(scene, 'review-office', {
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
    label: 'REVIEW',
    position: [0, 8.05, 5.95],
    width: 5.8
  })
  addRearIdentityPlaque(scene, 'review-office', building.near, {
    accent: 'archive-emissive',
    depth: 11.5,
    height: 9.2,
    label: 'REVIEW',
    width: 15.5
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
    material: 'garden-green',
    parent: near,
    position: [0, 0.66, 0],
    tessellation: 12
  })
  const plants = addPlants(scene, near)
  // A low rear pavilion gives the circular recovery district a readable
  // enclosure/landmark without turning it into a box or blocking the open
  // garden front. Reuse existing materials so the capped draw-call budget
  // remains stable after mergeLodMeshes().
  const pavilion = group(scene, 'garden:rear-pavilion', near)
  for (const side of [-1, 1]) {
    box(scene, `garden:rear-pavilion:post:${side}`, {
      depth: 0.62,
      height: 3.7,
      material: 'bone-metal',
      parent: pavilion,
      position: [side * 4.1, 2.15, -5.1],
      rotation: [0, 0, side * 0.08],
      width: 0.62
    })
  }
  box(scene, 'garden:rear-pavilion:beam', {
    depth: 0.7,
    height: 0.52,
    material: 'bone-metal',
    parent: pavilion,
    position: [0, 4.05, -5.1],
    rotation: [0, 0, -0.04],
    width: 8.8
  })
  box(scene, 'garden:rear-pavilion:glow-panel', {
    depth: 0.16,
    height: 1.15,
    material: 'signal-emissive',
    parent: pavilion,
    position: [0, 2.5, -5.46],
    width: 2.6
  })
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
  const building = specialistFrame(scene, 'council', {
    accent: 'archive-emissive',
    depth: 10.5,
    height: 8.8,
    width: 14
  })
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
    label: 'COUNCIL',
    position: [0, 7.65, 5.45],
    width: 4.5
  })
  addRearIdentityPlaque(scene, 'council', building.near, {
    accent: 'archive-emissive',
    depth: 10.5,
    height: 8.8,
    label: 'COUNCIL',
    width: 14
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
