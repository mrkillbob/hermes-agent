import type { Page } from '@playwright/test'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { TransportEvidence } from '../../e2e/core/transport-evidence'
import { observeTransportEvidence } from '../../e2e/core/transport-evidence-cdp'

const unexpectedTransport = vi.fn(() => {
  throw new Error('Unexpected live transport')
})

beforeEach(() => {
  unexpectedTransport.mockClear()
  vi.stubGlobal('fetch', unexpectedTransport)
  vi.stubGlobal('WebSocket', unexpectedTransport)
})
afterEach(() => {
  expect(unexpectedTransport).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})

it('orders a pre-ACK terminal frame and correlates results without retaining secret-bearing data', () => {
  let now = 100
  const evidence = new TransportEvidence('transcript-integrity', () => now)

  const send = (direction: 'sent' | 'received', frame: unknown) =>
    evidence.frame('secret-socket', direction, 1, JSON.stringify(frame))

  send('sent', {
    id: 'secret-request',
    method: 'session.resume',
    params: {
      session_id: 'secret-session',
      profile: 'secret-profile',
      text: 'secret-prompt',
      ticket: 'secret-ticket',
      url: 'https://secret-url',
      headers: { authorization: 'secret-auth' },
      expected_revision: 4
    }
  })
  now = 105
  send('received', {
    method: 'event',
    params: {
      type: 'message.complete',
      session_id: 'secret-session',
      profile: 'secret-profile',
      seq: 9,
      authority_epoch: 7,
      execution_generation: 3,
      payload: { text: 'secret-answer', running: false }
    }
  })
  now = 103
  send('received', {
    id: 'secret-request',
    result: {
      session_id: 'secret-session',
      profile: 'secret-profile',
      running: true,
      authority_epoch: 7,
      execution_generation: 3,
      last_sequence: 8,
      revision: 5,
      messages: [{ content: 'secret-history' }]
    }
  })
  send('sent', { id: 2, method: 'prompt.submit', params: { session_id: 'secret-session', text: 'secret-text' } })
  send('received', { id: 2, error: { code: -32603, message: 'secret-error', data: { token: 'secret-token' } } })
  const snapshot = evidence.snapshot()
  expect(snapshot.entries.map(entry => entry.kind)).toEqual(['request', 'event', 'result', 'request', 'error'])
  expect(snapshot.entries.map(entry => entry.ms)).toEqual([0, 5, 5, 5, 5])
  expect(snapshot.entries[2]).toMatchObject({
    request: snapshot.entries[0].request,
    session: snapshot.entries[1].session,
    profile: snapshot.entries[0].profile,
    running: true,
    authorityEpoch: 7,
    executionGeneration: 3,
    lastSequence: 8,
    revision: 5
  })
  expect(snapshot.entries[4]).toMatchObject({ errorCode: -32603, method: 'prompt.submit' })
  expect(JSON.stringify(snapshot)).not.toContain('secret-')
})

it('bounds every ring boundary and explicitly counts lost entries and correlations', () => {
  const evidence = new TransportEvidence('first-run-launch-2')
  evidence.frame('socket', 'received', 1, JSON.stringify({ method: 'event', params: { type: 'message.complete' } }))

  for (let id = 0; id < 516; id++) {
    evidence.frame('socket', 'sent', 1, JSON.stringify({ id, method: 'ping' }))
  }

  evidence.frame('socket', 'received', 1, JSON.stringify({ id: 0, result: {} }))
  const snapshot = evidence.snapshot()
  expect(snapshot.entries).toHaveLength(512)
  expect(snapshot.discardedEntries).toBe(5)
  expect(snapshot.discardedCorrelations).toBe(4)
  expect(snapshot.uncorrelatedResponses).toBe(1)
  expect(snapshot.entries.some(entry => entry.type === 'message.complete')).toBe(false)
})

it('keeps equal request IDs on different sockets and separate launch epochs independent', () => {
  const first = new TransportEvidence('first-run-launch-1')
  const second = new TransportEvidence('first-run-launch-2')
  const request = JSON.stringify({ id: 1, method: 'session.create' })
  const result = JSON.stringify({ id: 1, result: { session_id: 'same-session' } })
  first.frame('socket-a', 'sent', 1, request)
  first.frame('socket-b', 'sent', 1, request)
  first.frame('socket-b', 'received', 1, result)
  first.frame('socket-a', 'received', 1, result)
  second.frame('socket-a', 'sent', 1, request)
  second.frame('socket-a', 'received', 1, result)
  expect(first.snapshot().entries.map(entry => entry.request)).toEqual([1, 2, 2, 1])
  expect(second.snapshot().entries.map(entry => entry.request)).toEqual([1, 1])
  expect(first.snapshot().scope).toBe('first-run-launch-1')
  expect(second.snapshot().scope).toBe('first-run-launch-2')
})

it('bounds pseudonym maps and never exports raw identifiers', () => {
  const evidence = new TransportEvidence('first-run-launch-1')

  for (let id = 0; id < 514; id++) {
    evidence.frame(
      `socket-${id}`,
      'sent',
      1,
      JSON.stringify({
        id,
        method: 'session.resume',
        params: { session_id: `raw-session-${id}`, profile: `raw-profile-${id}` }
      })
    )
  }

  expect(evidence.snapshot().discardedIdentities).toBe(6)
  expect(JSON.stringify(evidence.snapshot())).not.toMatch(/raw-session|raw-profile|socket-/)
})

it('ignores malformed, binary, oversized, unallowlisted and uncorrelated frames', () => {
  const evidence = new TransportEvidence('transcript-integrity')
  evidence.frame('socket', 'received', 2, 'binary')
  evidence.frame('socket', 'received', 1, '{')
  evidence.frame('socket', 'received', 1, 'x'.repeat(131_073))
  evidence.frame('socket', 'sent', 1, JSON.stringify({ id: 1, method: 'secret-method' }))
  evidence.frame('socket', 'received', 1, JSON.stringify({ method: 'event', params: { type: 'secret-event' } }))
  evidence.frame('socket', 'received', 1, JSON.stringify({ id: 'secret-id', result: { text: 'secret-result' } }))
  expect(evidence.snapshot()).toMatchObject({ entries: [], ignoredFrames: 5, uncorrelatedResponses: 1,
    oversizedFrames: 1, binaryFrames: 1, malformedFrames: 1 })
})

class FakeCdp {
  readonly listeners = new Map<string, Set<(event: never) => void>>()
  readonly send = vi.fn(async (_method: string) => ({}))
  readonly detach = vi.fn(async () => undefined)
  on(name: string, listener: (event: never) => void) {
    const listeners = this.listeners.get(name) ?? new Set()
    listeners.add(listener)
    this.listeners.set(name, listeners)
  }
  off(name: string, listener: (event: never) => void) {
    this.listeners.get(name)?.delete(listener)
  }
  emit(name: string, event: unknown) {
    for (const listener of this.listeners.get(name) ?? []) {
      listener(event as never)
    }
  }
}

function pageFor(cdp: FakeCdp): Page {
  return { context: () => ({ newCDPSession: vi.fn(async () => cdp) }) } as unknown as Page
}

function info(status = 'passed') {
  return {
    status,
    expectedStatus: 'passed',
    attach: vi.fn(async (_name: string, _options: { body: string; contentType: string }) => undefined)
  }
}

it('observes future frames on an already-open socket without any handshake event, and disposes on success', async () => {
  const cdp = new FakeCdp()
  const observer = await observeTransportEvidence(pageFor(cdp), 'first-run-launch-2')
  expect(cdp.send).toHaveBeenCalledExactlyOnceWith('Network.enable')
  expect([...cdp.listeners.keys()]).toEqual([
    'Network.webSocketFrameSent',
    'Network.webSocketFrameReceived',
    'Network.webSocketClosed'
  ])
  cdp.emit('Network.webSocketFrameSent', {
    requestId: 'pre-existing-secret-socket',
    response: {
      opcode: 1,
      payloadData: JSON.stringify({ id: 1, method: 'session.create', params: { text: 'secret' } })
    }
  })
  cdp.emit('Network.webSocketFrameReceived', {
    requestId: 'pre-existing-secret-socket',
    response: { opcode: 1, payloadData: JSON.stringify({ id: 1, result: { session_id: 'secret-session' } }) }
  })
  cdp.emit('Network.webSocketClosed', { requestId: 'pre-existing-secret-socket' })
  expect(observer.snapshot().entries.map(entry => entry.kind)).toEqual(['request', 'result', 'close'])
  expect(observer.snapshot().scope).toBe('first-run-launch-2')
  const result = info()
  await observer.finish(result, false)
  expect(result.attach).not.toHaveBeenCalled()
  expect(cdp.detach).toHaveBeenCalledTimes(1)
  expect([...cdp.listeners.values()].every(listeners => listeners.size === 0)).toBe(true)
  await observer.dispose()
  expect(cdp.detach).toHaveBeenCalledTimes(1)
})

it.each([false, true])('attaches only failure evidence and always cleans up (attachment rejects=%s)', async rejects => {
  const cdp = new FakeCdp()
  const observer = await observeTransportEvidence(pageFor(cdp), 'transcript-integrity')
  const result = info()

  if (rejects) {
    result.attach.mockRejectedValue(new Error('secret attachment error'))
  }

  await expect(observer.finish(result, true)).resolves.toBeUndefined()
  expect(result.attach).toHaveBeenCalledTimes(1)
  expect(result.attach.mock.calls[0][0]).toBe('transport-evidence-transcript-integrity')
  expect(JSON.parse(result.attach.mock.calls[0][1].body)).toMatchObject({ observation: 'ready', capacity: 512 })
  expect(cdp.detach).toHaveBeenCalledTimes(1)
})

it('reports unavailable observation without exporting setup errors or changing the test verdict', async () => {
  const cdp = new FakeCdp()
  cdp.send.mockRejectedValue(new Error('secret ticket or URL'))
  const observer = await observeTransportEvidence(pageFor(cdp), 'first-run-launch-1')
  const result = info('failed')
  await observer.finish(result, false)
  expect(JSON.parse(result.attach.mock.calls[0][1].body)).toMatchObject({ observation: 'unavailable', entries: [] })
  expect(JSON.stringify(observer.snapshot())).not.toContain('secret')
  expect(cdp.detach).toHaveBeenCalledTimes(1)
  expect([...cdp.listeners.values()].every(listeners => listeners.size === 0)).toBe(true)
})
