import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  cleanupIsolatedRun,
  createIsolatedLaunchPlan,
  inspectPackagedTarget,
  reserveUniqueDebugPort,
  runPackagedLunarCityMeasurement,
  validateBridgeHandshake
} from './lunar-city-runner.mjs'
import { deriveRawSamplesFromProvenance } from './lib/lunar-city-provenance.mjs'

const SHA = 'a'.repeat(40)
const BINARY = '/packages/Hermes.app/Contents/MacOS/Hermes'
const STAMP = '/packages/Hermes.app/Contents/Resources/install-stamp.json'
const INFO = '/packages/Hermes.app/Contents/Info.plist'

function cleanStamp(overrides = {}) {
  return {
    schemaVersion: 1,
    commit: SHA,
    branch: null,
    builtAt: '2026-08-31T12:00:00.000Z',
    dirty: false,
    source: 'local',
    ...overrides
  }
}

function processRows(rendererPid, cpu = 1, rendererRssKiB = 204_800) {
  return [
    {
      pid: 10,
      type: 'Browser',
      cpu: { percentCPUUsage: cpu },
      memory: { workingSetSize: 102_400 }
    },
    {
      pid: rendererPid,
      type: 'Tab',
      cpu: { percentCPUUsage: cpu * 2 },
      memory: { workingSetSize: rendererRssKiB }
    }
  ]
}

function rendererMetrics(overrides = {}) {
  return {
    rendererPid: 20,
    rendererStartedAtMs: 1_000,
    gpuMemoryMiB: 40,
    gpuMemorySource: 'chromium-memory-infra-v1',
    frameMs: 20,
    worldUpdateMs: 3,
    renderFrames: 5,
    drawCalls: 100,
    visibleTriangles: 500_000,
    activeAnimations: 10,
    entities: 100,
    textures: 20,
    listeners: 12,
    timers: 2,
    population: { observed: 100, active: 100, lodMix: { near: 100 }, source: 'fake-backend' },
    qualityTier: 'Balanced',
    internalRenderScale: 1,
    cameraState: 'overview',
    dialogueState: 'idle',
    gpuEnabled: true,
    ...overrides
  }
}

function phase(name, points) {
  return {
    envelopeVersion: 1,
    phase: name,
    rendererIdentity: { pid: 20, startedAtMs: 1_000 },
    samples: points.map((point, index) => ({
      timestampMs: index * 1_000,
      processMetrics: processRows(20, point.cpu, point.rssKiB),
      rendererMetrics: rendererMetrics(point.renderer)
    }))
  }
}

function provenance() {
  return {
    provenanceVersion: 1,
    baselineShell: phase('baseline-shell', [
      { cpu: 1, rssKiB: 204_800, renderer: { gpuMemoryMiB: 40 } },
      { cpu: 1, rssKiB: 205_824, renderer: { gpuMemoryMiB: 42 } }
    ]),
    mountedCity: phase('mounted-city', [
      { cpu: 2, rssKiB: 307_200, renderer: { gpuMemoryMiB: 50 } },
      { cpu: 2.5, rssKiB: 309_248, renderer: { gpuMemoryMiB: 52 } }
    ])
  }
}

test('accepts only an existing packaged binary with an exact clean pinned embedded stamp', () => {
  const result = inspectPackagedTarget(
    { binaryPath: BINARY, expectedGitSha: SHA, platform: 'darwin' },
    {
      existsSync: path => path === BINARY || path === STAMP || path === INFO,
      readFileSync: path => {
        assert.equal(path, STAMP)
        return JSON.stringify(cleanStamp())
      },
      statSync: () => ({ isFile: () => true, mode: 0o100755 }),
      readInfoPlist: () => ({
        CFBundleIdentifier: 'com.nousresearch.hermes',
        CFBundleExecutable: 'Hermes',
        CFBundleName: 'Hermes'
      })
    }
  )

  assert.equal(result.binaryPath, BINARY)
  assert.equal(result.resourcesPath, '/packages/Hermes.app/Contents/Resources')
  assert.deepEqual(result.buildStamp, cleanStamp())
})

test('refuses dev Electron and missing or untrustworthy package stamps', () => {
  const deps = {
    existsSync: () => true,
    readFileSync: () => JSON.stringify(cleanStamp()),
    statSync: () => ({ isFile: () => true, mode: 0o100755 }),
    readInfoPlist: () => ({
      CFBundleIdentifier: 'com.nousresearch.hermes',
      CFBundleExecutable: 'Hermes',
      CFBundleName: 'Hermes'
    })
  }
  assert.throws(
    () =>
      inspectPackagedTarget(
        { binaryPath: '/repo/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron', expectedGitSha: SHA },
        deps
      ),
    /dev Electron|packaged Hermes/i
  )

  for (const [label, stamp, pattern] of [
    ['missing', null, /install-stamp.*missing/i],
    ['mismatch', cleanStamp({ commit: 'b'.repeat(40) }), /does not match/i],
    ['dirty', cleanStamp({ dirty: true }), /dirty/i],
    ['fallback', cleanStamp({ source: 'fallback' }), /fallback/i]
  ]) {
    assert.throws(
      () =>
        inspectPackagedTarget(
          { binaryPath: BINARY, expectedGitSha: SHA },
          {
            ...deps,
            existsSync: path => (label === 'missing' ? path === BINARY || path === INFO : true),
            readFileSync: () => JSON.stringify(stamp)
          }
        ),
      pattern,
      label
    )
  }
})

test('rejects non-Hermes macOS bundles, arbitrary executables, and nonexecutable files', () => {
  const deps = {
    existsSync: () => true,
    readFileSync: () => JSON.stringify(cleanStamp()),
    statSync: () => ({ isFile: () => true, mode: 0o100755 }),
    readInfoPlist: () => ({
      CFBundleIdentifier: 'com.nousresearch.hermes',
      CFBundleExecutable: 'Hermes',
      CFBundleName: 'Hermes'
    })
  }
  assert.throws(
    () =>
      inspectPackagedTarget(
        { binaryPath: '/Applications/Calculator.app/Contents/MacOS/Calculator', expectedGitSha: SHA },
        deps
      ),
    /Hermes[.]app.*Hermes|bundle identity/i
  )
  assert.throws(
    () =>
      inspectPackagedTarget(
        { binaryPath: BINARY, expectedGitSha: SHA },
        { ...deps, statSync: () => ({ isFile: () => true, mode: 0o100644 }) }
      ),
    /executable/i
  )
  assert.throws(
    () =>
      inspectPackagedTarget(
        { binaryPath: BINARY, expectedGitSha: SHA },
        {
          ...deps,
          readInfoPlist: () => ({
            CFBundleIdentifier: 'com.apple.calculator',
            CFBundleExecutable: 'Hermes',
            CFBundleName: 'Hermes'
          })
        }
      ),
    /bundle identity/i
  )
})

test('builds an isolated packaged launch without disabling the GPU', () => {
  const previousDevServer = process.env.HERMES_DESKTOP_DEV_SERVER
  const previousRunAsNode = process.env.ELECTRON_RUN_AS_NODE
  const previousBootFake = process.env.HERMES_DESKTOP_BOOT_FAKE
  const previousDisableGpu = process.env.DISABLE_GPU
  const previousApiKey = process.env.OPENAI_API_KEY
  const previousGithubToken = process.env.GITHUB_TOKEN
  process.env.HERMES_DESKTOP_DEV_SERVER = 'http://127.0.0.1:5174'
  process.env.ELECTRON_RUN_AS_NODE = '1'
  process.env.HERMES_DESKTOP_BOOT_FAKE = '1'
  process.env.DISABLE_GPU = '1'
  process.env.OPENAI_API_KEY = 'secret'
  process.env.GITHUB_TOKEN = 'secret'
  const plan = createIsolatedLaunchPlan({
    binaryPath: BINARY,
    debugPort: 49321,
    tempRoot: '/private/tmp/lunar-city-run-7',
    runId: 'run-7',
    launchNonce: 'nonce-7'
  })

  assert.equal(plan.command, BINARY)
  assert.equal(plan.env.HERMES_HOME, '/private/tmp/lunar-city-run-7/hermes-home')
  assert.equal(plan.env.HERMES_DESKTOP_APP_NAME, 'Hermes Lunar City Perf run-7')
  assert.ok(plan.args.includes('--user-data-dir=/private/tmp/lunar-city-run-7/user-data'))
  assert.ok(plan.args.includes('--remote-debugging-port=49321'))
  assert.equal(
    plan.args.some(arg => arg === '--disable-gpu' || arg.startsWith('--disable-gpu=')),
    false
  )
  assert.equal('HERMES_DESKTOP_DEV_SERVER' in plan.env, false)
  assert.equal('ELECTRON_RUN_AS_NODE' in plan.env, false)
  for (const forbidden of ['HERMES_DESKTOP_BOOT_FAKE', 'DISABLE_GPU', 'OPENAI_API_KEY', 'GITHUB_TOKEN']) {
    assert.equal(forbidden in plan.env, false, forbidden)
  }
  assert.equal(plan.env.HERMES_LUNAR_CITY_PERF_NONCE, 'nonce-7')
  if (previousDevServer === undefined) delete process.env.HERMES_DESKTOP_DEV_SERVER
  else process.env.HERMES_DESKTOP_DEV_SERVER = previousDevServer
  if (previousRunAsNode === undefined) delete process.env.ELECTRON_RUN_AS_NODE
  else process.env.ELECTRON_RUN_AS_NODE = previousRunAsNode
  for (const [key, previous] of [
    ['HERMES_DESKTOP_BOOT_FAKE', previousBootFake],
    ['DISABLE_GPU', previousDisableGpu],
    ['OPENAI_API_KEY', previousApiKey],
    ['GITHUB_TOKEN', previousGithubToken]
  ]) {
    if (previous === undefined) delete process.env[key]
    else process.env[key] = previous
  }
})

test('reserves distinct free loopback debug ports until each reservation is released', async () => {
  const first = await reserveUniqueDebugPort()
  const second = await reserveUniqueDebugPort()
  try {
    assert.notEqual(first.port, second.port)
    assert.ok(first.port >= 1024 && first.port <= 65535)
    assert.ok(second.port >= 1024 && second.port <= 65535)
  } finally {
    await first.release()
    await second.release()
  }
})

test('requires an exact versioned packaged bridge handshake bound to nonce, build, and process lifetime', () => {
  const valid = {
    bridgeVersion: 1,
    launchNonce: 'nonce-7',
    buildSha: SHA,
    packaged: true,
    mainPid: 999,
    rendererIdentity: { pid: 20, startedAtMs: 1_000 },
    supportedPhases: ['baseline-shell', 'mounted-city'],
    processMetricsSource: 'electron.app.getAppMetrics'
  }
  assert.deepEqual(validateBridgeHandshake(valid, { launchNonce: 'nonce-7', buildSha: SHA, mainPid: 999 }), valid)
  for (const [patch, pattern] of [
    [null, /bridge_unavailable/],
    [{ launchNonce: 'incumbent' }, /bridge_mismatch.*nonce/],
    [{ buildSha: 'b'.repeat(40) }, /bridge_mismatch.*SHA/],
    [{ mainPid: 111 }, /bridge_mismatch.*main PID/],
    [{ packaged: false }, /bridge_mismatch.*packaged/]
  ]) {
    assert.throws(
      () =>
        validateBridgeHandshake(patch === null ? null : { ...valid, ...patch }, {
          launchNonce: 'nonce-7',
          buildSha: SHA,
          mainPid: 999
        }),
      pattern
    )
  }
})

test('derives CPU, renderer RSS, and GPU deltas from retained baseline and city samples', () => {
  const derived = deriveRawSamplesFromProvenance(provenance())

  assert.deepEqual(derived.rawSamples.cpuDeltaPp, [3, 4.5])
  assert.deepEqual(derived.rawSamples.residentMemoryMiB, [300, 302])
  assert.deepEqual(derived.rawSamples.gpuMemoryDeltaMiB, [9, 11])
  assert.deepEqual(derived.resourceDeltas.rendererRssDeltaMiB, [99.5, 101.5])
  assert.deepEqual(derived.sampleTimestampsMs, [0, 1_000])
  assert.equal(derived.rendererIdentity.pid, 20)
})

test('refuses renderer lifetime changes and RSS masquerading as GPU memory', () => {
  const changed = provenance()
  changed.mountedCity.samples[1].rendererMetrics.rendererPid = 21
  assert.throws(() => deriveRawSamplesFromProvenance(changed), /renderer.*identity|lifetime/i)

  const fakeGpu = provenance()
  fakeGpu.mountedCity.samples[0].rendererMetrics.gpuMemorySource = 'electron-app-metrics-rss'
  assert.throws(() => deriveRawSamplesFromProvenance(fakeGpu), /GPU.*source|RSS.*GPU/i)

  const gpuProcessRss = provenance()
  gpuProcessRss.mountedCity.samples[0].rendererMetrics.gpuMemorySource = 'gpu-process-private-memory'
  assert.throws(() => deriveRawSamplesFromProvenance(gpuProcessRss), /GPU.*source|process.*memory|RSS.*GPU/i)
  const opaque = provenance()
  opaque.mountedCity.samples[0].rendererMetrics.gpuMemorySource = 'babylon-engine-counter'
  assert.throws(() => deriveRawSamplesFromProvenance(opaque), /GPU.*source|attributable/i)
})

test('allows an empty baseline shell but requires a consistent exact mounted population', () => {
  const raw = provenance()
  for (const sample of raw.baselineShell.samples) {
    sample.rendererMetrics.population = { observed: 0, active: 0, lodMix: {}, source: 'unmounted' }
  }
  assert.doesNotThrow(() => deriveRawSamplesFromProvenance(raw))
  raw.mountedCity.samples[1].rendererMetrics.population.observed = 99
  assert.throws(() => deriveRawSamplesFromProvenance(raw), /population.*consistent|exact/i)
})

test('refuses an empty city, disabled GPU, and unavailable required metrics', () => {
  const empty = provenance()
  empty.mountedCity.samples[0].rendererMetrics.population = {
    observed: 0,
    active: 0,
    lodMix: {},
    source: 'fake-backend'
  }
  assert.throws(() => deriveRawSamplesFromProvenance(empty), /empty city|populated/i)

  const disabled = provenance()
  disabled.mountedCity.samples[0].rendererMetrics.gpuEnabled = false
  assert.throws(() => deriveRawSamplesFromProvenance(disabled), /GPU.*disabled|GPU.*enabled/i)

  const unavailable = provenance()
  delete unavailable.mountedCity.samples[0].rendererMetrics.worldUpdateMs
  assert.throws(() => deriveRawSamplesFromProvenance(unavailable), /worldUpdateMs.*unavailable|required metric/i)
})

test('orchestrates injected packaged launcher, CDP, process, renderer, and clock probes', async () => {
  const launches = []
  const phases = []
  let now = 0
  const result = await runPackagedLunarCityMeasurement(
    {
      binaryPath: BINARY,
      expectedGitSha: SHA,
      sampleCount: 2,
      sampleIntervalMs: 1_000,
      warmupDurationMs: 0
    },
    {
      inspectTarget: () => ({ binaryPath: BINARY, buildStamp: cleanStamp() }),
      makeTempRoot: () => '/private/tmp/lunar-city-run-7',
      makeLaunchNonce: () => 'nonce-7',
      reserveDebugPort: async () => ({ port: 49321, release: async () => {} }),
      launch: plan => {
        launches.push(plan)
        return { pid: 999, kill() {} }
      },
      connectCdp: async ({ port }) => ({
        cdp: { port, close() {} },
        handshake: {
          bridgeVersion: 1,
          launchNonce: 'nonce-7',
          buildSha: SHA,
          packaged: true,
          mainPid: 999,
          rendererIdentity: { pid: 20, startedAtMs: 1_000 },
          supportedPhases: ['baseline-shell', 'mounted-city'],
          processMetricsSource: 'electron.app.getAppMetrics'
        }
      }),
      preparePhase: async (_cdp, name) => phases.push(name),
      processProbe: async () => processRows(20, phases.length === 1 ? 1 : 2),
      rendererProbe: async () => rendererMetrics({ gpuMemoryMiB: phases.length === 1 ? 40 : 50 }),
      clock: {
        now: () => now,
        sleep: async milliseconds => {
          now += milliseconds
        }
      },
      cleanup: async () => {}
    }
  )

  assert.equal(launches.length, 1)
  assert.deepEqual(phases, ['baseline-shell', 'mounted-city'])
  assert.equal(result.rawProvenance.provenanceVersion, 1)
  assert.deepEqual(result.rawSamples.cpuDeltaPp, [3, 3])
  assert.equal(result.buildStamp.commit, SHA)
  assert.equal(result.packagedPerformanceEligible, false)
  assert.equal(result.evidenceClass, 'deterministic')
})

test('cleanup attempts close, terminate, wait, kill, and removal despite earlier failures', async () => {
  const calls = []
  await assert.rejects(
    cleanupIsolatedRun(
      {
        cdp: {
          close: () => {
            calls.push('close')
            throw new Error('close failed')
          }
        },
        child: {
          kill: signal => {
            calls.push(signal)
            if (signal === 'SIGTERM') throw new Error('term failed')
          },
          waitForExit: async () => {
            calls.push('wait')
            throw new Error('wait failed')
          }
        },
        tempRoot: '/tmp/run'
      },
      {
        removeTemp: () => {
          calls.push('remove')
        }
      }
    ),
    /cleanup failed/i
  )
  assert.deepEqual(calls, ['close', 'SIGTERM', 'wait', 'SIGKILL', 'remove'])
})
