import { NodeIO } from '@gltf-transform/core'

import { Mesh, Quaternion, TransformNode } from './babylon.mjs'
import { APPROVED_PALETTE, paletteMaterial } from './palette.mjs'

// Bridges a hand-modeled Blender export (bevel/boolean/subdivision -- real
// modeling tools the procedural box() DSL in buildings.mjs cannot produce)
// back into the same Babylon.js scene-graph shape buildLibrary()/etc.
// return, so it flows through the existing optimizeModelScene/exportModel
// budget, merge, and hashing pipeline unchanged. See
// docs/lunar-city-handoff-2026-09-01-local-session.md for why this exists.

// Blender de-duplicates material datablock names across everything loaded
// into the shared staging .blend (e.g. "bone-metal" -> "bone-metal.008")
// whenever another building's import already claimed the bare name. Strip
// that suffix before matching against the approved palette.
export function normalizePaletteId(name) {
  return name?.replace(/\.\d{3}$/, '') ?? name
}

function materialForPrimitive(scene, primitive, id) {
  const rawName = primitive.getMaterial()?.getName()
  const name = normalizePaletteId(rawName)
  if (!name || !(name in APPROVED_PALETTE))
    throw new Error(
      `${id}: authored primitive material "${rawName}" is not an approved palette id (${Object.keys(APPROVED_PALETTE).join(', ')}). Rename the Blender material to reuse an existing approved id -- do not invent a new one.`
    )
  return paletteMaterial(scene, name)
}

function applyNodeTransform(target, node) {
  const [px, py, pz] = node.getTranslation()
  const [qx, qy, qz, qw] = node.getRotation()
  const [sx, sy, sz] = node.getScale()
  target.position.set(px, py, pz)
  target.rotationQuaternion = new Quaternion(qx, qy, qz, qw)
  target.scaling.set(sx, sy, sz)
}

// Groups whose descendants must survive mergeLodMeshes() as physical,
// individually-identifiable geometry rather than being folded into a
// generic per-material surface -- see the "$id:city-identity lacks a city-
// scale physical identity anchor" / "...is metadata without physical
// geometry" assertions in build-models.test.mjs. mergeLodMeshes() itself
// only understands mesh.metadata.keepSeparate, a transient Babylon-side
// flag the procedural box()/keepIdentity() DSL sets at build time -- it is
// never serialized to glTF, so an authored file can't carry it and this
// bridge has to reapply it by structural convention instead.
const KEEP_SEPARATE_GROUP_SUFFIXES = [':city-identity', ':portal']

function buildMeshFromPrimitive(scene, name, primitive, parent, id, keepSeparate) {
  const positions = primitive.getAttribute('POSITION')?.getArray()
  if (!positions) throw new Error(`${id}: primitive "${name}" has no POSITION attribute`)
  const normals = primitive.getAttribute('NORMAL')?.getArray()
  if (!normals)
    throw new Error(
      `${id}: primitive "${name}" has no NORMAL attribute -- enable normal export in Blender's glTF exporter`
    )
  const indicesAccessor = primitive.getIndices()
  const indices = indicesAccessor
    ? Array.from(indicesAccessor.getArray())
    : Array.from({ length: positions.length / 3 }, (_, index) => index)
  const uv = primitive.getAttribute('TEXCOORD_0')?.getArray()

  const mesh = new Mesh(name, scene)
  mesh.setVerticesData('position', Array.from(positions))
  mesh.setVerticesData('normal', Array.from(normals))
  if (uv) mesh.setVerticesData('uv', Array.from(uv))
  mesh.setIndices(indices)
  mesh.material = materialForPrimitive(scene, primitive, id)
  mesh.parent = parent
  mesh.isPickable = false
  if (keepSeparate) mesh.metadata = { ...(mesh.metadata ?? {}), keepSeparate: true }
  return mesh
}

function walk(scene, node, parent, id, keepSeparate) {
  const name = node.getName()
  if (!name)
    throw new Error(`${id}: authored file has an unnamed node -- every object needs the matching contract name`)
  const nodeKeepSeparate = keepSeparate || KEEP_SEPARATE_GROUP_SUFFIXES.some(suffix => name.endsWith(suffix))
  const mesh = node.getMesh()
  let target
  if (mesh) {
    const primitives = mesh.listPrimitives()
    if (primitives.length === 0) {
      target = new TransformNode(name, scene)
      target.parent = parent
    } else if (primitives.length === 1) {
      target = buildMeshFromPrimitive(scene, name, primitives[0], parent, id, nodeKeepSeparate)
    } else {
      target = new TransformNode(name, scene)
      target.parent = parent
      primitives.forEach((primitive, index) =>
        buildMeshFromPrimitive(scene, `${name}:part:${index}`, primitive, target, id, nodeKeepSeparate)
      )
    }
  } else {
    target = new TransformNode(name, scene)
    target.parent = parent
  }
  applyNodeTransform(target, node)
  for (const child of node.listChildren()) walk(scene, child, target, id, nodeKeepSeparate)
  return target
}

/**
 * Imports an authored (hand-modeled) GLB in place of a procedural build<Name>()
 * call. The file must contain a node named `${id}:root` somewhere in its
 * hierarchy (Blender's glTF exporter preserves object names verbatim,
 * including colons) with the same `:lod:near` / `:lod:far` / `:shell` /
 * `:roof` / `:entrance` child-node contract buildingNodes() establishes.
 * Every mesh's material must be named after an APPROVED_PALETTE id -- this
 * both enforces "reuse only this building's existing materials" and ensures
 * mergeLodMeshes() groups same-material meshes into one draw call exactly
 * as it does for procedurally built geometry.
 */
export async function importAuthoredModel(scene, id, filePath) {
  const document = await new NodeIO().read(filePath)
  const rootNode = document
    .getRoot()
    .listNodes()
    .find(node => node.getName() === `${id}:root`)
  if (!rootNode) throw new Error(`${id}: authored file ${filePath} has no node named "${id}:root"`)

  const root = walk(scene, rootNode, null, id, false)
  // The generated per-building GLB is always placed at local origin; runtime
  // placement comes from world-manifest.v2.json's per-model transform, not
  // from anything baked into this file. Reset explicitly regardless of what
  // Blender's exporter did with the (excluded) staging anchor parent.
  root.position.set(0, 0, 0)
  root.rotationQuaternion = new Quaternion(0, 0, 0, 1)
  root.scaling.set(1, 1, 1)
  return root
}
