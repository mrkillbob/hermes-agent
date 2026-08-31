import { Bone, Matrix, Skeleton, VertexBuffer } from './babylon.mjs'
import { stateClips } from './animation.mjs'
import { box, capsule, cone, cylinder, group, sphere, torus } from './primitives.mjs'
import { addAnimalTail, addAntler, addRobotLimb, addRobotTool } from './props.mjs'

const WORKER_CLIPS = Object.freeze([
  'idle',
  'walk',
  'talk',
  'listen',
  'work',
  'tool-use',
  'carry',
  'handoff',
  'queue',
  'wait',
  'blocked',
  'failed',
  'review',
  'triage',
  'heartbeat',
  'rest',
  'done'
])
const LEADER_CLIPS = Object.freeze([
  'idle',
  'listening',
  'talking',
  'thinking',
  'acknowledging',
  'unavailable',
  'listen',
  'talk',
  'think',
  'acknowledge'
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
  return skeleton
}

function bindRobotBody(mesh, skeleton) {
  const vertexCount = mesh.getTotalVertices()
  const indices = new Float32Array(vertexCount * 4)
  const weights = new Float32Array(vertexCount * 4)
  for (let vertex = 0; vertex < vertexCount; vertex += 1) weights[vertex * 4] = 1
  mesh.setVerticesData(VertexBuffer.MatricesIndicesKind, indices, false, 4)
  mesh.setVerticesData(VertexBuffer.MatricesWeightsKind, weights, false, 4)
  mesh.skeleton = skeleton
  mesh.metadata = { ...(mesh.metadata ?? {}), keepSeparate: true }
}

export function buildWorker(scene, variant = { id: 'orbital' }) {
  const root = group(scene, `worker:${variant.id}`)
  const body = group(scene, 'worker:body', root, { position: [0, 1.15, 0] })
  const bodyMesh = capsule(scene, 'worker:body:shell', {
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
  const accessories = group(scene, 'worker:role-accessories', attachment)
  addRobotTool(scene, 'worker:role:orbital', accessories, 'antenna', [0, 0.22, 0])
  addRobotTool(scene, 'worker:role:archivist', accessories, 'satchel', [-0.12, -0.38, 0.08])
  addRobotTool(scene, 'worker:role:builder', accessories, 'hammer', [0.16, -0.32, -0.03])
  addRobotTool(scene, 'worker:role:verifier', accessories, 'ring', [0.02, -0.68, 0.1])
  const skeleton = createRobotSkeleton(scene, { attachment, body, head, leftArm, leftLeg, rightArm, rightLeg })
  bindRobotBody(bodyMesh, skeleton)
  return { clips: stateClips(scene, root, WORKER_CLIPS), root }
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
  sphere(scene, `leader:${id}:head`, {
    diameter: width,
    material: bodyMaterial,
    parent: root,
    position: [0, height * 0.52, 0],
    segments: 8
  })
  box(scene, `leader:${id}:chest`, {
    depth: 0.2,
    height: 0.52,
    material: 'bone-metal',
    parent: root,
    position: [0, 0.18, -width * 0.46],
    width: width * 0.55
  })
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
      material: 'signal-emissive',
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
    robe: 'signal-emissive',
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
  addAnimalTail(scene, 'leader:fox:tail', fox, { position: [0.58, 0.2, 0.5], scale: [1.3, 1.15, 1.3] })
  return fox
}

function buildBadger(scene, parent) {
  const badger = animalBase(scene, 'badger', parent, {
    bodyMaterial: 'charcoal-structure',
    height: 2.45,
    position: [-1.45, 1.45, -2],
    robe: 'signal-emissive',
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
    robe: 'triage-amber',
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
    material: 'signal-emissive',
    parent: otter,
    position: [0.78, 0.1, -0.2],
    rotation: [0, 0, -0.3],
    width: 0.12
  })
  addAnimalTail(scene, 'leader:otter:tail', otter, {
    material: 'lunar-rust',
    position: [0.4, -0.3, 0.55],
    scale: [0.75, 1.05, 0.75]
  })
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
    material: 'triage-amber',
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
  torus(scene, 'leader:bird:verifier-ring', {
    diameter: 1.6,
    material: 'signal-emissive',
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
    material: 'signal-emissive',
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
  buildOwl(scene, near)
  buildFox(scene, near)
  buildBadger(scene, near)
  buildOtter(scene, near)
  buildBird(scene, near)
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
      material: index % 2 ? 'lunar-rust' : 'charcoal-structure',
      parent: far,
      position,
      radius: 0.62,
      tessellation: 6
    })
    sphere(scene, `leaders:far:head:${index}`, {
      diameter: 1.05,
      material: 'bone-metal',
      parent: far,
      position: [position[0], position[1] + 1.35, position[2]],
      segments: 6
    })
  })
  stateClips(scene, near, LEADER_CLIPS)
  return root
}
