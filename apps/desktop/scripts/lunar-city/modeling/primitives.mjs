import { Mesh, MeshBuilder, Quaternion, TransformNode, Vector3 } from './babylon.mjs'
import { paletteMaterial } from './palette.mjs'

function applyTransform(node, { position = [0, 0, 0], rotation = [0, 0, 0], scale = [1, 1, 1] } = {}) {
  node.position.set(...position)
  node.rotation.set(...rotation)
  node.scaling.set(...scale)
  return node
}

function finish(mesh, options) {
  mesh.parent = options.parent ?? null
  mesh.material =
    typeof options.material === 'string' ? paletteMaterial(mesh.getScene(), options.material) : options.material
  mesh.isPickable = false
  return applyTransform(mesh, options)
}

export function group(scene, name, parent = null, transform = {}) {
  const node = new TransformNode(name, scene)
  node.parent = parent
  node.metadata = { gltf: { extras: { semantic: name } } }
  return applyTransform(node, transform)
}

export function box(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateBox(
    name,
    {
      depth: options.depth ?? options.size ?? 1,
      height: options.height ?? options.size ?? 1,
      width: options.width ?? options.size ?? 1
    },
    scene
  )
  return finish(mesh, options)
}

export function capsule(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateCapsule(
    name,
    {
      capSubdivisions: 2,
      height: options.height ?? 1,
      radius: options.radius ?? 0.25,
      subdivisions: 2,
      tessellation: options.tessellation ?? 8
    },
    scene
  )
  return finish(mesh, options)
}

export function cone(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateCylinder(
    name,
    {
      diameterBottom: options.diameterBottom ?? options.diameter ?? 1,
      diameterTop: options.diameterTop ?? 0,
      height: options.height ?? 1,
      tessellation: options.tessellation ?? 6
    },
    scene
  )
  return finish(mesh, options)
}

export function cylinder(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateCylinder(
    name,
    {
      diameter: options.diameter ?? 1,
      height: options.height ?? 1,
      tessellation: options.tessellation ?? 8
    },
    scene
  )
  return finish(mesh, options)
}

export function sphere(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateSphere(
    name,
    {
      diameter: options.diameter ?? 1,
      segments: options.segments ?? 8
    },
    scene
  )
  return finish(mesh, options)
}

export function torus(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateTorus(
    name,
    {
      diameter: options.diameter ?? 1,
      tessellation: options.tessellation ?? 12,
      thickness: options.thickness ?? 0.18
    },
    scene
  )
  return finish(mesh, options)
}

export function beamBetween(scene, name, from, to, options = {}) {
  const start = new Vector3(...from)
  const end = new Vector3(...to)
  const midpoint = start.add(end).scale(0.5)
  const length = Vector3.Distance(start, end)
  const mesh = box(scene, name, {
    depth: options.width ?? 1,
    height: options.height ?? 0.5,
    material: options.material,
    parent: options.parent,
    position: midpoint.asArray(),
    width: length
  })
  mesh.rotation.y = -Math.atan2(end.z - start.z, end.x - start.x)
  return mesh
}

function directChildOf(node, ancestor) {
  let cursor = node.parent
  while (cursor && cursor !== ancestor) cursor = cursor.parent
  return cursor === ancestor
}

export function mergeLodMeshes(scene, lodRoot, prefix) {
  const candidates = scene.meshes.filter(mesh => directChildOf(mesh, lodRoot) && !mesh.metadata?.keepSeparate)
  const byMaterial = new Map()
  for (const mesh of candidates) {
    if (!mesh.material) continue
    const key = mesh.material.uniqueId
    if (!byMaterial.has(key)) byMaterial.set(key, [])
    byMaterial.get(key).push(mesh)
  }

  let index = 0
  for (const meshes of byMaterial.values()) {
    if (meshes.length === 1) {
      meshes[0].name = `${prefix}:surface:${index}`
      index += 1
      continue
    }
    const skeleton = meshes.every(mesh => mesh.skeleton === meshes[0].skeleton) ? meshes[0].skeleton : null
    for (const mesh of meshes) mesh.computeWorldMatrix(true)
    const merged = Mesh.MergeMeshes(meshes, true, true, undefined, false, true)
    if (!merged) throw new Error(`unable to merge ${prefix} geometry`)
    if (skeleton) merged.skeleton = skeleton
    merged.name = `${prefix}:surface:${index}`
    merged.parent = lodRoot
    merged.isPickable = false
    index += 1
  }
}

export function prismRailing(scene, name, parent, x, z, width, depth, material) {
  const rail = group(scene, name, parent)
  box(scene, `${name}:front`, {
    depth: 0.14,
    height: 0.32,
    material,
    parent: rail,
    position: [x, 0.62, z + depth / 2],
    width
  })
  box(scene, `${name}:back`, {
    depth: 0.14,
    height: 0.32,
    material,
    parent: rail,
    position: [x, 0.62, z - depth / 2],
    width
  })
  return rail
}

export { Quaternion, Vector3 }
