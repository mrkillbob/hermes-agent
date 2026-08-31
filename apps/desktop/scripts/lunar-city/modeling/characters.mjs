import { Bone, Matrix, Skeleton, VertexBuffer } from './babylon.mjs'
import { poseClip } from './animation.mjs'
import { box, capsule, cone, cylinder, group, sphere, torus } from './primitives.mjs'
import { addAnimalTail, addAntler, addRobotLimb } from './props.mjs'

const WORKER_VARIANTS = Object.freeze([
  'orbital',
  'archivist',
  'builder',
  'artist',
  'dispatcher',
  'verifier',
  'courier'
])

function createRobotSkeleton(scene, rig) {
  const skeleton = new Skeleton('worker:skeleton', 'worker:skeleton', scene)
  const rootBone = new Bone('worker:bone:root', skeleton, null, Matrix.Identity())
  const links = [
    ['worker:bone:head', rig.head, rootBone],
    ['worker:bone:left-arm', rig.leftArm, rootBone],
    ['worker:bone:right-arm', rig.rightArm, rootBone],
    ['worker:bone:left-leg', rig.leftLeg, rootBone],
    ['worker:bone:right-leg', rig.rightLeg, rootBone],
    ['worker:bone:attachment', rig.attachment, rootBone]
  ]
  rootBone.linkTransformNode(rig.body)
  for (const [name, node, parent] of links) new Bone(name, skeleton, parent, Matrix.Identity()).linkTransformNode(node)
  skeleton.metadata = { gltf: { extras: { rig: 'modular-robot-child' } } }
  return {
    boneIndex: { attachment: 6, body: 0, head: 1, leftArm: 2, leftLeg: 4, rightArm: 3, rightLeg: 5 },
    skeleton
  }
}

function bindRobotPart(meshes, skeleton, boneIndex) {
  for (const mesh of meshes) {
    const vertexCount = mesh.getTotalVertices()
    const indices = new Float32Array(vertexCount * 4)
    const weights = new Float32Array(vertexCount * 4)
    for (let vertex = 0; vertex < vertexCount; vertex += 1) {
      indices[vertex * 4] = boneIndex
      weights[vertex * 4] = 1
    }
    mesh.setVerticesData(VertexBuffer.MatricesIndicesKind, indices, false, 4)
    mesh.setVerticesData(VertexBuffer.MatricesWeightsKind, weights, false, 4)
    mesh.skeleton = skeleton
  }
}

function addWorkerVariantAccessories(scene, attachment) {
  const variants = group(scene, 'worker:role-accessories', attachment)
  let template = null
  const piece = (role, name, position, scale, rotation = [0, 0, 0]) => {
    const mesh = template
      ? template.createInstance(`${role.name}:piece:${name}`)
      : box(scene, `${role.name}:piece:${name}`, {
          material: 'signal-emissive',
          parent: role
        })
    template ??= mesh
    mesh.parent = role
    mesh.position.set(...position)
    mesh.rotation.set(...rotation)
    mesh.scaling.set(...scale)
    mesh.isPickable = false
    mesh.metadata = { ...(mesh.metadata ?? {}), keepSeparate: true }
    return mesh
  }
  const recipes = {
    archivist: [
      ['satchel', [0, -0.2, 0.14], [0.62, 0.5, 0.2]],
      ['flap', [0, 0.12, 0.02], [0.52, 0.12, 0.24], [0.18, 0, 0]]
    ],
    artist: [
      ['palette', [0, -0.12, 0.1], [0.58, 0.08, 0.42], [0.25, 0, 0.35]],
      ['brush', [0.34, 0.22, -0.02], [0.08, 0.68, 0.08], [0, 0, -0.32]]
    ],
    builder: [
      ['hammer-handle', [0, -0.04, 0], [0.1, 0.72, 0.1], [0, 0, -0.22]],
      ['hammer-head', [-0.12, 0.33, 0], [0.58, 0.16, 0.18], [0, 0, -0.22]]
    ],
    courier: [
      ['pack', [0.08, -0.12, 0.28], [0.66, 0.7, 0.24]],
      ['pack-top', [0.08, 0.3, 0.22], [0.5, 0.16, 0.28]],
      ['strap', [-0.36, -0.08, 0.04], [0.08, 0.88, 0.08], [0, 0, 0.12]]
    ],
    dispatcher: [
      ['baton', [0, 0.02, 0], [0.1, 0.82, 0.1], [0, 0, -0.12]],
      ['flag', [0.25, 0.33, 0], [0.5, 0.3, 0.08], [0, 0, -0.12]]
    ],
    orbital: [
      ['mast', [0, 0.02, 0], [0.11, 0.82, 0.11]],
      ['beacon', [0, 0.46, 0], [0.5, 0.14, 0.14]]
    ],
    verifier: [
      ['check-a', [-0.16, -0.06, 0], [0.1, 0.5, 0.1], [0, 0, -0.7]],
      ['check-b', [0.18, 0.08, 0], [0.1, 0.82, 0.1], [0, 0, 0.55]]
    ]
  }
  for (const id of WORKER_VARIANTS) {
    const variant = group(scene, `worker:variant:${id}`, variants)
    variant.metadata = {
      gltf: {
        extras: {
          activationScale: [1, 1, 1],
          defaultActive: id === 'orbital',
          exclusiveGroup: 'worker-role',
          semantic: variant.name,
          variantId: id
        }
      }
    }
    if (id !== 'orbital') variant.scaling.set(0, 0, 0)
    const role = group(scene, `worker:role:${id}`, variant)
    for (const [name, position, scale, rotation] of recipes[id]) piece(role, name, position, scale, rotation)
  }
  return variants
}

function buildWorkerClips(scene, rig) {
  const { attachment, body, head, leftArm, leftLeg, rightArm, rightLeg } = rig
  const clips = [
    [
      'idle',
      [
        [head, [0, 0.12, 0]],
        [body, [0, 0, 0.025]]
      ]
    ],
    [
      'walk',
      [
        [leftArm, [0.65, 0, -0.18]],
        [rightArm, [-0.65, 0, 0.18]],
        [leftLeg, [-0.58, 0, 0]],
        [rightLeg, [0.58, 0, 0]]
      ]
    ],
    [
      'talk',
      [
        [head, [0.08, -0.22, 0.05]],
        [rightArm, [-0.4, 0, -0.72]]
      ]
    ],
    [
      'listen',
      [
        [head, [0.18, 0.28, -0.16]],
        [leftArm, [0.1, 0, -0.4]]
      ]
    ],
    [
      'work',
      [
        [leftArm, [-0.65, 0, 0.45]],
        [rightArm, [-0.65, 0, -0.45]]
      ]
    ],
    [
      'tool-use',
      [
        [rightArm, [-0.9, 0, -0.65]],
        [attachment, [0.18, 0.3, -0.12]]
      ]
    ],
    [
      'carry',
      [
        [leftArm, [-0.82, 0, 0.34]],
        [rightArm, [-0.82, 0, -0.34]],
        [head, [0.12, 0, 0]]
      ]
    ],
    [
      'handoff',
      [
        [rightArm, [-1.05, 0, -0.52]],
        [head, [0.06, -0.28, 0]],
        [attachment, [0, 0.42, 0]]
      ]
    ],
    [
      'queue',
      [
        [leftLeg, [0.12, 0, 0]],
        [rightLeg, [-0.12, 0, 0]],
        [head, [0, -0.16, 0]]
      ]
    ],
    [
      'wait',
      [
        [leftArm, [0.2, 0, -0.2]],
        [head, [0, 0.35, 0]]
      ]
    ],
    [
      'blocked',
      [
        [leftArm, [-0.15, 0, -1.1]],
        [rightArm, [-0.15, 0, 1.1]],
        [body, [0, 0, -0.08]]
      ]
    ],
    [
      'failed',
      [
        [head, [0.42, 0, 0]],
        [body, [0.12, 0, 0]],
        [leftLeg, [0.16, 0, 0]]
      ]
    ],
    [
      'review',
      [
        [head, [0, -0.32, 0]],
        [attachment, [0, -0.25, 0.2]],
        [leftArm, [-0.45, 0, 0.22]]
      ]
    ],
    [
      'triage',
      [
        [head, [-0.08, 0.3, 0]],
        [leftArm, [-0.75, 0, 0.62]],
        [attachment, [0.12, 0, 0]]
      ]
    ],
    [
      'heartbeat',
      [
        [body, [1.08, 1.04, 1.08], 'scaling'],
        [head, [1.04, 1.04, 1.04], 'scaling'],
        [attachment, [0, 0.12, 0]]
      ]
    ],
    [
      'rest',
      [
        [head, [0.36, 0, 0]],
        [leftLeg, [0.2, 0, 0]],
        [rightLeg, [0.2, 0, 0]],
        [body, [0.08, 0, 0]]
      ]
    ],
    [
      'done',
      [
        [leftArm, [0, 0, -1.35]],
        [rightArm, [0, 0, 1.35]],
        [head, [-0.12, 0, 0]],
        [body, [0, 0, 0.06]]
      ]
    ]
  ]
  return clips.map(([name, channels], index) =>
    poseClip(
      scene,
      name,
      channels.map(([target, middle, property]) => ({ middle, property, target })),
      { duration: 30 + (index % 4) * 6 }
    )
  )
}

export function buildWorker(scene) {
  const root = group(scene, 'worker:base')
  const body = group(scene, 'worker:body', root, { position: [0, 1.15, 0] })
  capsule(scene, 'worker:body:shell', {
    height: 1.15,
    material: 'bone-metal',
    parent: body,
    radius: 0.42,
    tessellation: 8
  })
  box(scene, 'worker:body:chest', {
    depth: 0.18,
    height: 0.38,
    material: 'charcoal-structure',
    parent: body,
    position: [0, 0.05, -0.4],
    width: 0.5
  })
  sphere(scene, 'worker:body:signal', {
    diameter: 0.16,
    material: 'signal-emissive',
    parent: body,
    position: [0, 0.06, -0.51],
    segments: 6
  })

  const head = group(scene, 'worker:head', root, { position: [0, 2.05, 0] })
  sphere(scene, 'worker:head:shell', {
    diameter: 1.05,
    material: 'bone-metal',
    parent: head,
    scale: [1, 0.82, 0.86],
    segments: 8
  })
  box(scene, 'worker:head:face', {
    depth: 0.2,
    height: 0.48,
    material: 'charcoal-structure',
    parent: head,
    position: [0, -0.02, -0.46],
    scale: [1, 1, 1],
    width: 0.73
  })
  for (const x of [-0.19, 0.19])
    sphere(scene, `worker:head:eye:${x}`, {
      diameter: 0.12,
      material: 'signal-emissive',
      parent: head,
      position: [x, 0.02, -0.58],
      segments: 6
    })
  cylinder(scene, 'worker:head:antenna', {
    diameter: 0.08,
    height: 0.45,
    material: 'charcoal-structure',
    parent: head,
    position: [0, 0.62, 0]
  })
  sphere(scene, 'worker:head:antenna-light', {
    diameter: 0.18,
    material: 'signal-emissive',
    parent: head,
    position: [0, 0.86, 0],
    segments: 6
  })

  const leftArm = addRobotLimb(scene, 'worker:limb:left-arm', root, [-0.58, 1.28, 0], [0, 0, -0.12])
  const rightArm = addRobotLimb(scene, 'worker:limb:right-arm', root, [0.58, 1.28, 0], [0, 0, 0.12])
  const leftLeg = addRobotLimb(scene, 'worker:limb:left-leg', root, [-0.23, 0.52, 0], [0, 0, 0.02])
  const rightLeg = addRobotLimb(scene, 'worker:limb:right-leg', root, [0.23, 0.52, 0], [0, 0, -0.02])
  const attachment = group(scene, 'worker:attachment', root, { position: [0.64, 1.42, 0] })
  addWorkerVariantAccessories(scene, attachment)
  const rig = { attachment, body, head, leftArm, leftLeg, rightArm, rightLeg }
  const { boneIndex, skeleton } = createRobotSkeleton(scene, rig)
  bindRobotPart(body.getChildMeshes(true), skeleton, boneIndex.body)
  bindRobotPart(head.getChildMeshes(true), skeleton, boneIndex.head)
  bindRobotPart(leftArm.getChildMeshes(true), skeleton, boneIndex.leftArm)
  bindRobotPart(rightArm.getChildMeshes(true), skeleton, boneIndex.rightArm)
  bindRobotPart(leftLeg.getChildMeshes(true), skeleton, boneIndex.leftLeg)
  bindRobotPart(rightLeg.getChildMeshes(true), skeleton, boneIndex.rightLeg)
  return { clips: buildWorkerClips(scene, rig), root }
}

export function buildWorkers(scene) {
  const root = group(scene, 'workers:root')
  const near = group(scene, 'workers:lod:near', root)
  buildWorker(scene).root.parent = near
  const far = group(scene, 'workers:lod:far', root)
  capsule(scene, 'workers:far:body', {
    height: 1.35,
    material: 'bone-metal',
    parent: far,
    position: [0, 1, 0],
    radius: 0.44,
    tessellation: 6
  })
  sphere(scene, 'workers:far:head', {
    diameter: 0.96,
    material: 'bone-metal',
    parent: far,
    position: [0, 1.9, 0],
    segments: 6
  })
  box(scene, 'workers:far:face', {
    depth: 0.18,
    height: 0.42,
    material: 'signal-emissive',
    parent: far,
    position: [0, 1.9, -0.42],
    width: 0.65
  })
  return root
}

function animalBase(
  scene,
  id,
  parent,
  { bodyMaterial = 'charcoal-structure', height = 2.5, position, robe = 'archive-emissive', width = 1.25 } = {}
) {
  const root = group(scene, `leader:${id}`, parent, { position })
  capsule(scene, `leader:${id}:body`, { height, material: robe, parent: root, radius: width * 0.48, tessellation: 8 })
  cone(scene, `leader:${id}:layered-robe`, {
    diameterBottom: width * 1.22,
    diameterTop: width * 0.72,
    height: height * 0.82,
    material: robe,
    parent: root,
    position: [0, -height * 0.11, 0.12],
    tessellation: 8
  })
  const headRig = group(scene, `leader:${id}:head-rig`, root, { position: [0, height * 0.52, 0] })
  const headMesh = sphere(scene, `leader:${id}:head`, {
    diameter: width,
    material: bodyMaterial,
    parent: headRig,
    segments: 8
  })
  for (const side of [-1, 1]) {
    capsule(scene, `leader:${id}:arm:${side}`, {
      height: height * 0.62,
      material: robe,
      parent: root,
      position: [side * width * 0.58, 0.02, -0.03],
      radius: width * 0.13,
      rotation: [0, 0, side * -0.16],
      tessellation: 7
    })
    sphere(scene, `leader:${id}:hand:${side}`, {
      diameter: width * 0.28,
      material: bodyMaterial,
      parent: root,
      position: [side * width * 0.68, -height * 0.28, -0.08],
      segments: 6
    })
  }
  torus(scene, `leader:${id}:mantle`, {
    diameter: width * 1.22,
    material: 'bone-metal',
    parent: root,
    position: [0, height * 0.22, 0],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: width * 0.07
  })
  box(scene, `leader:${id}:chest`, {
    depth: 0.2,
    height: 0.52,
    material: 'bone-metal',
    parent: root,
    position: [0, 0.18, -width * 0.46],
    width: width * 0.55
  })
  root.leaderRig = { head: headRig, headMesh }
  return root
}

function buildOwl(scene, parent) {
  const owl = animalBase(scene, 'owl', parent, {
    bodyMaterial: 'charcoal-structure',
    height: 2.55,
    position: [-7.5, 1.5, -2.2],
    robe: 'archive-emissive',
    width: 1.5
  })
  owl.leaderRig.headMesh.metadata = { ...(owl.leaderRig.headMesh.metadata ?? {}), keepSeparate: true }
  for (const x of [-0.38, 0.38]) {
    sphere(scene, `leader:owl:eye:${x}`, {
      diameter: 0.42,
      material: 'bone-metal',
      parent: owl,
      position: [x, 1.42, -0.62],
      segments: 7
    })
    sphere(scene, `leader:owl:pupil:${x}`, {
      diameter: 0.16,
      material: 'archive-emissive',
      parent: owl,
      position: [x, 1.43, -0.81],
      segments: 6
    })
  }
  cone(scene, 'leader:owl:beak', {
    diameterBottom: 0.44,
    height: 0.72,
    material: 'triage-amber',
    parent: owl,
    position: [0, 1.1, -0.9],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 5
  })
  for (const side of [-1, 1])
    cone(scene, `leader:owl:ear:${side}`, {
      diameterBottom: 0.5,
      height: 0.75,
      material: 'charcoal-structure',
      parent: owl,
      position: [side * 0.5, 2.03, 0],
      rotation: [0, 0, side * -0.28],
      tessellation: 5
    })
  return owl
}

function buildFox(scene, parent) {
  const fox = animalBase(scene, 'fox', parent, {
    bodyMaterial: 'lunar-rust',
    height: 2.75,
    position: [-4.4, 1.55, 2.1],
    robe: 'archive-emissive',
    width: 1.25
  })
  for (const side of [-1, 1])
    cone(scene, `leader:fox:ear:${side}`, {
      diameterBottom: 0.58,
      height: 1.05,
      material: 'lunar-rust',
      parent: fox,
      position: [side * 0.38, 2.1, 0],
      tessellation: 5
    })
  cone(scene, 'leader:fox:muzzle', {
    diameterBottom: 0.52,
    height: 0.88,
    material: 'bone-metal',
    parent: fox,
    position: [0, 1.33, -0.78],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 6
  })
  const tail = addAnimalTail(scene, 'leader:fox:tail', fox, {
    position: [0.58, 0.2, 0.5],
    scale: [1.3, 1.15, 1.3]
  })
  tail.getChildMeshes(true)[0].metadata = { keepSeparate: true }
  return fox
}

function buildBadger(scene, parent) {
  const badger = animalBase(scene, 'badger', parent, {
    bodyMaterial: 'charcoal-structure',
    height: 2.45,
    position: [-1.45, 1.45, -2],
    robe: 'archive-emissive',
    width: 1.65
  })
  box(scene, 'leader:badger:stripe', {
    depth: 0.15,
    height: 1.35,
    material: 'bone-metal',
    parent: badger,
    position: [0, 1.43, -0.76],
    width: 0.46
  })
  for (const side of [-1, 1])
    sphere(scene, `leader:badger:ear:${side}`, {
      diameter: 0.48,
      material: 'charcoal-structure',
      parent: badger,
      position: [side * 0.58, 1.92, 0],
      segments: 6
    })
  box(scene, 'leader:badger:ledger', {
    depth: 0.3,
    height: 0.8,
    material: 'bone-metal',
    parent: badger,
    position: [0, -0.1, -0.9],
    rotation: [-0.25, 0, 0],
    width: 1.4
  })
  return badger
}

function buildOtter(scene, parent) {
  const otter = animalBase(scene, 'otter', parent, {
    bodyMaterial: 'lunar-rust',
    height: 2.5,
    position: [1.7, 1.5, 2.1],
    robe: 'lunar-rust',
    width: 1.28
  })
  sphere(scene, 'leader:otter:muzzle', {
    diameter: 0.68,
    material: 'bone-metal',
    parent: otter,
    position: [0, 1.35, -0.57],
    scale: [1.15, 0.64, 0.72],
    segments: 7
  })
  cylinder(scene, 'leader:otter:cap', {
    diameter: 1.15,
    height: 0.25,
    material: 'charcoal-structure',
    parent: otter,
    position: [0, 2.05, 0],
    tessellation: 8
  })
  box(scene, 'leader:otter:dispatch-baton', {
    depth: 0.12,
    height: 1.6,
    material: 'archive-emissive',
    parent: otter,
    position: [0.78, 0.1, -0.2],
    rotation: [0, 0, -0.3],
    width: 0.12
  })
  const tail = addAnimalTail(scene, 'leader:otter:tail', otter, {
    material: 'lunar-rust',
    position: [0.4, -0.3, 0.55],
    scale: [0.75, 1.05, 0.75]
  })
  tail.getChildMeshes(true)[0].metadata = { keepSeparate: true }
  return otter
}

function buildBird(scene, parent) {
  const bird = animalBase(scene, 'bird', parent, {
    bodyMaterial: 'charcoal-structure',
    height: 2.7,
    position: [4.7, 1.55, -2],
    robe: 'archive-emissive',
    width: 1.4
  })
  cone(scene, 'leader:bird:beak', {
    diameterBottom: 0.58,
    height: 0.95,
    material: 'lunar-rust',
    parent: bird,
    position: [0, 1.45, -0.88],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 5
  })
  for (const side of [-1, 1]) {
    cone(scene, `leader:bird:wing:${side}`, {
      diameterBottom: 0.8,
      diameterTop: 0.16,
      height: 2.1,
      material: 'archive-emissive',
      parent: bird,
      position: [side * 0.76, 0.12, 0],
      rotation: [0, 0, side * -0.24],
      tessellation: 6
    })
  }
  const wingRig = group(scene, 'leader:bird:wing-rig', bird, { position: [0, 0.2, 0.25] })
  const wingMantle = sphere(scene, 'leader:bird:wing-rig:mantle', {
    diameter: 2.6,
    material: 'archive-emissive',
    parent: wingRig,
    scale: [1.25, 0.42, 0.38],
    segments: 8
  })
  wingMantle.metadata = { keepSeparate: true }
  bird.leaderRig.wings = wingRig
  torus(scene, 'leader:bird:verifier-ring', {
    diameter: 1.6,
    material: 'archive-emissive',
    parent: bird,
    position: [0, -0.65, -0.45],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 10,
    thickness: 0.12
  })
  return bird
}

function buildStag(scene, parent) {
  const stag = animalBase(scene, 'stag', parent, {
    bodyMaterial: 'lunar-rust',
    height: 3,
    position: [7.7, 1.7, 2.1],
    robe: 'charcoal-structure',
    width: 1.45
  })
  cone(scene, 'leader:stag:muzzle', {
    diameterBottom: 0.62,
    height: 0.88,
    material: 'bone-metal',
    parent: stag,
    position: [0, 1.55, -0.82],
    rotation: [Math.PI / 2, 0, 0],
    tessellation: 6
  })
  addAntler(scene, 'leader:stag:antler:left', stag, -1)
  addAntler(scene, 'leader:stag:antler:right', stag, 1)
  box(scene, 'leader:stag:coordinator-tablet', {
    depth: 0.18,
    height: 1.05,
    material: 'archive-emissive',
    parent: stag,
    position: [0.8, 0.08, -0.52],
    rotation: [0, 0, -0.18],
    width: 0.7
  })
  return stag
}

export function buildLeaders(scene) {
  const root = group(scene, 'leaders:root')
  const near = group(scene, 'leaders:lod:near', root)
  near.scaling.set(1.28, 1.36, 1.28)
  const owl = buildOwl(scene, near)
  const fox = buildFox(scene, near)
  buildBadger(scene, near)
  const otter = buildOtter(scene, near)
  const bird = buildBird(scene, near)
  buildStag(scene, near)

  const far = group(scene, 'leaders:lod:far', root)
  const positions = [
    [-7.5, 1.5, -2.2],
    [-4.4, 1.55, 2.1],
    [-1.45, 1.45, -2],
    [1.7, 1.5, 2.1],
    [4.7, 1.55, -2],
    [7.7, 1.7, 2.1]
  ]
  positions.forEach((position, index) => {
    capsule(scene, `leaders:far:body:${index}`, {
      height: 2.5 + (index % 3) * 0.2,
      material: 'charcoal-structure',
      parent: far,
      position,
      radius: 0.62,
      tessellation: 6
    })
    sphere(scene, `leaders:far:head:${index}`, {
      diameter: 1.05,
      material: 'charcoal-structure',
      parent: far,
      position: [position[0], position[1] + 1.35, position[2]],
      segments: 6
    })
  })
  const parts = {
    birdWing: bird.leaderRig.wings,
    foxTail: fox.getChildTransformNodes(true).find(node => node.name === 'leader:fox:tail'),
    otterTail: otter.getChildTransformNodes(true).find(node => node.name === 'leader:otter:tail'),
    owlHead: owl.leaderRig.head
  }
  const leaderPoses = [
    [
      'idle',
      [
        [parts.owlHead, [0.02, 0.16, 0]],
        [parts.birdWing, [0, 0.08, 0.06]]
      ]
    ],
    [
      'listening',
      [
        [parts.owlHead, [0.12, -0.32, -0.12]],
        [parts.foxTail, [0.8, 0.14, -1.05]]
      ]
    ],
    [
      'talking',
      [
        [parts.owlHead, [-0.08, 0.28, 0.08]],
        [parts.otterTail, [0.62, 0.2, -0.5]]
      ]
    ],
    [
      'thinking',
      [
        [parts.owlHead, [0.2, -0.18, 0.16]],
        [parts.birdWing, [0.18, 0, -0.16]]
      ]
    ],
    [
      'acknowledging',
      [
        [parts.foxTail, [0.58, 0.2, -0.42]],
        [parts.otterTail, [0.92, 0, -1.12]]
      ]
    ],
    [
      'unavailable',
      [
        [parts.birdWing, [0.38, 0, 0]],
        [parts.owlHead, [0.28, 0, 0]]
      ]
    ],
    [
      'listen',
      [
        [parts.owlHead, [0.1, 0.34, -0.08]],
        [parts.otterTail, [0.7, -0.2, -0.8]]
      ]
    ],
    [
      'talk',
      [
        [parts.birdWing, [-0.22, 0.12, 0.18]],
        [parts.foxTail, [0.55, 0.32, -0.62]]
      ]
    ],
    [
      'think',
      [
        [parts.owlHead, [0.18, -0.24, 0.18]],
        [parts.foxTail, [0.9, 0.1, -1.18]],
        [parts.birdWing, [0.12, 0, 0]]
      ]
    ],
    [
      'acknowledge',
      [
        [parts.owlHead, [-0.14, 0, 0]],
        [parts.otterTail, [0.52, 0.32, -0.42]],
        [parts.birdWing, [-0.15, 0, 0.14]]
      ]
    ]
  ]
  leaderPoses.map(([name, channels], index) =>
    poseClip(
      scene,
      name,
      channels.map(([target, middle]) => ({ middle, target })),
      { duration: 36 + (index % 4) * 8 }
    )
  )
  return root
}
