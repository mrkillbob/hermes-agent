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

let firstRoot
let secondRoot
let firstReceipt
let secondReceipt

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
