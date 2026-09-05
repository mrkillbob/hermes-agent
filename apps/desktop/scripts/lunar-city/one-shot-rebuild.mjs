#!/usr/bin/env node

/**
 * One-command authoring rebuild: curate quarantined source kits, record
 * provenance, then build and render a completely new Blender world.
 */
import { cp, mkdir, readdir, readFile, stat, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { execFile, spawn } from 'node:child_process'
import { promisify } from 'node:util'
import { basename, dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileAsync = promisify(execFile)
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..')
const quarantineRoot = process.env.LUNAR_CITY_ONE_SHOT_ROOT ?? '/private/tmp/lunar-city-one-shot'
const sourceRoot = join(quarantineRoot, 'sources')
const existingKit = process.env.LUNAR_CITY_CURATED_KIT ?? '/private/tmp/lunar-city-open-asset-curated'
const mergedKit = join(quarantineRoot, 'merged-kit')
const assetRoot = join(repoRoot, 'apps/desktop/public/lunar-city/v2/models')
const sceneContract = join(repoRoot, 'apps/desktop/public/lunar-city/v2/scene-contract.v1.json')
const blenderLauncher = join(repoRoot, 'apps/desktop/scripts/lunar-city/run-one-shot-rebuild.sh')
const output = process.env.LUNAR_CITY_ONE_SHOT_BLEND ?? join(quarantineRoot, 'lunar-city-rebuilt.blend')
const renderOutput = process.env.LUNAR_CITY_ONE_SHOT_RENDER ?? join(quarantineRoot, 'lunar-city-rebuilt.png')
const rfxTexture = join(sourceRoot, 'rfx-blender-asset-library/Textures/Img/concrete_1.jpg')

const args = new Set(process.argv.slice(2))
if (args.has('--help')) {
  console.log('Usage: node apps/desktop/scripts/lunar-city/one-shot-rebuild.mjs')
  console.log('Optional env: LUNAR_CITY_ONE_SHOT_ROOT, LUNAR_CITY_BLENDER, LUNAR_CITY_ONE_SHOT_BLEND, LUNAR_CITY_ONE_SHOT_RENDER')
  process.exit(0)
}

async function walk(directory) {
  const result = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = join(directory, entry.name)
    if (entry.isDirectory()) result.push(...await walk(full))
    else result.push(full)
  }
  return result
}

async function sha256(path) {
  return createHash('sha256').update(await readFile(path)).digest('hex')
}

async function gitHead(path) {
  try {
    const { stdout } = await execFileAsync('git', ['-C', path, 'rev-parse', 'HEAD'])
    return stdout.trim()
  } catch {
    return null
  }
}

async function mergeSources() {
  await mkdir(quarantineRoot, { recursive: true })
  await mkdir(mergedKit, { recursive: true })
  try {
    await cp(existingKit, mergedKit, { recursive: true, force: true })
  } catch {
    // The prior local curation is optional; cloned source kits still run.
  }
  const sourceDirs = [
    ['selene-isru', join(sourceRoot, 'selene-isru/packages/app/src/assets/models')],
    ['modkit', join(sourceRoot, 'modkit/exports/GLTF')],
    ['kaykit', join(sourceRoot, 'kaykit-space-base-bits/addons/kaykit_space_base_bits/Assets/fbx(unity)')],
  ]
  for (const [prefix, directory] of sourceDirs) {
    try {
      for (const file of await walk(directory)) {
        if (!new Set(['.glb', '.gltf', '.fbx', '.obj']).has(extname(file).toLowerCase())) continue
        await cp(file, join(mergedKit, `${prefix}__${basename(file)}`), { force: true })
      }
    } catch {
      // A source may be absent on a future run; its absence is recorded below.
    }
  }
  const files = []
  for (const file of await walk(mergedKit)) {
    if (file.endsWith('one-shot-sources.receipt.json')) continue
    const stat = await readFile(file)
    files.push({ path: file.slice(mergedKit.length + 1), bytes: stat.byteLength, sha256: createHash('sha256').update(stat).digest('hex') })
  }
  files.sort((a, b) => a.path.localeCompare(b.path))
  const repositories = [
    ['selene-isru', 'https://github.com/dogum/selene-isru', 'CC0 assets per repository asset license; code license reviewed separately'],
    ['rfx-blender-asset-library', 'https://github.com/Pixelguru26/RFX-Blender-Asset-Library', 'CC0 repository claim; source materials remain quarantine-only'],
    ['modkit', 'https://github.com/JaronKBragg7337/asset-pack-ue-threejs-blender-unity', 'CC0 repository claim; source meshes remain quarantine-only'],
    ['kaykit-space-base-bits', 'https://github.com/KayKit-Game-Assets/KayKit-Space-Base-Bits-1.0', 'CC0 official asset repository'],
  ]
  const receipt = { version: 1, generatedAt: new Date().toISOString(), quarantineRoot, mergedKit, existingKit, repositories: [], files, reviewRequired: true, distribution: 'quarantine-only' }
  for (const [name, url, license] of repositories) {
    const head = await gitHead(join(sourceRoot, name))
    receipt.repositories.push({ name, url, head, license, licenseStatus: 'review-required' })
  }
  await writeFile(join(quarantineRoot, 'one-shot-sources.receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`)
  return receipt
}

async function runBlender() {
  await new Promise((resolvePromise, reject) => {
    const child = spawn(blenderLauncher, [
      '--asset-root', assetRoot,
      '--scene-contract', sceneContract,
      '--asset-kit-dir', mergedKit,
      '--rfx-texture', rfxTexture,
      '--output', output,
      '--render-output', renderOutput,
    ], { cwd: repoRoot, stdio: 'inherit', env: process.env })
    child.once('error', reject)
    child.once('exit', async code => {
      if (code !== 0) {
        reject(new Error(`Blender rebuild exited with ${code}`))
        return
      }
      try {
        await stat(output)
        const receipt = JSON.parse(await readFile(output.replace(/\.blend$/i, '.receipt.json'), 'utf8'))
        if (receipt.status !== 'authoring-rebuild' || receipt.existingModelsImported !== false)
          throw new Error('Blender rebuild receipt failed the empty-scene contract')
        resolvePromise()
      } catch (error) {
        reject(new Error(`Blender reported success without a valid rebuild artifact: ${error.message}`))
      }
    })
  })
}

const receipt = await mergeSources()
console.log(JSON.stringify({ stage: 'sources', mergedFiles: receipt.files.length, repositories: receipt.repositories.map(repo => ({ name: repo.name, head: repo.head })) }, null, 2))
await runBlender()
console.log(JSON.stringify({ stage: 'complete', blend: output, render: renderOutput, receipt: join(quarantineRoot, 'one-shot-sources.receipt.json') }, null, 2))
