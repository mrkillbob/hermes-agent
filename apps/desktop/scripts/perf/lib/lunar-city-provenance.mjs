export const LUNAR_CITY_PROVENANCE_VERSION = 1
export const LUNAR_CITY_PHASE_ENVELOPE_VERSION = 1

const RAW_RENDERER_FIELDS = Object.freeze([
  'frameMs',
  'worldUpdateMs',
  'renderFrames',
  'drawCalls',
  'visibleTriangles',
  'activeAnimations',
  'entities',
  'textures',
  'listeners',
  'timers'
])
const GPU_MEMORY_SOURCES = new Set(['chromium-memory-infra-v1'])

const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const isFiniteNumber = value => typeof value === 'number' && Number.isFinite(value)

function fail(message) {
  throw new Error(`Lunar City raw provenance: ${message}`)
}

function validateIdentity(identity, label) {
  if (!isRecord(identity) || !Number.isInteger(identity.pid) || identity.pid <= 0) {
    fail(`${label} renderer identity requires a positive pid`)
  }
  if (!isFiniteNumber(identity.startedAtMs) || identity.startedAtMs < 0) {
    fail(`${label} renderer identity requires a nonnegative startedAtMs`)
  }
}

function sameIdentity(left, right) {
  return left.pid === right.pid && left.startedAtMs === right.startedAtMs
}

function validateProcessRows(rows, label) {
  if (!Array.isArray(rows) || rows.length === 0) fail(`${label} app.getAppMetrics process rows are unavailable`)
  const pids = new Set()
  for (const row of rows) {
    if (!isRecord(row) || !Number.isInteger(row.pid) || row.pid <= 0 || typeof row.type !== 'string') {
      fail(`${label} has a malformed app.getAppMetrics process row`)
    }
    if (pids.has(row.pid)) fail(`${label} repeats process pid ${row.pid}`)
    pids.add(row.pid)
    if (!isRecord(row.cpu) || !isFiniteNumber(row.cpu.percentCPUUsage) || row.cpu.percentCPUUsage < 0) {
      fail(`${label} process ${row.pid} CPU metric is unavailable`)
    }
    if (!isRecord(row.memory) || !isFiniteNumber(row.memory.workingSetSize) || row.memory.workingSetSize < 0) {
      fail(`${label} process ${row.pid} RSS metric is unavailable`)
    }
  }
}

function validatePopulation(population, label, allowEmpty) {
  if (
    !isRecord(population) ||
    !Number.isInteger(population.observed) ||
    !Number.isInteger(population.active) ||
    !isRecord(population.lodMix) ||
    typeof population.source !== 'string'
  ) {
    fail(`${label} exact population metrics are unavailable`)
  }
  if (population.observed < 0 || population.active < 0 || population.active > population.observed) {
    fail(`${label} exact population is invalid`)
  }
  const lodTotal = Object.values(population.lodMix).reduce(
    (sum, count) => sum + (Number.isInteger(count) && count >= 0 ? count : Number.NaN),
    0
  )
  if (!Number.isFinite(lodTotal) || lodTotal !== population.observed) fail(`${label} exact LOD population is invalid`)
  if (!allowEmpty && population.observed === 0) fail(`${label} reports an empty city`)
}

function validateRendererMetrics(metrics, identity, label, allowEmpty) {
  if (!isRecord(metrics)) fail(`${label} renderer metrics are unavailable`)
  if (metrics.rendererPid !== identity.pid || metrics.rendererStartedAtMs !== identity.startedAtMs) {
    fail(`${label} renderer lifetime identity changed`)
  }
  if (metrics.gpuEnabled !== true) fail(`${label} GPU is disabled; packaged performance requires GPU enabled`)
  if (!isFiniteNumber(metrics.gpuMemoryMiB)) fail(`${label} GPU memory metric is unavailable`)
  if (typeof metrics.gpuMemorySource !== 'string' || metrics.gpuMemorySource.length === 0) {
    fail(`${label} GPU memory source is unavailable`)
  }
  if (!GPU_MEMORY_SOURCES.has(metrics.gpuMemorySource)) fail(`${label} GPU memory source is not proven attributable`)
  for (const field of RAW_RENDERER_FIELDS) {
    if (!isFiniteNumber(metrics[field]) || metrics[field] < 0) fail(`${label} required metric ${field} is unavailable`)
  }
  validatePopulation(metrics.population, label, allowEmpty)
}

function validatePhase(envelope, expectedPhase) {
  if (!isRecord(envelope)) fail(`${expectedPhase} envelope is unavailable`)
  if (envelope.envelopeVersion !== LUNAR_CITY_PHASE_ENVELOPE_VERSION) {
    fail(`${expectedPhase} envelopeVersion must equal ${LUNAR_CITY_PHASE_ENVELOPE_VERSION}`)
  }
  if (envelope.phase !== expectedPhase) fail(`${expectedPhase} envelope has the wrong phase`)
  validateIdentity(envelope.rendererIdentity, expectedPhase)
  if (!Array.isArray(envelope.samples) || envelope.samples.length === 0)
    fail(`${expectedPhase} samples are unavailable`)

  let previousTimestamp = -1
  for (const [index, sample] of envelope.samples.entries()) {
    const label = `${expectedPhase} sample ${index}`
    if (!isRecord(sample) || !isFiniteNumber(sample.timestampMs) || sample.timestampMs < 0) {
      fail(`${label} timestamp is unavailable`)
    }
    if (sample.timestampMs <= previousTimestamp) fail(`${expectedPhase} timestamps must be strictly increasing`)
    previousTimestamp = sample.timestampMs
    validateProcessRows(sample.processMetrics, label)
    validateRendererMetrics(
      sample.rendererMetrics,
      envelope.rendererIdentity,
      label,
      expectedPhase === 'baseline-shell'
    )
    if (!sample.processMetrics.some(row => row.pid === envelope.rendererIdentity.pid)) {
      fail(`${label} does not retain the renderer app.getAppMetrics row`)
    }
  }
  if (expectedPhase === 'mounted-city') {
    const expected = JSON.stringify(envelope.samples[0].rendererMetrics.population)
    if (envelope.samples.some(sample => JSON.stringify(sample.rendererMetrics.population) !== expected)) {
      fail('mounted-city exact population must remain consistent across samples')
    }
  }
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function totalCpu(rows) {
  return rows.reduce((sum, row) => sum + row.cpu.percentCPUUsage, 0)
}

function rendererRssMiB(sample, rendererPid) {
  const row = sample.processMetrics.find(candidate => candidate.pid === rendererPid)
  if (!row) fail(`renderer pid ${rendererPid} is absent from app.getAppMetrics rows`)
  return row.memory.workingSetSize / 1024
}

/**
 * Validate retained native samples and derive the validator's numeric arrays.
 * CPU and GPU deltas are measured against the baseline-shell average. Renderer
 * RSS remains an absolute series so the receipt validator derives its drift.
 */
export function deriveRawSamplesFromProvenance(provenance) {
  if (!isRecord(provenance) || provenance.provenanceVersion !== LUNAR_CITY_PROVENANCE_VERSION) {
    fail(`provenanceVersion must equal ${LUNAR_CITY_PROVENANCE_VERSION}`)
  }
  validatePhase(provenance.baselineShell, 'baseline-shell')
  validatePhase(provenance.mountedCity, 'mounted-city')
  if (!sameIdentity(provenance.baselineShell.rendererIdentity, provenance.mountedCity.rendererIdentity)) {
    fail('renderer lifetime identity changed between baseline-shell and mounted-city')
  }

  const baselineCpu = average(provenance.baselineShell.samples.map(sample => totalCpu(sample.processMetrics)))
  const baselineGpu = average(provenance.baselineShell.samples.map(sample => sample.rendererMetrics.gpuMemoryMiB))
  const identity = provenance.mountedCity.rendererIdentity
  const baselineRendererRss = average(
    provenance.baselineShell.samples.map(sample => rendererRssMiB(sample, identity.pid))
  )
  const city = provenance.mountedCity.samples
  const cpuDeltaPp = city.map(sample => totalCpu(sample.processMetrics) - baselineCpu)
  const gpuMemoryDeltaMiB = city.map(sample => sample.rendererMetrics.gpuMemoryMiB - baselineGpu)
  const residentMemoryMiB = city.map(sample => rendererRssMiB(sample, identity.pid))

  return {
    rendererIdentity: { ...identity },
    sampleTimestampsMs: city.map(sample => sample.timestampMs),
    resourceDeltas: {
      cpuDeltaPp,
      gpuMemoryDeltaMiB,
      rendererRssDeltaMiB: residentMemoryMiB.map(value => value - baselineRendererRss)
    },
    rawSamples: {
      frameMs: city.map(sample => sample.rendererMetrics.frameMs),
      worldUpdateMs: city.map(sample => sample.rendererMetrics.worldUpdateMs),
      cpuDeltaPp,
      gpuMemoryDeltaMiB,
      residentMemoryMiB,
      renderFrames: city.map(sample => sample.rendererMetrics.renderFrames),
      drawCalls: city.map(sample => sample.rendererMetrics.drawCalls),
      visibleTriangles: city.map(sample => sample.rendererMetrics.visibleTriangles),
      activeAnimations: city.map(sample => sample.rendererMetrics.activeAnimations),
      entities: city.map(sample => sample.rendererMetrics.entities),
      textures: city.map(sample => sample.rendererMetrics.textures),
      listeners: city.map(sample => sample.rendererMetrics.listeners),
      timers: city.map(sample => sample.rendererMetrics.timers)
    }
  }
}
