import assert from 'node:assert/strict'

import { describe, test } from 'vitest'

import {
  buildLunarCityPerfHandshake,
  createChromiumMemoryInfraGpuProbe,
  createLunarCityPerfMainController,
  parseChromiumMemoryInfraGpuAllocation,
  resolveLunarCityPerfLaunch,
  sanitizeAppMetrics
} from './lunar-city-perf-main'

const stamp = {
  builtAt: '2026-08-31T12:00:00.000Z',
  commit: 'a'.repeat(40),
  dirty: false,
  schemaVersion: 1,
  source: 'local'
} as const

describe('Lunar City packaged performance launch gate', () => {
  test('is absent in normal, dev, flag-only, nonce-only, and malformed-nonce launches', () => {
    for (const input of [
      { isPackaged: true, env: {} },
      {
        isPackaged: false,
        env: { HERMES_LUNAR_CITY_PERF_ACCEPTANCE: '1', HERMES_LUNAR_CITY_PERF_NONCE: 'n'.repeat(32) }
      },
      { isPackaged: true, env: { HERMES_LUNAR_CITY_PERF_ACCEPTANCE: '1' } },
      { isPackaged: true, env: { HERMES_LUNAR_CITY_PERF_NONCE: 'n'.repeat(32) } },
      {
        isPackaged: true,
        env: { HERMES_LUNAR_CITY_PERF_ACCEPTANCE: '1', HERMES_LUNAR_CITY_PERF_NONCE: 'short' }
      }
    ]) {
      assert.equal(resolveLunarCityPerfLaunch({ ...input, buildStamp: stamp }), undefined)
    }
  })

  test('accepts only a clean exact packaged build stamp and a unique nonce', () => {
    const launch = resolveLunarCityPerfLaunch({
      buildStamp: stamp,
      env: { HERMES_LUNAR_CITY_PERF_ACCEPTANCE: '1', HERMES_LUNAR_CITY_PERF_NONCE: 'nonce-0123456789abcdef-unique' },
      isPackaged: true
    })

    assert.deepEqual(launch, {
      buildStamp: stamp,
      launchNonce: 'nonce-0123456789abcdef-unique'
    })
    assert.equal(
      resolveLunarCityPerfLaunch({
        buildStamp: { ...stamp, dirty: true },
        env: { HERMES_LUNAR_CITY_PERF_ACCEPTANCE: '1', HERMES_LUNAR_CITY_PERF_NONCE: 'nonce-0123456789abcdef-unique' },
        isPackaged: true
      }),
      undefined
    )
  })
})

test('handshake carries exact lifetime, build, phase, and process provenance', () => {
  assert.deepEqual(
    buildLunarCityPerfHandshake({
      buildStamp: stamp,
      launchNonce: 'nonce-0123456789abcdef-unique',
      mainPid: 41,
      rendererPid: 82,
      rendererStartedAtMs: 1234
    }),
    {
      bridgeVersion: 1,
      buildSha: stamp.commit,
      buildStamp: stamp,
      launchNonce: 'nonce-0123456789abcdef-unique',
      mainPid: 41,
      packaged: true,
      processMetricsSource: 'electron.app.getAppMetrics',
      rendererIdentity: { pid: 82, startedAtMs: 1234 },
      supportedPhases: ['baseline-shell', 'mounted-city']
    }
  )
})

test('app metrics retain only required native CPU and working-set fields', () => {
  assert.deepEqual(
    sanitizeAppMetrics([
      {
        cpu: { idleWakeupsPerSecond: 9, percentCPUUsage: 2.5 },
        memory: { peakWorkingSetSize: 456, privateBytes: 789, workingSetSize: 123 },
        name: 'Tab',
        pid: 55,
        secret: 'must-not-cross-ipc',
        type: 'Tab'
      }
    ]),
    [{ cpu: { percentCPUUsage: 2.5 }, memory: { workingSetSize: 123 }, pid: 55, type: 'Tab' }]
  )
})

describe('chromium-memory-infra-v1 GPU parser', () => {
  test('sums attributable GPU allocator effective sizes without using RSS', () => {
    const result = parseChromiumMemoryInfraGpuAllocation({
      traceEvents: [
        {
          args: {
            dumps: {
              allocators: {
                'gpu/gl/textures': { attrs: { effective_size: { units: 'bytes', value: '100000' } } },
                'gpu/shared_images': { attrs: { effective_size: { units: 'bytes', value: '80000' } } },
                malloc: { attrs: { effective_size: { units: 'bytes', value: 'ffffff' } } }
              }
            }
          },
          cat: 'disabled-by-default-memory-infra',
          ph: 'v'
        }
      ]
    })

    assert.deepEqual(result, { gpuMemoryMiB: 1.5, gpuMemorySource: 'chromium-memory-infra-v1' })
  })

  test('reports unavailable for missing or ambiguous allocator attribution', () => {
    assert.deepEqual(parseChromiumMemoryInfraGpuAllocation({ traceEvents: [] }), {
      gpuMemoryMiB: null,
      gpuMemorySource: 'unavailable'
    })
    assert.deepEqual(
      parseChromiumMemoryInfraGpuAllocation({
        traceEvents: [
          {
            args: { dumps: { allocators: { gpu: { attrs: { effective_size: { units: 'objects', value: '2' } } } } } },
            cat: 'disabled-by-default-memory-infra',
            ph: 'v'
          }
        ]
      }),
      { gpuMemoryMiB: null, gpuMemorySource: 'unavailable' }
    )
  })
})

test('controller binds bootstrap, metrics, and renderer requests to the launched window', async () => {
  const sent: Array<{ channel: string; payload: unknown }> = []

  const sender = {
    getOSProcessId: () => 82,
    id: 7,
    isDestroyed: () => false,
    send: (channel: string, payload: unknown) => sent.push({ channel, payload })
  }

  const otherSender = { ...sender, id: 8 }

  const controller = createLunarCityPerfMainController({
    appMetrics: () => [{ cpu: { percentCPUUsage: 2 }, memory: { workingSetSize: 400 }, pid: 82, type: 'Tab' }],
    gpuSnapshot: async () => ({ gpuMemoryMiB: 12, gpuMemorySource: 'chromium-memory-infra-v1' }),
    launch: { buildStamp: stamp, launchNonce: 'nonce-0123456789abcdef-unique' },
    mainPid: 41,
    now: () => 1234,
    ownsSender: candidate => candidate.id === 7,
    requestTimeoutMs: 1_000
  })

  assert.equal(controller.bootstrap({ sender: otherSender }), undefined)
  assert.deepEqual(controller.bootstrap({ sender }), {
    bridgeVersion: 1,
    buildSha: stamp.commit,
    buildStamp: stamp,
    launchNonce: 'nonce-0123456789abcdef-unique',
    mainPid: 41,
    packaged: true,
    processMetricsSource: 'electron.app.getAppMetrics',
    rendererIdentity: { pid: 82, startedAtMs: 1234 },
    supportedPhases: ['baseline-shell', 'mounted-city']
  })
  assert.deepEqual(controller.processMetrics({ sender }), [
    { cpu: { percentCPUUsage: 2 }, memory: { workingSetSize: 400 }, pid: 82, type: 'Tab' }
  ])
  assert.equal(controller.processMetrics({ sender: otherSender }), undefined)

  const pending = controller.requestRenderer({ sender }, 'snapshot')
  const request = sent.at(-1)?.payload as { requestId: string }
  assert.equal(sent.at(-1)?.channel, 'hermes:lunar-city-perf:request')
  assert.equal(controller.resolveRendererResponse({ sender: otherSender }, request.requestId, { forged: true }), false)
  assert.equal(controller.resolveRendererResponse({ sender }, request.requestId, { frameMs: 4 }), true)
  assert.deepEqual(await pending, {
    gpuMemoryMiB: 12,
    gpuMemorySource: 'chromium-memory-infra-v1',
    frameMs: 4,
    rendererPid: 82,
    rendererStartedAtMs: 1234
  })

  controller.dispose()
  assert.equal(await controller.requestRenderer({ sender }, 'snapshot'), undefined)
})

test('controller reports unavailable GPU attribution without substituting RSS', async () => {
  const responses: Array<{ channel: string; payload: unknown }> = []

  const sender = {
    getOSProcessId: () => 82,
    id: 7,
    isDestroyed: () => false,
    send: (channel: string, payload: unknown) => responses.push({ channel, payload })
  }

  const controller = createLunarCityPerfMainController({
    appMetrics: () => [],
    gpuSnapshot: async () => ({ gpuMemoryMiB: null, gpuMemorySource: 'unavailable' }),
    launch: { buildStamp: stamp, launchNonce: 'nonce-0123456789abcdef-unique' },
    mainPid: 41,
    now: () => 1234,
    ownsSender: candidate => candidate.id === 7,
    requestTimeoutMs: 1_000
  })

  controller.bootstrap({ sender })
  const pending = controller.requestRenderer({ sender }, 'snapshot')
  const request = responses.at(-1)?.payload as { requestId: string }
  controller.resolveRendererResponse({ sender }, request.requestId, {})

  assert.deepEqual(await pending, {
    gpuMemoryMiB: null,
    gpuMemorySource: 'unavailable',
    rendererPid: 82,
    rendererStartedAtMs: 1234
  })
})

test('GPU probe requests only Chromium memory-infra and fails closed on capture errors', async () => {
  let requested: unknown

  const probe = createChromiumMemoryInfraGpuProbe(async config => {
    requested = config

    return {
      traceEvents: [
        {
          args: {
            dumps: { allocators: { 'gpu/gl': { attrs: { effective_size: { units: 'bytes', value: '200000' } } } } }
          },
          cat: 'disabled-by-default-memory-infra',
          ph: 'v'
        }
      ]
    }
  })

  assert.deepEqual(await probe(), { gpuMemoryMiB: 2, gpuMemorySource: 'chromium-memory-infra-v1' })
  assert.deepEqual(requested, {
    category: 'disabled-by-default-memory-infra',
    dumpMode: 'detailed',
    periodicIntervalMs: 250
  })

  const unavailable = createChromiumMemoryInfraGpuProbe(async () => {
    throw new Error('trace unavailable')
  })

  assert.deepEqual(await unavailable(), { gpuMemoryMiB: null, gpuMemorySource: 'unavailable' })
})
