import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import type { ElectronApplication, Page } from '@playwright/test'
import { afterEach, expect, test, vi } from 'vitest'

import { runUpdateWindowChat, type UpdateWindowChatOptions } from '../tests/install/e2e-assets/update-window-chat.mjs'

import type * as SelectionDiagnostics from './scripts/desktop-selection-diagnostics.ts'
import { beginSelectionCapture, type installSelectionObserver } from './scripts/desktop-selection-diagnostics.ts'

const chat = vi.hoisted(() => ({
  readChatIdentity: vi.fn(), runDesktopChatSmoke: vi.fn(), waitForChatReady: vi.fn(),
}))

const provenance = vi.hoisted(() => ({
  assertBackendOrigin: vi.fn(), localBackendProcess: vi.fn(), readInstallationCommit: vi.fn(),
}))

vi.mock('./scripts/desktop-chat-smoke.ts', () => chat)
vi.mock('./scripts/desktop-smoke-process.ts', () => provenance)
vi.mock('./scripts/desktop-selection-diagnostics.ts', async importOriginal => {
  const actual = await importOriginal<typeof SelectionDiagnostics>()

  return { ...actual, beginSelectionCapture: vi.fn(actual.beginSelectionCapture) }
})

afterEach(() => { vi.clearAllMocks() })

function fixture() {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'update-selection-'))
  const executable = path.join(outDir, 'inert-executable')
  fs.writeFileSync(executable, '')
  const values = new Map<string, string>()

  class FixtureStorage {
    getItem(key: string) { return values.get(key) ?? null }
    setItem(key: string, value: string) { values.set(key, value) }
    removeItem(key: string) { values.delete(key) }
  }

  class FixtureSocket {
    send(_data: unknown) {}
  }

  const storage = new FixtureStorage()
  const environment = { localStorage: storage as Storage, WebSocket: FixtureSocket as unknown as typeof WebSocket }

  const original = { send: FixtureSocket.prototype.send, setItem: FixtureStorage.prototype.setItem,
    removeItem: FixtureStorage.prototype.removeItem }

  const calls: string[] = []
  let observer: ReturnType<typeof installSelectionObserver> | undefined
  const dispose = vi.fn(async () => {})

  const page = {
    evaluateHandle: vi.fn(async (install: typeof installSelectionObserver) => {
      calls.push('capture')
      observer = install(environment, () => 0)

      return {
        evaluate: async (read: (value: NonNullable<typeof observer>, arg: unknown) => unknown, arg: unknown) =>
          read(observer!, arg),
        dispose,
      }
    }),
    evaluate: vi.fn(async () => {
      calls.push('connection')

      return { mode: 'local', baseUrl: 'http://127.0.0.1:1234' }
    }),
    screenshot: vi.fn(async () => {}),
  } as unknown as Page

  const app = { evaluate: vi.fn(async () => {
    calls.push('process')

    return { pid: 1, executable, resources: outDir, userData: outDir }
  }) } as unknown as ElectronApplication

  const prepare = vi.fn(async () => {
    calls.push('prepare')
    storage.setItem('hermes.desktop.composer.model', 'mock-model')
    storage.setItem('hermes.desktop.composer.provider', 'custom')
  })

  const options: UpdateWindowChatOptions = {
    mockUrl: 'http://127.0.0.1:1234', outDir, expectCommit: 'a'.repeat(40),
    origin: 'source', root: outDir, executable, userData: outDir, prepare,
  }

  chat.waitForChatReady.mockImplementation(async () => { calls.push('ready') })
  chat.readChatIdentity.mockImplementation(async () => {
    calls.push('identity')

    return { hermesRoot: outDir }
  })
  provenance.localBackendProcess.mockImplementation(() => {
    calls.push('backend')

    return { pid: 2, parentPid: 1, executable }
  })
  provenance.assertBackendOrigin.mockImplementation(() => {})
  provenance.readInstallationCommit.mockImplementation(() => {
    calls.push('provenance')

    return options.expectCommit
  })
  chat.runDesktopChatSmoke.mockImplementation(async (_page, smokeOptions) => {
    calls.push('chat')
    await smokeOptions.selectionCapture.checkpoint('complete')

    return { status: 'passed', phase: 'old' }
  })

  return {
    app, page, options, calls, prepare, dispose, storage,
    evidence: () => JSON.parse(fs.readFileSync(path.join(outDir, 'desktop-chat-old-selection.json'), 'utf8')),
    restored: () => {
      expect(FixtureSocket.prototype.send).toBe(original.send)
      expect(FixtureStorage.prototype.setItem).toBe(original.setItem)
      expect(FixtureStorage.prototype.removeItem).toBe(original.removeItem)
      expect(dispose).toHaveBeenCalledOnce()
    },
    cleanup: () => {
      observer?.stop()
      fs.rmSync(outDir, { recursive: true, force: true })
    },
  }
}

test('one OLD capture retains preparation-time selection and follows the window through chat', async () => {
  const f = fixture()

  try {
    await runUpdateWindowChat(f.app, f.page, f.options)
    expect(f.calls).toEqual(['capture', 'prepare', 'process', 'ready', 'identity', 'connection', 'backend', 'provenance', 'chat'])
    expect(beginSelectionCapture).toHaveBeenCalledExactlyOnceWith(f.page, f.options.outDir, 'old')
    const capture = await vi.mocked(beginSelectionCapture).mock.results[0]!.value
    expect(chat.runDesktopChatSmoke).toHaveBeenCalledWith(f.page, expect.objectContaining({ phase: 'old', selectionCapture: capture }))
    expect(f.evidence().events).toEqual(expect.arrayContaining([
      expect.objectContaining({ event: 'capture-start', composer: { model: null, provider: null, source: null } }),
      expect.objectContaining({ event: 'composer-selection-write', composer: { model: 'mock-model', provider: 'custom', source: null } }),
      expect.objectContaining({ event: 'complete' }), expect.objectContaining({ event: 'capture-stop' }),
    ]))
    f.restored()
  } finally { f.cleanup() }
})

test.each(['preparation', 'process', 'readiness', 'provenance', 'chat'] as const)(
  '%s failure retains the earlier capture, restores wrappers and propagates the original error',
  async stage => {
    const f = fixture()
    const failure = new Error(`${stage} failed`)

    const reject = async () => { throw failure }

    if (stage === 'preparation') {
      f.prepare.mockImplementation(async () => {
        f.storage.setItem('hermes.desktop.composer.model', 'mock-model')
        throw failure
      })
    } else if (stage === 'process') {
      vi.mocked(f.app.evaluate).mockImplementation(reject)
    } else if (stage === 'readiness') {
      chat.waitForChatReady.mockImplementation(reject)
    } else if (stage === 'provenance') {
      provenance.readInstallationCommit.mockImplementation(() => { throw failure })
    } else {
      chat.runDesktopChatSmoke.mockImplementation(reject)
    }

    try {
      await expect(runUpdateWindowChat(f.app, f.page, f.options)).rejects.toBe(failure)
      expect(beginSelectionCapture).toHaveBeenCalledExactlyOnceWith(f.page, f.options.outDir, 'old')
      const events = f.evidence().events
      expect(events[0]).toMatchObject({ event: 'capture-start', composer: { model: null, provider: null, source: null } })
      expect(events).toEqual(expect.arrayContaining([expect.objectContaining({ event: 'composer-selection-write' }), expect.objectContaining({ event: 'failed' })]))
      expect(events.at(-1)).toMatchObject({ event: 'capture-stop' })
      f.restored()
    } finally { f.cleanup() }
  },
)

test('diagnostic cleanup errors do not replace the preparation error', async () => {
  const f = fixture()
  const failure = new Error('preparation failed')
  f.prepare.mockRejectedValue(failure)
  const actual = await vi.importActual<typeof SelectionDiagnostics>('./scripts/desktop-selection-diagnostics.ts')
  vi.mocked(beginSelectionCapture).mockImplementationOnce(async (...args) => {
    const capture = await actual.beginSelectionCapture(...args)

    return {
      checkpoint: async stage => {
        await capture.checkpoint(stage)
        throw new Error('diagnostic checkpoint failed')
      },
      stop: async () => {
        await capture.stop()
        throw new Error('diagnostic stop failed')
      },
    }
  })

  try {
    await expect(runUpdateWindowChat(f.app, f.page, f.options)).rejects.toBe(failure)
    expect(beginSelectionCapture).toHaveBeenCalledOnce()
    f.restored()
  } finally { f.cleanup() }
})
