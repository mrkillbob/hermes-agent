import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { Document } from '@gltf-transform/core'

import { APPROVED_SHA, validateAssetContract, validateAssetContractFiles, validateAssetPack } from './validate-assets.mjs'

const APPROVED_SOURCE_URI = '../moon-settlement-approved.jpg'

function modelFixture(overrides = {}) {
  return {
    id: 'terrain',
    uri: 'models/terrain.glb',
    maxTriangles: 1200,
    maxDrawCalls: 4,
    maxMaterials: 2,
    maxTextures: 2,
    maxGpuMiB: 4,
    requiredNodes: ['terrain:root', 'terrain:lod:near', 'terrain:lod:far'],
    requiredClips: [],
    lods: [
      { distance: 0, node: 'terrain:lod:near' },
      { distance: 80, node: 'terrain:lod:far' }
    ],
    transform: { position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1] },
    pivot: [0, 0, 0],
    bounds: { min: [-2, 0, -2], max: [2, 4, 2] },
    anchors: { foot: [0, 0, 0], camera: [0, 4, 8] },
    cameraAnchor: [0, 4, 8],
    occlusionGroup: 'terrain',
    collision: { kind: 'mesh', navigationArea: 'walkable' },
    materialSlots: ['structural'],
    ...overrides
  }
}

function fixture(overrides = {}) {
  return {
    version: 2,
    assetVersion: '2.0.0',
    source: { sha256: APPROVED_SHA },
    materials: [{ id: 'structural' }],
    models: [modelFixture()],
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
      areas: ['walkable'],
      links: [{ from: [-8, 0, 0], to: [8, 0, 0], bidirectional: true }]
    },
    destinations: { bus: [0, 0, 0] },
    projectSlots: [
      {
        id: 'compound-1',
        position: [12, 0, 0],
        bounds: { min: [8, 0, -4], max: [16, 8, 4] },
        navigationLink: { from: [-8, 0, 0], to: [8, 0, 0], bidirectional: true }
      }
    ],
    qualityBudgets: {
      balancedOverview: { drawCalls: 180, visibleTriangles: 1_500_000, gpuMiB: 256 },
      balancedWorkerFocus: { drawCalls: 220, visibleTriangles: 2_000_000, gpuMiB: 256 }
    },
    ...overrides
  }
}

function fakeDocument({ triangles = 1200, nodes, clips, textureUris } = {}) {
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
        listNodes: () => nodeNames.map(name => ({ getName: () => name })),
        listTextures: () => (textureUris ?? []).map(uri => ({ getURI: () => uri }))
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

async function writeContractFiles(root, sourceReference) {
  const directory = await mkdtemp(join(tmpdir(), 'lunar-city-contract-'))
  const manifestPath = join(directory, 'world-manifest.v2.json')
  await writeFile(manifestPath, JSON.stringify(root))
  await writeFile(join(directory, 'source-reference.v2.json'), JSON.stringify(sourceReference))
  return { directory, manifestPath }
}

test('rejects the approved JPG as runtime geometry or texture', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [modelFixture({ id: 'world', uri: APPROVED_SOURCE_URI })]
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
        modelFixture({
          id: 'worker',
          textures: [APPROVED_SOURCE_URI]
        })
      ]
    }),
    fakeIo
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /approved source cannot be a runtime asset/)
})

test('rejects the approved JPG embedded as an external glTF texture URI', async () => {
  const document = new Document()
  document.createTexture('approved-reference').setURI(APPROVED_SOURCE_URI)

  const result = await validateAssetPack(fixture(), { read: async () => document })

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /approved source cannot be a runtime asset/)
})

test('rejects a model above its declared triangle budget', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [modelFixture({ id: 'worker', uri: 'models/worker.glb', maxTriangles: 1200 })]
    }),
    fakeIo.withTriangles('models/worker.glb', 1201)
  )

  assert.match(result.errors.join('\n'), /worker exceeds 1200 triangles/)
})

test('requires a measurable triangle budget for every runtime model', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [modelFixture({ id: 'worker', uri: 'models/worker.glb', maxTriangles: undefined })]
    }),
    fakeIo
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /worker requires a triangle budget/)
})

test('requires at least one runtime model', async () => {
  const result = await validateAssetPack(fixture({ models: [] }), fakeIo)

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /at least one runtime model is required/)
})

test('requires an exact asset version and unique stable model IDs', async () => {
  const result = await validateAssetPack(
    fixture({
      assetVersion: '2.0',
      models: [modelFixture({ id: '' }), modelFixture({ id: 'terrain' }), modelFixture({ id: 'terrain' })]
    }),
    fakeIo
  )

  assert.match(result.errors.join('\n'), /asset version must equal 2\.0\.0/)
  assert.match(result.errors.join('\n'), /model id is required/)
  assert.match(result.errors.join('\n'), /duplicate model id terrain/)
})

test('returns structured errors for malformed file-level declarations', async () => {
  const sourceReference = {
    version: 2,
    source: {
      uri: APPROVED_SOURCE_URI,
      sha256: APPROVED_SHA,
      dimensions: { width: 1280, height: 910 }
    }
  }
  const cases = [
    { expected: /models must be an array/, root: fixture({ models: {} }) },
    { expected: /terrain model URI must be a GLB/, root: fixture({ models: [modelFixture({ uri: null })] }) },
    { expected: /navigation mesh URI must be a GLB/, root: fixture({ navigation: { ...fixture().navigation, meshUri: null } }) },
    { expected: /approved source URI mismatch/, root: fixture(), sourceReference: { ...sourceReference, source: { ...sourceReference.source, uri: null } } }
  ]

  for (const testCase of cases) {
    const { directory, manifestPath } = await writeContractFiles(testCase.root, testCase.sourceReference ?? sourceReference)
    try {
      const result = await validateAssetContractFiles(manifestPath, fakeIo)
      assert.equal(result.ok, false)
      assert.match(result.errors.join('\n'), testCase.expected)
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  }
})

test('requires every declared node, animation clip, and LOD node', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [
        modelFixture({
          id: 'leader',
          uri: 'models/leaders.glb',
          requiredNodes: ['leader:root', 'leader:camera'],
          requiredClips: ['idle', 'talk'],
          lods: [{ distance: 0, node: 'leader:lod:near' }]
        })
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
      navigation: { meshUri: APPROVED_SOURCE_URI, areas: [], links: [] }
    }),
    fakeIo
  )

  assert.deepEqual(result.errors, [
    'camera overview landmark is invalid',
    'camera bounds are invalid',
    'approved source cannot be a runtime asset',
    'navigation mesh URI must be a GLB',
    'navigation requires declared areas',
    'navigation requires at least one link',
    'project slot compound-1 is invalid',
    'terrain navigation area walkable is not declared'
  ])
})

test('enforces the balanced overview and focused-worker quality ceilings', async () => {
  const result = await validateAssetPack(
    fixture({
      qualityBudgets: {
        balancedOverview: { drawCalls: 181, visibleTriangles: 1_500_001, gpuMiB: 257 },
        balancedWorkerFocus: { drawCalls: 221, visibleTriangles: 2_000_001, gpuMiB: 257 }
      }
    }),
    fakeIo
  )

  assert.match(result.errors.join('\n'), /balanced overview exceeds 180 draw calls/)
  assert.match(result.errors.join('\n'), /balanced overview exceeds 1500000 visible triangles/)
  assert.match(result.errors.join('\n'), /balanced overview exceeds 256 MiB GPU memory/)
  assert.match(result.errors.join('\n'), /balanced worker focus exceeds 220 draw calls/)
  assert.match(result.errors.join('\n'), /balanced worker focus exceeds 2000000 visible triangles/)
  assert.match(result.errors.join('\n'), /balanced worker focus exceeds 256 MiB GPU memory/)
})

test('rejects missing or inconsistent runtime manifest declarations', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [
        modelFixture({
          maxDrawCalls: undefined,
          lods: [{ distance: 10, node: 'terrain:lod:near' }, { distance: 5, node: 'terrain:lod:far' }],
          transform: { position: [0, 0], rotation: [0, 0, 0], scale: [1, 1, 1] },
          bounds: { min: [2, 0, 0], max: [1, 1, 1] },
          anchors: { foot: [0, 0, 0] },
          cameraAnchor: [0, 0],
          occlusionGroup: '',
          collision: { kind: 'box', navigationArea: 'unknown' },
          materialSlots: ['missing']
        })
      ],
      destinations: { bus: [0, 0] },
      projectSlots: [
        {
          id: 'compound-1',
          position: [12, 0],
          bounds: { min: [8, 0, -4], max: [16, 8, 4] },
          navigationLink: { from: [1, 0, 0], to: [2, 0, 0], bidirectional: true }
        }
      ]
    }),
    fakeIo
  )

  assert.match(result.errors.join('\n'), /terrain requires a draw-call budget/)
  assert.match(result.errors.join('\n'), /terrain LOD distances must be strictly increasing/)
  assert.match(result.errors.join('\n'), /terrain transform is invalid/)
  assert.match(result.errors.join('\n'), /terrain bounds are invalid/)
  assert.match(result.errors.join('\n'), /terrain camera anchor is invalid/)
  assert.match(result.errors.join('\n'), /terrain occlusion group is required/)
  assert.match(result.errors.join('\n'), /terrain navigation area unknown is not declared/)
  assert.match(result.errors.join('\n'), /terrain material slot missing is not declared/)
  assert.match(result.errors.join('\n'), /destination bus is invalid/)
  assert.match(result.errors.join('\n'), /project slot compound-1 is invalid/)
})

test('cross-checks the local source reference against the actual approved JPG', async () => {
  const sourceReference = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/source-reference.v2.json', import.meta.url), 'utf8')
  )
  const result = await validateAssetContract({
    root: fixture(),
    io: fakeIo,
    sourcePath: new URL('../../public/lunar-city/moon-settlement-approved.jpg', import.meta.url),
    sourceReference
  })

  assert.deepEqual(result, { ok: true, errors: [] })
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
  assert.equal(manifest.assetVersion, '2.0.0')
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
