#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { CDP } from './lib/cdp.mjs'
import {
  LUNAR_CITY_PHASE_ENVELOPE_VERSION,
  LUNAR_CITY_PROVENANCE_VERSION,
  deriveRawSamplesFromProvenance
} from './lib/lunar-city-provenance.mjs'

const SHA_PATTERN = /^[0-9a-f]{40}$/i
const CANONICAL_ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/

function resourcesPathFor(binaryPath, platform = process.platform) {
  const binary = resolve(binaryPath)
  if (platform === 'darwin') {
    const marker = `${normalize('.app')}/Contents/MacOS/`
    const index = binary.lastIndexOf(marker)
    if (index < 0) throw new Error('packaged Hermes macOS binary must live under Hermes.app/Contents/MacOS')
    return join(binary.slice(0, index + marker.length - 'MacOS/'.length), 'Resources')
  }
  return join(dirname(binary), 'resources')
}

function isDevElectron(binaryPath) {
  const normalized = normalize(binaryPath).toLowerCase()
  return (
    basename(normalized).replace(/\.exe$/u, '') === 'electron' ||
    normalized.includes(`${normalize('node_modules/electron')}/`) ||
    normalized.includes(`${normalize('electron/dist')}/`)
  )
}

function parseBuildStamp(raw) {
  let stamp
  try {
    stamp = JSON.parse(raw)
  } catch {
    throw new Error('packaged install-stamp.json is malformed')
  }
  if (!stamp || typeof stamp !== 'object' || Array.isArray(stamp) || stamp.schemaVersion !== 1) {
    throw new Error('packaged install-stamp.json has an unsupported schema')
  }
  if (!SHA_PATTERN.test(stamp.commit ?? '')) throw new Error('packaged install-stamp commit is not an exact SHA')
  if (!CANONICAL_ISO_UTC.test(stamp.builtAt ?? '')) throw new Error('packaged install-stamp builtAt is invalid')
  if (stamp.dirty !== false) throw new Error('packaged install-stamp is dirty')
  if (stamp.source === 'fallback' || /^0{40}$/u.test(stamp.commit)) {
    throw new Error('packaged install-stamp uses fallback provenance')
  }
  if (!['local', 'ci'].includes(stamp.source)) throw new Error('packaged install-stamp source is not pinned')
  return stamp
}

/** Resolve and verify a pre-existing electron-builder package before launch. */
export function inspectPackagedTarget(
  { binaryPath, expectedGitSha, platform = process.platform },
  deps = { existsSync, readFileSync, statSync }
) {
  if (typeof binaryPath !== 'string' || binaryPath.length === 0)
    throw new Error('packaged Hermes binary path is required')
  if (!SHA_PATTERN.test(expectedGitSha ?? '')) throw new Error('expected packaged git SHA must be exact')
  if (isDevElectron(binaryPath))
    throw new Error('dev Electron is forbidden; target an electron-builder packaged Hermes binary')
  if (!deps.existsSync(binaryPath) || !deps.statSync(binaryPath).isFile()) {
    throw new Error(`packaged Hermes binary is missing: ${binaryPath}`)
  }
  const resourcesPath = resourcesPathFor(binaryPath, platform)
  const stampPath = join(resourcesPath, 'install-stamp.json')
  if (!deps.existsSync(stampPath)) throw new Error(`packaged install-stamp.json is missing: ${stampPath}`)
  const buildStamp = parseBuildStamp(deps.readFileSync(stampPath, 'utf8'))
  if (buildStamp.commit.toLowerCase() !== expectedGitSha.toLowerCase()) {
    throw new Error(`packaged install-stamp commit ${buildStamp.commit} does not match ${expectedGitSha}`)
  }
  return { binaryPath: resolve(binaryPath), resourcesPath, stampPath, buildStamp }
}

/** Build the direct packaged-binary launch contract without mutating the filesystem. */
export function createIsolatedLaunchPlan({ binaryPath, debugPort, tempRoot, runId }) {
  if (!Number.isInteger(debugPort) || debugPort < 1024 || debugPort > 65535) {
    throw new Error('isolated CDP debug port must be an integer from 1024 through 65535')
  }
  if (typeof tempRoot !== 'string' || tempRoot.length === 0) throw new Error('isolated temp root is required')
  const safeRunId = String(runId || 'run').replace(/[^a-z0-9._-]/giu, '-')
  const hermesHome = join(tempRoot, 'hermes-home')
  const userDataDir = join(tempRoot, 'user-data')
  const launchEnv = { ...process.env }
  delete launchEnv.HERMES_DESKTOP_DEV_SERVER
  delete launchEnv.ELECTRON_RUN_AS_NODE
  return {
    command: resolve(binaryPath),
    args: [`--user-data-dir=${userDataDir}`, `--remote-debugging-port=${debugPort}`],
    cwd: dirname(resolve(binaryPath)),
    env: {
      ...launchEnv,
      HERMES_HOME: hermesHome,
      HERMES_DESKTOP_APP_NAME: `Hermes Lunar City Perf ${safeRunId}`,
      HERMES_DESKTOP_CDP_PORT: String(debugPort)
    },
    paths: { hermesHome, userDataDir, tempRoot }
  }
}

function defaultLaunch(plan) {
  for (const path of [plan.paths.hermesHome, plan.paths.userDataDir]) mkdirSync(path, { recursive: true })
  return spawn(plan.command, plan.args, { cwd: plan.cwd, env: plan.env, stdio: 'inherit' })
}

async function defaultConnectCdp({ port }) {
  return CDP.connect({ port, timeoutMs: 60_000 })
}

async function defaultPreparePhase(cdp, phase) {
  const method = phase === 'baseline-shell' ? 'prepareBaselineShell' : 'mountCity'
  const result = await cdp.eval(`(() => {
    const probe = window.__LUNAR_CITY_PERF__
    if (!probe || typeof probe.${method} !== 'function') return { ok: false, reason: 'probe-unavailable' }
    return Promise.resolve(probe.${method}()).then(() => ({ ok: true }))
  })()`)
  if (!result?.ok) throw new Error(`required packaged Lunar City performance probe is unavailable for ${phase}`)
}

async function defaultRendererProbe(cdp) {
  const result = await cdp.eval(`(() => {
    const probe = window.__LUNAR_CITY_PERF__
    return probe && typeof probe.snapshot === 'function' ? probe.snapshot() : null
  })()`)
  if (!result) throw new Error('required packaged Lunar City renderer metrics are unavailable')
  return result
}

async function defaultProcessProbe(cdp) {
  const result = await cdp.eval(`(() => {
    const probe = window.__LUNAR_CITY_PERF__
    return probe && typeof probe.processMetrics === 'function' ? probe.processMetrics() : null
  })()`)
  if (!result) throw new Error('required packaged app.getAppMetrics process rows are unavailable')
  return result
}

const defaultClock = {
  now: () => Date.now(),
  sleep: milliseconds => new Promise(resolveSleep => setTimeout(resolveSleep, milliseconds))
}

async function capturePhase({ cdp, phase, sampleCount, sampleIntervalMs, processProbe, rendererProbe, clock }) {
  const firstRenderer = await rendererProbe(cdp)
  const rendererIdentity = { pid: firstRenderer.rendererPid, startedAtMs: firstRenderer.rendererStartedAtMs }
  const startedAt = clock.now()
  const samples = []
  for (let index = 0; index < sampleCount; index += 1) {
    const rendererMetrics = index === 0 ? firstRenderer : await rendererProbe(cdp)
    const processMetrics = await processProbe(cdp)
    samples.push({ timestampMs: clock.now() - startedAt, processMetrics, rendererMetrics })
    if (index + 1 < sampleCount) await clock.sleep(sampleIntervalMs)
  }
  return { envelopeVersion: LUNAR_CITY_PHASE_ENVELOPE_VERSION, phase, rendererIdentity, samples }
}

/**
 * Launch and sample a packaged app through injectable boundaries. This returns
 * raw evidence only; the receipt validator remains the acceptance authority.
 */
export async function runPackagedLunarCityMeasurement(options, injected = {}) {
  const deps = {
    inspectTarget: inspectPackagedTarget,
    makeTempRoot: () => mkdtempSync(join(tmpdir(), 'hermes-lunar-city-perf-')),
    launch: defaultLaunch,
    connectCdp: defaultConnectCdp,
    preparePhase: defaultPreparePhase,
    processProbe: defaultProcessProbe,
    rendererProbe: defaultRendererProbe,
    clock: defaultClock,
    cleanup: async ({ child, cdp, tempRoot }) => {
      cdp?.close()
      child?.kill('SIGTERM')
      rmSync(tempRoot, { recursive: true, force: true })
    },
    ...injected
  }
  const target = deps.inspectTarget({ binaryPath: options.binaryPath, expectedGitSha: options.expectedGitSha })
  const tempRoot = deps.makeTempRoot()
  const runId = options.runId ?? `${process.pid}-${deps.clock.now()}`
  const plan = createIsolatedLaunchPlan({
    binaryPath: target.binaryPath,
    debugPort: options.debugPort ?? 49321,
    tempRoot,
    runId
  })
  let child
  let cdp
  try {
    child = await deps.launch(plan)
    cdp = await deps.connectCdp({ port: options.debugPort ?? 49321, child })
    const sampleCount = options.sampleCount ?? 4
    const sampleIntervalMs = options.sampleIntervalMs ?? 10_000
    const warmupDurationMs = options.warmupDurationMs ?? 30_000
    await deps.preparePhase(cdp, 'baseline-shell')
    if (warmupDurationMs > 0) await deps.clock.sleep(warmupDurationMs)
    const baselineShell = await capturePhase({
      cdp,
      phase: 'baseline-shell',
      sampleCount,
      sampleIntervalMs,
      processProbe: deps.processProbe,
      rendererProbe: deps.rendererProbe,
      clock: deps.clock
    })
    await deps.preparePhase(cdp, 'mounted-city')
    if (warmupDurationMs > 0) await deps.clock.sleep(warmupDurationMs)
    const mountedCity = await capturePhase({
      cdp,
      phase: 'mounted-city',
      sampleCount,
      sampleIntervalMs,
      processProbe: deps.processProbe,
      rendererProbe: deps.rendererProbe,
      clock: deps.clock
    })
    const rawProvenance = { provenanceVersion: LUNAR_CITY_PROVENANCE_VERSION, baselineShell, mountedCity }
    const derived = deriveRawSamplesFromProvenance(rawProvenance)
    return { buildStamp: target.buildStamp, package: { binaryPath: target.binaryPath }, rawProvenance, ...derived }
  } finally {
    await deps.cleanup({ child, cdp, tempRoot })
  }
}

function parseArgs(argv) {
  const result = {}
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index]
    if (value === '--binary') result.binaryPath = argv[++index]
    else if (value === '--sha') result.expectedGitSha = argv[++index]
    else if (value === '--port') result.debugPort = Number(argv[++index])
    else throw new Error(`unknown Lunar City performance argument: ${value}`)
  }
  return result
}

async function main() {
  const result = await runPackagedLunarCityMeasurement(parseArgs(process.argv.slice(2)))
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(`[perf:lunar-city] REFUSED: ${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
