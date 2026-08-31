#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { createServer } from 'node:net'
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
const BRIDGE_VERSION = 1

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
  deps = {
    existsSync,
    readFileSync,
    statSync,
    readInfoPlist: path =>
      JSON.parse(execFileSync('/usr/bin/plutil', ['-convert', 'json', '-o', '-', path], { encoding: 'utf8' }))
  }
) {
  if (typeof binaryPath !== 'string' || binaryPath.length === 0)
    throw new Error('packaged Hermes binary path is required')
  if (!SHA_PATTERN.test(expectedGitSha ?? '')) throw new Error('expected packaged git SHA must be exact')
  if (isDevElectron(binaryPath))
    throw new Error('dev Electron is forbidden; target an electron-builder packaged Hermes binary')
  if (
    platform === 'darwin' &&
    !normalize(resolve(binaryPath)).endsWith(normalize('/Hermes.app/Contents/MacOS/Hermes'))
  ) {
    throw new Error('packaged Hermes bundle identity requires Hermes.app/Contents/MacOS/Hermes')
  }
  if (!deps.existsSync(binaryPath) || !deps.statSync(binaryPath).isFile()) {
    throw new Error(`packaged Hermes binary is missing: ${binaryPath}`)
  }
  if (platform !== 'win32' && (deps.statSync(binaryPath).mode & 0o111) === 0) {
    throw new Error(`packaged Hermes binary is not executable: ${binaryPath}`)
  }
  const resourcesPath = resourcesPathFor(binaryPath, platform)
  if (platform === 'darwin') {
    const infoPath = join(dirname(dirname(binaryPath)), 'Info.plist')
    if (!deps.existsSync(infoPath)) throw new Error(`packaged Hermes Info.plist is missing: ${infoPath}`)
    const info = deps.readInfoPlist(infoPath)
    if (
      info?.CFBundleIdentifier !== 'com.nousresearch.hermes' ||
      info?.CFBundleExecutable !== 'Hermes' ||
      info?.CFBundleName !== 'Hermes'
    ) {
      throw new Error('packaged Hermes bundle identity does not match electron-builder Hermes')
    }
  }
  const stampPath = join(resourcesPath, 'install-stamp.json')
  if (!deps.existsSync(stampPath)) throw new Error(`packaged install-stamp.json is missing: ${stampPath}`)
  const buildStamp = parseBuildStamp(deps.readFileSync(stampPath, 'utf8'))
  if (buildStamp.commit.toLowerCase() !== expectedGitSha.toLowerCase()) {
    throw new Error(`packaged install-stamp commit ${buildStamp.commit} does not match ${expectedGitSha}`)
  }
  return { binaryPath: resolve(binaryPath), resourcesPath, stampPath, buildStamp }
}

/** Build the direct packaged-binary launch contract without mutating the filesystem. */
export function createIsolatedLaunchPlan({ binaryPath, debugPort, tempRoot, runId, launchNonce }) {
  if (!Number.isInteger(debugPort) || debugPort < 1024 || debugPort > 65535) {
    throw new Error('isolated CDP debug port must be an integer from 1024 through 65535')
  }
  if (typeof tempRoot !== 'string' || tempRoot.length === 0) throw new Error('isolated temp root is required')
  const safeRunId = String(runId || 'run').replace(/[^a-z0-9._-]/giu, '-')
  const hermesHome = join(tempRoot, 'hermes-home')
  const userDataDir = join(tempRoot, 'user-data')
  const launchEnv = {}
  for (const key of [
    'PATH',
    'TMPDIR',
    'TEMP',
    'TMP',
    'SystemRoot',
    'WINDIR',
    'LANG',
    'LC_ALL',
    'SHELL',
    'USER',
    'LOGNAME',
    'DISPLAY',
    'WAYLAND_DISPLAY',
    'XDG_RUNTIME_DIR',
    'DBUS_SESSION_BUS_ADDRESS'
  ]) {
    if (process.env[key] !== undefined) launchEnv[key] = process.env[key]
  }
  return {
    command: resolve(binaryPath),
    args: [`--user-data-dir=${userDataDir}`, `--remote-debugging-port=${debugPort}`],
    cwd: dirname(resolve(binaryPath)),
    env: {
      ...launchEnv,
      HERMES_HOME: hermesHome,
      HERMES_DESKTOP_APP_NAME: `Hermes Lunar City Perf ${safeRunId}`,
      HERMES_DESKTOP_CDP_PORT: String(debugPort),
      HERMES_LUNAR_CITY_PERF_ACCEPTANCE: '1',
      HERMES_LUNAR_CITY_PERF_NONCE: launchNonce
    },
    paths: { hermesHome, userDataDir, tempRoot }
  }
}

function defaultLaunch(plan) {
  for (const path of [plan.paths.hermesHome, plan.paths.userDataDir]) mkdirSync(path, { recursive: true })
  return spawn(plan.command, plan.args, { cwd: plan.cwd, env: plan.env, stdio: 'inherit' })
}

export function validateBridgeHandshake(handshake, expected) {
  if (!handshake || typeof handshake !== 'object')
    throw new Error('bridge_unavailable: versioned packaged bridge absent')
  if (handshake.bridgeVersion !== BRIDGE_VERSION) throw new Error('bridge_mismatch: bridge version')
  if (typeof handshake.launchNonce !== 'string' || handshake.launchNonce !== expected.launchNonce)
    throw new Error('bridge_mismatch: launch nonce')
  if (handshake.buildSha !== expected.buildSha) throw new Error('bridge_mismatch: build SHA')
  if (handshake.packaged !== true) throw new Error('bridge_mismatch: packaged status')
  if (handshake.mainPid !== expected.mainPid) throw new Error('bridge_mismatch: main PID')
  if (!handshake.rendererIdentity || !Number.isInteger(handshake.rendererIdentity.pid))
    throw new Error('bridge_mismatch: renderer lifetime')
  if (
    !Array.isArray(handshake.supportedPhases) ||
    !['baseline-shell', 'mounted-city'].every(phase => handshake.supportedPhases.includes(phase))
  )
    throw new Error('bridge_mismatch: phase support')
  if (handshake.processMetricsSource !== 'electron.app.getAppMetrics')
    throw new Error('bridge_mismatch: process metrics source')
  return handshake
}

async function defaultConnectCdp({ port, expectedHandshake }) {
  const deadline = Date.now() + 60_000
  let lastMismatch = null
  while (Date.now() < deadline) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
      for (const target of targets.filter(item => item.type === 'page' && item.webSocketDebuggerUrl)) {
        const cdp = await CDP.open(target.webSocketDebuggerUrl)
        try {
          const handshake = await cdp.eval(`(() => {
            const bridge = window.__LUNAR_CITY_PERF__
            return bridge && typeof bridge.handshake === 'function'
              ? bridge.handshake(${JSON.stringify({ bridgeVersion: BRIDGE_VERSION, launchNonce: expectedHandshake.launchNonce })})
              : null
          })()`)
          validateBridgeHandshake(handshake, expectedHandshake)
          return { cdp, handshake }
        } catch (error) {
          if (error instanceof Error && error.message.startsWith('bridge_mismatch:')) lastMismatch = error
          cdp.close()
        }
      }
    } catch {
      // No matching launched target yet.
    }
    await new Promise(resolveSleep => setTimeout(resolveSleep, 250))
  }
  if (lastMismatch) throw lastMismatch
  throw new Error('bridge_unavailable: no CDP target matched launch nonce, build SHA, and main PID')
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

export async function reserveUniqueDebugPort() {
  const server = createServer()
  await new Promise((resolveListen, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolveListen)
  })
  const address = server.address()
  return {
    port: address.port,
    release: () =>
      new Promise((resolveClose, reject) => server.close(error => (error ? reject(error) : resolveClose())))
  }
}

async function waitForChild(child) {
  if (!child) return
  if (typeof child.waitForExit === 'function') return child.waitForExit()
  if (child.exitCode !== null) return
  await new Promise(resolveExit => {
    const timeout = setTimeout(resolveExit, 2_000)
    child.once('exit', () => {
      clearTimeout(timeout)
      resolveExit()
    })
  })
}

export async function cleanupIsolatedRun({ cdp, child, tempRoot }, injected = {}) {
  const errors = []
  const attempt = async action => {
    try {
      await action()
    } catch (error) {
      errors.push(error)
    }
  }
  await attempt(() => cdp?.close())
  await attempt(() => child?.kill('SIGTERM'))
  await attempt(() => waitForChild(child))
  await attempt(() => child?.kill('SIGKILL'))
  await attempt(() => (injected.removeTemp ?? (path => rmSync(path, { recursive: true, force: true })))(tempRoot))
  if (errors.length) throw new AggregateError(errors, 'cleanup failed')
}

async function capturePhase({
  cdp,
  phase,
  warmupDurationMs,
  sampleCount,
  sampleIntervalMs,
  processProbe,
  rendererProbe,
  clock
}) {
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
  return {
    envelopeVersion: LUNAR_CITY_PHASE_ENVELOPE_VERSION,
    phase,
    warmupDurationMs,
    rendererIdentity,
    samples
  }
}

/**
 * Launch and sample a packaged app through injectable boundaries. This returns
 * raw evidence only; the receipt validator remains the acceptance authority.
 */
export async function runPackagedLunarCityMeasurement(options, injected = {}) {
  const deps = {
    inspectTarget: inspectPackagedTarget,
    makeTempRoot: () => mkdtempSync(join(tmpdir(), 'hermes-lunar-city-perf-')),
    makeLaunchNonce: randomUUID,
    reserveDebugPort: reserveUniqueDebugPort,
    launch: defaultLaunch,
    connectCdp: defaultConnectCdp,
    preparePhase: defaultPreparePhase,
    processProbe: defaultProcessProbe,
    rendererProbe: defaultRendererProbe,
    clock: defaultClock,
    cleanup: cleanupIsolatedRun,
    ...injected
  }
  const target = deps.inspectTarget({ binaryPath: options.binaryPath, expectedGitSha: options.expectedGitSha })
  const tempRoot = deps.makeTempRoot()
  const runId = options.runId ?? `${process.pid}-${deps.clock.now()}`
  const launchNonce = deps.makeLaunchNonce()
  const reservation = await deps.reserveDebugPort()
  const plan = createIsolatedLaunchPlan({
    binaryPath: target.binaryPath,
    debugPort: reservation.port,
    tempRoot,
    runId,
    launchNonce
  })
  let child
  let cdp
  try {
    await reservation.release()
    child = await deps.launch(plan)
    const connected = await deps.connectCdp({
      port: reservation.port,
      child,
      expectedHandshake: { launchNonce, buildSha: target.buildStamp.commit, mainPid: child.pid }
    })
    validateBridgeHandshake(connected.handshake, {
      launchNonce,
      buildSha: target.buildStamp.commit,
      mainPid: child.pid
    })
    cdp = connected.cdp
    const sampleCount = options.sampleCount ?? 4
    const sampleIntervalMs = options.sampleIntervalMs ?? 10_000
    const warmupDurationMs = options.warmupDurationMs ?? 30_000
    await deps.preparePhase(cdp, 'baseline-shell')
    if (warmupDurationMs > 0) await deps.clock.sleep(warmupDurationMs)
    const baselineShell = await capturePhase({
      cdp,
      phase: 'baseline-shell',
      warmupDurationMs,
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
      warmupDurationMs,
      sampleCount,
      sampleIntervalMs,
      processProbe: deps.processProbe,
      rendererProbe: deps.rendererProbe,
      clock: deps.clock
    })
    const rawProvenance = { provenanceVersion: LUNAR_CITY_PROVENANCE_VERSION, baselineShell, mountedCity }
    const derived = deriveRawSamplesFromProvenance(rawProvenance, { scenario: options.scenario })
    if (
      derived.rendererIdentity.pid !== connected.handshake.rendererIdentity.pid ||
      derived.rendererIdentity.startedAtMs !== connected.handshake.rendererIdentity.startedAtMs
    ) {
      throw new Error('bridge_mismatch: sampled renderer lifetime differs from handshake')
    }
    rawProvenance.bridgeHandshake = connected.handshake
    return {
      buildStamp: target.buildStamp,
      package: { binaryPath: target.binaryPath },
      bridgeHandshake: connected.handshake,
      rawProvenance,
      ...derived,
      evidenceClass: Object.keys(injected).length ? 'deterministic' : 'raw-packaged-capture',
      packagedPerformanceEligible: false
    }
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
    else if (value === '--scenario') result.scenario = argv[++index]
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
