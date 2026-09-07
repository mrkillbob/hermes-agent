import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import { after, before, test } from 'node:test'

import { NodeIO } from '@gltf-transform/core'

import { buildAssetPack } from './build-models.mjs'
import { NullEngine, Scene } from './modeling/babylon.mjs'
import {
  buildArchive,
  buildArtsStudio,
  buildCouncil,
  buildDepot,
  buildEngineeringWorkshop,
  buildLibrary,
  buildReleaseGatehouse,
  buildResearchLab,
  buildReviewOffice
} from './modeling/buildings.mjs'
import { buildTriage } from './modeling/props.mjs'
import { DISTRICTS, WALKWAY_ROUTES, buildTerrain, roadRoutePoints } from './modeling/terrain.mjs'

const execFileAsync = promisify(execFile)

const APPROVED_SOURCE_NAME = 'moon-settlement-approved.jpg'
const MODEL_IDS = [
  'archive',
  'arts-studio',
  'bus',
  'council',
  'depot',
  'engineering-workshop',
  'garden',
  'leaders',
  'library',
  'release-gatehouse',
  'research-lab',
  'review-office',
  'terrain',
  'triage',
  'workers'
]
const SEMANTIC_NODES = {
  archive: ['archive:stacks', 'archive:vault', 'archive:city-identity', 'archive:leader-anchor'],
  'arts-studio': [
    'arts-studio:gallery',
    'arts-studio:easels',
    'arts-studio:palette',
    'arts-studio:city-identity',
    'arts-studio:leader-anchor'
  ],
  bus: ['bus:cabin', 'bus:signal', 'bus:wheels'],
  council: ['council:console', 'council:dais', 'council:roost'],
  depot: ['depot:crates', 'depot:stocked-shelves', 'depot:workbench'],
  'engineering-workshop': [
    'engineering-workshop:workbenches',
    'engineering-workshop:gantry',
    'engineering-workshop:gear',
    'engineering-workshop:city-identity',
    'engineering-workshop:leader-anchor'
  ],
  garden: ['garden:bench', 'garden:cyan-fixture', 'garden:plants'],
  leaders: ['leader:badger', 'leader:bird', 'leader:fox', 'leader:otter', 'leader:owl', 'leader:stag'],
  library: ['library:archive-stacks', 'library:leader-anchor', 'library:violet-orb'],
  'research-lab': ['research-lab:consoles', 'research-lab:specimen', 'research-lab:telescope'],
  'release-gatehouse': [
    'release-gatehouse:release-gate',
    'release-gatehouse:beacon',
    'release-gatehouse:city-identity',
    'release-gatehouse:leader-anchor'
  ],
  'review-office': ['review-office:consoles', 'review-office:portal', 'review-office:verifier-dais'],
  terrain: ['terrain:bus-stop', 'terrain:cliffs', 'terrain:walkways:deck', 'terrain:world-surface'],
  triage: ['triage:cross', 'triage:door', 'triage:station'],
  workers: ['worker:attachment', 'worker:body', 'worker:head', 'worker:role-accessories']
}
const SPECIALIST_STATE_CHANNELS = {
  depot: ['workbench-cycle'],
  'research-lab': ['workbench-cycle'],
  triage: ['triage-station-idle']
}
const MIN_TRIANGLES = {
  archive: 220,
  'arts-studio': 220,
  bus: 100,
  council: 180,
  depot: 180,
  'engineering-workshop': 220,
  garden: 180,
  leaders: 900,
  library: 220,
  'release-gatehouse': 180,
  'research-lab': 260,
  'review-office': 220,
  terrain: 180,
  triage: 140,
  workers: 220
}
const SPECIALIST_DETAIL_FLOORS = {
  council: 900,
  depot: 900,
  library: 1500,
  'research-lab': 1400,
  'review-office': 1100,
  triage: 420
}
const SPECIALIST_IDS = ['library', 'research-lab', 'depot', 'review-office', 'council']
const BUILDING_BUILDERS = Object.freeze({
  archive: buildArchive,
  'arts-studio': buildArtsStudio,
  council: buildCouncil,
  depot: buildDepot,
  'engineering-workshop': buildEngineeringWorkshop,
  library: buildLibrary,
  'release-gatehouse': buildReleaseGatehouse,
  'research-lab': buildResearchLab,
  'review-office': buildReviewOffice,
  triage: buildTriage
})
const LEADER_IDS = ['owl', 'fox', 'badger', 'otter', 'bird', 'stag']
const LEADER_STATES = ['idle', 'listening', 'talking', 'thinking', 'acknowledging', 'unavailable']
const CLEANUP_TRIANGLE_CAPS = {
  council: 10500,
  leaders: 18000,
  library: 15000
}
const HERMES_GROUPS = [
  'Acceptance & Release',
  'Archive and Acquisition',
  'Arts Studio',
  'CI Repair Triage',
  'Community Intake',
  'Content Studio',
  'Control Plane Incidents',
  'Core Runtime & UX Repairs',
  'Data & Performance Repairs',
  'Editorial Desk',
  'Engineering Guild',
  'Federation Council',
  'Knowledge Commons',
  'Memory Stewardship',
  'Operations and Release',
  'PR Merge Train',
  'Research Lab',
  'Research Review Board',
  'Upstream Hermes Maintenance'
]

let firstRoot
let secondRoot
let firstReceipt
let secondReceipt

function isDescendantOf(node, ancestor) {
  for (let current = node; current; current = current.getParentNode()) if (current === ancestor) return true
  return false
}

function transformPoint([x, y, z], matrix) {
  return [
    matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
    matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
    matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
  ]
}

function worldTriangles(root, ancestorName) {
  const ancestor = root.listNodes().find(node => node.getName() === ancestorName)
  assert.ok(ancestor, `missing geometry ancestor ${ancestorName}`)
  const triangles = []
  for (const node of root.listNodes()) {
    const mesh = node.getMesh()
    if (!mesh || !isDescendantOf(node, ancestor)) continue
    const world = node.getWorldMatrix()
    for (const primitive of mesh.listPrimitives()) {
      const positions = primitive.getAttribute('POSITION')
      if (!positions) continue
      const values = positions.getArray()
      const indices =
        primitive.getIndices()?.getArray() ?? Uint32Array.from({ length: positions.getCount() }, (_, i) => i)
      for (let index = 0; index + 2 < indices.length; index += 3) {
        triangles.push(
          [indices[index], indices[index + 1], indices[index + 2]].map(vertex =>
            transformPoint([values[vertex * 3], values[vertex * 3 + 1], values[vertex * 3 + 2]], world)
          )
        )
      }
    }
  }
  return triangles
}

function boundsOfTriangles(triangles) {
  const points = triangles.flat()
  return {
    max: [0, 1, 2].map(axis => Math.max(...points.map(point => point[axis]))),
    min: [0, 1, 2].map(axis => Math.min(...points.map(point => point[axis])))
  }
}

function projectedAreaXY(triangle) {
  const [a, b, c] = triangle
  return Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
}

function triangleArea3d([a, b, c]) {
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]]
  const cross = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]]
  return Math.hypot(...cross) / 2
}

function projectPoint(point, angle) {
  return [point[0] * Math.cos(angle) - point[2] * Math.sin(angle), point[1]]
}

function projectedSilhouette(triangles, angle, columns = 30, rows = 20) {
  const bounds = boundsOfTriangles(triangles)
  const architecturalFloor = bounds.min[1] + (bounds.max[1] - bounds.min[1]) * 0.12
  const dominant = triangles.filter(
    triangle => triangleArea3d(triangle) >= 0.8 && Math.max(...triangle.map(point => point[1])) > architecturalFloor
  )
  const projected = dominant.flatMap(triangle => triangle.map(point => projectPoint(point, angle)))
  const min = [0, 1].map(axis => Math.min(...projected.map(point => point[axis])))
  const max = [0, 1].map(axis => Math.max(...projected.map(point => point[axis])))
  const occupied = new Set()
  for (const triangle of dominant) {
    const [a, b, c] = triangle.map(point => projectPoint(point, angle))
    for (let i = 0; i <= 8; i += 1) {
      for (let j = 0; j <= 8 - i; j += 1) {
        const u = i / 8
        const v = j / 8
        const point = [a[0] + (b[0] - a[0]) * u + (c[0] - a[0]) * v, a[1] + (b[1] - a[1]) * u + (c[1] - a[1]) * v]
        const column = Math.min(
          columns - 1,
          Math.max(0, Math.floor(((point[0] - min[0]) / (max[0] - min[0])) * columns))
        )
        const row = Math.min(rows - 1, Math.max(0, Math.floor(((point[1] - min[1]) / (max[1] - min[1])) * rows)))
        occupied.add(row * columns + column)
      }
    }
  }
  return occupied
}

function jaccardSimilarity(left, right) {
  const intersection = [...left].filter(value => right.has(value)).length
  return intersection / new Set([...left, ...right]).size
}

function nodeTriangles(root, ancestor) {
  return descendantsWithMeshes(root, ancestor).flatMap(node => {
    const triangles = []
    const world = node.getWorldMatrix()
    for (const primitive of node.getMesh().listPrimitives()) {
      const positions = primitive.getAttribute('POSITION')
      if (!positions) continue
      const values = positions.getArray()
      const indices =
        primitive.getIndices()?.getArray() ?? Uint32Array.from({ length: positions.getCount() }, (_, i) => i)
      for (let index = 0; index + 2 < indices.length; index += 3) {
        triangles.push(
          [indices[index], indices[index + 1], indices[index + 2]].map(vertex =>
            transformPoint([values[vertex * 3], values[vertex * 3 + 1], values[vertex * 3 + 2]], world)
          )
        )
      }
    }
    return triangles
  })
}

function descendantsWithMeshes(root, ancestor) {
  return root.listNodes().filter(node => node.getMesh() && isDescendantOf(node, ancestor))
}

function tierCost(root, id, suffix) {
  const lod = root.listNodes().find(node => node.getName() === `${id}:lod:${suffix}`)
  assert.ok(lod, `${id} missing ${suffix} LOD`)
  const nodes = descendantsWithMeshes(root, lod)
  return {
    animatedChannels: root
      .listAnimations()
      .flatMap(animation => animation.listChannels())
      .filter(channel => isDescendantOf(channel.getTargetNode(), lod)).length,
    primitives: nodes.reduce((total, node) => total + node.getMesh().listPrimitives().length, 0),
    skinnedNodes: nodes.filter(node => node.getSkin()).length,
    triangles: worldTriangles(root, `${id}:lod:${suffix}`).length
  }
}

before(async () => {
  firstRoot = await mkdtemp(join(tmpdir(), 'lunar-city-assets-a-'))
  secondRoot = await mkdtemp(join(tmpdir(), 'lunar-city-assets-b-'))
  ;[firstReceipt, secondReceipt] = await Promise.all([buildAssetPack(firstRoot), buildAssetPack(secondRoot)])
})

after(async () => {
  await Promise.all([rm(firstRoot, { force: true, recursive: true }), rm(secondRoot, { force: true, recursive: true })])
})

test('builds every approved landmark and character family', () => {
  assert.deepEqual(firstReceipt.missing, [])
  assert.deepEqual(firstReceipt.models.toSorted(), MODEL_IDS)
})

test('every building has a continuous wireframe shell with a separate skinned surface', () => {
  for (const [id, build] of Object.entries(BUILDING_BUILDERS)) {
    const engine = new NullEngine({ renderingPipeline: false })
    const scene = new Scene(engine)
    try {
      const root = build(scene)
      const near = scene.getTransformNodeByName(`${id}:lod:near`)
      assert.ok(root, `${id} builder must return a root`)
      assert.ok(near, `${id} must expose a near LOD`)
      assert.equal(near.metadata?.construction, 'wireframe-with-skin', `${id} must declare its construction contract`)
      assert.ok(
        scene.meshes.filter(mesh => mesh.metadata?.lunarCityRole === 'wireframe-member').length >= 6,
        `${id} needs a continuous structural wireframe`
      )
      assert.ok(
        scene.meshes.some(mesh => mesh.metadata?.lunarCityRole === 'skinned-surface'),
        `${id} needs a visible skinned surface`
      )
    } finally {
      scene.dispose()
      engine.dispose()
    }
  }
})

test('wraps every building in multiple curved skin panels without sealing the room front', () => {
  for (const [id, build] of Object.entries(BUILDING_BUILDERS)) {
    const engine = new NullEngine({ renderingPipeline: false })
    const scene = new Scene(engine)
    try {
      build(scene)
      const shell = scene.getTransformNodeByName(`${id}:wireframe-envelope`)
      const skins = scene.meshes.filter(mesh => mesh.metadata?.lunarCityRole === 'skinned-surface')
      assert.ok(shell?.metadata?.skinSurfaces?.length >= 4, `${id} needs a wraparound skin surface contract`)
      assert.ok(skins.length >= 4, `${id} needs rear, side, roof, and front skin panels`)
      assert.equal(
        skins.some(mesh => mesh.name.endsWith(':skin:front')),
        true,
        `${id} must retain an open-front interior read with a front pressure panel`
      )
    } finally {
      scene.dispose()
      engine.dispose()
    }
  }
})

test('uses rounded manufactured panels for shared facade detail instead of raw block plates', () => {
  for (const [id, build] of Object.entries(BUILDING_BUILDERS)) {
    const engine = new NullEngine({ renderingPipeline: false })
    const scene = new Scene(engine)
    try {
      build(scene)
      const roundedPanels = scene.meshes.filter(mesh => mesh.metadata?.construction === 'rounded-panel')
      assert.ok(roundedPanels.length >= 8, `${id} needs rounded facade panels instead of block plates`)
    } finally {
      scene.dispose()
      engine.dispose()
    }
  }
})

// Half the plan-view footprint diagonal (or the plain radius for garden,
// which is circular) per district -- matches
// scripts/lunar-city/layout-solver.mjs's FOOTPRINTS/footprintRadius, which
// is the source of truth DISTRICTS was last solved against. A flat
// distance check here (the previous version of this test) missed two real
// problems: it only covered the 10 ids in BUILDING_BUILDERS (bus and
// garden were never checked against anything), and a fixed 24-unit
// threshold doesn't know that wireframeShell's per-building shellScale
// width multiplier (e.g. council x1.42) can make one building meaningfully
// bigger than the next -- both gaps let a real interpenetration regression
// through silently.
const DISTRICT_FOOTPRINT_RADIUS = Object.freeze({
  archive: Math.hypot(13.5, 11) / 2,
  'arts-studio': Math.hypot(14, 10.5) / 2,
  bus: Math.hypot(12, 8.2) / 2,
  council: Math.hypot(14 * 1.42, 10.5) / 2,
  depot: Math.hypot(14.5 * 1.14, 11) / 2,
  'engineering-workshop': Math.hypot(15.5, 12) / 2,
  garden: 9.5,
  library: Math.hypot(15 * 1.06, 12) / 2,
  'release-gatehouse': Math.hypot(12.5, 9.5) / 2,
  'research-lab': Math.hypot(18 * 0.96, 14) / 2,
  'review-office': Math.hypot(15.5 * 0.62, 11.5) / 2,
  triage: Math.hypot(9.5, 7.4) / 2
})
const DISTRICT_CLEARANCE_MARGIN = 7

test('keeps building district anchors clear so authored footprints cannot interpenetrate', () => {
  const ids = Object.keys(DISTRICTS)
  assert.deepEqual(ids.toSorted(), Object.keys(DISTRICT_FOOTPRINT_RADIUS).toSorted())
  for (let leftIndex = 0; leftIndex < ids.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < ids.length; rightIndex += 1) {
      const leftId = ids[leftIndex]
      const rightId = ids[rightIndex]
      const left = DISTRICTS[leftId].position
      const right = DISTRICTS[rightId].position
      const distance = Math.hypot(left[0] - right[0], left[2] - right[2])
      const required =
        DISTRICT_FOOTPRINT_RADIUS[leftId] + DISTRICT_FOOTPRINT_RADIUS[rightId] + DISTRICT_CLEARANCE_MARGIN
      assert.ok(
        distance >= required,
        `${leftId} and ${rightId} anchors are ${distance.toFixed(2)} units apart, need >= ${required.toFixed(2)} for their footprints`
      )
    }
  }
})

test('keeps every district footprint inside the camera bounds', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url), 'utf8')
  )
  const { max, min } = manifest.camera.bounds
  for (const [id, { position }] of Object.entries(DISTRICTS)) {
    const radius = DISTRICT_FOOTPRINT_RADIUS[id]
    const [x, , z] = position
    assert.ok(x - radius >= min[0] && x + radius <= max[0], `${id} footprint exceeds camera X bounds`)
    assert.ok(z - radius >= min[2] && z + radius <= max[2], `${id} footprint exceeds camera Z bounds`)
  }
})

test('is byte-stable for identical source input', () => {
  assert.deepEqual(firstReceipt.sha256ByModel, secondReceipt.sha256ByModel)
  assert.equal(firstReceipt.textures[0].sha256, secondReceipt.textures[0].sha256)
})

test('exports genuine bounded three-dimensional geometry instead of flat replacement art', async () => {
  assert.equal(new Set(Object.values(firstReceipt.sha256ByModel)).size, MODEL_IDS.length)

  for (const id of MODEL_IDS) {
    const stats = firstReceipt.statistics[id]
    assert.ok(stats.triangles >= MIN_TRIANGLES[id], `${id} needs recognizable modeled detail`)
    assert.ok(stats.triangles <= stats.budget.maxTriangles, `${id} exceeds its triangle budget`)
    assert.ok(stats.meshes > 1, `${id} must not be a placeholder primitive`)
    assert.ok(stats.materials <= stats.budget.maxMaterials, `${id} exceeds its material budget`)
    assert.ok(
      stats.extent.every(axis => axis > 0.25),
      `${id} must have depth on every axis`
    )

    const bytes = await readFile(join(firstRoot, 'models', `${id}.glb`))
    assert.equal(bytes.subarray(0, 4).toString('ascii'), 'glTF')
    assert.equal(bytes.includes(Buffer.from(APPROVED_SOURCE_NAME)), false)
  }
})

test('keeps the highest-cost procedural landmarks under the cleanup caps', () => {
  for (const [id, cap] of Object.entries(CLEANUP_TRIANGLE_CAPS)) {
    assert.ok(
      firstReceipt.statistics[id].triangles <= cap,
      `${id} cleanup target is ${cap} triangles (got ${firstReceipt.statistics[id].triangles})`
    )
  }
})

test('uses the supplied colony-builder visual baseline for pads, transit, and dome caps', () => {
  const engine = new NullEngine({ renderingPipeline: false })
  const scene = new Scene(engine)
  try {
    buildTerrain(scene)
    const pads = scene.meshes.filter(mesh => mesh.name.startsWith('terrain:district-pad:'))
    assert.equal(pads.length, 12, 'the baseline must give every district a modular pad')
    assert.ok(
      pads.every(mesh => mesh.getTotalVertices() <= 30),
      'district pads must be six-sided low-poly tiles'
    )

    const utilityPods = scene.meshes.filter(mesh => mesh.name.startsWith('terrain:utility-pod:'))
    assert.equal(utilityPods.length, 24, 'the baseline must provide two low-poly support modules per district')

    const transitAccents = scene.meshes.filter(
      mesh =>
        mesh.name.includes('walkway') &&
        mesh.material?.subMaterials?.some(material => material.name === 'signal-emissive')
    )
    assert.ok(transitAccents.length > 0, 'transit accents must read as cyan luminous lines')

    buildCouncil(scene)
    const domeCap = scene.getMeshByName('council:roof-dome:signal')
    assert.equal(domeCap?.material?.name, 'lunar-rust', 'dome caps must carry the warm colony-builder accent')
  } finally {
    scene.dispose()
    engine.dispose()
  }
})

test('extends terrain into a concave planetary ground beyond the settlement island', async () => {
  const io = new NodeIO()
  const root = (await io.read(join(firstRoot, 'models', 'terrain.glb'))).getRoot()
  const triangles = worldTriangles(root, 'terrain:world-surface')
  const bounds = boundsOfTriangles(triangles)
  assert.ok(bounds.max[0] - bounds.min[0] >= 350, 'planetary ground must extend well beyond the current island')
  assert.ok(bounds.max[2] - bounds.min[2] >= 350, 'planetary ground must extend well beyond the current island')
  const points = triangles.flat()
  const centerHeights = points.filter(point => Math.hypot(point[0], point[2] - 3) < 1).map(point => point[1])
  const rimHeights = points.filter(point => Math.hypot(point[0], point[2] - 3) > 170).map(point => point[1])
  assert.ok(centerHeights.length > 0 && rimHeights.length > 0, 'planetary ground must expose center and rim samples')
  assert.ok(Math.min(...rimHeights) - Math.min(...centerHeights) >= 6, 'planetary ground must be visibly concave')
})

test('preserves approved specialist silhouettes and all declared animation/state channels', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url), 'utf8')
  )
  const io = new NodeIO()

  for (const model of manifest.models) {
    const root = (await io.read(join(firstRoot, model.uri))).getRoot()
    const nodes = new Set(root.listNodes().map(node => node.getName()))
    const clips = new Set(root.listAnimations().map(animation => animation.getName()))
    for (const node of [...model.requiredNodes, ...SEMANTIC_NODES[model.id]]) {
      assert.ok(nodes.has(node), `${model.id} missing semantic node ${node}`)
    }
    for (const clip of model.requiredClips) assert.ok(clips.has(clip), `${model.id} missing clip ${clip}`)
    for (const clip of SPECIALIST_STATE_CHANNELS[model.id] ?? []) {
      assert.ok(clips.has(clip), `${model.id} missing specialist state channel ${clip}`)
    }
    if (model.id === 'workers') {
      const skins = root.listSkins()
      assert.equal(skins.length, 1, 'workers must export a genuine modular skeleton skin')
      const joints = new Set(skins[0].listJoints().map(joint => joint.getName()))
      for (const joint of ['worker:body', 'worker:head', 'worker:limb:left-arm', 'worker:limb:right-arm']) {
        assert.ok(joints.has(joint), `workers missing rig joint ${joint}`)
      }
    }
  }
})

test('exports physically distinct specialist massing with genuinely open room fronts', async () => {
  const io = new NodeIO()
  for (const [id, detailFloor] of Object.entries(SPECIALIST_DETAIL_FLOORS)) {
    const root = (await io.read(join(firstRoot, 'models', `${id}.glb`))).getRoot()
    const triangles = worldTriangles(root, `${id}:lod:near`)
    assert.ok(triangles.length >= detailFloor, `${id} lacks approved specialist detail density`)
  }

  const triageRoot = (await io.read(join(firstRoot, 'models', 'triage.glb'))).getRoot()
  const triageTriangles = worldTriangles(triageRoot, 'triage:lod:near')
  const solidFrontFaces = triageTriangles.filter(triangle => {
    const center = [0, 1, 2].map(axis => triangle.reduce((sum, point) => sum + point[axis], 0) / 3)
    return (
      Math.abs(center[0]) < 2.5 &&
      center[1] > 0.8 &&
      center[1] < 4.2 &&
      center[2] > 1.5 &&
      projectedAreaXY(triangle) > 4
    )
  })
  assert.equal(solidFrontFaces.length, 0, 'triage must expose a physical open-front interior, not a covered solid box')

  const reviewRoot = (await io.read(join(firstRoot, 'models', 'review-office.glb'))).getRoot()
  const portal = reviewRoot.listNodes().find(node => node.getName() === 'review-office:portal')
  const portalGeometry = descendantsWithMeshes(reviewRoot, portal)
  assert.ok(portalGeometry.length > 0, 'review portal state channel has no physical chamber geometry')
  const portalTriangles = portalGeometry.flatMap(node => {
    const world = node.getWorldMatrix()
    return node
      .getMesh()
      .listPrimitives()
      .flatMap(primitive => {
        const positions = primitive.getAttribute('POSITION')
        const values = positions.getArray()
        const indices =
          primitive.getIndices()?.getArray() ?? Uint32Array.from({ length: positions.getCount() }, (_, i) => i)
        const triangles = []
        for (let index = 0; index + 2 < indices.length; index += 3) {
          triangles.push(
            [indices[index], indices[index + 1], indices[index + 2]].map(vertex =>
              transformPoint([values[vertex * 3], values[vertex * 3 + 1], values[vertex * 3 + 2]], world)
            )
          )
        }
        return triangles
      })
  })
  const portalBounds = boundsOfTriangles(portalTriangles)
  assert.ok(portalBounds.max[1] - portalBounds.min[1] > 3.4, 'review portal must read as a tall verification chamber')
})

test('gives every specialist a unique dominant city-view silhouette and readable warm identity', async () => {
  const io = new NodeIO()
  const angles = [-Math.PI / 4, Math.PI / 4, Math.PI]
  const silhouettes = new Map()
  for (const id of SPECIALIST_IDS) {
    const root = (await io.read(join(firstRoot, 'models', `${id}.glb`))).getRoot()
    const triangles = worldTriangles(root, `${id}:lod:near`)
    silhouettes.set(
      id,
      angles.map(angle => projectedSilhouette(triangles, angle))
    )

    const identity = root.listNodes().find(node => node.getName() === `${id}:city-identity`)
    assert.ok(identity, `${id} lacks a city-scale physical identity anchor`)
    const identityTriangles = nodeTriangles(root, identity)
    assert.ok(identityTriangles.length > 0, `${id} city identity is metadata without physical geometry`)
    const modelBounds = boundsOfTriangles(triangles)
    const identityBounds = boundsOfTriangles(identityTriangles)
    const modelSpan = modelBounds.max.map((value, axis) => value - modelBounds.min[axis])
    const identitySpan = identityBounds.max.map((value, axis) => value - identityBounds.min[axis])
    assert.ok(
      identitySpan[0] / modelSpan[0] >= 0.28 || identitySpan[1] / modelSpan[1] >= 0.3,
      `${id} identity equipment/signage is too small to read in the city view`
    )
    const identityMaterials = new Set(
      descendantsWithMeshes(root, identity).flatMap(node =>
        node
          .getMesh()
          .listPrimitives()
          .map(primitive => primitive.getMaterial()?.getName())
      )
    )
    assert.ok(
      identityMaterials.has('lunar-rust') || identityMaterials.has('bone-metal'),
      `${id} identity lacks prominent approved warm trim`
    )
  }

  const sharedSilhouettes = []
  for (let leftIndex = 0; leftIndex < SPECIALIST_IDS.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < SPECIALIST_IDS.length; rightIndex += 1) {
      const left = SPECIALIST_IDS[leftIndex]
      const right = SPECIALIST_IDS[rightIndex]
      const similarities = silhouettes
        .get(left)
        .map((signature, angleIndex) => jaccardSimilarity(signature, silhouettes.get(right)[angleIndex]))
      if (
        Math.max(...similarities) >= 0.82 ||
        similarities.reduce((sum, value) => sum + value, 0) / similarities.length >= 0.73
      )
        sharedSilhouettes.push(`${left}/${right}=${similarities.map(value => value.toFixed(3)).join(',')}`)
    }
  }
  assert.deepEqual(sharedSilhouettes, [], `shared dominant city-scale silhouettes: ${sharedSilhouettes.join('; ')}`)
})

test('exports seven exclusive selectable worker role variants with physical accessories', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url), 'utf8')
  )
  const variantIds = manifest.models.find(model => model.id === 'workers').instancing.variants
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'workers.glb'))).getRoot()
  const variantNodes = new Map(
    root
      .listNodes()
      .filter(node => node.getName().startsWith('worker:variant:'))
      .map(node => [node.getName().slice('worker:variant:'.length), node])
  )
  assert.deepEqual([...variantNodes.keys()].toSorted(), variantIds.toSorted())

  let activeVariants = 0
  const accessorySignatures = new Set()
  for (const id of variantIds) {
    const variant = variantNodes.get(id)
    const extras = variant.getExtras()
    assert.equal(extras.variantId, id)
    assert.equal(extras.exclusiveGroup, 'worker-role')
    assert.deepEqual(extras.activationScale, [1, 1, 1])
    if (variant.getScale().every(value => value > 0.9)) activeVariants += 1

    const roleNodes = root
      .listNodes()
      .filter(node => /^worker:role:[^:]+$/.test(node.getName()) && isDescendantOf(node, variant))
    assert.deepEqual(
      roleNodes.map(node => node.getName()),
      [`worker:role:${id}`]
    )
    const physicalParts = descendantsWithMeshes(root, roleNodes[0])
    assert.ok(physicalParts.length > 0, `${id} accessory is named but has no exported geometry`)
    accessorySignatures.add(
      physicalParts
        .map(
          node =>
            `${node.getTranslation().map(value => value.toFixed(2))}:${node.getScale().map(value => value.toFixed(2))}`
        )
        .toSorted()
        .join('|')
    )
  }
  assert.equal(activeVariants, 1, 'exactly one accessory variant may be active in the default worker')
  assert.equal(
    accessorySignatures.size,
    variantIds.length,
    'worker role variants must have distinct physical accessories'
  )
})

test('models reference colony workers as compact armored robots with helmet, panel, and boot language', async () => {
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'workers.glb'))).getRoot()
  const nodes = new Set(root.listNodes().map(node => node.getName()))
  for (const name of [
    'worker:limb:left-arm:forearm',
    'worker:limb:right-arm:forearm',
    'worker:limb:left-leg:boot',
    'worker:limb:right-leg:boot'
  ]) {
    assert.ok(nodes.has(name), `reference worker silhouette is missing ${name}`)
  }

  const workerRoot = root.listNodes().find(node => node.getName() === 'workers:lod:near')
  const materials = new Set(
    descendantsWithMeshes(root, workerRoot).flatMap(node =>
      node
        .getMesh()
        .listPrimitives()
        .map(primitive => primitive.getMaterial()?.getName())
    )
  )
  assert.ok(materials.has('bone-metal'), 'reference worker needs a light armored shell')
  assert.ok(materials.has('signal-emissive'), 'reference worker needs a readable cyan face and status language')
  assert.ok(materials.has('lunar-rust'), 'reference worker needs warm red-orange kit accents')
})

test('models every leader as a finished character with expressive face and layered robe trim', async () => {
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'leaders.glb'))).getRoot()
  assert.equal(root.listSkins().length, 1, 'leaders must share one deformable character rig')
  const jointNames = new Set(
    root
      .listSkins()[0]
      .listJoints()
      .map(joint => joint.getName())
  )
  for (const id of LEADER_IDS) {
    const leader = root.listNodes().find(node => node.getName() === `leader:${id}`)
    assert.deepEqual(leader.getExtras().featureSet, [
      'deformable-body',
      'expressive-face',
      'layered-robe',
      'chest-insignia'
    ])
    assert.ok(jointNames.has(`leader:${id}:body-rig`), `${id} is missing its body skin joint`)
    assert.ok(jointNames.has(`leader:${id}:head-rig`), `${id} is missing its head skin joint`)
    assert.ok(jointNames.has(`leader:${id}:arm-rig:left`), `${id} is missing its left-arm skin joint`)
    assert.ok(jointNames.has(`leader:${id}:arm-rig:right`), `${id} is missing its right-arm skin joint`)
  }
})

test('exports one distinct physical kit for every Hermes group without multiplying worker resources', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url), 'utf8')
  )
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'workers.glb'))).getRoot()
  const kitNodes = new Map(
    root
      .listNodes()
      .filter(node => /^worker:group-kit:[^:]+$/.test(node.getName()))
      .map(node => [node.getExtras().group, node])
  )

  assert.deepEqual([...kitNodes.keys()].toSorted(), HERMES_GROUPS.toSorted())
  assert.equal(root.listSkins().length, 1, 'group diversity must reuse the one worker skin')
  assert.ok(root.listMaterials().length <= 4, 'group diversity must reuse shared materials')
  assert.equal(root.listTextures().length, 0, 'group diversity must not allocate per-kit textures')

  const signatures = new Set()
  for (const groupName of HERMES_GROUPS) {
    const kit = kitNodes.get(groupName)
    assert.equal(kit.getExtras().exclusiveGroup, 'worker-group-kit')
    const physicalParts = descendantsWithMeshes(root, kit)
    assert.ok(physicalParts.length > 0, `${groupName} kit has no physical silhouette geometry`)
    signatures.add(
      physicalParts
        .map(
          node =>
            `${node.getTranslation().map(value => value.toFixed(2))}:${node.getScale().map(value => value.toFixed(2))}`
        )
        .toSorted()
        .join('|')
    )
  }
  assert.equal(signatures.size, HERMES_GROUPS.length, 'Hermes groups must not share full physical kit signatures')
})

test('exports shared physical worker profile controls and collision-free kit accents', async () => {
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'workers.glb'))).getRoot()
  const nodes = root.listNodes()
  const byName = new Map(nodes.map(node => [node.getName(), node]))
  const physicalMeshes = name => {
    const node = byName.get(name)
    assert.ok(node, `missing worker profile node ${name}`)
    const meshes = descendantsWithMeshes(root, node).map(child => child.getMesh())
    assert.ok(meshes.length > 0, `${name} has no physical geometry`)
    return meshes
  }
  const profileRoots = [
    'worker:body-variant:compact',
    'worker:body-variant:standard',
    'worker:head-variant:orb',
    'worker:head-variant:visor',
    'worker:palette:rust-bone',
    'worker:palette:violet-cyan'
  ]
  const profileMeshes = profileRoots.flatMap(physicalMeshes)
  assert.equal(new Set(profileMeshes).size, 1, 'profile controls must reuse one shared mesh resource')

  const manifest = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url), 'utf8')
  )
  const kits = manifest.characterAssets.groupKits
  const accentMeshes = []
  for (const { kitId } of kits) {
    const prefix = `worker:group-kit:${kitId}`
    physicalMeshes(`${prefix}:silhouette`)
    const emblem = byName.get(`${prefix}:emblem`)
    assert.ok(emblem, `${kitId} is missing its physical emblem`)
    const accent = byName.get(`${prefix}:identity-accent`)
    assert.equal(accent?.getParentNode(), emblem, `${kitId} identity accent must remain under its emblem`)
    accentMeshes.push(...physicalMeshes(`${prefix}:identity-accent`))
  }
  assert.equal(new Set(accentMeshes).size, 1, 'kit accents must reuse one shared mesh resource')
})

test('exports deterministic near, strictly reduced mid, and cheaper static far character LODs', async () => {
  const io = new NodeIO()
  for (const id of ['leaders', 'workers']) {
    const root = (await io.read(join(firstRoot, 'models', `${id}.glb`))).getRoot()
    const near = tierCost(root, id, 'near')
    const mid = tierCost(root, id, 'mid')
    const far = tierCost(root, id, 'far')
    assert.ok(near.triangles > mid.triangles, `${id} mid LOD is not cheaper than near`)
    assert.ok(mid.triangles > far.triangles, `${id} far LOD is not cheaper than mid`)
    assert.equal(mid.skinnedNodes, 0, `${id} mid LOD must not carry a skin`)
    assert.equal(far.skinnedNodes, 0, `${id} far LOD must not carry a skin`)
    assert.equal(far.animatedChannels, 0, `${id} far LOD must be static`)
  }
})

test('targets genuine worker skin joints with materially distinct motion clips', async () => {
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'workers.glb'))).getRoot()
  const skin = root.listSkins()[0]
  const joints = skin.listJoints()
  const jointNames = new Set(joints.map(joint => joint.getName()))
  const influencedJoints = new Set()
  for (const node of root.listNodes()) {
    if (node.getSkin() !== skin || !node.getMesh()) continue
    for (const primitive of node.getMesh().listPrimitives()) {
      const indices = primitive.getAttribute('JOINTS_0')?.getArray()
      const weights = primitive.getAttribute('WEIGHTS_0')?.getArray()
      if (!indices || !weights) continue
      for (let index = 0; index < weights.length; index += 1) {
        if (weights[index] > 0.5) influencedJoints.add(joints[indices[index]].getName())
      }
    }
  }
  for (const name of [
    'worker:body',
    'worker:head',
    'worker:limb:left-arm',
    'worker:limb:right-arm',
    'worker:limb:left-leg',
    'worker:limb:right-leg'
  ]) {
    assert.ok(influencedJoints.has(name), `${name} does not deform exported worker geometry`)
  }

  const signatures = new Set()
  for (const animation of root.listAnimations()) {
    const targets = animation.listChannels().map(channel => channel.getTargetNode().getName())
    assert.ok(targets.length >= 2, `${animation.getName()} is not a composed pose`)
    assert.ok(
      targets.every(target => jointNames.has(target)),
      `${animation.getName()} targets a non-joint root node`
    )
    signatures.add(targets.toSorted().join('|'))
  }
  assert.ok(signatures.size >= 12, 'worker clips must encode materially distinct joint target combinations')
})

test('exports identity-qualified leader states without cross-wired character channels', async () => {
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'leaders.glb'))).getRoot()
  const triangles = worldTriangles(root, 'leaders:lod:near')
  const bounds = boundsOfTriangles(triangles)
  assert.ok(bounds.max[1] - bounds.min[1] >= 5.2, 'leaders need approved character presence beside the buildings')

  const animations = new Map(root.listAnimations().map(animation => [animation.getName(), animation]))
  const leaderRoot = root.listNodes().find(node => node.getName() === 'leaders:root')
  assert.equal(leaderRoot.getExtras().defaultLeader, 'owl')
  for (const id of LEADER_IDS) {
    const leader = root.listNodes().find(node => node.getName() === `leader:${id}`)
    assert.equal(leader.getExtras().leaderId, id)
    assert.equal(leader.getExtras().species, id)
    assert.equal(leader.getExtras().visualId, `${id}-leader-v1`)
    assert.equal(leader.getExtras().silhouetteId, `${id}-silhouette-v1`)
    const stateClips = leader.getExtras().stateClips
    assert.deepEqual(Object.keys(stateClips).toSorted(), LEADER_STATES.toSorted())
    for (const state of LEADER_STATES) {
      const clipName = `leader:${id}:${state}`
      assert.equal(stateClips[state], clipName, `${id} ${state} does not select its authoritative identity clip`)
      const animation = animations.get(clipName)
      assert.ok(animation, `missing ${clipName}`)
      const targetNodes = animation.listChannels().map(channel => channel.getTargetNode())
      assert.ok(targetNodes.length >= 2, `${clipName} must animate a materially composed physical pose`)
      assert.ok(
        targetNodes.every(node => node.getName().startsWith(`leader:${id}:`)),
        `${clipName} cross-wires another leader identity`
      )
      assert.ok(
        targetNodes.every(target => descendantsWithMeshes(root, target).length > 0),
        `${clipName} targets named nodes without physical geometry`
      )
    }
  }

  for (const state of LEADER_STATES) {
    const animation = animations.get(state)
    assert.ok(animation, `missing compatibility alias ${state}`)
    const targetNodes = animation.listChannels().map(channel => channel.getTargetNode())
    assert.ok(
      targetNodes.every(node => node.getName().startsWith('leader:owl:')),
      `${state} must be an owl-only alias`
    )
  }
})

test('keeps near leaders aligned with their district anchors across LODs', async () => {
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'leaders.glb'))).getRoot()
  const expected = [
    [-27.5, 5.5, -18],
    [23, 6.5, -22],
    [-31, 3.5, 12],
    [0, 3.5, -1],
    [27, 2.5, 31],
    [33, 4.5, 10]
  ]
  const nearIds = ['owl', 'fox', 'badger', 'otter', 'bird', 'stag']
  const round = values => values.map(value => Number(value.toFixed(3)))
  const nearGroup = root.listNodes().find(node => node.getName() === 'leaders:lod:near')

  for (const [index, id] of nearIds.entries()) {
    const near = root.listNodes().find(node => node.getName() === `leader:${id}`)
    assert.deepEqual(round(near.getWorldTranslation()), expected[index], `${id} near anchor drifted`)
  }
  for (const lod of ['leaders:lod:mid', 'leaders:lod:far']) {
    const group = root.listNodes().find(node => node.getName() === lod)
    assert.deepEqual(group.getExtras().districtAnchors, expected, `${lod} anchor metadata drifted`)
  }
  assert.ok(nearGroup, 'missing near leader LOD group')
})

test('keeps every visible walkway aligned with a semantic navigation link', async () => {
  const io = new NodeIO()
  const terrain = (await io.read(join(firstRoot, 'models', 'terrain.glb'))).getRoot()
  const navigation = (await io.read(join(firstRoot, 'models', 'navigation.glb'))).getRoot()
  const visibleLinks = terrain
    .listNodes()
    .map(node => node.getName())
    .filter(name => name.startsWith('terrain:walkway:'))
    .map(name => name.slice('terrain:walkway:'.length))
    .toSorted()
  const navigationLinks = navigation
    .listNodes()
    .map(node => node.getName())
    .filter(name => name.startsWith('navigation:link:'))
    .map(name => name.slice('navigation:link:'.length))
    .toSorted()
  assert.deepEqual(navigationLinks, visibleLinks)
})

test('uses the approved district road graph instead of a crossing shortcut', async () => {
  const root = (await new NodeIO().read(join(firstRoot, 'models', 'terrain.glb'))).getRoot()
  const normalize = name => name.split('-').toSorted().join('-')
  const actual = root
    .listNodes()
    .map(node => node.getName())
    .filter(name => name.startsWith('terrain:walkway:'))
    .map(name => normalize(name.slice('terrain:walkway:'.length)))
    .toSorted()
  const expected = [
    ['archive', 'arts-studio'],
    ['arts-studio', 'council'],
    ['arts-studio', 'research-lab'],
    ['bus', 'council'],
    ['bus', 'release-gatehouse'],
    ['bus', 'review-office'],
    ['depot', 'engineering-workshop'],
    ['release-gatehouse', 'triage'],
    ['review-office', 'depot'],
    ['review-office', 'library'],
    ['triage', 'garden']
  ]
    .map(edge => normalize(edge.join('-')))
    .toSorted()
  assert.deepEqual(actual, expected)
})

test('builds roads as low segmented ground routes rather than elevated straight beams', async () => {
  for (const { from, to } of WALKWAY_ROUTES) {
    const points = roadRoutePoints(from, to)
    assert.ok(points.length >= 9, `${from}-${to} must be built from multiple ground-following segments`)
    assert.ok(
      points.every(point => point[1] <= 0.55),
      `${from}-${to} must contact the terrain instead of floating at deck height`
    )
    const lengths = points
      .slice(1)
      .map((point, index) => Math.hypot(point[0] - points[index][0], point[2] - points[index][2]))
    assert.ok(
      lengths.every(length => length >= 0.45),
      `${from}-${to} must not collapse into overlapping micro-segments`
    )
    assert.ok(
      lengths.reduce((total, length) => total + length, 0) >= 5,
      `${from}-${to} must retain a usable architectural path length`
    )
  }
})

test('makes overview transit rails wide enough to read as deliberate cyan routes', () => {
  const engine = new NullEngine({ renderingPipeline: false })
  const scene = new Scene(engine)
  try {
    const root = buildTerrain(scene)
    const signal = root.getChildMeshes().find(mesh => mesh.name === 'terrain:walkways:signals')
    assert.ok(signal, 'the planned road network needs one merged signal rail mesh')

    const positions = signal.getVerticesData('position')
    const points = []
    for (let index = 0; index < 24; index += 3) {
      const point = [positions[index], positions[index + 1], positions[index + 2]]
      if (!points.some(existing => point.every((value, axis) => Math.abs(value - existing[axis]) < 1e-8))) {
        points.push(point)
      }
    }
    const pairDistances = []
    for (let left = 0; left < points.length; left += 1) {
      for (let right = left + 1; right < points.length; right += 1) {
        pairDistances.push(Math.hypot(...points[left].map((value, axis) => value - points[right][axis])))
      }
    }
    const shortestNonHeightEdge = pairDistances.filter(distance => distance > 0.15).toSorted((a, b) => a - b)[0]
    assert.ok(shortestNonHeightEdge >= 0.34, 'signal rails must have enough width to remain visible at overview scale')
  } finally {
    scene.dispose()
    engine.dispose()
  }
})

test('ships only generated palette texture data and an auxiliary navigation mesh', async () => {
  assert.deepEqual(
    firstReceipt.textures.map(texture => texture.uri),
    ['textures/approved-palette.png']
  )
  assert.equal(firstReceipt.textures[0].source, 'generated-approved-palette')
  assert.ok(firstReceipt.auxiliary.navigation.sha256)

  const paletteTexture = await readFile(join(firstRoot, firstReceipt.textures[0].uri))
  assert.equal(paletteTexture.subarray(1, 4).toString('ascii'), 'PNG')
  const navigation = await readFile(join(firstRoot, 'models', 'navigation.glb'))
  assert.equal(navigation.subarray(0, 4).toString('ascii'), 'glTF')
})

test('keeps the deterministic build CLI free of engine warnings and timestamped log noise', async () => {
  const cliRoot = await mkdtemp(join(tmpdir(), 'lunar-city-assets-cli-'))
  try {
    const { stderr, stdout } = await execFileAsync(process.execPath, [
      new URL('./build-models.mjs', import.meta.url).pathname,
      cliRoot
    ])
    assert.equal(stderr, '')
    assert.doesNotMatch(stdout, /BJS - \[/)
    assert.deepEqual(JSON.parse(stdout).missing, [])
  } finally {
    await rm(cliRoot, { force: true, recursive: true })
  }
})
