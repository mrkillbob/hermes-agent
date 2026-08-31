import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, realpathSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { SCENARIOS, isAcceptancePopulationState } from './lunar-city.mjs'
import {
  assembleLunarCityReceipt,
  canonicalJson,
  orchestrateLunarCityAcceptance,
  resolveScenarioMeasurement,
  validateIsolatedFixturePaths,
  validateCanonicalFixture
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
    environment: {
      chromiumVersion: '134.0.0.0',
      displayScaleFactor: 2,
      electronMode: 'packaged',
      electronVersion: '41.10.3',
      gpuEnabled: true,
      gpuInfo: { gpuDevice: [{ deviceString: 'Test GPU' }] },
      windowBounds: { height: 900, width: 1440 }
    },
    targetFps: 15,
    scenarioExecution: { scenario, actions: [] }
  }
  const value = {
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
    mountedClaims,
    hostEnvironment
  }
  const environment = {
    ...hostEnvironment,
    backendMode: 'unbound',
    chromiumVersion: mountedClaims.environment.chromiumVersion,
    cityPopulated: true,
    displayScale: 2,
    electronMode: 'packaged',
    electronVersion: mountedClaims.environment.electronVersion,
    gpuAdapter: 'Test GPU',
    gpuEnabled: true,
    windowSize: { height: 900, width: 1440 }
  }
  value.rawProvenance.acceptanceBindings = {
    environmentDigest: createHash('sha256').update(canonicalJson(environment)).digest('hex')
  }
  return value
}

const metadata = { note: 'supplemental operator note only' }
const hostEnvironment = {
  architecture: 'arm64',
  hardwareModel: 'Test Mac',
  os: 'macOS 15.6',
  powerState: 'ac'
}

test('defines a bounded measurement policy for every validator scenario', () => {
  assert.equal(SCENARIOS.length, 18)
  for (const scenario of SCENARIOS) {
    if (['25-active', '100-active', '250-lod', '30-minute-stability'].includes(scenario)) continue
    const policy = resolveScenarioMeasurement(scenario, {})
    assert.ok(policy.durationMs >= 30_000, scenario)
    assert.ok(policy.warmupDurationMs >= 30_000, scenario)
    assert.ok(policy.sampleCount >= 3, scenario)
    assert.equal((policy.sampleCount - 1) * policy.sampleIntervalMs, policy.durationMs, scenario)
  }
  assert.equal(resolveScenarioMeasurement('visible-idle', {}).durationMs, 60_000)
  assert.throws(() => resolveScenarioMeasurement('30-minute-stability', {}), /fixture/i)
  assert.throws(() => resolveScenarioMeasurement('visible-idle', { durationMs: 59_999 }), /cannot shorten/i)
  assert.throws(() => resolveScenarioMeasurement('30-minute-stability', { durationMs: 60_000 }), /cannot shorten/i)
})

test('assembles raw capture into a validator-shaped fake-backend packaged receipt without claiming eligibility', () => {
  const receipt = assembleLunarCityReceipt({
    capture: capture(),
    evidenceClass: 'fake-backend-packaged',
    metadata,
    hostEnvironment,
    scenario: 'balanced-overview',
    timestamp: '2026-08-31T12:01:00.000Z'
  })

  assert.equal(receipt.evidenceClass, 'fake-backend-packaged')
  assert.equal('packagedPerformanceEligible' in receipt, false)
  assert.deepEqual(receipt.rawProvenance, capture().rawProvenance)
  assert.equal(receipt.gitSha, SHA)
  assert.equal(receipt.environment.backendMode, 'unbound')
  assert.equal(receipt.fpsCap, 15)
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
          hostEnvironment,
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
    version: 'lunar-city-population-v3',
    evidenceClass: 'fake-backend-packaged',
    expectedPopulation: 25,
    hermesHome: '/isolated/run/hermes-home',
    contractPath: '/isolated/run/population.json',
    root: '/isolated/run',
    userDataDir: '/isolated/run/user-data'
  }
  assert.equal(resolveScenarioMeasurement('25-active', { fixture }).fixture.expectedPopulation, 25)
  assert.throws(() => resolveScenarioMeasurement('100-active', { fixture }), /population 100/i)
  const stabilityFixture = { ...fixture, expectedPopulation: 100 }
  assert.equal(resolveScenarioMeasurement('30-minute-stability', { fixture: stabilityFixture }).durationMs, 1_800_000)
  const subset = resolveScenarioMeasurement('balanced-overview', { fixture })
  assert.equal(subset.fixture.expectedPopulation, 25)
})

test('every exact population scenario requires canonical v3 fixture evidence', () => {
  for (const scenario of ['25-active', '100-active', '250-lod', '30-minute-stability']) {
    assert.throws(() => resolveScenarioMeasurement(scenario, {}), /canonical.*fixture|required/i, scenario)
  }
})

test('canonical fixture verifies v3 bytes, digest, source mix, and authenticated observed subagents', () => {
  const unsigned = {
    activity: { active: 1, idle: 0, unavailable: 0 },
    entities: Array.from({ length: 25 }, (_, index) => ({
      activity: 'active',
      connectionId: 'local',
      exactKey: index === 0 ? 'subagent-key' : `profile-${index}`,
      kind: index === 0 ? 'subagent' : 'profile'
    })),
    entitiesByKind: { profile: 24, session: 0, subagent: 1, task: 0, worker: 0 },
    groups: [],
    leaderFamilies: [],
    lod: { aggregate: 0, far: 0, mid: 0, near: 1 },
    population: 25,
    version: 'lunar-city-population-v3'
  }
  const contract = {
    ...unsigned,
    digest: createHash('sha256').update(canonicalJson(unsigned)).digest('hex')
  }
  const bytes = `${JSON.stringify(contract, null, 2)}\n`
  const proof = {
    authenticated: true,
    source: 'owned-authenticated-gateways-v1',
    gatewayProcesses: [101, 102, 103],
    entityKeys: ['subagent-key'],
    observedPopulation: 25,
    sourceMix: { local: 25 }
  }
  const validated = validateCanonicalFixture({ bytes, proof })
  assert.equal(validated.contractDigest, contract.digest)
  assert.equal(validated.bytesSha256, createHash('sha256').update(bytes).digest('hex'))

  assert.throws(() => validateCanonicalFixture({ bytes: bytes.replace('subagent-key', 'tampered'), proof }), /digest/i)
  assert.throws(() => validateCanonicalFixture({ bytes, proof: { ...proof, authenticated: false } }), /authenticated/i)
  assert.throws(() => validateCanonicalFixture({ bytes, proof: { ...proof, entityKeys: [] } }), /subagent/i)
})

test('fixture isolation rejects broad, real-home, and symlinked paths and requires a run-owned sentinel', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'lunar-owned-')))
  const hermesHome = join(root, 'hermes-home')
  const userDataDir = join(root, 'user-data')
  const contractPath = join(root, 'population.json')
  mkdirSync(hermesHome)
  mkdirSync(userDataDir)
  writeFileSync(contractPath, '{}')
  const fixture = {
    contractPath,
    evidenceClass: 'fake-backend-packaged',
    expectedPopulation: 25,
    hermesHome,
    root,
    runNonce: 'owned-nonce',
    userDataDir,
    version: 'lunar-city-population-v3'
  }
  assert.throws(() => validateIsolatedFixturePaths(fixture), /sentinel|owned/i)
  writeFileSync(
    join(root, '.lunar-city-fixture-owner.json'),
    JSON.stringify({ nonce: 'owned-nonce', pid: process.pid, version: 1 })
  )
  assert.equal(validateIsolatedFixturePaths(fixture).root, root)
  const link = join(root, 'linked-home')
  symlinkSync(hermesHome, link)
  assert.throws(() => validateIsolatedFixturePaths({ ...fixture, hermesHome: link }), /symlink|canonical/i)
  assert.throws(
    () => validateIsolatedFixturePaths({ ...fixture, root: process.env.HOME, hermesHome: process.env.HOME }),
    /home|broad|isolated/i
  )
})

test('generic receipt assembly refuses live relabel and missing runtime-owned environment or scheduler fps', () => {
  assert.throws(
    () =>
      assembleLunarCityReceipt({
        capture: capture(),
        evidenceClass: 'supervised-live',
        hostEnvironment,
        metadata,
        scenario: 'balanced-overview',
        timestamp: '2026-08-31T12:01:00.000Z'
      }),
    /supervised-live.*dedicated/i
  )
  const noFps = capture()
  delete noFps.mountedClaims.targetFps
  assert.throws(
    () =>
      assembleLunarCityReceipt({
        capture: noFps,
        evidenceClass: 'fake-backend-packaged',
        hostEnvironment,
        metadata,
        scenario: 'balanced-overview',
        timestamp: '2026-08-31T12:01:00.000Z'
      }),
    /fps/i
  )
})

test('fixture binding rejects mounted source/population mismatch instead of trusting descriptor claims', () => {
  const value = capture()
  value.rawProvenance.acceptanceBindings.fixture = {
    bytesSha256: 'a'.repeat(64),
    contractDigest: 'b'.repeat(64),
    expectedPopulation: 25,
    proofDigest: 'c'.repeat(64),
    sourceMix: { local: 25 },
    subagentKeys: ['subagent-key']
  }
  const fakeEnvironment = {
    ...hostEnvironment,
    backendMode: 'fake-backend',
    chromiumVersion: '134.0.0.0',
    cityPopulated: true,
    displayScale: 2,
    electronMode: 'packaged',
    electronVersion: '41.10.3',
    gpuAdapter: 'Test GPU',
    gpuEnabled: true,
    windowSize: { height: 900, width: 1440 }
  }
  value.rawProvenance.acceptanceBindings.environmentDigest = createHash('sha256')
    .update(canonicalJson(fakeEnvironment))
    .digest('hex')
  assert.throws(
    () =>
      assembleLunarCityReceipt({
        capture: value,
        evidenceClass: 'fake-backend-packaged',
        hostEnvironment,
        metadata,
        scenario: 'balanced-overview',
        timestamp: '2026-08-31T12:01:00.000Z'
      }),
    /source mix|fixture binding/i
  )
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
      hostEnvironment,
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
      hostEnvironment,
      scenario: 'disposal',
      timestamp: '2026-08-31T12:01:00.000Z'
    }).disposal,
    'disposed'
  )
})
