/**
 * Machine-readable Lunar City performance receipt contract.
 *
 * This module deliberately has no Electron or Babylon dependency. It validates
 * receipts produced by the packaged runner and recomputes outcome metrics from
 * the retained raw samples so a summary cannot be edited to hide a regression.
 */

import { readFileSync } from 'node:fs'

export const RECEIPT_VERSION = 1

export const EVIDENCE_CLASSES = Object.freeze(['deterministic', 'fake-backend-packaged', 'supervised-live'])

export const THRESHOLDS = Object.freeze({
  dormantCpuDeltaPp: 0.5,
  visibleIdleCpuDeltaPp: 3,
  active100CpuDeltaPp: 12,
  active250CpuDeltaPp: 18,
  activeFrameP95Ms: 33.3,
  activeWorldUpdateP95Ms: 6,
  maxGpuMemoryDeltaMiB: 256,
  balancedOverviewDrawCalls: 180,
  balancedOverviewTriangles: 1_500_000,
  balancedWorkerFocusDrawCalls: 220,
  balancedWorkerFocusTriangles: 2_000_000,
  stabilityResidentDriftMiB: 75,
  maxRawSamples: 10_000,
  maxRawValue: 1_000_000_000_000
})

const RAW_FIELDS = Object.freeze([
  'frameMs',
  'worldUpdateMs',
  'cpuDeltaPp',
  'gpuMemoryDeltaMiB',
  'residentMemoryMiB',
  'renderFrames',
  'drawCalls',
  'visibleTriangles',
  'activeAnimations',
  'entities',
  'textures',
  'listeners',
  'timers'
])

const NON_NEGATIVE_RAW_FIELDS = new Set(RAW_FIELDS)
const MONOTONIC_FIELDS = Object.freeze([
  ['entities', 'entities'],
  ['textures', 'textures'],
  ['listeners', 'listeners'],
  ['activeAnimations', 'animations'],
  ['timers', 'timers']
])

const SCENARIO_ALIASES = new Map([
  ['unmounted', 'route-unmounted'],
  ['route-unmounted', 'route-unmounted'],
  ['hidden', 'hidden'],
  ['minimized', 'minimized'],
  ['visible-idle', 'visible-idle'],
  ['visible_idle', 'visible-idle'],
  ['100-active', '100-active'],
  ['100_active', '100-active'],
  ['250-lod', '250-lod'],
  ['250_lod', '250-lod'],
  ['balanced-overview', 'balanced-overview'],
  ['balanced_overview', 'balanced-overview'],
  ['balanced-worker-focus', 'balanced-worker-focus'],
  ['balanced_worker_focus', 'balanced-worker-focus'],
  ['30-minute-stability', '30-minute-stability'],
  ['30_minute_stability', '30-minute-stability']
])

const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const isFiniteNumber = value => typeof value === 'number' && Number.isFinite(value)

function equalNumber(left, right) {
  return (
    Object.is(left, right) || Math.abs(left - right) <= Number.EPSILON * Math.max(1, Math.abs(left), Math.abs(right))
  )
}

function percentile(values, p) {
  if (!values.length) return 0

  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))]
}

function max(values) {
  return values.length ? Math.max(...values) : 0
}

/** Recompute all receipt summaries from raw samples using a deterministic rank. */
export function summarizeRawSamples(rawSamples) {
  const raw = isRecord(rawSamples) ? rawSamples : {}
  const resident = Array.isArray(raw.residentMemoryMiB) ? raw.residentMemoryMiB : []

  return {
    sampleCount: Array.isArray(raw.frameMs) ? raw.frameMs.length : 0,
    p95FrameMs: percentile(Array.isArray(raw.frameMs) ? raw.frameMs : [], 0.95),
    p95WorldUpdateMs: percentile(Array.isArray(raw.worldUpdateMs) ? raw.worldUpdateMs : [], 0.95),
    maxCpuDeltaPp: max(Array.isArray(raw.cpuDeltaPp) ? raw.cpuDeltaPp : []),
    maxGpuMemoryDeltaMiB: max(Array.isArray(raw.gpuMemoryDeltaMiB) ? raw.gpuMemoryDeltaMiB : []),
    residentMemoryDriftMiB: resident.length ? resident[resident.length - 1] - resident[0] : 0,
    maxRenderFrames: max(Array.isArray(raw.renderFrames) ? raw.renderFrames : []),
    maxDrawCalls: max(Array.isArray(raw.drawCalls) ? raw.drawCalls : []),
    maxVisibleTriangles: max(Array.isArray(raw.visibleTriangles) ? raw.visibleTriangles : []),
    maxActiveAnimations: max(Array.isArray(raw.activeAnimations) ? raw.activeAnimations : []),
    maxEntities: max(Array.isArray(raw.entities) ? raw.entities : []),
    maxTextures: max(Array.isArray(raw.textures) ? raw.textures : []),
    maxListeners: max(Array.isArray(raw.listeners) ? raw.listeners : []),
    maxTimers: max(Array.isArray(raw.timers) ? raw.timers : [])
  }
}

function canonicalScenario(scenario) {
  return typeof scenario === 'string' ? (SCENARIO_ALIASES.get(scenario.toLowerCase()) ?? scenario.toLowerCase()) : null
}

function addRequired(errors, object, field, label = field) {
  if (!(field in object)) errors.push(`${label} is required`)
}

function validateRawSamples(rawSamples, errors) {
  if (!isRecord(rawSamples)) {
    errors.push('rawSamples is required and must be an object')
    return
  }

  for (const field of RAW_FIELDS) {
    const values = rawSamples[field]

    if (!Array.isArray(values)) {
      errors.push(`rawSamples.${field} is required and must be an array`)
      continue
    }
    if (values.length === 0) errors.push(`rawSamples.${field} must not be empty`)
    if (values.length > THRESHOLDS.maxRawSamples) {
      errors.push(`rawSamples.${field} has too many samples (max ${THRESHOLDS.maxRawSamples})`)
    }
    for (const value of values) {
      if (!isFiniteNumber(value)) errors.push(`rawSamples.${field} contains nonfinite values`)
      else if (value > THRESHOLDS.maxRawValue) errors.push(`rawSamples.${field} contains unbounded values`)
      else if (NON_NEGATIVE_RAW_FIELDS.has(field) && value < 0)
        errors.push(`rawSamples.${field} contains negative values`)
    }
  }
}

function validateSummaries(rawSamples, summaries, errors) {
  if (!isRecord(summaries)) {
    errors.push('summaries is required and must be an object')
    return {}
  }

  const expected = summarizeRawSamples(rawSamples)
  for (const [field, value] of Object.entries(summaries)) {
    if (!(field in expected)) {
      errors.push(`summaries.${field} is not a recognized summary field`)
    } else if (!isFiniteNumber(value)) {
      errors.push(`summaries.${field} must be finite`)
    } else if (!equalNumber(value, expected[field])) {
      errors.push(`summary ${field} does not match raw samples (${value} !== ${expected[field]})`)
    }
  }
  for (const [field, value] of Object.entries(expected)) {
    if (!(field in summaries)) errors.push(`summaries.${field} is required`)
    else if (!isFiniteNumber(value)) errors.push(`summaries.${field} must be finite`)
  }

  return summaries
}

function numericMetric(receipt, summaries, directField, summaryField, errors, label) {
  const value = receipt[directField]
  if (directField in receipt && value === undefined) {
    errors.push(`${label} unavailable; missing ${label} is not zero`)
    return undefined
  }
  if (value !== undefined && !isFiniteNumber(value)) errors.push(`${label} must be finite`)
  if (value !== undefined) return value
  return summaries[summaryField]
}

function validateCommonShape(receipt, errors) {
  if (!isRecord(receipt)) {
    errors.push('receipt must be an object')
    return
  }
  addRequired(errors, receipt, 'receiptVersion')
  if (receipt.receiptVersion !== undefined && receipt.receiptVersion !== RECEIPT_VERSION) {
    errors.push(`receiptVersion must equal ${RECEIPT_VERSION}`)
  }
  addRequired(errors, receipt, 'evidenceClass')
  addRequired(errors, receipt, 'scenario')
  addRequired(errors, receipt, 'gitSha')
  addRequired(errors, receipt, 'buildStamp')
  addRequired(errors, receipt, 'timestamp')
  addRequired(errors, receipt, 'pass')
  addRequired(errors, receipt, 'errors')
  addRequired(errors, receipt, 'environment')
  addRequired(errors, receipt, 'qualityTier')
  addRequired(errors, receipt, 'internalRenderScale')
  addRequired(errors, receipt, 'warmupDurationMs')
  addRequired(errors, receipt, 'population')
  addRequired(errors, receipt, 'cameraState')
  addRequired(errors, receipt, 'dialogueState')
  addRequired(errors, receipt, 'rawSamples')
  addRequired(errors, receipt, 'summaries')
  for (const field of [
    'fpsCap',
    'renderFrames',
    'processCpuBaselinePp',
    'processCpuDeltaPp',
    'gpuMemoryBaselineMiB',
    'gpuMemoryDeltaMiB',
    'residentMemoryDriftMiB',
    'drawCalls',
    'visibleTriangles',
    'activeAnimations',
    'entities',
    'textures',
    'listeners',
    'timers',
    'disposal',
    'recovery'
  ]) {
    addRequired(errors, receipt, field)
  }

  if (receipt.gitSha !== undefined && (typeof receipt.gitSha !== 'string' || !/^[0-9a-f]{40}$/i.test(receipt.gitSha))) {
    errors.push('gitSha must be an exact 40-character hexadecimal SHA')
  }
  if (
    receipt.buildStamp !== undefined &&
    !(typeof receipt.buildStamp === 'string' ? receipt.buildStamp.length > 0 : isRecord(receipt.buildStamp))
  ) {
    errors.push('buildStamp must be a nonempty string or stamp object')
  }
  if (isRecord(receipt.buildStamp) && Object.keys(receipt.buildStamp).length === 0) {
    errors.push('buildStamp object must not be empty')
  }
  if (
    receipt.timestamp !== undefined &&
    (typeof receipt.timestamp !== 'string' || Number.isNaN(Date.parse(receipt.timestamp)))
  ) {
    errors.push('timestamp must be an ISO timestamp')
  }
  if (receipt.pass !== undefined && typeof receipt.pass !== 'boolean') errors.push('pass must be boolean')
  if (receipt.errors !== undefined && !Array.isArray(receipt.errors)) errors.push('errors must be an array')
  if (receipt.evidenceClass !== undefined && !EVIDENCE_CLASSES.includes(receipt.evidenceClass)) {
    errors.push(`unknown evidence class: ${String(receipt.evidenceClass)}`)
  }
  if (receipt.scenario !== undefined && canonicalScenario(receipt.scenario) === null)
    errors.push('scenario must be a string')
  if (receipt.qualityTier !== undefined && !['Efficient', 'Balanced', 'Detailed'].includes(receipt.qualityTier)) {
    errors.push('qualityTier must be Efficient, Balanced, or Detailed')
  }
  if (
    receipt.internalRenderScale !== undefined &&
    (!isFiniteNumber(receipt.internalRenderScale) ||
      receipt.internalRenderScale <= 0 ||
      receipt.internalRenderScale > 1)
  ) {
    errors.push('internalRenderScale must be finite and in (0, 1]')
  }
  if (
    receipt.warmupDurationMs !== undefined &&
    (!isFiniteNumber(receipt.warmupDurationMs) || receipt.warmupDurationMs < 0)
  ) {
    errors.push('warmupDurationMs must be a nonnegative finite number')
  }
  if (receipt.environment !== undefined && !isRecord(receipt.environment)) errors.push('environment must be an object')
  if (receipt.population !== undefined && !isRecord(receipt.population)) errors.push('population must be an object')
  for (const field of [
    'fpsCap',
    'renderFrames',
    'processCpuBaselinePp',
    'processCpuDeltaPp',
    'gpuMemoryBaselineMiB',
    'gpuMemoryDeltaMiB',
    'residentMemoryDriftMiB',
    'drawCalls',
    'visibleTriangles',
    'activeAnimations',
    'entities',
    'textures',
    'listeners',
    'timers'
  ]) {
    if (field in receipt && (!isFiniteNumber(receipt[field]) || receipt[field] < 0)) {
      errors.push(`${field} must be a nonnegative finite number`)
    } else if (field in receipt && receipt[field] > THRESHOLDS.maxRawValue) {
      errors.push(`${field} contains an unbounded value`)
    }
  }
}

function validateEnvironment(environment, errors) {
  if (!isRecord(environment)) return
  for (const field of [
    'hardwareModel',
    'architecture',
    'os',
    'electronVersion',
    'chromiumVersion',
    'powerState',
    'gpuAdapter',
    'electronMode',
    'backendMode'
  ]) {
    addRequired(errors, environment, field, `environment.${field}`)
    if (field in environment && (typeof environment[field] !== 'string' || environment[field].length === 0)) {
      errors.push(`environment.${field} must be a nonempty string`)
    }
  }
  addRequired(errors, environment, 'windowSize', 'environment.windowSize')
  addRequired(errors, environment, 'displayScale', 'environment.displayScale')
  addRequired(errors, environment, 'gpuEnabled', 'environment.gpuEnabled')
  addRequired(errors, environment, 'cityPopulated', 'environment.cityPopulated')
  if (isRecord(environment.windowSize)) {
    for (const field of ['width', 'height']) {
      if (!isFiniteNumber(environment.windowSize[field]) || environment.windowSize[field] <= 0) {
        errors.push(`environment.windowSize.${field} must be positive`)
      }
    }
  }
  if (!isFiniteNumber(environment.displayScale) || environment.displayScale <= 0)
    errors.push('environment.displayScale must be positive')
  if (typeof environment.gpuEnabled !== 'boolean') errors.push('environment.gpuEnabled must be boolean')
  if (typeof environment.cityPopulated !== 'boolean') errors.push('environment.cityPopulated must be boolean')
}

function validatePopulation(population, errors) {
  if (!isRecord(population)) return
  for (const field of ['observed', 'active', 'lodMix', 'source']) {
    addRequired(errors, population, field, `population.${field}`)
  }
  for (const field of ['observed', 'active']) {
    if (field in population && (!Number.isInteger(population[field]) || population[field] < 0)) {
      errors.push(`population.${field} must be a nonnegative integer`)
    }
  }
  if ('lodMix' in population && !isRecord(population.lodMix)) errors.push('population.lodMix must be an object')
  if ('source' in population && typeof population.source !== 'string') errors.push('population.source must be a string')
}

function validateDirectSummaries(receipt, expected, errors) {
  for (const [field, summaryField] of [
    ['renderFrames', 'maxRenderFrames'],
    ['processCpuDeltaPp', 'maxCpuDeltaPp'],
    ['gpuMemoryDeltaMiB', 'maxGpuMemoryDeltaMiB'],
    ['residentMemoryDriftMiB', 'residentMemoryDriftMiB'],
    ['drawCalls', 'maxDrawCalls'],
    ['visibleTriangles', 'maxVisibleTriangles'],
    ['activeAnimations', 'maxActiveAnimations'],
    ['entities', 'maxEntities'],
    ['textures', 'maxTextures'],
    ['listeners', 'maxListeners'],
    ['timers', 'maxTimers']
  ]) {
    if (field in receipt && isFiniteNumber(receipt[field]) && !equalNumber(receipt[field], expected[summaryField])) {
      errors.push(`${field} does not match raw samples (${receipt[field]} !== ${expected[summaryField]})`)
    }
  }
  if (
    'p95FrameMs' in receipt &&
    isFiniteNumber(receipt.p95FrameMs) &&
    !equalNumber(receipt.p95FrameMs, expected.p95FrameMs)
  ) {
    errors.push(`p95FrameMs does not match raw samples (${receipt.p95FrameMs} !== ${expected.p95FrameMs})`)
  }
  if (
    'p95WorldUpdateMs' in receipt &&
    isFiniteNumber(receipt.p95WorldUpdateMs) &&
    !equalNumber(receipt.p95WorldUpdateMs, expected.p95WorldUpdateMs)
  ) {
    errors.push(
      `p95WorldUpdateMs does not match raw samples (${receipt.p95WorldUpdateMs} !== ${expected.p95WorldUpdateMs})`
    )
  }
}

function validateMonotonicStability(rawSamples, errors) {
  for (const [field, label] of MONOTONIC_FIELDS) {
    const values = rawSamples?.[field]
    if (!Array.isArray(values) || values.length < 2) continue
    const growsMonotonically =
      values.every((value, index) => index === 0 || value >= values[index - 1]) &&
      values.some((value, index) => index > 0 && value > values[index - 1])
    if (growsMonotonically) errors.push(`monotonic ${label} growth detected`)
  }
}

function validateAcceptanceGate(receipt, scenario, errors) {
  const environment = receipt.environment
  const evidenceClass = receipt.evidenceClass
  const eligibleClass = evidenceClass === 'fake-backend-packaged' || evidenceClass === 'supervised-live'
  const packaged = isRecord(environment) && environment.electronMode === 'packaged'
  const gpuEnabled = isRecord(environment) && environment.gpuEnabled === true
  const populated = isRecord(environment) && environment.cityPopulated === true
  const eligible = eligibleClass && packaged && gpuEnabled && populated

  if (!eligible) {
    errors.push(`receipt is not eligible for packaged performance acceptance (${scenario ?? 'unknown scenario'})`)
    if (!eligibleClass)
      errors.push('packaged performance requires fake-backend-packaged or supervised-live evidence class')
    if (!packaged) errors.push('packaged performance requires packaged Electron, not dev Electron')
    if (!gpuEnabled) errors.push('packaged performance requires GPU enabled')
    if (!populated) errors.push('packaged performance requires a real populated city, not an empty fake boot')
  }

  return eligible
}

/** Validate a receipt and return recomputed summaries plus acceptance status. */
export function validateReceipt(receipt) {
  const errors = []
  if (!isRecord(receipt)) {
    validateCommonShape(receipt, errors)
    return { ok: false, packagedPerformanceEligible: false, errors, summary: {} }
  }

  validateCommonShape(receipt, errors)
  validateEnvironment(receipt.environment, errors)
  validatePopulation(receipt.population, errors)
  validateRawSamples(receipt.rawSamples, errors)
  const summaries = validateSummaries(receipt.rawSamples, receipt.summaries, errors)
  const expected = summarizeRawSamples(receipt.rawSamples)
  validateDirectSummaries(receipt, expected, errors)
  const scenario = canonicalScenario(receipt.scenario)

  const renderFrames = receipt.renderFrames ?? expected.maxRenderFrames
  const cpuDelta = numericMetric(receipt, summaries, 'processCpuDeltaPp', 'maxCpuDeltaPp', errors, 'process CPU delta')
  const gpuDelta = numericMetric(
    receipt,
    summaries,
    'gpuMemoryDeltaMiB',
    'maxGpuMemoryDeltaMiB',
    errors,
    'GPU memory delta'
  )
  const p95Frame = numericMetric(receipt, summaries, 'p95FrameMs', 'p95FrameMs', errors, 'p95 frame time')
  const p95Update = numericMetric(
    receipt,
    summaries,
    'p95WorldUpdateMs',
    'p95WorldUpdateMs',
    errors,
    'p95 world update'
  )
  const drawCalls = receipt.drawCalls ?? expected.maxDrawCalls
  const triangles = receipt.visibleTriangles ?? expected.maxVisibleTriangles
  const residentDrift = receipt.residentMemoryDriftMiB ?? expected.residentMemoryDriftMiB

  for (const [value, label] of [
    [renderFrames, 'renderFrames'],
    [drawCalls, 'drawCalls'],
    [triangles, 'visibleTriangles'],
    [residentDrift, 'residentMemoryDriftMiB']
  ]) {
    if (!isFiniteNumber(value) || value < 0) errors.push(`${label} must be a nonnegative finite number`)
  }
  if (cpuDelta === undefined || cpuDelta === null) errors.push('process CPU delta unavailable; missing CPU is not zero')
  if (gpuDelta === undefined || gpuDelta === null) errors.push('GPU memory delta unavailable; missing GPU is not zero')
  if (!isFiniteNumber(cpuDelta)) errors.push('process CPU delta unavailable; missing CPU is not zero')
  if (!isFiniteNumber(gpuDelta)) errors.push('GPU memory delta unavailable; missing GPU is not zero')

  if (['hidden', 'minimized', 'route-unmounted'].includes(scenario)) {
    if (renderFrames !== 0) errors.push(`${scenario} requires zero render frames`)
    if (isFiniteNumber(cpuDelta) && cpuDelta > THRESHOLDS.dormantCpuDeltaPp)
      errors.push(`CPU delta exceed ${THRESHOLDS.dormantCpuDeltaPp} percentage points`)
  }
  if (scenario === 'visible-idle' && isFiniteNumber(cpuDelta) && cpuDelta > THRESHOLDS.visibleIdleCpuDeltaPp)
    errors.push(`CPU delta exceed ${THRESHOLDS.visibleIdleCpuDeltaPp} percentage points`)
  if (scenario === '100-active') {
    if (receipt.fpsCap !== 30) errors.push('100 active requires a 30 FPS cap')
    if (isFiniteNumber(p95Frame) && p95Frame > THRESHOLDS.activeFrameP95Ms)
      errors.push(`p95 frame exceed ${THRESHOLDS.activeFrameP95Ms}ms`)
    if (isFiniteNumber(p95Update) && p95Update > THRESHOLDS.activeWorldUpdateP95Ms)
      errors.push(`p95 world update exceed ${THRESHOLDS.activeWorldUpdateP95Ms}ms`)
    if (isFiniteNumber(cpuDelta) && cpuDelta > THRESHOLDS.active100CpuDeltaPp)
      errors.push(`CPU delta exceed ${THRESHOLDS.active100CpuDeltaPp} percentage points`)
  }
  if (scenario === '250-lod') {
    if (isFiniteNumber(p95Frame) && p95Frame > THRESHOLDS.activeFrameP95Ms)
      errors.push(`p95 frame exceed ${THRESHOLDS.activeFrameP95Ms}ms`)
    if (isFiniteNumber(cpuDelta) && cpuDelta > THRESHOLDS.active250CpuDeltaPp)
      errors.push(`CPU delta exceed ${THRESHOLDS.active250CpuDeltaPp} percentage points`)
  }
  if (scenario === 'balanced-overview') {
    if (isFiniteNumber(drawCalls) && drawCalls > THRESHOLDS.balancedOverviewDrawCalls)
      errors.push(`draw calls exceed ${THRESHOLDS.balancedOverviewDrawCalls}`)
    if (isFiniteNumber(triangles) && triangles > THRESHOLDS.balancedOverviewTriangles)
      errors.push(`triangles exceed ${THRESHOLDS.balancedOverviewTriangles}`)
  }
  if (scenario === 'balanced-worker-focus') {
    if (isFiniteNumber(drawCalls) && drawCalls > THRESHOLDS.balancedWorkerFocusDrawCalls)
      errors.push(`draw calls exceed ${THRESHOLDS.balancedWorkerFocusDrawCalls}`)
    if (isFiniteNumber(triangles) && triangles > THRESHOLDS.balancedWorkerFocusTriangles)
      errors.push(`triangles exceed ${THRESHOLDS.balancedWorkerFocusTriangles}`)
  }
  if (scenario === '30-minute-stability') {
    if (isFiniteNumber(residentDrift) && residentDrift > THRESHOLDS.stabilityResidentDriftMiB)
      errors.push(`resident memory drift exceed ${THRESHOLDS.stabilityResidentDriftMiB} MiB`)
    validateMonotonicStability(receipt.rawSamples, errors)
  }
  if (isFiniteNumber(gpuDelta) && gpuDelta > THRESHOLDS.maxGpuMemoryDeltaMiB) {
    errors.push(`GPU memory exceed ${THRESHOLDS.maxGpuMemoryDeltaMiB} MiB`)
  }
  if (
    receipt.qualityTier === 'Efficient' &&
    /integrated/i.test(String(receipt.environment?.gpuAdapter ?? '')) &&
    receipt.interactionUsable !== true
  ) {
    errors.push('Efficient integrated GPU receipt must prove interaction usability')
  }

  const packagedPerformanceEligible = validateAcceptanceGate(receipt, scenario, errors)
  return { ok: errors.length === 0, packagedPerformanceEligible, errors, summary: expected }
}

/** Validate a JSON receipt file for command-line/package integration. */
export function validateReceiptFile(path) {
  return validateReceipt(JSON.parse(readFileSync(path, 'utf8')))
}

function main() {
  const path = process.argv[2]
  if (!path) {
    console.error('usage: node lunar-city.mjs <receipt.json>')
    process.exitCode = 2
    return
  }
  const result = validateReceiptFile(path)
  console.log(JSON.stringify(result, null, 2))
  if (!result.ok) process.exitCode = 1
}

if (import.meta.url === `file://${process.argv[1]}`) main()
