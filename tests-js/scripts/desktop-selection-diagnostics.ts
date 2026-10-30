import fs from 'node:fs'
import path from 'node:path'

import type { JSHandle, Page } from '@playwright/test'

export type SelectionStage = 'capture-start' | 'chat-ready' | 'before-submit' | 'after-submit' | 'complete' | 'failed' | 'capture-stop'

export interface ComposerSelection {
  model: 'mock-model' | 'non-fixture' | null
  provider: 'custom' | 'non-fixture' | null
  source: 'manual' | 'default' | null
}

export interface SelectionCapture {
  checkpoint: (stage: SelectionStage) => Promise<void>
  readSelection: () => Promise<ComposerSelection | null>
  stop: () => Promise<void>
}

interface SelectionEnvironment {
  localStorage: Storage
  WebSocket: typeof WebSocket
}

/** Serialized into the renderer. Observe three local composer keys and session-create model/provider. */
export function installSelectionObserver(
  environment: SelectionEnvironment | void = window,
  clock: () => number = () => performance.now(),
) {
  const started = clock()
  const target = environment || window
  const events: Record<string, unknown>[] = []
  const keys = ['hermes.desktop.composer.model', 'hermes.desktop.composer.provider', 'hermes.desktop.composer.model-source']
  const restored: (() => void)[] = []
  let dropped = 0
  let active = true

  // A string's shape cannot establish that it contains no credentials or URL.
  // Keep only the two identifiers owned by the inert smoke configuration.
  const identifier = <T extends 'mock-model' | 'custom'>(value: unknown, fixture: T): T | 'non-fixture' | null =>
    typeof value !== 'string' || value === '' ? null : value === fixture ? fixture : 'non-fixture'

  const selection = (): ComposerSelection => {
    try {
      const source = target.localStorage.getItem(keys[2])

      return { model: identifier(target.localStorage.getItem(keys[0]), 'mock-model'),
        provider: identifier(target.localStorage.getItem(keys[1]), 'custom'),
        source: source === 'manual' || source === 'default' ? source : null }
    } catch {
      return { model: null, provider: null, source: null }
    }
  }

  const record = (event: string, fields: Record<string, unknown> = {}) => {
    if (!active) { return }

    if (events.length >= 64) {
      dropped = Math.min(dropped + 1, 1_000_000)

      return
    }

    events.push({ sequence: events.length + 1, elapsedMs: Math.max(0, Math.round(clock() - started)), event, ...fields })
  }

  const socketPrototype = target.WebSocket.prototype
  const send = socketPrototype.send

  const observeSend: WebSocket['send'] = function (this: WebSocket, data) {
    // Parse only small text frames; retain neither raw payloads, IDs, prompts nor endpoint URLs.
    if (typeof data === 'string' && data.length <= 8192) {
      try {
        const request = JSON.parse(data)

        if (request?.method === 'session.create') {
          record('session-create', { model: identifier(request.params?.model, 'mock-model'),
            provider: identifier(request.params?.provider, 'custom'), composer: selection() })
        }
      } catch { /* Non-JSON frames remain unchanged. */ }
    }

    return Reflect.apply(send, this, [data])
  }

  const socketObserved = Object.getOwnPropertyDescriptor(socketPrototype, 'send')?.writable === true

  if (socketObserved) {
    socketPrototype.send = observeSend
    restored.push(() => { if (socketPrototype.send === observeSend) { socketPrototype.send = send } })
  }

  let storageObserved = false

  try {
    const storage = target.localStorage
    const prototype = Object.getPrototypeOf(storage) as Record<'setItem' | 'removeItem', (...args: string[]) => void>

    for (const method of ['setItem', 'removeItem'] as const) {
      const original = prototype[method]

      if (Object.getOwnPropertyDescriptor(prototype, method)?.writable !== true) { continue }

      const observeWrite = function (this: Storage, ...args: string[]) {
        const result = Reflect.apply(original, this, args)

        if (this === storage && keys.includes(args[0])) { record('composer-selection-write', { composer: selection() }) }

        return result
      }

      prototype[method] = observeWrite
      storageObserved = true
      restored.push(() => { if (prototype[method] === observeWrite) { prototype[method] = original } })
    }
  } catch { /* Sandboxed storage can be unavailable; do not change smoke behavior. */ }

  record('capture-start', { composer: selection(), socketObserved, storageObserved })

  return {
    selection,
    checkpoint(stage: SelectionStage) {
      if (stage !== 'capture-start') { record(stage, { composer: selection() }) }

      return { events: [...events], dropped }
    },
    stop() {
      active = false

      for (const restore of restored) { restore() }
    },
  }
}

/** Capture starts after window attachment; it makes no model/config or inference requests. */
export async function beginSelectionCapture(page: Page, outDir: string, phase: string): Promise<SelectionCapture> {
  let handle: JSHandle<ReturnType<typeof installSelectionObserver>> | null = null

  try { handle = await page.evaluateHandle(installSelectionObserver) }
  catch { /* Diagnostic attachment must not change the chat result. */ }

  const output = path.join(outDir, `desktop-chat-${phase}-selection.json`)
  let last: { events: Record<string, unknown>[]; dropped: number } = { events: [], dropped: 0 }
  let unavailable = handle === null
  let writeFailed = false

  const checkpoint = async (stage: SelectionStage): Promise<void> => {
    try { if (handle) { last = await handle.evaluate((observer, next) => observer.checkpoint(next), stage) } }
    catch { unavailable = true }

    try {
      fs.writeFileSync(output, `${JSON.stringify({ schemaVersion: 1, basis: 'legacy-local-composer-keys',
        starts: 'after-window-attachment', unavailable, writeFailed, ...last }, null, 2)}\n`)
    } catch {
      if (!writeFailed) { console.warn('Selection diagnostic artifact could not be written') }
      writeFailed = true
    }
  }

  const readSelection = async (): Promise<ComposerSelection | null> => {
    if (!handle) { return null }

    try { return await handle.evaluate(observer => observer.selection()) }
    catch {
      unavailable = true

      return null
    }
  }

  await checkpoint('capture-start')

  return { checkpoint, readSelection, stop: async () => {
    try { await checkpoint('capture-stop') }
    finally {
      if (handle) {
        await handle.evaluate(observer => observer.stop()).catch(() => {})
        await handle.dispose().catch(() => {})
      }
    }
  } }
}
