import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DICTATION_FINAL_TIMEOUT_MS, openDictationStream } from './voice-stream'

vi.mock('@/lib/sibling-ws-url', () => ({
  resolveSiblingWsUrl: vi.fn(async () => 'ws://localhost/api/audio/transcribe-stream?token=test')
}))

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []
  readyState = 0
  binaryType = ''
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn(() => { this.readyState = 3 })

  constructor(readonly url: string) { FakeWebSocket.instances.push(this) }
  open() { this.readyState = FakeWebSocket.OPEN; this.onopen?.() }
  message(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }) }
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function open(onPartial = vi.fn()) {
  const opening = openDictationStream({ connectionId: 'gateway-a', profile: 'worker_alpha' }, 16_000, onPartial)
  await Promise.resolve()
  const ws = FakeWebSocket.instances[0]
  ws.open()
  return { session: (await opening)!, ws }
}

describe('dictation stream lifecycle', () => {
  it('rejects an awaiting stop on cancel and ignores retired partials and audio', async () => {
    const partial = vi.fn()
    const { session, ws } = await open(partial)
    const outcome = session.stop().catch(error => error)
    session.cancel()
    expect(await outcome).toEqual(new Error('live transcription cancelled'))
    ws.message({ type: 'partial', text: 'late words' })
    session.pushAudio(new ArrayBuffer(2))
    expect(partial).not.toHaveBeenCalled()
    expect(ws.send).toHaveBeenCalledTimes(2) // initial rate + EOS
    expect(ws.close).toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('bounds a missing final response and closes the socket so blob transcription can take over', async () => {
    const { session, ws } = await open()
    const outcome = session.stop().catch(error => error)
    await vi.advanceTimersByTimeAsync(DICTATION_FINAL_TIMEOUT_MS)
    expect(await outcome).toEqual(new Error('live transcription final response timed out'))
    expect(ws.close).toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('returns a final transcript and cancels its deadline', async () => {
    const { session, ws } = await open()
    const stopped = session.stop()
    ws.message({ type: 'final', transcript: ' hello world ' })
    expect(await stopped).toBe('hello world')
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(DICTATION_FINAL_TIMEOUT_MS)
    expect(ws.close).toHaveBeenCalledTimes(1)
  })
})
