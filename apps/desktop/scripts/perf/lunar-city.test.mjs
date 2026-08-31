import assert from 'node:assert/strict'
import { test } from 'node:test'

import { summarizeRawSamples, validateReceipt } from './lunar-city.mjs'

const SHA = 'a'.repeat(40)

function samples(overrides = {}) {
  return {
    frameMs: [18, 20, 22, 24, 26],
    worldUpdateMs: [2, 3, 3, 4, 5],
    cpuDeltaPp: [2, 3, 4, 4, 5],
    gpuMemoryDeltaMiB: [80, 90, 100, 110, 120],
    residentMemoryMiB: [500, 501, 501, 502, 502],
    renderFrames: [5, 5, 5, 5, 5],
    drawCalls: [100, 110, 120, 130, 140],
    visibleTriangles: [500_000, 600_000, 700_000, 800_000, 900_000],
    activeAnimations: [10, 10, 11, 11, 12],
    entities: [100, 100, 100, 100, 100],
    textures: [20, 20, 20, 20, 20],
    listeners: [12, 12, 12, 12, 12],
    timers: [2, 2, 2, 2, 2],
    ...overrides
  }
}

function receipt(overrides = {}) {
  const rawOverrides = { ...(overrides.rawSamples ?? {}) }
  const directToRaw = {
    renderFrames: 'renderFrames',
    processCpuDeltaPp: 'cpuDeltaPp',
    gpuMemoryDeltaMiB: 'gpuMemoryDeltaMiB',
    residentMemoryDriftMiB: 'residentMemoryMiB',
    drawCalls: 'drawCalls',
    visibleTriangles: 'visibleTriangles',
    activeAnimations: 'activeAnimations',
    entities: 'entities',
    textures: 'textures',
    listeners: 'listeners',
    timers: 'timers'
  }
  for (const [direct, raw] of Object.entries(directToRaw)) {
    if (direct in overrides && !(raw in rawOverrides)) rawOverrides[raw] = [overrides[direct]]
  }
  const rawSamples = samples(rawOverrides)
  const computed = summarizeRawSamples(rawSamples)
  const scenario = overrides.scenario ?? 'balanced-overview'
  const base = {
    receiptVersion: 1,
    evidenceClass: 'fake-backend-packaged',
    scenario,
    gitSha: SHA,
    buildStamp: '2026-08-31T12:00:00.000Z+dirty:false',
    timestamp: '2026-08-31T12:00:00.000Z',
    pass: true,
    errors: [],
    environment: {
      hardwareModel: 'Test Mac',
      architecture: 'arm64',
      os: 'macOS 15.6',
      electronVersion: '41.10.3',
      chromiumVersion: '134.0.0.0',
      powerState: 'ac',
      windowSize: { width: 1440, height: 900 },
      displayScale: 2,
      gpuAdapter: 'Test GPU',
      gpuEnabled: true,
      electronMode: 'packaged',
      backendMode: 'fake-backend',
      cityPopulated: true
    },
    qualityTier: 'Balanced',
    internalRenderScale: 1,
    warmupDurationMs: 30_000,
    population: { observed: 100, active: 100, lodMix: { near: 100 }, source: 'fake-backend' },
    cameraState: 'overview',
    dialogueState: 'idle',
    rawSamples,
    summaries: summarizeRawSamples(rawSamples),
    fpsCap: 30,
    renderFrames: computed.maxRenderFrames,
    processCpuBaselinePp: 10,
    processCpuDeltaPp: computed.maxCpuDeltaPp,
    gpuMemoryBaselineMiB: 1000,
    gpuMemoryDeltaMiB: computed.maxGpuMemoryDeltaMiB,
    residentMemoryDriftMiB: computed.residentMemoryDriftMiB,
    drawCalls: computed.maxDrawCalls,
    visibleTriangles: computed.maxVisibleTriangles,
    activeAnimations: computed.maxActiveAnimations,
    entities: computed.maxEntities,
    textures: computed.maxTextures,
    listeners: computed.maxListeners,
    timers: computed.maxTimers,
    disposal: 'not-applicable',
    recovery: 'not-applicable'
  }

  return { ...base, ...overrides, rawSamples, summaries: overrides.summaries ?? base.summaries }
}

test('rejects malformed receipts and hidden receipts with one frame', () => {
  const result = validateReceipt({ scenario: 'hidden', renderFrames: 1 })

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /receiptVersion|required|malformed/i)
  assert.match(result.errors.join('\n'), /hidden.*zero render frames|render frames.*zero/i)
})

test('reports balanced overview draw calls over the hard limit', () => {
  const result = validateReceipt(receipt({ scenario: 'balanced-overview', drawCalls: 181 }))

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /draw calls exceed 180/)
})

test('reports focused worker triangles over the hard limit', () => {
  const result = validateReceipt(
    receipt({ scenario: 'balanced-worker-focus', drawCalls: 180, visibleTriangles: 2_000_001 })
  )

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /triangles exceed 2000000/)
})

test('hidden, minimized, and unmounted require no frames and at most 0.5 CPU points', () => {
  for (const scenario of ['hidden', 'minimized', 'route-unmounted']) {
    const result = validateReceipt(
      receipt({
        scenario,
        renderFrames: 0,
        processCpuDeltaPp: 0.5,
        rawSamples: samples({ renderFrames: [0, 0, 0], cpuDeltaPp: [0, 0, 0.5] })
      })
    )

    assert.equal(result.ok, true, `${scenario}: ${result.errors.join('; ')}`)
  }

  const badFrames = validateReceipt(receipt({ scenario: 'hidden', renderFrames: 1 }))
  assert.match(badFrames.errors.join('\n'), /zero render frames/)
  const badCpu = validateReceipt(receipt({ scenario: 'minimized', processCpuDeltaPp: 0.51 }))
  assert.match(badCpu.errors.join('\n'), /CPU delta exceed 0\.5/)
})

test('enforces idle, active, and LOD CPU/frame budgets', () => {
  assert.equal(validateReceipt(receipt({ scenario: 'visible-idle', processCpuDeltaPp: 3 })).ok, true)
  assert.match(
    validateReceipt(receipt({ scenario: 'visible-idle', processCpuDeltaPp: 3.01 })).errors.join('\n'),
    /CPU delta exceed 3/
  )

  const active = receipt({
    scenario: '100-active',
    processCpuDeltaPp: 12
  })
  assert.equal(validateReceipt(active).ok, true)

  assert.match(
    validateReceipt(
      receipt({
        scenario: '100-active',
        processCpuDeltaPp: 12.1
      })
    ).errors.join('\n'),
    /CPU delta exceed 12/
  )
  assert.match(
    validateReceipt(
      receipt({
        scenario: '100-active',
        rawSamples: samples({ frameMs: [18, 20, 22, 24, 34] })
      })
    ).errors.join('\n'),
    /p95 frame exceed 33\.3ms/
  )
  assert.match(
    validateReceipt(
      receipt({
        scenario: '100-active',
        rawSamples: samples({ worldUpdateMs: [2, 3, 3, 4, 7] })
      })
    ).errors.join('\n'),
    /p95 world update exceed 6ms/
  )
  assert.match(
    validateReceipt(
      receipt({
        scenario: '250-lod',
        processCpuDeltaPp: 18.1,
        population: { observed: 250, active: 100, lodMix: { near: 50, far: 200 }, source: 'fake-backend' }
      })
    ).errors.join('\n'),
    /CPU delta exceed 18/
  )
})

test('enforces GPU, overview, and focused-worker geometry budgets', () => {
  assert.equal(validateReceipt(receipt({ gpuMemoryDeltaMiB: 256 })).ok, true)
  assert.match(validateReceipt(receipt({ gpuMemoryDeltaMiB: 256.1 })).errors.join('\n'), /GPU memory exceed 256/)
  assert.match(validateReceipt(receipt({ drawCalls: 181 })).errors.join('\n'), /draw calls exceed 180/)
  assert.match(
    validateReceipt(receipt({ scenario: 'balanced-worker-focus', visibleTriangles: 2_000_001 })).errors.join('\n'),
    /triangles exceed 2000000/
  )
})

test('rejects 30-minute memory drift and monotonic resource growth', () => {
  const rawSamples = samples({
    residentMemoryMiB: [500, 520, 540, 560, 576],
    entities: [100, 101, 102, 103, 104],
    textures: [20, 21, 22, 23, 24],
    listeners: [12, 13, 14, 15, 16],
    activeAnimations: [10, 11, 12, 13, 14],
    timers: [2, 3, 4, 5, 6]
  })
  const result = validateReceipt(receipt({ scenario: '30-minute-stability', residentMemoryDriftMiB: 76, rawSamples }))

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /resident memory drift exceed 75/)
  assert.match(result.errors.join('\n'), /monotonic entities growth/)
  assert.match(result.errors.join('\n'), /monotonic textures growth/)
  assert.match(result.errors.join('\n'), /monotonic listeners growth/)
  assert.match(result.errors.join('\n'), /monotonic animations growth/)
  assert.match(result.errors.join('\n'), /monotonic timers growth/)
})

test('missing CPU and GPU measurements are unavailable and block acceptance', () => {
  const missingCpu = validateReceipt(receipt({ processCpuDeltaPp: undefined }))
  assert.match(missingCpu.errors.join('\n'), /CPU.*unavailable/i)
  assert.equal(missingCpu.errors.join('\n').match(/CPU delta.*\b0\b/), null)

  const missingGpu = validateReceipt(receipt({ gpuMemoryDeltaMiB: undefined }))
  assert.match(missingGpu.errors.join('\n'), /GPU.*unavailable/i)
})

test('rejects dev, disabled-GPU, empty fake boot, and unknown evidence as packaged acceptance', () => {
  for (const patch of [
    { evidenceClass: 'deterministic' },
    { environment: { ...receipt().environment, electronMode: 'dev' } },
    { environment: { ...receipt().environment, gpuEnabled: false } },
    { environment: { ...receipt().environment, cityPopulated: false } },
    { evidenceClass: 'unknown' }
  ]) {
    const result = validateReceipt(receipt(patch))

    assert.equal(result.packagedPerformanceEligible, false)
    assert.match(result.errors.join('\n'), /packaged performance|evidence class|GPU|population/i)
  }
})

test('rejects forged summaries and nonfinite or unbounded raw samples', () => {
  const forged = validateReceipt(receipt({ summaries: { ...summarizeRawSamples(samples()), p95FrameMs: 1 } }))
  assert.match(forged.errors.join('\n'), /summary.*match raw samples/i)

  const nonfinite = validateReceipt(receipt({ rawSamples: samples({ frameMs: [18, Number.NaN] }) }))
  assert.match(nonfinite.errors.join('\n'), /finite|nonfinite/i)

  const unbounded = validateReceipt(receipt({ rawSamples: samples({ frameMs: Array(10001).fill(18) }) }))
  assert.match(unbounded.errors.join('\n'), /sample.*bound|too many/i)
})
