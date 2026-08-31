import assert from 'node:assert/strict'

import { test } from 'vitest'

import { createLunarCityPerfPreload } from './lunar-city-perf-preload'

const handshake = {
  bridgeVersion: 1,
  buildSha: 'a'.repeat(40),
  buildStamp: {
    builtAt: '2026-08-31T12:00:00.000Z',
    commit: 'a'.repeat(40),
    dirty: false,
    schemaVersion: 1,
    source: 'local'
  },
  launchNonce: 'nonce-0123456789abcdef-unique',
  mainPid: 41,
  packaged: true,
  processMetricsSource: 'electron.app.getAppMetrics',
  rendererIdentity: { pid: 82, startedAtMs: 1234 },
  supportedPhases: ['baseline-shell', 'mounted-city']
}

function fakeIpc(bootstrap: unknown) {
  const listeners = new Map<string, (...args: unknown[]) => void>()
  const sent: Array<{ channel: string; values: unknown[] }> = []

  return {
    invoke: async (channel: string, ...values: unknown[]) => ({ channel, values }),
    listeners,
    removeListener: (channel: string, listener: (...args: unknown[]) => void) => {
      if (listeners.get(channel) === listener) {
        listeners.delete(channel)
      }
    },
    send: (channel: string, ...values: unknown[]) => sent.push({ channel, values }),
    sendSync: () => bootstrap,
    sent,
    on: (channel: string, listener: (...args: unknown[]) => void) => listeners.set(channel, listener)
  }
}

test('preload bridge is wholly absent when main does not authorize launch', () => {
  assert.equal(createLunarCityPerfPreload(fakeIpc(undefined)), undefined)
})

test('handshake rejects the wrong nonce or bridge version and returns an immutable copy', () => {
  const bridge = createLunarCityPerfPreload(fakeIpc(handshake))!

  assert.equal(bridge.surface.handshake({ bridgeVersion: 1, launchNonce: 'wrong' }), null)
  assert.equal(bridge.surface.handshake({ bridgeVersion: 2, launchNonce: 'nonce-0123456789abcdef-unique' }), null)

  const first = bridge.surface.handshake({ bridgeVersion: 1, launchNonce: 'nonce-0123456789abcdef-unique' })!
  first.rendererIdentity.pid = 999
  assert.equal(
    bridge.surface.handshake({ bridgeVersion: 1, launchNonce: 'nonce-0123456789abcdef-unique' })!.rendererIdentity.pid,
    82
  )
})

test('surface exposes only bounded read and scenario operations', async () => {
  const bridge = createLunarCityPerfPreload(fakeIpc(handshake))!

  assert.deepEqual(await bridge.surface.processMetrics(), {
    channel: 'hermes:lunar-city-perf:process-metrics',
    values: []
  })
  assert.deepEqual(await bridge.surface.snapshot(), {
    channel: 'hermes:lunar-city-perf:renderer-request',
    values: ['snapshot', undefined]
  })
  assert.deepEqual(await bridge.surface.prepareBaselineShell(), {
    channel: 'hermes:lunar-city-perf:renderer-request',
    values: ['prepare-baseline-shell', undefined]
  })
  assert.deepEqual(await bridge.surface.mountCity(), {
    channel: 'hermes:lunar-city-perf:renderer-request',
    values: ['mount-city', undefined]
  })
  assert.deepEqual(await bridge.surface.runAction('orbit', { delta: 1 }), {
    channel: 'hermes:lunar-city-perf:renderer-request',
    values: ['scenario-action', { action: 'orbit', payload: { delta: 1 } }]
  })
})

test('renderer endpoint replies to bound requests and removes its listener on teardown', async () => {
  const ipc = fakeIpc(handshake)
  const bridge = createLunarCityPerfPreload(ipc)!
  const release = bridge.renderer.onRequest(async (action, payload) => ({ action, payload, proof: 1 }))
  const listener = ipc.listeners.get('hermes:lunar-city-perf:request')!

  await listener({}, { action: 'snapshot', payload: { exact: true }, requestId: '7:82:1' })
  assert.deepEqual(ipc.sent, [
    {
      channel: 'hermes:lunar-city-perf:response',
      values: ['7:82:1', { action: 'snapshot', payload: { exact: true }, proof: 1 }]
    }
  ])

  release()
  assert.equal(ipc.listeners.has('hermes:lunar-city-perf:request'), false)
})
