/**
 * Regenerates world-manifest.v2.json's per-model transforms and the
 * worker-routing `destinations` table from terrain.mjs's DISTRICTS --
 * the single source of truth for the city layout. Before this script,
 * DISTRICTS, each model's transform.position, and destinations were three
 * independently hand-kept copies of the same coordinates that had to be
 * updated in lock-step by hand. Run after any change to DISTRICTS, before
 * `node scripts/lunar-city/build-models.mjs`.
 *
 * Usage: node scripts/lunar-city/sync-district-layout.mjs
 */
import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { format } from 'prettier'

import { DISTRICTS, facingRotationY } from './modeling/terrain.mjs'

const MANIFEST_URL = new URL('../../public/lunar-city/v2/world-manifest.v2.json', import.meta.url)

// Each building's vertical seat above its terrain pad -- tuned per model
// height, independent of the district-layout redesign this script owns.
// Preserved from the manifest's prior hand-authored values rather than
// re-derived, since "how tall is this building's own mount point" isn't
// something DISTRICTS' x/z layout has any information about.
const MOUNT_OFFSET = Object.freeze({
  archive: 1.5,
  'arts-studio': 1.3,
  bus: 1.45,
  council: 0.65,
  depot: 1.55,
  'engineering-workshop': 1.4,
  garden: -0.25,
  library: 3.2,
  'release-gatehouse': 1.5,
  'research-lab': 3.9,
  'review-office': 2.3,
  triage: 0.6
})

// destinations keys don't all match district ids 1:1 (worker routing uses
// its own vocabulary, and "project"/"unavailable" aren't places on the
// map at all) -- this is the explicit, reviewable mapping between them.
const DESTINATION_TO_DISTRICT = Object.freeze({
  archive: 'archive',
  'arts-studio': 'arts-studio',
  bus: 'bus',
  council: 'council',
  depot: 'depot',
  'engineering-workshop': 'engineering-workshop',
  garden: 'garden',
  'release-gatehouse': 'release-gatehouse',
  review: 'review-office',
  triage: 'triage'
})

function syncManifest(manifest) {
  const models = manifest.models.map(model => {
    const district = DISTRICTS[model.id]
    if (!district) return model
    const [x, y, z] = district.position
    const offset = MOUNT_OFFSET[model.id]
    if (offset === undefined) throw new Error(`sync-district-layout: no MOUNT_OFFSET entry for "${model.id}"`)
    return {
      ...model,
      transform: {
        ...model.transform,
        position: [x, Number((y + offset).toFixed(4)), z],
        rotation: [0, Number(facingRotationY(model.id).toFixed(4)), 0]
      }
    }
  })

  const destinations = { ...manifest.destinations }
  for (const [key, districtId] of Object.entries(DESTINATION_TO_DISTRICT)) {
    if (!(key in destinations)) continue
    const [x, , z] = DISTRICTS[districtId].position
    destinations[key] = [x, 0, z]
  }

  return { ...manifest, destinations, models }
}

async function main() {
  const manifestPath = fileURLToPath(MANIFEST_URL)
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const synced = syncManifest(manifest)
  await writeFile(
    manifestPath,
    await format(JSON.stringify(synced), {
      arrowParens: 'avoid',
      bracketSpacing: true,
      endOfLine: 'auto',
      filepath: manifestPath,
      printWidth: 120,
      semi: false,
      singleQuote: true,
      tabWidth: 2,
      trailingComma: 'none',
      useTabs: false
    })
  )
  console.log('synced world-manifest.v2.json transforms and destinations from DISTRICTS')
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await main()
