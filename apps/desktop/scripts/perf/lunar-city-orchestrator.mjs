#!/usr/bin/env node

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { runPackagedLunarCityMeasurement } from './lunar-city-runner.mjs'
import { RECEIPT_VERSION, SCENARIOS, SCENARIO_PROFILES, summarizeRawSamples, validateReceipt } from './lunar-city.mjs'

const EXACT_SHA = /^[a-f0-9]{40}$/iu
const POPULATION_SCENARIOS = new Set(['25-active', '100-active', '250-lod'])
const REQUIRED_METADATA = Object.freeze([
  'architecture',
  'backendMode',
  'chromiumVersion',
  'electronVersion',
  'gpuAdapter',
  'hardwareModel',
  'os',
  'powerState'
])

function finitePositive(value, label) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    throw new Error(`${label} must be a positive finite number`)
  }
  return value
}

function validateFixture(fixture, scenario) {
  if (!fixture) return undefined
  if (
    fixture.contractVersion !== 1 ||
    fixture.evidenceClass !== 'fake-backend-packaged' ||
    ![25, 100, 250].includes(fixture.expectedPopulation) ||
    !['supported', 'unsupported'].includes(fixture.subagentEmission)
  ) {
    throw new Error('fixture connection contract is malformed')
  }
  const root = resolve(fixture.root ?? '')
  if (root === resolve('/') || root.length < 2) throw new Error('fixture root must be isolated')
  for (const field of ['hermesHome', 'populationContractPath', 'userDataDir']) {
    const path = resolve(fixture[field] ?? '')
    if (path !== root && !path.startsWith(`${root}/`)) throw new Error(`fixture ${field} escapes its isolated root`)
  }
  if (POPULATION_SCENARIOS.has(scenario) && fixture.subagentEmission !== 'supported') {
    throw new Error(`${scenario} is blocked: authenticated subagent emission is unavailable`)
  }
  return Object.freeze({ ...fixture, root })
}

/** Resolve an exact clock policy. Overrides may lengthen a run, never shorten it. */
export function resolveScenarioMeasurement(scenario, overrides = {}) {
  const profile = SCENARIO_PROFILES[scenario]
  if (!profile) throw new Error(`unknown Lunar City scenario: ${String(scenario)}`)
  const minimumDurationMs = profile.durationMs
  const minimumWarmupMs = profile.warmupMs
  const durationMs = overrides.durationMs ?? minimumDurationMs
  const warmupDurationMs = overrides.warmupDurationMs ?? minimumWarmupMs
  if (!Number.isInteger(durationMs) || durationMs < minimumDurationMs) {
    throw new Error(`${scenario} duration cannot shorten below ${minimumDurationMs}ms`)
  }
  if (!Number.isInteger(warmupDurationMs) || warmupDurationMs < minimumWarmupMs) {
    throw new Error(`${scenario} warmup cannot shorten below ${minimumWarmupMs}ms`)
  }
  const requestedCadence = overrides.sampleIntervalMs ?? profile.maxCadenceMs
  const maxCadence = profile.maxCadenceMs
  if (!Number.isInteger(requestedCadence) || requestedCadence <= 0 || requestedCadence > maxCadence) {
    throw new Error(`${scenario} cadence must be an integer from 1 through ${maxCadence}ms`)
  }
  // Use a divisor so the final timestamp lands exactly on the required duration.
  const intervalCount = Math.max(2, Math.ceil(durationMs / requestedCadence))
  if (durationMs % intervalCount !== 0) {
    throw new Error(`${scenario} duration must divide evenly into its sampling cadence`)
  }
  const sampleIntervalMs = durationMs / intervalCount
  const fixture = validateFixture(overrides.fixture, scenario)
  return Object.freeze({
    durationMs,
    fixture,
    sampleCount: intervalCount + 1,
    sampleIntervalMs,
    warmupDurationMs
  })
}

function average(values) {
  return values.reduce((total, value) => total + value, 0) / values.length
}

function baselineMetric(capture, selector) {
  const values = capture.rawProvenance?.baselineShell?.samples?.map(selector)
  if (!Array.isArray(values) || values.length === 0 || values.some(value => !Number.isFinite(value))) {
    throw new Error('raw baseline provenance is incomplete')
  }
  return average(values)
}

function validateMetadata(metadata) {
  if (!metadata || typeof metadata !== 'object') throw new Error('operator environment metadata is required')
  for (const field of REQUIRED_METADATA) {
    if (typeof metadata[field] !== 'string' || metadata[field].length === 0) {
      throw new Error(`operator environment metadata ${field} is required`)
    }
  }
  finitePositive(metadata.displayScale, 'operator displayScale')
  finitePositive(metadata.windowSize?.width, 'operator window width')
  finitePositive(metadata.windowSize?.height, 'operator window height')
  return metadata
}

/** Assemble raw capture into the receipt schema. Eligibility remains validator-owned. */
export function assembleLunarCityReceipt({ capture, evidenceClass, metadata, scenario, timestamp }) {
  if (!['fake-backend-packaged', 'supervised-live'].includes(evidenceClass)) {
    throw new Error('orchestration evidence class must be fake-backend-packaged or supervised-live')
  }
  validateMetadata(metadata)
  if (!EXACT_SHA.test(capture?.buildStamp?.commit ?? '')) throw new Error('capture build stamp is not exact')
  const claims = capture.mountedClaims
  if (!claims || !capture.rawProvenance || !capture.rawSamples) throw new Error('raw packaged capture is incomplete')
  const summaries = summarizeRawSamples(capture.rawSamples)
  const runtimeEnvironment = claims.environment ?? {}
  const environment = {
    ...metadata,
    cityPopulated: claims.population.observed > 0,
    electronMode: runtimeEnvironment.electronMode,
    gpuEnabled: runtimeEnvironment.gpuEnabled
  }
  const receipt = {
    receiptVersion: RECEIPT_VERSION,
    evidenceClass,
    scenario,
    gitSha: capture.buildStamp.commit,
    buildStamp: capture.buildStamp,
    timestamp,
    environment,
    qualityTier: claims.qualityTier,
    internalRenderScale: claims.internalRenderScale,
    warmupDurationMs: claims.warmupDurationMs,
    population: claims.population,
    cameraState: claims.cameraState,
    dialogueState: claims.dialogueState,
    lifecycleState: claims.lifecycleState,
    measurement: {
      durationMs: claims.durationMs,
      sampleIntervalMs: capture.sampleTimestampsMs[1] - capture.sampleTimestampsMs[0],
      sampleTimestampsMs: capture.sampleTimestampsMs
    },
    rawSamples: capture.rawSamples,
    rawProvenance: capture.rawProvenance,
    summaries,
    fpsCap: 30,
    renderFrames: summaries.maxRenderFrames,
    processCpuBaselinePp: baselineMetric(capture, sample =>
      sample.processMetrics.reduce((total, row) => total + row.cpu.percentCPUUsage, 0)
    ),
    processCpuDeltaPp: summaries.avgCpuDeltaPp,
    gpuMemoryBaselineMiB: baselineMetric(capture, sample => sample.rendererMetrics.gpuMemoryMiB),
    gpuMemoryDeltaMiB: summaries.avgGpuMemoryDeltaMiB,
    residentMemoryDriftMiB: summaries.residentMemoryDriftMiB,
    drawCalls: summaries.maxDrawCalls,
    visibleTriangles: summaries.maxVisibleTriangles,
    activeAnimations: summaries.maxActiveAnimations,
    entities: summaries.maxEntities,
    textures: summaries.maxTextures,
    listeners: summaries.maxListeners,
    timers: summaries.maxTimers,
    disposal: scenario === 'disposal' ? claims.lifecycleState : 'not-applicable',
    recovery: scenario === 'context-loss-recovery' ? claims.lifecycleState : 'not-applicable',
    ...(claims.interactionUsable === true ? { interactionUsable: true } : {})
  }
  // The validator defines canonical outcome fields. The assembler never writes
  // packagedPerformanceEligible; no producer may self-declare acceptance.
  const first = validateReceipt({ ...receipt, pass: true, errors: [] })
  const canonicalErrors = first.errors.filter(error => !/^(?:pass|errors) contradicts canonical outcome/u.test(error))
  return { ...receipt, pass: canonicalErrors.length === 0, errors: canonicalErrors }
}

function defaultWriteJson(path, value) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' })
}

export async function orchestrateLunarCityAcceptance(options, injected = {}) {
  const deps = {
    capture: runPackagedLunarCityMeasurement,
    mkdir: path => mkdirSync(path, { recursive: true }),
    nowIso: () => new Date().toISOString(),
    validate: validateReceipt,
    writeJson: defaultWriteJson,
    ...injected
  }
  if (!EXACT_SHA.test(options.expectedGitSha ?? '')) throw new Error('expected git SHA must be exact')
  if (!Array.isArray(options.scenarios) || options.scenarios.length === 0)
    throw new Error('at least one scenario is required')
  if (options.evidenceClass === 'supervised-live') {
    throw new Error(
      'supervised-live orchestration is unavailable: the packaged runner has no supported real-gateway preseed API'
    )
  }
  if (options.evidenceClass !== 'fake-backend-packaged') {
    throw new Error('packaged orchestration requires evidence class fake-backend-packaged')
  }
  validateMetadata(options.metadata)
  deps.mkdir(options.outputDirectory)
  const results = []
  for (const scenario of options.scenarios) {
    const policy = resolveScenarioMeasurement(scenario, {
      ...options.measurementOverrides?.[scenario],
      fixture: options.fixture
    })
    const capture = await deps.capture({
      binaryPath: options.binaryPath,
      expectedGitSha: options.expectedGitSha,
      fixture: policy.fixture,
      sampleCount: policy.sampleCount,
      sampleIntervalMs: policy.sampleIntervalMs,
      scenario,
      warmupDurationMs: policy.warmupDurationMs
    })
    const prefix = `${scenario}-${capture.buildStamp.commit}`
    deps.writeJson(join(options.outputDirectory, `${prefix}.raw.json`), capture)
    const receipt = assembleLunarCityReceipt({
      capture,
      evidenceClass: options.evidenceClass,
      metadata: options.metadata,
      scenario,
      timestamp: deps.nowIso()
    })
    deps.writeJson(join(options.outputDirectory, `${prefix}.receipt.json`), receipt)
    const validation = deps.validate(receipt)
    results.push({ receipt, scenario, validation })
    if (!validation.ok) throw new Error(`${scenario} receipt validation failed: ${validation.errors.join('; ')}`)
    if (!validation.packagedPerformanceEligible) throw new Error(`${scenario} receipt is not eligible for acceptance`)
  }
  return results
}

function parseJsonFile(path, label) {
  if (!path) throw new Error(`${label} JSON path is required`)
  return JSON.parse(readFileSync(path, 'utf8'))
}

function parseArgs(argv) {
  const result = { evidenceClass: 'fake-backend-packaged', scenarios: [] }
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index]
    if (value === '--binary') result.binaryPath = argv[++index]
    else if (value === '--sha') result.expectedGitSha = argv[++index]
    else if (value === '--output') result.outputDirectory = argv[++index]
    else if (value === '--metadata') result.metadata = parseJsonFile(argv[++index], 'metadata')
    else if (value === '--fixture') result.fixture = parseJsonFile(argv[++index], 'fixture')
    else if (value === '--scenario') result.scenarios.push(argv[++index])
    else if (value === '--all') result.scenarios = [...SCENARIOS]
    else if (value === '--help') result.help = true
    else throw new Error(`unknown Lunar City acceptance argument: ${value}`)
  }
  return result
}

function usage() {
  return `usage: node ${basename(fileURLToPath(import.meta.url))} --binary <packaged Hermes> --sha <exact SHA> --output <dir> --metadata <json> [--fixture <json>] (--all | --scenario <name>...)\n\nThis command performs capture -> receipt assembly -> validation for fake-backend-packaged evidence. It refuses missing GPU/metrics, skips, ineligible receipts, shortened scenario clocks, dirty/mismatched packages, and unsupported exact-population fixtures. Supervised-live evidence is deliberately unavailable until the runner has a supported real-gateway preseed API.\n`
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  if (options.help) {
    process.stdout.write(usage())
    return
  }
  await orchestrateLunarCityAcceptance(options)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(
      `[perf:lunar-city:accept] REFUSED: ${error instanceof Error ? error.message : String(error)}\n`
    )
    process.exitCode = 1
  })
}
