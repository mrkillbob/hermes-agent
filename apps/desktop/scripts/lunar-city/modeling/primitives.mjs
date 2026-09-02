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

function markRole(mesh, role) {
  mesh.metadata = { ...(mesh.metadata ?? {}), lunarCityRole: role }
  return mesh
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

/**
 * Build the pressure-shell language used by the reference buildings.
 * Structural members are rounded tubes and the occupied volume is one curved
 * ribbon skin, so the building reads as a designed shell instead of a stack
 * of unrelated cuboids. The shell is deliberately local to one LOD root so
 * the normal material merge still keeps the exported asset inexpensive.
 */
export function wireframeShell(
  scene,
  name,
  parent,
  {
    accent = 'signal-emissive',
    depth = 10,
    height = 8,
    structure = 'bone-metal',
    width = 14,
    skin = 'charcoal-structure'
  } = {}
) {
  const root = group(scene, name, parent)
  const frame = group(scene, `${name}:frame`, root)
  const skinRoot = group(scene, `${name}:skin`, root)
  const entrance = group(scene, `${name}:entrance`, root, { position: [0, 0, depth / 2 + 0.35] })
  const roof = group(scene, `${name}:roof`, root)
  const radius = Math.max(0.18, Math.min(width, height, depth) * 0.032)
  const profileId = name.split(':')[0]
  const roofProfiles = {
    council: [0.45, 0.72, 1.08, 1.42, 1.08, 0.72, 0.45],
    depot: [0.78, 0.82, 0.84, 0.8, 0.84, 0.82, 0.78],
    library: [0.88, 1.0, 1.08, 1.2, 1.08, 1.0, 0.88],
    'research-lab': [1.34, 1.08, 0.78, 0.62, 0.78, 1.12, 1.38],
    'review-office': [0.48, 1.28, 0.58, 0.44, 0.58, 1.28, 0.48]
  }[profileId] ?? [0.9, 0.98, 1.04, 1.08, 1.04, 0.98, 0.9]
  const shellScales = {
    council: [1.42, 1.02],
    depot: [1.14, 0.82],
    library: [1.06, 1.08],
    'research-lab': [0.96, 1.34],
    'review-office': [0.62, 1.12]
  }[profileId] ?? [1, 1]
  width *= shellScales[0]
  height *= shellScales[1]
  const yBottom = 0.55
  const yTop = height * 0.86
  const frontZ = depth / 2
  const backZ = -depth / 2

  const members = []
  for (const x of [-width / 2, width / 2]) {
    for (const z of [frontZ, backZ]) {
      members.push(
        markRole(
          cylinder(scene, `${name}:frame:post:${x}:${z}`, {
            diameter: radius * 2,
            height: yTop - yBottom,
            material: structure,
            parent: frame,
            position: [x, (yBottom + yTop) / 2, z],
            tessellation: 6
          }),
          'wireframe-member'
        )
      )
    }
  }

  for (const z of [frontZ, backZ]) {
    members.push(
      markRole(
        tubeBetween(scene, `${name}:frame:top-rail:${z}`, [-width / 2, yTop, z], [width / 2, yTop, z], {
          height: radius * 2,
          material: structure,
          parent: frame,
          width: radius * 2
        }),
        'wireframe-member'
      )
    )
    members.push(
      markRole(
        tubeBetween(scene, `${name}:frame:base-rail:${z}`, [-width / 2, yBottom, z], [width / 2, yBottom, z], {
          height: radius * 1.6,
          material: structure,
          parent: frame,
          width: radius * 1.6
        }),
        'wireframe-member'
      )
    )
  }
  for (const x of [-width / 2, width / 2])
    members.push(
      markRole(
        tubeBetween(scene, `${name}:frame:side-rail:${x}`, [x, yTop, frontZ], [x, yTop, backZ], {
          height: radius * 2,
          material: structure,
          parent: frame,
          width: radius * 2
        }),
        'wireframe-member'
      )
    )

  const arch = markRole(
    torus(scene, `${name}:frame:front-arch`, {
      diameter: Math.min(width * 0.9, 12),
      material: structure,
      parent: entrance,
      position: [0, height * 0.48, 0],
      rotation: [Math.PI / 2, 0, 0],
      scale: [1, Math.max(0.78, (height / Math.max(width, 1)) * 0.9), 1],
      tessellation: 12,
      thickness: radius * 1.7
    }),
    'wireframe-member'
  )
  members.push(arch)

  const paths = []
  const columns = 7
  const rows = 4
  for (let column = 0; column < columns; column += 1) {
    const t = column / (columns - 1)
    const x = -width * 0.43 + t * width * 0.86
    const bulge = Math.sin(t * Math.PI) * 0.28
    paths.push(
      Array.from({ length: rows }, (_, row) => {
        const rowT = row / (rows - 1)
        // Keep the front skin below the open-room line. The frame carries the
        // full pressure-shell silhouette while the interior remains visible;
        // this is what makes the reference read as a habitable module rather
        // than a sealed rectangular box.
        const topY = yBottom + height * 0.46 * roofProfiles[column]
        const y = yBottom + (topY - yBottom) * rowT
        return new Vector3(x, y, frontZ + 0.12 + bulge)
      })
    )
  }
  const surface = MeshBuilder.CreateRibbon(
    `${name}:skin:front`,
    { closeArray: false, closePath: false, pathArray: paths, sideOrientation: Mesh.DOUBLESIDE },
    scene
  )
  surface.parent = skinRoot
  surface.material = paletteMaterial(scene, skin)
  surface.isPickable = false
  markRole(surface, 'skinned-surface')
  surface.metadata = { ...surface.metadata, skinType: 'curved-pressure-panel', wireframeMembers: members.length }

  const skinSurfaces = [surface.name]
  const createSkinPanel = (suffix, pathArray, material = skin) => {
    const panel = MeshBuilder.CreateRibbon(
      `${name}:skin:${suffix}`,
      { closeArray: false, closePath: false, pathArray, sideOrientation: Mesh.DOUBLESIDE },
      scene
    )
    panel.parent = skinRoot
    panel.material = paletteMaterial(scene, material)
    panel.isPickable = false
    markRole(panel, 'skinned-surface')
    panel.metadata = { ...panel.metadata, skinType: 'curved-pressure-panel' }
    skinSurfaces.push(panel.name)
    return panel
  }

  // Rear/side panels used to be a thin 2-row band at ~55% height (a strip,
  // not a wall), leaving most of the wireframe frame exposed on three of
  // four faces -- the building read as an unfinished cage rather than the
  // "open front, enclosed everywhere else" habitable module the approved
  // reference art establishes. A first attempt made rear/sides span the
  // full frame height/depth like the front skin, which fixed that but
  // collapsed every specialist's silhouette into the same filled-box shape
  // (build-models.test.mjs's uniqueness guard measured 0.77-0.91 similarity
  // against a ~0.73-0.82 threshold) and pushed council over its triangle
  // cap. This is deliberately a middle ground: a wide band, not a sliver
  // and not a full wall, so the frame still reads structurally (its own
  // per-building roofProfile/shellScale keep silhouettes apart) while
  // closing most of the previously-empty gap.
  const wrapRows = 2
  // review-office and council still converged at this coverage level
  // (0.74 average similarity against the ~0.73 threshold) despite their
  // very different roofProfile/shellScale entries -- the wrap band itself
  // was identical in every building, in normalized-height terms, so it
  // dominated the comparison. A small per-profile offset on the same
  // lookup keys used above breaks that without touching the (now-tested)
  // generic coverage every other specialist relies on.
  const [wrapBandStart, wrapBandSpan] = { council: [0.08, 0.3], 'review-office': [0.2, 0.34] }[profileId] ?? [
    0.14, 0.42
  ]
  const wrapRowAt = row => {
    const rowT = row / (wrapRows - 1)
    return yBottom + (yTop - yBottom) * (wrapBandStart + rowT * wrapBandSpan)
  }
  const rearPaths = Array.from({ length: columns }, (_, column) => {
    const t = column / (columns - 1)
    const x = -width * 0.43 + t * width * 0.86
    const bulge = Math.sin(t * Math.PI) * 0.22
    return Array.from({ length: wrapRows }, (_, row) => new Vector3(x, wrapRowAt(row), backZ + 0.12 - bulge))
  })
  createSkinPanel('rear', rearPaths)

  for (const side of [-1, 1]) {
    const x = side * (width / 2 - 0.12)
    // Runs from just behind the open front edge to about 55% of the way
    // back -- enough that the side reads as a real wall near the entrance
    // instead of a sliver, without fully closing the whole depth (which is
    // what drove the silhouette convergence above).
    const sidePaths = [
      Array.from({ length: wrapRows }, (_, row) => new Vector3(x, wrapRowAt(row), frontZ - 0.2)),
      Array.from({ length: wrapRows }, (_, row) => new Vector3(x, wrapRowAt(row), frontZ - depth * 0.27))
    ]
    createSkinPanel(`side:${side < 0 ? 'left' : 'right'}`, sidePaths)
  }

  const roofPaths = Array.from({ length: columns }, (_, column) => {
    const t = column / (columns - 1)
    const x = -width * 0.43 + t * width * 0.86
    const crown = Math.pow(Math.abs(t - 0.5) * 2, 1.6) * 0.34
    const y = yTop + 0.04 + crown
    return [new Vector3(x, y, frontZ - 0.2), new Vector3(x, y - 0.08, frontZ - 0.65)]
  })
  createSkinPanel('roof', roofPaths, 'bone-metal')

  const accentBand = markRole(
    torus(scene, `${name}:skin:accent-band`, {
      diameter: Math.min(width * 0.78, 10),
      material: accent,
      parent: roof,
      position: [0, height * 0.82, -depth * 0.06],
      rotation: [Math.PI / 2, 0, 0],
      scale: [1, 0.32, 1],
      tessellation: 10,
      thickness: Math.max(0.08, radius * 0.65)
    }),
    'skinned-surface'
  )

  root.metadata = {
    ...root.metadata,
    construction: 'wireframe-with-skin',
    shellProfile: 'curved-pressure-frame',
    skin: surface.name,
    skinSurfaces
  }
  return { accentBand, entrance, frame, members, root, roof, skin: surface, skinRoot }
}

/**
 * A small authored construction detail used by the landmark facades.  The
 * runtime intentionally stays asset-neutral, but a plain cuboid everywhere
 * reads like a debug blockout.  A pair of inset trim rails gives the same
 * layered, manufactured silhouette as the reference buildings while adding
 * only two tiny merged meshes per material.
 */
export function trimmedBox(scene, name, options = {}) {
  const mesh = roundedPanel(scene, name, options)
  const trim = options.trimMaterial ?? 'bone-metal'
  const width = options.width ?? options.size ?? 1
  const height = options.height ?? options.size ?? 1
  const depth = options.depth ?? options.size ?? 1
  const parent = options.parent ?? null
  const inset = Math.min(0.08, Math.max(0.035, Math.min(width, height, depth) * 0.08))
  const position = options.position ?? [0, 0, 0]
  const trimOptions = {
    depth: Math.max(0.04, depth + inset),
    height: Math.max(0.04, inset),
    material: trim,
    parent,
    position: [position[0], position[1] + height / 2 - inset * 0.5, position[2]],
    rotation: options.rotation,
    width: Math.max(0.08, width + inset)
  }
  box(scene, `${name}:trim:top`, trimOptions)
  box(scene, `${name}:trim:bottom`, {
    ...trimOptions,
    position: [position[0], position[1] - height / 2 + inset * 0.5, position[2]]
  })
  return mesh
}

/** Repeating shallow fins are cheaper than texture decals and survive LOD. */
export function facadeFins(
  scene,
  name,
  parent,
  { accent = 'signal-emissive', count = 5, depth = 0.12, height = 2, y = 2, z = 0, width = 8 } = {}
) {
  const root = group(scene, name, parent)
  const spacing = count > 1 ? width / (count - 1) : 0
  for (let index = 0; index < count; index += 1) {
    box(scene, `${name}:fin:${index}`, {
      depth,
      height: height * (index % 2 ? 0.78 : 1),
      material: index % 3 === 0 ? accent : 'bone-metal',
      parent: root,
      position: [-width / 2 + spacing * index, y, z],
      width: 0.12
    })
  }
  return root
}

export function capsule(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateCapsule(
    name,
    {
      capSubdivisions: options.capSubdivisions ?? 3,
      height: options.height ?? 1,
      radius: options.radius ?? 0.25,
      subdivisions: options.subdivisions ?? 3,
      tessellation: options.tessellation ?? 12
    },
    scene
  )
  return finish(mesh, options)
}

/** A low-tessellation rounded panel for manufactured facade skins and trims. */
export function roundedPanel(scene, name, options = {}) {
  const width = Math.max(0.08, options.width ?? 1)
  const height = Math.max(0.08, options.height ?? 1)
  const depth = Math.max(0.04, options.depth ?? 0.12)
  const horizontal = width >= height
  const short = horizontal ? height : width
  const mesh = capsule(scene, name, {
    height: horizontal ? width : height,
    material: options.material,
    parent: options.parent,
    position: options.position,
    radius: short / 2,
    rotation: horizontal
      ? [options.rotation?.[0] ?? 0, options.rotation?.[1] ?? 0, (options.rotation?.[2] ?? 0) + Math.PI / 2]
      : options.rotation,
    capSubdivisions: options.capSubdivisions ?? 2,
    subdivisions: options.subdivisions ?? 2,
    tessellation: options.tessellation ?? 4
  })
  mesh.scaling.z *= depth / short
  mesh.metadata = { ...(mesh.metadata ?? {}), construction: 'rounded-panel' }
  return mesh
}

export function cone(scene, name, options = {}) {
  const mesh = MeshBuilder.CreateCylinder(
    name,
    {
      diameterBottom: options.diameterBottom ?? options.diameter ?? 1,
      diameterTop: options.diameterTop ?? 0,
      height: options.height ?? 1,
      tessellation: options.tessellation ?? 8
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
      tessellation: options.tessellation ?? 12
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
      diameterX: options.diameterX,
      diameterY: options.diameterY,
      diameterZ: options.diameterZ,
      segments: options.segments ?? 12
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
      tessellation: options.tessellation ?? 16,
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

export function tubeBetween(scene, name, from, to, options = {}) {
  const start = new Vector3(...from)
  const end = new Vector3(...to)
  const direction = end.subtract(start)
  const length = direction.length()
  if (length <= 0) throw new Error(`cannot build zero-length tube ${name}`)
  const midpoint = start.add(end).scale(0.5)
  const tube = cylinder(scene, name, {
    diameter: options.diameter ?? options.width ?? 0.4,
    height: length,
    material: options.material,
    parent: options.parent,
    position: midpoint.asArray(),
    tessellation: options.tessellation ?? 6
  })
  const up = new Vector3(0, 1, 0)
  const target = direction.scale(1 / length)
  const axis = Vector3.Cross(up, target)
  const dot = Math.max(-1, Math.min(1, Vector3.Dot(up, target)))
  tube.rotationQuaternion =
    axis.lengthSquared() < 1e-8
      ? dot < 0
        ? Quaternion.RotationAxis(new Vector3(1, 0, 0), Math.PI)
        : Quaternion.Identity()
      : Quaternion.RotationAxis(axis.normalize(), Math.acos(dot))
  return tube
}

function directChildOf(node, ancestor) {
  let cursor = node.parent
  while (cursor && cursor !== ancestor) cursor = cursor.parent
  return cursor === ancestor
}

export function mergeLodMeshes(scene, lodRoot, prefix) {
  const candidates = scene.meshes.filter(mesh => directChildOf(mesh, lodRoot) && !mesh.metadata?.keepSeparate)
  const bySurfaceContract = new Map()
  for (const mesh of candidates) {
    if (!mesh.material) continue
    // Babylon cannot merge skinned and unskinned vertex layouts.  Keep the
    // shared-material optimization, but partition by deformation contract so
    // character skins retain their JOINTS_0/WEIGHTS_0 attributes.
    const skinKey = mesh.skeleton ? `skinned:${mesh.skeleton.uniqueId}` : 'static'
    const mergeGroup = mesh.metadata?.mergeGroup ?? 'shared'
    const key = `${mesh.material.uniqueId}:${skinKey}:${mergeGroup}`
    if (!bySurfaceContract.has(key)) bySurfaceContract.set(key, [])
    bySurfaceContract.get(key).push(mesh)
  }

  let index = 0
  for (const meshes of bySurfaceContract.values()) {
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
