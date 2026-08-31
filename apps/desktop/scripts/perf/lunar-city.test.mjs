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
    if (direct in overrides && !(raw in rawOverrides)) rawOverrides[raw] = Array(5).fill(overrides[direct])
  }
  const rawSamples = samples(rawOverrides)
  const computed = summarizeRawSamples(rawSamples)
  const scenario = overrides.scenario ?? 'balanced-overview'
  const durationMs =
    overrides.measurement?.durationMs ??
    (scenario === 'visible-idle' ? 60_000 : scenario === '30-minute-stability' ? 1_800_000 : 30_000)
  const sampleTimestampsMs = overrides.measurement?.sampleTimestampsMs ?? [
    0,
    durationMs / 4,
    durationMs / 2,
    (durationMs * 3) / 4,
    durationMs
  ]
  const base = {
    receiptVersion: 1,
    evidenceClass: 'deterministic',
    scenario,
    gitSha: SHA,
    buildStamp: {
      schemaVersion: 1,
      commit: SHA,
      branch: 'test',
      builtAt: '2026-08-31T12:00:00.000Z',
      dirty: false,
      source: 'ci'
    },
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
    measurement: {
      durationMs,
      sampleIntervalMs: durationMs / 4,
      sampleTimestampsMs
    },
    rawSamples,
    summaries: summarizeRawSamples(rawSamples),
    fpsCap: 30,
    renderFrames: computed.maxRenderFrames,
    processCpuBaselinePp: 10,
    processCpuDeltaPp: computed.avgCpuDeltaPp,
    gpuMemoryBaselineMiB: 1000,
    gpuMemoryDeltaMiB: computed.avgGpuMemoryDeltaMiB,
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

test('rejects unknown scenarios instead of silently applying no budget', () => {
  const result = validateReceipt(receipt({ scenario: 'invented-scenario' }))

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /unknown scenario|scenario.*allowed/i)
  assert.equal(result.packagedPerformanceEligible, false)
})

test('requires duration, cadence, and aligned raw coverage for visible measurements', () => {
  const short = validateReceipt(
    receipt({
      scenario: '100-active',
      measurement: { durationMs: 29_999, sampleIntervalMs: 1_000, sampleTimestampsMs: [0, 1_000] }
    })
  )
  assert.match(short.errors.join('\n'), /duration|warmup|sample/i)

  const misaligned = validateReceipt(
    receipt({
      scenario: '100-active',
      measurement: { durationMs: 30_000, sampleIntervalMs: 7_500, sampleTimestampsMs: [0, 7_500] }
    })
  )
  assert.match(misaligned.errors.join('\n'), /aligned|coverage|timestamp/i)

  const idle = validateReceipt(
    receipt({
      scenario: 'visible-idle',
      measurement: {
        durationMs: 59_999,
        sampleIntervalMs: 1_000,
        sampleTimestampsMs: [0, 15_000, 30_000, 45_000, 59_999]
      }
    })
  )
  assert.match(idle.errors.join('\n'), /60 seconds|duration/i)

  const shortWarmup = validateReceipt(receipt({ scenario: 'visible-idle', warmupDurationMs: 29_999 }))
  assert.match(shortWarmup.errors.join('\n'), /warmup.*30000|warmup.*30 seconds/i)

  const dormantWarmup = validateReceipt(receipt({ scenario: 'hidden', warmupDurationMs: 0, renderFrames: 0 }))
  assert.match(dormantWarmup.errors.join('\n'), /warmup.*30000|warmup.*30 seconds/i)
})

test('binds exact 100-active and 250-lod population invariants', () => {
  const active = validateReceipt(
    receipt({
      scenario: '100-active',
      population: { observed: 99, active: 100, lodMix: { near: 100 }, source: 'fake-backend' }
    })
  )
  assert.match(active.errors.join('\n'), /100-active.*observed|population/i)

  const lod = validateReceipt(
    receipt({
      scenario: '250-lod',
      population: { observed: 250, active: 100, lodMix: { near: 50, far: 199 }, source: 'fake-backend' }
    })
  )
  assert.match(lod.errors.join('\n'), /250-lod.*LOD|lodMix|population/i)
})

test('requires truthful 250-lod near/far aggregation', () => {
  const valid = validateReceipt(
    receipt({
      scenario: '250-lod',
      population: { observed: 250, active: 100, lodMix: { near: 50, far: 200 }, source: 'fake-backend' }
    })
  )
  assert.equal(valid.ok, true, valid.errors.join('; '))

  for (const [label, lodMix, pattern] of [
    ['unknown key', { near: 50, far: 199, bogus: 1 }, /not an allowed LOD key/i],
    ['all near', { near: 250 }, /requires LOD level far|positive far/i],
    ['missing near', { far: 250 }, /requires LOD level near|positive near/i],
    ['sum mismatch', { near: 50, far: 199 }, /LOD total 250|total 249/i],
    ['fractional', { near: 50.5, far: 199.5 }, /near.*nonnegative integer|LOD total/i],
    ['negative', { near: -1, far: 251 }, /near.*nonnegative integer/i]
  ]) {
    const result = validateReceipt(
      receipt({
        scenario: '250-lod',
        population: { observed: 250, active: 100, lodMix, source: 'fake-backend' }
      })
    )
    assert.equal(result.ok, false, `${label} unexpectedly passed`)
    assert.match(result.errors.join('\n'), pattern, label)
  }
})

test('requires a typed build stamp tied to git SHA and evidence cleanliness', () => {
  const stringStamp = validateReceipt(receipt({ buildStamp: 'arbitrary' }))
  assert.match(stringStamp.errors.join('\n'), /buildStamp.*object|schema|commit/i)

  const mismatched = validateReceipt(receipt({ buildStamp: { ...receipt().buildStamp, commit: 'b'.repeat(40) } }))
  assert.match(mismatched.errors.join('\n'), /buildStamp.*gitSha|commit.*match/i)

  const dirtyPackaged = validateReceipt(
    receipt({ evidenceClass: 'fake-backend-packaged', buildStamp: { ...receipt().buildStamp, dirty: true } })
  )
  assert.match(dirtyPackaged.errors.join('\n'), /dirty|packaged/i)

  const nonCanonicalTimestamp = validateReceipt(receipt({ timestamp: '0' }))
  assert.match(nonCanonicalTimestamp.errors.join('\n'), /canonical ISO|timestamp/i)
  assert.equal(nonCanonicalTimestamp.packagedPerformanceEligible, false)
  const localeStamp = validateReceipt(receipt({ buildStamp: { ...receipt().buildStamp, builtAt: 'August 31, 2026' } }))
  assert.match(localeStamp.errors.join('\n'), /builtAt.*canonical ISO/i)
  assert.equal(localeStamp.packagedPerformanceEligible, false)
})

test('requires exact 25-active and stability populations and scenario state bindings', () => {
  const twentyFive = validateReceipt(
    receipt({
      scenario: '25-active',
      population: { observed: 25, active: 25, lodMix: { near: 25 }, source: 'fake-backend' }
    })
  )
  assert.equal(twentyFive.ok, true, twentyFive.errors.join('; '))
  const wrongTwentyFive = validateReceipt(
    receipt({
      scenario: '25-active',
      population: { observed: 25, active: 24, lodMix: { near: 25 }, source: 'fake-backend' }
    })
  )
  assert.match(wrongTwentyFive.errors.join('\n'), /25-active.*active population/i)

  const stableSamples = samples({
    activeAnimations: [10, 10, 10, 10, 10],
    entities: [100, 100, 100, 100, 100],
    textures: [20, 20, 20, 20, 20],
    listeners: [12, 12, 12, 12, 12],
    timers: [2, 2, 2, 2, 2]
  })
  const stableReceipt = validateReceipt(receipt({ scenario: '30-minute-stability', rawSamples: stableSamples }))
  assert.equal(stableReceipt.ok, true, stableReceipt.errors.join('; '))

  const wrongTier = validateReceipt(receipt({ scenario: 'tier-efficient' }))
  assert.match(wrongTier.errors.join('\n'), /tier-efficient.*quality tier/i)
  const wrongDialogue = validateReceipt(receipt({ scenario: 'dialogue-camera' }))
  assert.match(wrongDialogue.errors.join('\n'), /dialogue-camera.*dialogue state/i)
})

test('rejects contradictory declared pass and errors outcome', () => {
  const result = validateReceipt(receipt({ drawCalls: 181, pass: true, errors: [] }))

  assert.match(result.errors.join('\n'), /pass.*contradict|errors.*canonical|outcome/i)
})

test('short-circuits oversized arrays without reducer or stack failure', () => {
  const raw = samples({ frameMs: Array(10_001).fill(18) })
  assert.doesNotThrow(() => summarizeRawSamples(raw))
  const result = validateReceipt(receipt({ rawSamples: raw }))

  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /too many|sample.*bound/i)
})

test('checks every actual timestamp gap and measured-coverage bound', () => {
  const gap = validateReceipt(
    receipt({
      scenario: '100-active',
      measurement: {
        durationMs: 30_000,
        sampleIntervalMs: 7_500,
        sampleTimestampsMs: [0, 7_500, 20_000, 27_500, 30_000]
      }
    })
  )
  assert.match(gap.errors.join('\n'), /timestamp gap|declared cadence/i)

  const duplicate = validateReceipt(
    receipt({
      scenario: '100-active',
      measurement: {
        durationMs: 30_000,
        sampleIntervalMs: 7_500,
        sampleTimestampsMs: [0, 7_500, 7_500, 22_500, 30_000]
      }
    })
  )
  assert.match(duplicate.errors.join('\n'), /strictly increasing|duplicate/i)

  const overlong = validateReceipt(
    receipt({
      scenario: '100-active',
      measurement: {
        durationMs: 30_000,
        sampleIntervalMs: 7_500,
        sampleTimestampsMs: [0, 7_500, 15_000, 22_500, 30_002]
      }
    })
  )
  assert.match(overlong.errors.join('\n'), /coverage.*duration|measured duration/i)
})

test('uses nearest-rank p95 and averages signed CPU/GPU deltas', () => {
  const summaryRaw = samples({
    frameMs: Array.from({ length: 20 }, (_, index) => index + 1),
    cpuDeltaPp: [-4, -2, 1, 2, 2],
    gpuMemoryDeltaMiB: [-4, -2, 1, 2, 2]
  })
  const summary = summarizeRawSamples(summaryRaw)
  assert.equal(summary.p95FrameMs, 19)
  assert.equal(summary.avgCpuDeltaPp, -0.2)
  assert.equal(summary.maxAbsGpuMemoryDeltaMiB, 4)

  const result = validateReceipt(
    receipt({
      rawSamples: samples({ cpuDeltaPp: [-4, -2, 1, 2, 2], gpuMemoryDeltaMiB: [-4, -2, 1, 2, 2] }),
      processCpuDeltaPp: -0.2,
      gpuMemoryDeltaMiB: -0.2
    })
  )
  assert.equal(result.ok, true, result.errors.join('; '))
})

test('re-derives receipt arrays from versioned baseline-shell and mounted-city provenance', () => {
  const rawSamples = samples({
    cpuDeltaPp: [3, 3, 3, 3, 3],
    gpuMemoryDeltaMiB: [10, 10, 10, 10, 10],
    residentMemoryMiB: [300, 301, 301, 302, 302]
  })
  const rendererIdentity = { pid: 20, startedAtMs: 1_000 }
  const phase = (name, cpu, gpu, residentMemoryMiB) => ({
    envelopeVersion: 1,
    phase: name,
    rendererIdentity,
    samples: rawSamples.frameMs.map((frameMs, index) => ({
      timestampMs: index * 7_500,
      processMetrics: [
        {
          pid: 10,
          type: 'Browser',
          cpu: { percentCPUUsage: 1 },
          memory: { workingSetSize: 102_400 }
        },
        {
          pid: 20,
          type: 'Tab',
          cpu: { percentCPUUsage: cpu - 1 },
          memory: { workingSetSize: residentMemoryMiB[index] * 1024 }
        }
      ],
      rendererMetrics: {
        rendererPid: 20,
        rendererStartedAtMs: 1_000,
        gpuMemoryMiB: gpu,
        gpuMemorySource: 'chromium-memory-infra-v1',
        gpuEnabled: true,
        frameMs,
        worldUpdateMs: rawSamples.worldUpdateMs[index],
        renderFrames: rawSamples.renderFrames[index],
        drawCalls: rawSamples.drawCalls[index],
        visibleTriangles: rawSamples.visibleTriangles[index],
        activeAnimations: rawSamples.activeAnimations[index],
        entities: rawSamples.entities[index],
        textures: rawSamples.textures[index],
        listeners: rawSamples.listeners[index],
        timers: rawSamples.timers[index],
        population: { observed: 100, active: 100, lodMix: { near: 100 }, source: 'fake-backend' }
      }
    }))
  })
  const rawProvenance = {
    provenanceVersion: 1,
    baselineShell: phase('baseline-shell', 3, 40, [200, 200, 200, 200, 200]),
    mountedCity: phase('mounted-city', 6, 50, rawSamples.residentMemoryMiB)
  }
  const valid = validateReceipt(receipt({ rawSamples, rawProvenance }))
  assert.equal(valid.ok, true, valid.errors.join('; '))

  const forgedRaw = { ...rawSamples, cpuDeltaPp: [0, 0, 0, 0, 0] }
  const forged = validateReceipt(receipt({ rawSamples: forgedRaw, rawProvenance }))
  assert.equal(forged.ok, false)
  assert.match(forged.errors.join('\n'), /raw provenance.*cpuDeltaPp|cpuDeltaPp.*provenance/i)
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
        rawSamples: samples({ renderFrames: [0, 0, 0, 0, 0], cpuDeltaPp: [0.5, 0.5, 0.5, 0.5, 0.5] })
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
  const idlePass = validateReceipt(receipt({ scenario: 'visible-idle', processCpuDeltaPp: 3 }))
  assert.equal(idlePass.ok, true, idlePass.errors.join('; '))
  assert.match(
    validateReceipt(receipt({ scenario: 'visible-idle', processCpuDeltaPp: 3.01 })).errors.join('\n'),
    /CPU delta exceed 3/
  )

  const active = receipt({
    scenario: '100-active',
    processCpuDeltaPp: 12
  })
  assert.equal(validateReceipt(active).ok, true, validateReceipt(active).errors.join('; '))

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
  const deterministic = validateReceipt(receipt())
  assert.equal(deterministic.ok, true, deterministic.errors.join('; '))
  assert.equal(deterministic.packagedPerformanceEligible, false)
  for (const patch of [
    { evidenceClass: 'fake-backend-packaged' },
    { evidenceClass: 'fake-backend-packaged', environment: { ...receipt().environment, electronMode: 'dev' } },
    { evidenceClass: 'fake-backend-packaged', environment: { ...receipt().environment, gpuEnabled: false } },
    { evidenceClass: 'fake-backend-packaged', environment: { ...receipt().environment, cityPopulated: false } },
    { evidenceClass: 'unknown' }
  ]) {
    const result = validateReceipt(receipt(patch))

    assert.equal(result.packagedPerformanceEligible, false)
    assert.match(result.errors.join('\n'), /packaged performance|evidence class|GPU|population|rawProvenance/i)
  }
})

test('rejects forged summaries and nonfinite or unbounded raw samples', () => {
  const forged = validateReceipt(receipt({ summaries: { ...summarizeRawSamples(samples()), p95FrameMs: 1 } }))
  assert.match(forged.errors.join('\n'), /summary.*match raw samples/i)

  const nonfinite = validateReceipt(receipt({ rawSamples: samples({ frameMs: [18, Number.NaN] }) }))
  assert.match(nonfinite.errors.join('\n'), /finite|nonfinite/i)

  const unbounded = validateReceipt(receipt({ rawSamples: samples({ frameMs: Array(10001).fill(18) }) }))
  assert.match(unbounded.errors.join('\n'), /sample.*bound|too many/i)

  const unboundedMetric = validateReceipt(receipt({ visibleTriangles: Number.MAX_VALUE }))
  assert.match(unboundedMetric.errors.join('\n'), /visibleTriangles.*unbounded/i)

  const signedUnbounded = validateReceipt(
    receipt({
      rawSamples: samples({
        cpuDeltaPp: [-Number.MAX_VALUE, -Number.MAX_VALUE, -Number.MAX_VALUE, -Number.MAX_VALUE, -Number.MAX_VALUE]
      })
    })
  )
  assert.match(signedUnbounded.errors.join('\n'), /cpuDeltaPp.*unbounded/i)

  const unboundedClock = validateReceipt(
    receipt({
      measurement: { durationMs: Number.MAX_VALUE, sampleIntervalMs: 1, sampleTimestampsMs: [0, 1, 2, 3, 4] }
    })
  )
  assert.match(unboundedClock.errors.join('\n'), /duration.*finite|unbounded/i)
})
