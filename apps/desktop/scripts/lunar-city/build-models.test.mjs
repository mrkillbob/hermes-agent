import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import { after, before, test } from 'node:test'

import { NodeIO } from '@gltf-transform/core'

import { buildAssetPack } from './build-models.mjs'

const execFileAsync = promisify(execFile)

const APPROVED_SOURCE_NAME = 'moon-settlement-approved.jpg'
const MODEL_IDS = [
  'bus',
  'council',
  'depot',
  'garden',
  'leaders',
  'library',
  'research-lab',
  'review-office',
  'terrain',
  'triage',
  'workers'
]
const SEMANTIC_NODES = {
  bus: ['bus:cabin', 'bus:signal', 'bus:wheels'],
  council: ['council:console', 'council:dais', 'council:roost'],
  depot: ['depot:crates', 'depot:stocked-shelves', 'depot:workbench'],
  garden: ['garden:bench', 'garden:cyan-fixture', 'garden:plants'],
  leaders: ['leader:badger', 'leader:bird', 'leader:fox', 'leader:otter', 'leader:owl', 'leader:stag'],
  library: ['library:archive-stacks', 'library:leader-anchor', 'library:violet-orb'],
  'research-lab': ['research-lab:consoles', 'research-lab:specimen', 'research-lab:telescope'],
  'review-office': ['review-office:consoles', 'review-office:portal', 'review-office:verifier-dais'],
  terrain: ['terrain:bus-stop', 'terrain:cliffs', 'terrain:walkway:library-research'],
  triage: ['triage:cross', 'triage:door', 'triage:station'],
  workers: ['worker:attachment', 'worker:body', 'worker:head', 'worker:role-accessories']
}
const SPECIALIST_STATE_CHANNELS = {
  depot: ['workbench-cycle'],
  'research-lab': ['workbench-cycle'],
  triage: ['triage-station-idle']
}
const MIN_TRIANGLES = {
  bus: 100,
  council: 180,
  depot: 180,
  garden: 180,
  leaders: 900,
  library: 220,
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
const LEADER_IDS = ['owl', 'fox', 'badger', 'otter', 'bird', 'stag']
const LEADER_STATES = ['idle', 'listening', 'talking', 'thinking', 'acknowledging', 'unavailable']

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
  assert.ok(visibleLinks.includes('library-research'))
  assert.deepEqual(navigationLinks, visibleLinks)
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
