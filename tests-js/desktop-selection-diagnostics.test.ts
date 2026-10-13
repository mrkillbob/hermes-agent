import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import vm from 'node:vm'

import type { Page } from '@playwright/test'
import { expect, test, vi } from 'vitest'

import { beginSelectionCapture, installSelectionObserver } from './scripts/desktop-selection-diagnostics.ts'

function fixture() {
  const values = new Map<string, string>()

  class FixtureStorage {
    getItem(key: string) { return values.get(key) ?? null }
    setItem(key: string, value: string) { values.set(key, value) }
    removeItem(key: string) { values.delete(key) }
  }
  const sent: unknown[] = []

  class FixtureSocket {
    send(data: unknown) { sent.push(data) }
  }
  const storage = new FixtureStorage()
  const environment = { localStorage: storage as Storage, WebSocket: FixtureSocket as unknown as typeof WebSocket }
  let time = 100
  const originalSend = FixtureSocket.prototype.send
  const originalSetItem = FixtureStorage.prototype.setItem
  const observer = installSelectionObserver(environment, () => time)

  return { storage, sent, environment, clock: () => time, socket: new FixtureSocket(), originalSend, originalSetItem, observer, advance: () => { time += 10 } }
}

test('records initialization and session selection order without changing writes or frames', async () => {
  const f = fixture()
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'smoke-selection-'))
  let closed = false
  let disposed = false
  f.observer.stop()
  let observer = f.observer

  const handle = {
    evaluate: async (fn: (observer: typeof f.observer, arg: unknown) => unknown, arg: unknown) => {
      if (closed) { throw new Error('private renderer error') }

      return fn(observer, arg)
    },
    dispose: async () => { disposed = true },
  }

  const page = { evaluateHandle: async (fn: typeof installSelectionObserver) => {
    observer = vm.runInNewContext(`(${fn.toString()})()`, { window: f.environment, performance: { now: f.clock } })

    return handle
  } } as unknown as Page

  const capture = await beginSelectionCapture(page, out, 'old')
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

  try {
    f.storage.setItem('hermes.desktop.composer.model', 'anthropic/claude-opus-4.6')
    f.storage.setItem('hermes.desktop.composer.provider', 'openrouter')
    f.storage.setItem('hermes.desktop.composer.model-source', 'default')
    await capture.checkpoint('before-submit')
    f.advance()
    const frame = JSON.stringify({ method: 'session.create', params: { model: 'anthropic/claude-opus-4.6', provider: 'openrouter', messages: ['private prompt'], api_key: 'private-key' } })

    f.socket.send(frame)
    f.advance()
    f.storage.setItem('hermes.desktop.composer.model', 'mock-model')
    f.storage.setItem('hermes.desktop.composer.provider', 'custom')
    await capture.checkpoint('after-submit')
    const { events } = JSON.parse(fs.readFileSync(path.join(out, 'desktop-chat-old-selection.json'), 'utf8'))
    const creation = events.find((event: Record<string, unknown>) => event.event === 'session-create')!

    expect(creation).toMatchObject({ model: 'non-fixture', provider: 'non-fixture', elapsedMs: 10,
      composer: { source: 'default' } })
    expect(events.at(-1)).toMatchObject({ elapsedMs: 20, composer: { model: 'mock-model', provider: 'custom' } })
    expect(events.map((event: Record<string, unknown>) => event.sequence)).toEqual(events.map((_: unknown, index: number) => index + 1))
    expect(f.sent).toEqual([frame])
    expect(f.storage.getItem('hermes.desktop.composer.model')).toBe('mock-model')
    expect(JSON.stringify(events)).not.toMatch(/anthropic\/claude-opus-4.6|openrouter|private prompt|private-key|messages|api_key/)
    closed = true
    await capture.checkpoint('failed')
    const unavailable = fs.readFileSync(path.join(out, 'desktop-chat-old-selection.json'), 'utf8')

    expect(JSON.parse(unavailable)).toMatchObject({ unavailable: true, starts: 'after-window-attachment' })
    expect(unavailable).not.toContain('private renderer error')
    closed = false
    fs.unlinkSync(path.join(out, 'desktop-chat-old-selection.json'))
    fs.mkdirSync(path.join(out, 'desktop-chat-old-selection.json'))
    await expect(capture.stop()).resolves.toBeUndefined()
    expect(warn).toHaveBeenCalledTimes(1)
    expect(disposed).toBe(true)
    expect(Object.getPrototypeOf(f.socket).send).toBe(f.originalSend)
    expect(Object.getPrototypeOf(f.storage).setItem).toBe(f.originalSetItem)
  } finally {
    observer.stop()
    warn.mockRestore()
    fs.rmSync(out, { recursive: true, force: true })
  }

})

test('bounds evidence, filters secrets and ignores other preferences and non-create frames', () => {
  const f = fixture()

  try {
    f.storage.setItem('unrelated-preference', 'private preference')
    f.storage.setItem('hermes.desktop.composer.model', 'sk-sensitive-model-value')
    f.storage.setItem('hermes.desktop.composer.provider', 'https://user:password@example.test')

    for (const frame of ['not-json', JSON.stringify({ method: 'prompt', params: { text: 'private prompt' } }), 'x'.repeat(8193)]) {
      f.socket.send(frame)
    }

    f.socket.send(JSON.stringify({ method: 'session.create', params: { model: 'mock-model', provider: 'custom', token: 'private-token' } }))
    f.storage.setItem('hermes.desktop.composer.model', 'file:///private/model-path')
    f.storage.setItem('hermes.desktop.composer.provider', 'wss://internal.example/path')
    f.socket.send(JSON.stringify({ method: 'session.create', params: { model: 'file:///private/model-path', provider: 'wss://internal.example/path' } }))

    for (let index = 0; index < 80; index++) { f.observer.checkpoint('chat-ready') }
    const evidence = f.observer.checkpoint('failed')

    expect(evidence.events).toHaveLength(64)
    expect(evidence.dropped).toBeGreaterThan(0)
    expect(evidence.events.find(event => event.event === 'session-create')).toMatchObject({ model: 'mock-model', provider: 'custom', composer: { model: 'non-fixture', provider: 'non-fixture' } })
    expect(JSON.stringify(evidence)).not.toMatch(/private|password|sensitive|example\.test|internal\.example|unrelated/)
    expect(f.sent).toHaveLength(5)
  } finally { f.observer.stop() }
})

test.each(['inert-short-key', 'lab-node:4321', 'a.b/c_d-7', 'x'.repeat(128)])(
  'categorizes every non-fixture string on storage and RPC paths (%s)', value => {
    const f = fixture()

    try {
      f.storage.setItem('hermes.desktop.composer.model', value)
      f.storage.setItem('hermes.desktop.composer.provider', value)
      const frame = JSON.stringify({ method: 'session.create', params: { model: value, provider: value } })

      f.socket.send(frame)
      const evidence = f.observer.checkpoint('complete')
      const creation = evidence.events.find(event => event.event === 'session-create')

      expect(creation).toMatchObject({ model: 'non-fixture', provider: 'non-fixture',
        composer: { model: 'non-fixture', provider: 'non-fixture' } })
      expect(JSON.stringify(evidence)).not.toContain(value)
      expect(f.sent).toEqual([frame])
      expect(f.storage.getItem('hermes.desktop.composer.model')).toBe(value)
    } finally { f.observer.stop() }
  },
)
