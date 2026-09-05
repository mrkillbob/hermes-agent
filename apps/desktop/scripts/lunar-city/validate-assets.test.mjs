import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { Document } from '@gltf-transform/core'

import {
  APPROVED_SHA,
  validateAssetContract,
  validateAssetContractFiles,
  validateAssetPack,
  validateSceneContract
} from './validate-assets.mjs'

const APPROVED_SOURCE_URI = '../moon-settlement-approved.jpg'

const HERMES_GROUPS = Object.freeze([
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
])

function characterAssetsFixture(overrides = {}) {
  const accessories = HERMES_GROUPS.map((_, index) => `accessory-${index + 1}`)
  const emblems = HERMES_GROUPS.map((_, index) => `emblem-${index + 1}`)
  return {
    fleetIdentityFloor: 128,
    leaders: ['owl', 'fox', 'badger', 'otter', 'bird', 'stag'].map(id => ({
      id,
      species: id,
      silhouetteId: `${id}-silhouette`,
      visualId: `${id}-visual`
    })),
    workerVocabulary: {
      bodies: ['compact', 'standard'],
      heads: ['orb', 'visor'],
      silhouetteAccessories: accessories,
      palettes: ['rust', 'bone'],
      emblems
    },
    groupKits: HERMES_GROUPS.map((group, index) => ({
      group,
      kitId: `kit-${index + 1}`,
      signature: {
        body: index % 2 ? 'compact' : 'standard',
        head: index % 2 ? 'visor' : 'orb',
        silhouetteAccessory: accessories[index],
        palette: index % 2 ? 'bone' : 'rust',
        emblem: emblems[index]
      }
    })),
    sharedResourceStrategy: {
      rig: 'worker:shared-rig',
      gpuBuffers: 'shared',
      animationClips: 'shared',
      materials: 'shared',
      textureAtlas: 'textures/approved-palette.png',
      perProfile: { skeletons: 0, meshes: 0, materials: 0, textures: 0 }
    },
    physicalVariantRoots: {
      body: { compact: 'worker:body-variant:compact', standard: 'worker:body-variant:standard' },
      head: { orb: 'worker:head-variant:orb', visor: 'worker:head-variant:visor' },
      palette: { bone: 'worker:palette:violet-cyan', rust: 'worker:palette:rust-bone' },
      groupKit: { emblemSuffix: 'emblem', identityAccentSuffix: 'identity-accent', silhouetteSuffix: 'silhouette' }
    },
    lodRepresentations: [
      { id: 'near', animated: true, representation: 'full' },
      { id: 'mid', animated: false, representation: 'reduced' },
      { id: 'far', animated: false, representation: 'static-or-aggregate' }
    ],
    ...overrides
  }
}

function modelFixture(overrides = {}) {
  const model = {
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
    materialSlots: ['structural']
  }
  const uri = overrides.uri ?? model.uri
  const artifact = Buffer.from(`fixture:${uri}`)
  const statistics = {
    accessorBytes: 1024,
    animationClips: [],
    budget: {
      maxDrawCalls: overrides.maxDrawCalls ?? model.maxDrawCalls,
      maxGpuMiB: overrides.maxGpuMiB ?? model.maxGpuMiB,
      maxMaterials: overrides.maxMaterials ?? model.maxMaterials,
      maxTextures: overrides.maxTextures ?? model.maxTextures,
      maxTriangles: overrides.maxTriangles ?? model.maxTriangles
    },
    bytes: artifact.byteLength,
    drawCalls: 1,
    gpuBytes: 1024,
    gpuMiB: 0.001,
    materials: 1,
    meshes: 1,
    nodes: 3,
    sha256: createHash('sha256').update(artifact).digest('hex'),
    textures: 0,
    triangles: 1200,
    extent: [4, 4, 4]
  }
  return {
    ...model,
    ...overrides,
    statistics: Object.hasOwn(overrides, 'statistics') ? overrides.statistics : statistics
  }
}

function fixture(overrides = {}) {
  return {
    version: 2,
    assetVersion: '2.0.0',
    source: { sha256: APPROVED_SHA },
    characterAssets: characterAssetsFixture(),
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

function fakeDocument({
  triangles = 1200,
  nodes,
  clips,
  textureUris,
  primitiveCount = 1,
  materialCount = 1,
  skinCount = 0,
  gpuBytes = 1024
} = {}) {
  const nodeNames = nodes ?? ['terrain:root', 'terrain:lod:near', 'terrain:lod:far']
  const primitive = {
    getIndices: () => ({ getCount: () => triangles * 3 }),
    getMode: () => 4
  }

  return {
    getRoot() {
      return {
        listAnimations: () => (clips ?? []).map(name => ({ getName: () => name })),
        listAccessors: () => [{ getArray: () => ({ byteLength: gpuBytes }) }],
        listMaterials: () => Array.from({ length: materialCount }, () => ({})),
        listMeshes: () => [
          {
            listPrimitives: () => Array.from({ length: primitiveCount }, () => primitive)
          }
        ],
        listNodes: () => nodeNames.map(name => ({ getName: () => name })),
        listSkins: () => Array.from({ length: skinCount }, () => ({})),
        listTextures: () => (textureUris ?? []).map(uri => ({ getURI: () => uri }))
      }
    }
  }
}

function fakeIoFor(statsByUri = {}, bytesByUri = {}) {
  return {
    async read(uri) {
      return fakeDocument(statsByUri[uri])
    },
    async readBytes(uri) {
      return bytesByUri[uri] ?? Buffer.from(`fixture:${uri}`)
    },
    withTriangles(uri, triangles) {
      return fakeIoFor({ ...statsByUri, [uri]: { ...statsByUri[uri], triangles } }, bytesByUri)
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

test('requires a complete generated statistics receipt before attesting a GLB', async () => {
  for (const [statistics, expected] of [
    [undefined, /receipt requires generated statistics/],
    [{}, /receipt generated statistics bytes is required/]
  ]) {
    const result = await validateAssetPack(
      fixture({ models: [modelFixture({ id: 'receipt', uri: 'models/receipt.glb', statistics })] }),
      fakeIo
    )
    assert.equal(result.ok, false)
    assert.match(result.errors.join('\n'), expected)
  }

  for (const field of [
    'accessorBytes',
    'animationClips',
    'budget',
    'bytes',
    'drawCalls',
    'extent',
    'gpuBytes',
    'gpuMiB',
    'materials',
    'meshes',
    'nodes',
    'sha256',
    'textures',
    'triangles'
  ]) {
    const statistics = structuredClone(modelFixture().statistics)
    delete statistics[field]
    const result = await validateAssetPack(fixture({ models: [modelFixture({ statistics })] }), fakeIo)
    assert.equal(result.ok, false, `${field} must be required`)
    assert.match(result.errors.join('\n'), new RegExp(`terrain generated statistics ${field} is required`))
  }

  for (const [budget] of Object.entries(modelFixture().statistics.budget)) {
    const statistics = structuredClone(modelFixture().statistics)
    delete statistics.budget[budget]
    const result = await validateAssetPack(fixture({ models: [modelFixture({ statistics })] }), fakeIo)
    assert.equal(result.ok, false, `${budget} must be required`)
    assert.match(result.errors.join('\n'), new RegExp(`terrain generated budget ${budget} is invalid`))
  }
})

test('rejects malformed generated statistics and contradictory generated budgets', async () => {
  const cases = [
    ['negative byte count', { bytes: -1 }, /terrain generated statistics bytes is invalid/],
    ['fractional draw calls', { drawCalls: 1.5 }, /terrain generated statistics drawCalls is invalid/],
    ['non-numeric GPU bytes', { gpuBytes: '1024' }, /terrain generated statistics gpuBytes is invalid/],
    ['nonfinite GPU MiB', { gpuMiB: Infinity }, /terrain generated statistics gpuMiB is invalid/],
    ['negative materials', { materials: -1 }, /terrain generated statistics materials is invalid/],
    ['fractional textures', { textures: 0.5 }, /terrain generated statistics textures is invalid/],
    ['negative triangles', { triangles: -1 }, /terrain generated statistics triangles is invalid/],
    ['non-string digest', { sha256: 42 }, /terrain generated statistics sha256 is invalid/],
    ['malformed digest', { sha256: 'not-a-sha256' }, /terrain generated statistics sha256 is invalid/],
    [
      'wrong byte count',
      { bytes: modelFixture().statistics.bytes + 1 },
      /terrain generated bytes does not match its actual GLB/
    ],
    ['inconsistent GPU estimate', { gpuMiB: 0.002 }, /terrain generated gpuMiB is inconsistent with gpuBytes/],
    [
      'inconsistent budget',
      { budget: { ...modelFixture().statistics.budget, maxDrawCalls: 99 } },
      /terrain generated budget maxDrawCalls does not match model budget/
    ],
    [
      'negative generated budget',
      { budget: { ...modelFixture().statistics.budget, maxTriangles: -1 } },
      /terrain generated budget maxTriangles is invalid/
    ],
    ['unknown statistics field', { unexpected: true }, /terrain generated statistics unexpected is not supported/],
    [
      'unknown budget field',
      { budget: { ...modelFixture().statistics.budget, unexpected: 1 } },
      /terrain generated budget unexpected is not supported/
    ]
  ]

  for (const [, mutation, expected] of cases) {
    const statistics = { ...modelFixture().statistics, ...mutation }
    const result = await validateAssetPack(fixture({ models: [modelFixture({ statistics })] }), fakeIo)
    assert.equal(result.ok, false)
    assert.match(result.errors.join('\n'), expected)
  }
})

test('measures actual draw, material, texture, GPU, and hash costs instead of trusting the manifest', async () => {
  const uri = 'models/replaced.glb'
  const result = await validateAssetPack(
    fixture({
      models: [
        modelFixture({
          id: 'replaced',
          uri,
          maxTriangles: 1000,
          maxDrawCalls: 6,
          maxMaterials: 4,
          maxTextures: 1,
          maxGpuMiB: 0.001,
          statistics: { sha256: '0'.repeat(64) }
        })
      ]
    }),
    fakeIoFor(
      {
        [uri]: {
          triangles: 1,
          primitiveCount: 20,
          materialCount: 23,
          textureUris: ['atlas-a.png', 'atlas-b.png'],
          gpuBytes: 2048
        }
      },
      { [uri]: Buffer.from('replacement glb bytes') }
    )
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /replaced exceeds 6 draw calls/)
  assert.match(result.errors.join('\n'), /replaced exceeds 4 materials/)
  assert.match(result.errors.join('\n'), /replaced exceeds 1 textures/)
  assert.match(result.errors.join('\n'), /replaced exceeds 0.001 GPU MiB/)
  assert.match(result.errors.join('\n'), /replaced artifact digest does not match its generated statistics/)
})

test('rejects multiple worker skins even when the manifest claims zero per-profile resources', async () => {
  const uri = 'models/workers.glb'
  const result = await validateAssetPack(
    fixture({
      models: [
        modelFixture({
          id: 'workers',
          uri,
          maxTriangles: 10_000,
          requiredNodes: ['workers:root', 'workers:lod:near', 'workers:lod:mid', 'workers:lod:far'],
          lods: [
            { distance: 0, node: 'workers:lod:near' },
            { distance: 14, node: 'workers:lod:mid' },
            { distance: 28, node: 'workers:lod:far' }
          ]
        })
      ]
    }),
    fakeIoFor({
      [uri]: {
        nodes: ['workers:root', 'workers:lod:near', 'workers:lod:mid', 'workers:lod:far'],
        skinCount: 2
      }
    })
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /workers must contain exactly one shared skin, found 2/)
})

test('measures character LOD graph costs instead of accepting equal or animated far tiers', async () => {
  const uri = 'models/workers.glb'
  const root = { getName: () => 'workers:root', getParent: () => null, getMesh: () => null, getSkin: () => null }
  const lod = suffix => ({
    getName: () => `workers:lod:${suffix}`,
    getParent: () => root,
    getMesh: () => null,
    getSkin: () => null
  })
  const primitive = triangles => ({
    getIndices: () => ({ getCount: () => triangles * 3 }),
    getMode: () => 4
  })
  const near = lod('near')
  const mid = lod('mid')
  const far = lod('far')
  const meshNode = (name, parent, triangles) => ({
    getName: () => name,
    getParent: () => parent,
    getMesh: () => ({ listPrimitives: () => [primitive(triangles)] }),
    getSkin: () => null
  })
  const farMesh = meshNode('workers:far:mesh', far, 30)
  const document = {
    getRoot: () => ({
      listAccessors: () => [],
      listAnimations: () => [
        { getName: () => 'invalid-far-animation', listChannels: () => [{ getTargetNode: () => farMesh }] }
      ],
      listMaterials: () => [{}],
      listMeshes: () => [
        meshNode('workers:near:mesh', near, 100).getMesh(),
        meshNode('workers:mid:mesh', mid, 30).getMesh(),
        farMesh.getMesh()
      ],
      listNodes: () => [
        root,
        near,
        meshNode('workers:near:mesh', near, 100),
        mid,
        meshNode('workers:mid:mesh', mid, 30),
        far,
        farMesh
      ],
      listSkins: () => [{}],
      listTextures: () => []
    })
  }

  const result = await validateAssetPack(
    fixture({
      models: [
        modelFixture({
          id: 'workers',
          uri,
          requiredNodes: ['workers:root', 'workers:lod:near', 'workers:lod:mid', 'workers:lod:far'],
          lods: [
            { distance: 0, node: 'workers:lod:near' },
            { distance: 14, node: 'workers:lod:mid' },
            { distance: 28, node: 'workers:lod:far' }
          ]
        })
      ]
    }),
    { read: async () => document, readBytes: async () => Buffer.from('worker graph') }
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /workers LOD geometry must strictly decrease near > mid > far/)
  assert.match(result.errors.join('\n'), /workers far LOD must not contain animation channels/)
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
    {
      expected: /navigation mesh URI must be a GLB/,
      root: fixture({ navigation: { ...fixture().navigation, meshUri: null } })
    },
    {
      expected: /approved source URI mismatch/,
      root: fixture(),
      sourceReference: { ...sourceReference, source: { ...sourceReference.source, uri: null } }
    }
  ]

  for (const testCase of cases) {
    const { directory, manifestPath } = await writeContractFiles(
      testCase.root,
      testCase.sourceReference ?? sourceReference
    )
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
    'leader generated nodes does not match its actual GLB',
    'leader generated animationClips do not match its actual GLB',
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

test('rejects reused leader visual, species, or silhouette identities', async () => {
  const characterAssets = characterAssetsFixture()
  characterAssets.leaders[1] = {
    ...characterAssets.leaders[1],
    species: characterAssets.leaders[0].species,
    silhouetteId: characterAssets.leaders[0].silhouetteId,
    visualId: characterAssets.leaders[0].visualId
  }

  const result = await validateAssetPack(fixture({ characterAssets }), fakeIo)

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /leader species owl is reused/)
  assert.match(result.errors.join('\n'), /leader silhouette owl-silhouette is reused/)
  assert.match(result.errors.join('\n'), /leader visual owl-visual is reused/)
})

test('requires one physically distinct worker kit for every declared Hermes group', async () => {
  const characterAssets = characterAssetsFixture()
  characterAssets.groupKits.pop()
  characterAssets.groupKits[1] = {
    ...characterAssets.groupKits[1],
    kitId: characterAssets.groupKits[0].kitId
  }

  const result = await validateAssetPack(fixture({ characterAssets }), fakeIo)

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /worker kits must cover exactly 19 Hermes groups/)
  assert.match(result.errors.join('\n'), /worker kit kit-1 is reused/)
  assert.match(result.errors.join('\n'), /missing worker kit for Upstream Hermes Maintenance/)
})

test('rejects worker signature collisions and vocabulary references that cannot be rendered', async () => {
  const characterAssets = characterAssetsFixture()
  characterAssets.groupKits[1] = {
    ...characterAssets.groupKits[1],
    signature: { ...characterAssets.groupKits[0].signature }
  }
  characterAssets.groupKits[2] = {
    ...characterAssets.groupKits[2],
    signature: { ...characterAssets.groupKits[2].signature, head: 'missing-head' }
  }

  const result = await validateAssetPack(fixture({ characterAssets }), fakeIo)

  assert.equal(result.ok, false)
  assert.match(
    result.errors.join('\n'),
    /worker signature collision between Acceptance & Release and Archive and Acquisition/
  )
  assert.match(result.errors.join('\n'), /Arts Studio worker signature head missing-head is not declared/)
})

test('rejects per-profile GPU resources and an identity vocabulary below the fleet floor', async () => {
  const characterAssets = characterAssetsFixture({
    fleetIdentityFloor: 10_000,
    sharedResourceStrategy: {
      ...characterAssetsFixture().sharedResourceStrategy,
      perProfile: { skeletons: 1, meshes: 1, materials: 1, textures: 1 }
    }
  })

  const result = await validateAssetPack(fixture({ characterAssets }), fakeIo)

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /worker signature vocabulary capacity .* is below fleet floor 10000/)
  assert.match(result.errors.join('\n'), /per-profile skeletons must be zero/)
  assert.match(result.errors.join('\n'), /per-profile meshes must be zero/)
  assert.match(result.errors.join('\n'), /per-profile materials must be zero/)
  assert.match(result.errors.join('\n'), /per-profile textures must be zero/)
})

test('requires near, mid, and far low-power character representations', async () => {
  const characterAssets = characterAssetsFixture({
    lodRepresentations: [
      { id: 'near', animated: true, representation: 'full' },
      { id: 'far', animated: true, representation: 'full' }
    ]
  })

  const result = await validateAssetPack(fixture({ characterAssets }), fakeIo)

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /character LODs must declare near, mid, and far representations/)
  assert.match(result.errors.join('\n'), /far character LOD must be static-or-aggregate/)
})

test('rejects missing or inconsistent runtime manifest declarations', async () => {
  const result = await validateAssetPack(
    fixture({
      models: [
        modelFixture({
          maxDrawCalls: undefined,
          lods: [
            { distance: 10, node: 'terrain:lod:near' },
            { distance: 5, node: 'terrain:lod:far' }
          ],
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
  const sceneContract = JSON.parse(
    await readFile(new URL('../../public/lunar-city/v2/scene-contract.v1.json', import.meta.url), 'utf8')
  )
  assert.deepEqual(validateSceneContract(sceneContract), { ok: true, errors: [] })
})

test('accepts a complete Blender building and environment scene contract', () => {
  const result = validateSceneContract({
    version: 1,
    activeClip: 'sky-scene',
    frameRange: [1, 240],
    world: {
      name: 'LunarCityWorld',
      background: { color: [0.005, 0.008, 0.02], strength: 0.22 },
      raySettings: { engine: 'BLENDER_EEVEE_NEXT', samples: 32, maxBounces: 4 },
      surface: { type: 'SKY', horizonColor: [0.005, 0.008, 0.02], zenithColor: [0.001, 0.002, 0.012] }
    },
    collections: [
      'LUNAR_CITY::BUILDINGS',
      'LUNAR_CITY::TERRAIN',
      'LUNAR_CITY::WORKERS',
      'LUNAR_CITY::SKYBOX',
      'LUNAR_CITY::COLLISION'
    ],
    instancing: [{ collection: 'LUNAR_CITY::BUILDING_INSTANCES', source: 'LUNAR_CITY::BUILDINGS', count: 12 }],
    motionPaths: [{ object: 'LUNAR_CITY::SKYBOX', mode: 'OBJECT', frames: [1, 240] }],
    shading: { colorType: 'MATERIAL', showShadows: true, showCavity: true, cavityType: 'BOTH' },
    motionBlur: { enabled: true, shutter: 0.35 },
    visibility: { skybox: true, buildings: true, terrain: true, collisionViewport: false },
    lineArt: { mode: 'FREESTYLE', enabled: true },
    physics: {
      rigidBodyWorld: { enabled: true, frameRate: 60, substeps: 4 },
      constraints: [{ name: 'LUNAR_CITY::CONSTRAINT::TRANSIT_GUIDE', type: 'FIXED' }]
    },
    geometry: {
      vertexGroups: ['LUNAR_GROUND_CONTACT', 'LUNAR_FACADE_ACCENT'],
      shapeKeys: ['LunarSurfaceRest', 'LunarFacadeFlex'],
      textureSpace: true,
      remesh: { mode: 'VOXEL', voxelSize: 0.18, showViewport: false, showRender: false }
    },
    data: {
      animation: { skyClip: 'sky-scene', modifier: 'CYCLES' },
      texture: { name: 'LunarCity::SkyGradient', type: 'IMAGE' },
      brushes: ['LunarCity::SurfaceBrush']
    }
  })

  assert.deepEqual(result, { ok: true, errors: [] })
})

test('rejects a scene contract that silently drops the active sky clip or physics world', () => {
  const result = validateSceneContract({
    version: 1,
    activeClip: '',
    frameRange: [1, 1],
    world: {},
    collections: [],
    instancing: [],
    motionPaths: [],
    shading: {},
    motionBlur: {},
    visibility: {},
    lineArt: {},
    physics: { rigidBodyWorld: {}, constraints: [] },
    geometry: {},
    data: {}
  })

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /activeClip is required/)
  assert.match(result.errors.join('\n'), /world ray settings are invalid/)
  assert.match(result.errors.join('\n'), /rigid body world is invalid/)
  assert.match(result.errors.join('\n'), /at least one physics constraint is required/)
})

test('returns an immutable validation result', async () => {
  const result = await validateAssetPack(fixture({ version: 1 }), fakeIo)

  assert.equal(result.ok, false)
  assert.equal(Object.isFrozen(result.errors), true)
  assert.throws(() => result.errors.push('later'), TypeError)
})
