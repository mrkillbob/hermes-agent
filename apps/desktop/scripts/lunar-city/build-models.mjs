import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { NullEngine, Scene } from './modeling/babylon.mjs'
import {
  buildCouncil,
  buildDepot,
  buildGarden,
  buildLibrary,
  buildResearchLab,
  buildReviewOffice
} from './modeling/buildings.mjs'
import { buildLeaders, buildWorkers } from './modeling/characters.mjs'
import {
  exportModel,
  prepareOutputDirectories,
  updateManifestStatistics,
  writeGeneratedTexture
} from './modeling/export.mjs'
import { mergeLodMeshes } from './modeling/primitives.mjs'
import { buildBus, buildTriage } from './modeling/props.mjs'
import { buildNavigation, buildTerrain } from './modeling/terrain.mjs'

const DEFAULT_OUTPUT_ROOT = fileURLToPath(new URL('../../public/lunar-city/v2/', import.meta.url))
const MANIFEST_URL = new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url)
const SOURCE_REFERENCE_URL = new URL('../../public/lunar-city/v2/source-reference.v2.json', import.meta.url)
const MODEL_BUILDERS = Object.freeze({
  bus: buildBus,
  council: buildCouncil,
  depot: buildDepot,
  garden: buildGarden,
  leaders: buildLeaders,
  library: buildLibrary,
  'research-lab': buildResearchLab,
  'review-office': buildReviewOffice,
  terrain: buildTerrain,
  triage: buildTriage,
  workers: buildWorkers
})

function createScene() {
  const engine = new NullEngine({
    deterministicLockstep: true,
    lockstepMaxSteps: 4,
    renderHeight: 512,
    renderWidth: 512,
    textureSize: 512
  })
  const scene = new Scene(engine)
  scene.useRightHandedSystem = true
  return { engine, scene }
}

async function readContracts() {
  const [manifest, sourceReference] = await Promise.all([
    readFile(MANIFEST_URL, 'utf8').then(JSON.parse),
    readFile(SOURCE_REFERENCE_URL, 'utf8').then(JSON.parse)
  ])
  if (manifest.source.sha256 !== sourceReference.source.sha256) throw new Error('Lunar City source contracts disagree')
  return { manifest, sourceReference }
}

async function buildNavigationAsset(outputRoot) {
  const { engine, scene } = createScene()
  try {
    const root = buildNavigation(scene)
    mergeLodMeshes(scene, root, 'navigation')
    return await exportModel({
      budget: { maxDrawCalls: 4, maxGpuMiB: 4, maxMaterials: 1, maxTextures: 0, maxTriangles: 12000 },
      id: 'navigation',
      outputRoot,
      scene
    })
  } finally {
    scene.dispose()
    engine.dispose()
  }
}

export async function buildAssetPack(outputRoot = DEFAULT_OUTPUT_ROOT) {
  const { manifest, sourceReference } = await readContracts()
  await prepareOutputDirectories(outputRoot)
  const statistics = {}
  const sha256ByModel = {}
  const missing = []

  for (const model of manifest.models) {
    const build = MODEL_BUILDERS[model.id]
    if (!build) {
      missing.push(model.id)
      continue
    }
    const { engine, scene } = createScene()
    try {
      const root = build(scene)
      if (root.name !== `${model.id}:root`) throw new Error(`${model.id} builder returned ${root.name}`)
      statistics[model.id] = await exportModel({ budget: model, id: model.id, outputRoot, scene })
      sha256ByModel[model.id] = statistics[model.id].sha256
    } finally {
      scene.dispose()
      engine.dispose()
    }
  }

  const [texture, navigation] = await Promise.all([writeGeneratedTexture(outputRoot), buildNavigationAsset(outputRoot)])
  const receipt = {
    auxiliary: { navigation },
    missing,
    models: Object.keys(statistics),
    sha256ByModel,
    sourceSha256: sourceReference.source.sha256,
    statistics,
    textures: [texture]
  }
  await updateManifestStatistics(outputRoot, receipt)
  return receipt
}

async function main() {
  const outputRoot = process.argv[2] ?? DEFAULT_OUTPUT_ROOT
  const receipt = await buildAssetPack(outputRoot)
  console.log(
    JSON.stringify(
      {
        missing: receipt.missing,
        models: receipt.models,
        navigation: receipt.auxiliary.navigation.sha256,
        sha256ByModel: receipt.sha256ByModel,
        textures: receipt.textures
      },
      null,
      2
    )
  )
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main()
