import { describe, expect, it, vi } from 'vitest'

import { JsonRpcGatewayError, JsonRpcRequestChannel, type JsonRpcTransport } from './json-rpc-channel.js'

const spyTransport = () => {
  const sent: string[] = []
  const transport: JsonRpcTransport = { send: text => void sent.push(text) }

  return { sent, transport, last: () => JSON.parse(sent.at(-1) ?? '{}') as { id: string; method: string } }
}

describe('JsonRpcRequestChannel', () => {
  it('routes responses to the pending call and keeps JSON-RPC code/data on errors', async () => {
    const events: string[] = []
    const channel = new JsonRpcRequestChannel({ onEvent: ev => void events.push(ev.type), requestIdPrefix: 'x' })
    const { transport, last } = spyTransport()

    channel.attach(transport)

    const ok = channel.request<{ ok: boolean }>('session.create', { cols: 80 })
    const okId = last().id
    const failing = channel.request('projects.create')
    const failId = last().id

    expect(okId).toBe('x1')
    channel.handleFrame(JSON.stringify({ id: okId, jsonrpc: '2.0', result: { ok: true } }))
    channel.handleFrame(
      JSON.stringify({
        error: { code: -32601, data: { method: 'projects.create' }, message: 'unknown method: projects.create' },
        id: failId,
        jsonrpc: '2.0'
      })
    )
    channel.handleFrame(JSON.stringify({ jsonrpc: '2.0', method: 'event', params: { type: 'session.info', payload: {} } }))
    // Non-JSON and unknown ids are ignored, never thrown.
    expect(channel.handleFrame('not json')).toBeNull()
    channel.handleFrame(JSON.stringify({ id: 'never-sent', jsonrpc: '2.0', result: 1 }))

    await expect(ok).resolves.toEqual({ ok: true })
    const error = (await failing.catch((e: unknown) => e)) as JsonRpcGatewayError

    expect(error).toBeInstanceOf(JsonRpcGatewayError)
    expect(error.code).toBe(-32601)
    expect(error.data).toEqual({ method: 'projects.create' })
    expect(events).toEqual(['session.info'])
  })

  it('detach fails every in-flight call and a per-call timeout names the method', async () => {
    vi.useFakeTimers()

    try {
      const channel = new JsonRpcRequestChannel({ requestTimeoutMs: 60_000 })
      const { transport } = spyTransport()

      channel.attach(transport)

      const slow = expect(channel.request('a.slow', {}, 1_000)).rejects.toThrow('request timed out after 1s: a.slow')
      const untilDetach = channel.request('b.wait')

      await vi.advanceTimersByTimeAsync(1_000)
      await slow

      channel.detach(new Error('gateway exited (1)'))
      await expect(untilDetach).rejects.toThrow('gateway exited (1)')
      await expect(channel.request('c.after')).rejects.toThrow('gateway not connected')
    } finally {
      vi.useRealTimers()
    }
  })

  it('heartbeat pings while frames keep arriving and reports a silent transport', async () => {
    vi.useFakeTimers()

    try {
      const failures: string[] = []

      const channel = new JsonRpcRequestChannel({
        heartbeatDeadlineMs: 300,
        heartbeatIntervalMs: 100,
        onHeartbeatFailure: e => void failures.push(e.message)
      })

      const { sent, transport } = spyTransport()

      channel.attach(transport)
      channel.startHeartbeat()

      for (let i = 0; i < 6; i++) {
        await vi.advanceTimersByTimeAsync(100)
        channel.handleFrame(JSON.stringify({ jsonrpc: '2.0', method: 'event', params: { type: 'status.update' } }))
      }

      expect(sent.filter(f => f.includes('gateway.ping')).length).toBeGreaterThanOrEqual(5)
      expect(failures).toEqual([])

      await vi.advanceTimersByTimeAsync(400)
      expect(failures).toEqual(['WebSocket heartbeat acknowledgement timed out'])
      // Failure stops the timer: no further pings after the report.
      const pings = sent.length
      await vi.advanceTimersByTimeAsync(500)
      expect(sent.length).toBe(pings)
    } finally {
      vi.useRealTimers()
    }
  })
})
