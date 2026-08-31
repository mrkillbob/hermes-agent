import assert from 'node:assert/strict'
import { test } from 'node:test'

import { SCENARIOS, isAcceptancePopulationState } from './lunar-city.mjs'
import {
  assembleLunarCityReceipt,
  orchestrateLunarCityAcceptance,
  resolveScenarioMeasurement
} from './lunar-city-orchestrator.mjs'

const SHA = 'a'.repeat(40)

function capture(scenario = 'balanced-overview') {
  const timestamps = scenario === 'visible-idle' ? [0, 30_000, 60_000] : [0, 15_000, 30_000]
  const rawSamples = {
    frameMs: [18, 19, 20],
    worldUpdateMs: [2, 2, 3],
    cpuDeltaPp: [1, 2, 2],
    gpuMemoryDeltaMiB: [10, 12, 11],
    residentMemoryMiB: [500, 501, 502],
    renderFrames: [4, 4, 4],
    drawCalls: [80, 80, 80],
    visibleTriangles: [400_000, 400_000, 400_000],
    activeAnimations: [10, 10, 10],
    entities: [25, 25, 25],
    textures: [8, 8, 8],
    listeners: [4, 4, 4],
    timers: [1, 1, 1]
  }
  const mountedClaims = {
    durationMs: timestamps.at(-1),
    warmupDurationMs: 30_000,
    population: { observed: 25, active: 8, lodMix: { near: 24, far: 1 }, source: 'standard-adapters-v1' },
    populationSourceMix: { local: 9, 'remote-lab': 8, 'remote-archive': 8 },
    qualityTier: 'Balanced',
    internalRenderScale: 1,
    cameraState: 'overview',
    dialogueState: 'idle',
    lifecycleState: 'mounted',
    environment: { electronMode: 'packaged', gpuEnabled: true },
    scenarioExecution: { scenario, actions: [] }
  }
  return {
    buildStamp: {
      schemaVersion: 1,
      commit: SHA,
      branch: null,
      builtAt: '2026-08-31T12:00:00.000Z',
      dirty: false,
      source: 'local'
    },
    package: { binaryPath: '/packages/Hermes.app/Contents/MacOS/Hermes' },
    rawProvenance: {
      provenanceVersion: 3,
      bridgeHandshake: {
        bridgeVersion: 1,
        buildSha: SHA,
        launchNonce: 'nonce',
        packaged: true,
        mainPid: 12,
        rendererIdentity: { pid: 20, startedAtMs: 1_000 },
        supportedPhases: ['baseline-shell', 'mounted-city'],
        processMetricsSource: 'electron.app.getAppMetrics'
      },
      baselineShell: {
        samples: [
          {
            processMetrics: [{ cpu: { percentCPUUsage: 2 } }],
            rendererMetrics: { gpuMemoryMiB: 40 }
          }
        ]
      },
      mountedCity: { samples: [] }
    },
    rawSamples,
    sampleTimestampsMs: timestamps,
    mountedClaims
  }
}

const metadata = {
  architecture: 'arm64',
  backendMode: 'fake-backend',
  chromiumVersion: '134.0.0.0',
  displayScale: 2,
  electronVersion: '41.10.3',
  gpuAdapter: 'Test GPU',
  hardwareModel: 'Test Mac',
  os: 'macOS 15.6',
  powerState: 'ac',
  windowSize: { width: 1440, height: 900 }
}

test('defines a bounded measurement policy for every validator scenario', () => {
  assert.equal(SCENARIOS.length, 18)
  for (const scenario of SCENARIOS) {
    const policy = resolveScenarioMeasurement(scenario, {})
    assert.ok(policy.durationMs >= 30_000, scenario)
    assert.ok(policy.warmupDurationMs >= 30_000, scenario)
    assert.ok(policy.sampleCount >= 3, scenario)
    assert.equal((policy.sampleCount - 1) * policy.sampleIntervalMs, policy.durationMs, scenario)
  }
  assert.equal(resolveScenarioMeasurement('visible-idle', {}).durationMs, 60_000)
  assert.equal(resolveScenarioMeasurement('30-minute-stability', {}).durationMs, 1_800_000)
  assert.throws(() => resolveScenarioMeasurement('visible-idle', { durationMs: 59_999 }), /cannot shorten/i)
  assert.throws(() => resolveScenarioMeasurement('30-minute-stability', { durationMs: 60_000 }), /cannot shorten/i)
})

test('assembles raw capture into a validator-shaped fake-backend packaged receipt without claiming eligibility', () => {
  const receipt = assembleLunarCityReceipt({
    capture: capture(),
    evidenceClass: 'fake-backend-packaged',
    metadata,
    scenario: 'balanced-overview',
    timestamp: '2026-08-31T12:01:00.000Z'
  })

  assert.equal(receipt.evidenceClass, 'fake-backend-packaged')
  assert.equal('packagedPerformanceEligible' in receipt, false)
  assert.deepEqual(receipt.rawProvenance, capture().rawProvenance)
  assert.equal(receipt.gitSha, SHA)
  assert.equal(receipt.environment.backendMode, 'fake-backend')
  assert.deepEqual(receipt.measurement.sampleTimestampsMs, [0, 15_000, 30_000])
})

test('top-level orchestration writes raw and complete receipts then fails closed unless validation is eligible', async () => {
  const writes = new Map()
  const seenOptions = []
  await assert.rejects(
    () =>
      orchestrateLunarCityAcceptance(
        {
          binaryPath: '/packages/Hermes.app/Contents/MacOS/Hermes',
          evidenceClass: 'fake-backend-packaged',
          expectedGitSha: SHA,
          metadata,
          outputDirectory: '/receipts',
          scenarios: ['balanced-overview']
        },
        {
          capture: async options => {
            seenOptions.push(options)
            return capture(options.scenario)
          },
          mkdir: () => undefined,
          nowIso: () => '2026-08-31T12:01:00.000Z',
          validate: () => ({ ok: true, packagedPerformanceEligible: false, errors: [] }),
          writeJson: (path, value) => writes.set(path, value)
        }
      ),
    /not eligible/i
  )

  assert.equal(seenOptions.length, 1)
  assert.equal(writes.size, 2)
  assert.ok([...writes.keys()].some(path => path.endsWith('.raw.json')))
  assert.ok([...writes.keys()].some(path => path.endsWith('.receipt.json')))
})

test('subagent-inclusive exact population remains blocked while runnable fixture subset is explicit', () => {
  const fixture = {
    contractVersion: 1,
    evidenceClass: 'fake-backend-packaged',
    expectedPopulation: 25,
    hermesHome: '/isolated/run/hermes-home',
    populationContractPath: '/isolated/run/population.json',
    root: '/isolated/run',
    subagentEmission: 'unsupported',
    userDataDir: '/isolated/run/user-data'
  }
  assert.throws(() => resolveScenarioMeasurement('25-active', { fixture }), /subagent.*unavailable|blocked/i)
  const subset = resolveScenarioMeasurement('balanced-overview', { fixture })
  assert.equal(subset.fixture.expectedPopulation, 25)
})

test('route-unmounted accepts truthful zero population while every mounted scenario requires population', () => {
  assert.equal(isAcceptancePopulationState('route-unmounted', false, 0), true)
  assert.equal(isAcceptancePopulationState('route-unmounted', true, 1), false)
  assert.equal(isAcceptancePopulationState('balanced-overview', true, 25), true)
  assert.equal(isAcceptancePopulationState('balanced-overview', false, 0), false)
})

test('assembles canonical recovery and disposal outcome vocabulary from lifecycle claims', () => {
  const recovered = capture('context-loss-recovery')
  recovered.mountedClaims.lifecycleState = 'recovered'
  assert.equal(
    assembleLunarCityReceipt({
      capture: recovered,
      evidenceClass: 'fake-backend-packaged',
      metadata,
      scenario: 'context-loss-recovery',
      timestamp: '2026-08-31T12:01:00.000Z'
    }).recovery,
    'recovered'
  )

  const disposed = capture('disposal')
  disposed.mountedClaims.lifecycleState = 'disposed'
  assert.equal(
    assembleLunarCityReceipt({
      capture: disposed,
      evidenceClass: 'fake-backend-packaged',
      metadata,
      scenario: 'disposal',
      timestamp: '2026-08-31T12:01:00.000Z'
    }).disposal,
    'disposed'
  )
})
