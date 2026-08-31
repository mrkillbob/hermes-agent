import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

import { APPROVED_SHA, validateAssetPack } from './validate-assets.mjs'

const APPROVED_SOURCE_URI = '../moon-settlement-approved.jpg'

function fixture(overrides = {}) {
  return {
    version: 2,
    source: { sha256: APPROVED_SHA },
    models: [
      {
        id: 'terrain',
        uri: 'models/terrain.glb',
        maxTriangles: 1200,
        requiredNodes: ['terrain:root', 'terrain:lod:near', 'terrain:lod:far'],
        requiredClips: [],
        lods: [
          { distance: 0, node: 'terrain:lod:near' },
          { distance: 80, node: 'terrain:lod:far' }
        ]
      }
    ],
    camera: {
      overview: {
        id: 'approved-overview',
        alpha: -0.78,
        beta: 1.02,
        radius: 54,
        target: [0, 0, 0],
        minBeta: 0.72,
        maxBeta: 1.3,
        minRadius: 18,
        maxRadius: 96
      },
      bounds: { min: [-60, -12, -60], max: [60, 36, 60] }
    },
    navigation: {
      meshUri: 'models/navigation.glb',
      links: [{ from: [-8, 0, 0], to: [8, 0, 0], bidirectional: true }]
    },
    destinations: { bus: [0, 0, 0] },
    ...overrides
  }
}

function fakeDocument({ triangles = 1200, nodes, clips } = {}) {
  const nodeNames = nodes ?? ['terrain:root', 'terrain:lod:near', 'terrain:lod:far']

  return {
    getRoot() {
      return {
        listAnimations: () => (clips ?? []).map(name => ({ getName: () => name })),
        listMeshes: () => [
          {
            listPrimitives: () => [
              {
                getIndices: () => ({ getCount: () => triangles * 3 }),
                getMode: () => 4
              }
            ]
          }
        ],
        listNodes: () => nodeNames.map(name => ({ getName: () => name }))
      }
    }
  }
}

function fakeIoFor(statsByUri = {}) {
  return {
    async read(uri) {
      return fakeDocument(statsByUri[uri])
    },
    withTriangles(uri, triangles) {
      return fakeIoFor({ ...statsByUri, [uri]: { ...statsByUri[uri], triangles } })
    }
  }
}

const fakeIo = fakeIoFor()

test('rejects the approved JPG as runtime geometry or texture', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [{ id: 'world', uri: APPROVED_SOURCE_URI }]
    }),
    fakeIo
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /approved source cannot be a runtime asset/)
})

test('rejects the approved JPG from a model texture URI', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [
        {
          id: 'worker',
          uri: 'models/worker.glb',
          maxTriangles: 1200,
          textures: [APPROVED_SOURCE_URI]
        }
      ]
    }),
    fakeIo
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /approved source cannot be a runtime asset/)
})

test('rejects a model above its declared triangle budget', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [{ id: 'worker', uri: 'models/worker.glb', maxTriangles: 1200 }]
    }),
    fakeIo.withTriangles('models/worker.glb', 1201)
  )

  assert.match(result.errors.join('\n'), /worker exceeds 1200 triangles/)
})

test('requires a measurable triangle budget for every runtime model', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [{ id: 'worker', uri: 'models/worker.glb' }]
    }),
    fakeIo
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /worker requires a triangle budget/)
})

test('requires every declared node, animation clip, and LOD node', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [
        {
          id: 'leader',
          uri: 'models/leaders.glb',
          maxTriangles: 1200,
          requiredNodes: ['leader:root', 'leader:camera'],
          requiredClips: ['idle', 'talk'],
          lods: [{ distance: 0, node: 'leader:lod:near' }]
        }
      ]
    }),
    fakeIoFor({ 'models/leaders.glb': { nodes: ['leader:root'], clips: ['idle'] } })
  )

  assert.deepEqual(result.errors, [
    'leader missing node leader:camera',
    'leader missing clip talk',
    'leader missing LOD node leader:lod:near'
  ])
})

test('requires a bounded overview and linked navigation mesh', async () => {
  const result = await validateAssetPack(
    fixture({
      camera: { overview: { id: 'overview' }, bounds: { min: [0, 0, 0], max: [0, 0, 0] } },
      navigation: { meshUri: APPROVED_SOURCE_URI, links: [] }
    }),
    fakeIo
  )

  assert.deepEqual(result.errors, [
    'camera overview landmark is invalid',
    'camera bounds are invalid',
    'approved source cannot be a runtime asset',
    'navigation mesh URI must be a GLB',
    'navigation requires at least one link'
  ])
})

test('ships source and manifest records that preserve provenance without a runtime JPG URI', async () => {
  const source = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/source-reference.v2.json', import.meta.url), 'utf8')
  )
  const manifest = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url), 'utf8')
  )

  assert.equal(source.version, 2)
  assert.equal(source.source.sha256, APPROVED_SHA)
  assert.deepEqual(source.source.dimensions, { width: 1280, height: 910 })
  assert.ok(source.palette.length >= 6)
  assert.ok(source.districtLandmarks.length >= 8)
  assert.ok(source.silhouettes.leaders.length >= 6)
  assert.equal(manifest.source.sha256, source.source.sha256)
  assert.ok(manifest.models.length >= 11)
  assert.ok(manifest.models.every(model => !/moon-settlement-approved\.jpg$/i.test(model.uri)))
  assert.ok(manifest.models.every(model => model.requiredNodes.length > 0 && model.lods.length > 0))
  assert.ok(manifest.models.every(model => Array.isArray(model.cameraAnchor)))
  assert.ok(manifest.navigation.links.length > 0)
  assert.ok(manifest.projectSlots.length > 0)
  assert.ok(manifest.qualityBudgets.balancedOverview.visibleTriangles <= 1_500_000)
  assert.ok(manifest.qualityBudgets.balancedWorkerFocus.visibleTriangles <= 2_000_000)
})

test('returns an immutable validation result', async () => {
  const result = await validateAssetPack(fixture({ version: 1 }), fakeIo)

  assert.equal(result.ok, false)
  assert.equal(Object.isFrozen(result.errors), true)
  assert.throws(() => result.errors.push('later'), TypeError)
})
