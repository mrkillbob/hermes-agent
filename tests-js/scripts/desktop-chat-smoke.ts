import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import { type ConsoleMessage, errors, expect, type Locator, type Page } from '@playwright/test'
import { z } from 'zod'

import { validateMockUrl } from './mock-provider-config.ts'
import { MOCK_REPLY } from './mock-server.ts'

export type ChatPhase = 'old' | 'new' | 'installed'

export interface DesktopChatSmokeOptions {
  mockUrl: string
  phase: ChatPhase
  outDir: string
  expectCommit?: string
  provenanceCommit?: string
  observePrompts?: () => Promise<readonly string[]>
}

export interface ChatIdentity {
  appVersion: string
  commit: string | null
  hermesRoot: string
  platform: string
  /** The resolved Hermes home (newer desktops report it; absent on older ones). */
  hermesHome?: string
}

interface SmokeWindow extends Window {
  hermesDesktop?: { getVersion: () => Promise<ChatIdentity> }
}

export interface TranscriptMessage {
  id: string
  /** Historical user roots omit the message ID; label their content identity honestly. */
  idSource?: 'legacy-user-text'
  role: string
  text: string
  streaming: boolean
  error: boolean
}

export interface DesktopChatReceipt {
  status: 'passed'
  phase: ChatPhase
  expectedCommit: string | null
  identity: ChatIdentity
  provenanceCommit: string | null
  prompt: string
  requestWitness: { index: number; prompt: string; before: number }
  transcript: { beforeIds: string[]; user: TranscriptMessage; assistant: TranscriptMessage }
  turnComplete: true
  screenshot: string
}

const identitySchema = z.object({
  appVersion: z.string(), commit: z.string().nullable().optional(),
  hermesRoot: z.string(), platform: z.string(), hermesHome: z.string().optional(),
})

export async function readChatIdentity(page: Page): Promise<ChatIdentity> {
  const identity = identitySchema.parse(await page.evaluate(async (): Promise<ChatIdentity> => {
    // SAFETY: the driver uses a desktop page; the returned IPC data is parsed outside the renderer.
    const bridge = (window as SmokeWindow).hermesDesktop

    if (!bridge) {
      throw new Error('Desktop version bridge is unavailable')
    }

    return bridge.getVersion()
  }))

  return { ...identity, commit: identity.commit ?? null }
}

/** Old desktops do not report a commit; their caller must verify the installed runtime. */
export function assertChatCommit(identity: ChatIdentity, expected: string, provenanceCommit?: string): void {
  if (identity.commit !== null && identity.commit !== expected) {
    throw new Error(`Running commit ${identity.commit} does not equal expected ${expected}`)
  }

  if (provenanceCommit !== undefined && provenanceCommit !== expected) {
    throw new Error(`Verified installation commit ${provenanceCommit} does not equal expected ${expected}`)
  }

  if (identity.commit === null && provenanceCommit !== expected) {
    throw new Error('Desktop does not report a commit; verified installation provenance is required')
  }
}

export async function readMockPrompts(mockUrl: string): Promise<string[]> {
  const response = await fetch(`${validateMockUrl(mockUrl)}/__e2e__/prompts`, {
    signal: AbortSignal.timeout(5000), redirect: 'error',
  })

  if (!response.ok) {
    throw new Error(`Mock request witness returned HTTP ${response.status}`)
  }

  return z.object({ receivedPrompts: z.array(z.string()) }).parse(await response.json()).receivedPrompts
}

export interface ComposerClickPosition { x: number; y: number }

/** Read-only hit testing also handles an editor clipped by a historical pane. */
export function composerClickPosition(editor: Element): ComposerClickPosition | null {
  const rect = editor.getBoundingClientRect()
  const left = Math.max(0, rect.left)
  const right = Math.min(window.innerWidth, rect.right)
  const top = Math.max(0, rect.top)
  const bottom = Math.min(window.innerHeight, rect.bottom)

  if (right <= left || bottom <= top) { return null }

  for (const yFraction of [0.5, 0.25, 0.75, 0.1, 0.9]) {
    for (const xFraction of [0.5, 0.25, 0.75, 0.1, 0.9]) {
      const x = left + (right - left) * xFraction
      const y = top + (bottom - top) * yFraction
      const hit = document.elementFromPoint(x, y)

      if (hit && editor.contains(hit)) { return { x: x - rect.left, y: y - rect.top } }
    }
  }

  return null
}

export interface PointerSample {
  x: number; y: number; requestedX: number; requestedY: number; withinEditor: boolean
}

/** Confirm a uniform input-transport scale from delivered events, never from host/DPI guesses. */
export function pointerTransportScale(samples: PointerSample[]): number | null {
  if (samples.length < 2) { return null }

  const scales = samples.flatMap(sample => [sample.requestedX / sample.x, sample.requestedY / sample.y])
  const scale = scales.reduce((sum, value) => sum + value, 0) / scales.length

  if (!Number.isFinite(scale) || scale <= 0 || Math.abs(scale - 1) < 0.02
    || scales.some(value => !Number.isFinite(value) || Math.abs(value - scale) > 0.02)) { return null }

  return scale
}

export async function clickComposer(composer: Locator, trial: boolean, timeoutMs = 120_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let lastClickFailure: string | undefined
  let lastPosition: ComposerClickPosition | null = null

  const pointer = await composer.evaluateHandle(el => {
    const samples: PointerSample[] = []
    let position: ComposerClickPosition | null = null

    const observe = (event: MouseEvent) => {
      if (position === null) { return }

      const target = event.target instanceof Element ? event.target : null
      const rect = el.getBoundingClientRect()

      samples.push({ x: event.clientX, y: event.clientY,
        requestedX: rect.x + position.x, requestedY: rect.y + position.y,
        withinEditor: target !== null && el.contains(target) })

      if (samples.length > 4) { samples.shift() }
    }

    window.addEventListener('mousemove', observe, true)
    window.addEventListener('mousedown', observe, true)

    return { samples, setPosition: (next: ComposerClickPosition) => { position = next }, stop: () => {
      window.removeEventListener('mousemove', observe, true)
      window.removeEventListener('mousedown', observe, true)
    } }
  })

  try {
    await expect.poll(async () => {
      const position = await composer.evaluate(composerClickPosition)

      lastPosition = position

      if (position === null) { return false }
      await pointer.evaluate((record, next) => record.setPosition(next), position)

      try {
        // Startup layout and scrolling can invalidate a point; remeasure bounded attempts.
        await composer.click({ trial, position, timeout: Math.min(2000, Math.max(1, deadline - Date.now())) })

        return true
      } catch (error) {
        if (!(error instanceof errors.TimeoutError)) { throw error }
        lastClickFailure = error.message
      }

      // Historical Windows Electron delivers CDP mouse coordinates divided by window zoom.
      // Correct only an observed uniform mismatch, then verify a delivered hover hits the editor.
      const scale = pointerTransportScale(await pointer.evaluate(record => record.samples))

      if (scale === null) { return false }

      const target = await composer.evaluate((el, offset) => {
        const rect = el.getBoundingClientRect()
        const x = rect.x + offset.x
        const y = rect.y + offset.y
        const hit = document.elementFromPoint(x, y)

        return hit && el.contains(hit) ? { x, y } : null
      }, position)

      if (target === null) { return false }
      const mouse = composer.page().mouse
      const corrected = { x: target.x * scale, y: target.y * scale }

      await mouse.move(corrected.x, corrected.y)
      const delivered = await pointer.evaluate(record => record.samples.at(-1))

      if (!delivered?.withinEditor || Math.abs(delivered.x - target.x) > 2
        || Math.abs(delivered.y - target.y) > 2) { return false }

      if (!trial) { await mouse.click(corrected.x, corrected.y) }

      return true
    }, { timeout: timeoutMs, message: 'Composer must accept a normal click at a visible input point' }).toBe(true)
  } catch (error) {
    if (lastClickFailure === undefined) { throw error }

    const geometry = await composer.evaluate(el => {
      const rect = el.getBoundingClientRect()

      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height,
        viewport: { width: innerWidth, height: innerHeight }, devicePixelRatio }
    })

    const box = await composer.boundingBox()
    const events = await pointer.evaluate(record => record.samples)

    throw new Error(`${(error as Error).message}\nLast normal click: ${lastClickFailure}`
      + `\nClick geometry: ${JSON.stringify({ position: lastPosition, dom: geometry, playwright: box, events })}`)
  } finally {
    // A closed page cannot run diagnostic cleanup; it already discarded these listeners.
    await pointer.evaluate(record => record.stop()).catch(() => {})
    await pointer.dispose()
  }
}

export async function waitForChatReady(page: Page, timeoutMs = 120_000): Promise<Locator> {
  // The visible editor is a contentEditable div. assistant-ui also renders an
  // aria-hidden, sr-only <textarea> that carries the composer binding: it is
  // "editable" but clipped out of the viewport, so a bare `textarea` selector
  // latches onto it, sails through toBeEditable, and then can never be
  // hit-tested (225 trial-click retries, then "element is outside of the
  // viewport"). Require the editor; keep a textarea fallback only for a real,
  // non-hidden input.
  const root = page.locator('[data-slot="composer-root"]')

  const composer = root
    .locator('[contenteditable="true"]:visible, textarea:not([aria-hidden="true"]):not(.sr-only):visible')
    .first()

  try {
    await composer.waitFor({ state: 'visible', timeout: timeoutMs })
    await expect(composer).toBeEditable({ timeout: timeoutMs })
    await clickComposer(composer, true, timeoutMs)
  } catch (error) {
    throw new Error(`${(error as Error).message} -- composer not interactable `
      + `(composer-root=${await root.count()}, contenteditable=${await root.locator('[contenteditable]').count()}): `
      + await composerDiagnostics(root))
  }

  return composer
}

/** What the composer actually contains, for a failure that explains itself. */
async function composerDiagnostics(root: Locator): Promise<string> {
  try {
    if (await root.count() === 0) {
      return '(no [data-slot="composer-root"] in the DOM)'
    }

    return (await root.first().evaluate((el: Element): string => el.outerHTML.slice(0, 1500)))
  } catch (error) {
    return `(diagnostics unavailable: ${(error as Error).message})`
  }
}

/** The composer's text, whether the app rendered it as a contentEditable or a real textarea. */
export async function composerText(composer: Locator): Promise<string> {
  return composer.evaluate((node: HTMLElement): string =>
    node instanceof HTMLTextAreaElement ? node.value : node.textContent ?? '')
}

export function transcriptMessages(viewports: Element[]): TranscriptMessage[] {
  return viewports.flatMap((viewport: Element): TranscriptMessage[] =>
    [...viewport.querySelectorAll('[data-message-id][data-role], [data-slot="aui_user-message-root"][data-role="user"]')]
      .map((node: Element): TranscriptMessage => {
        const id = node.getAttribute('data-message-id') ?? ''
        const role = node.getAttribute('data-role') ?? ''
        const text = node.textContent ?? ''
        // v2026.6.19's sticky user wrapper drops MessagePrimitive.Root's ID.
        // Its full text is stable across remounts, unlike a DOM index/handle.
        // The new nonce and ordered, fresh assistant ID remain mandatory.
        const legacyUser = !id && role === 'user' && node.getAttribute('data-slot') === 'aui_user-message-root'

        const message: TranscriptMessage = {
          id: legacyUser ? `legacy-user-text:${text}` : id,
          role, text,
          streaming: node.getAttribute('data-streaming') === 'true' || Boolean(node.querySelector('[data-message-streaming="true"]')),
          error: Boolean(node.querySelector('[role="alert"]')),
        }

        if (legacyUser) { message.idSource = 'legacy-user-text' }

        return message
      }))
}

async function readTranscript(page: Page): Promise<TranscriptMessage[]> {
  return page.locator('[data-slot="aui_thread-viewport"]:visible').evaluateAll(transcriptMessages)
}

/** Match an ordered new pair, never a reply carried over from an earlier checkpoint. */
export function newCompletedPair(
  messages: TranscriptMessage[], beforeIds: readonly string[], prompt: string,
): { user: TranscriptMessage; assistant: TranscriptMessage } | null {
  const userIndex = messages.findIndex((message: TranscriptMessage): boolean =>
    message.role === 'user' && message.text.includes(prompt) && !beforeIds.includes(message.id))

  const user = messages[userIndex]
  const assistant = messages[userIndex + 1]

  if (!user || !user.id || !assistant?.id || assistant.role !== 'assistant'
      || beforeIds.includes(assistant.id) || !assistant.text.includes(MOCK_REPLY)
      || assistant.streaming || assistant.error) {
    return null
  }

  return { user, assistant }
}

/** Evidence before assertion: what the renderer held when a checkpoint failed. */
async function rendererEvidence(page: Page, consoleLines: readonly string[]): Promise<string> {
  const state = await page.evaluate((): Record<string, unknown> => {
    const viewport = document.querySelector('[data-slot="aui_thread-viewport"]')
    const composer = document.querySelector('[data-slot="composer-root"]')

    return {
      url: location.href,
      threadMessageCount: document.querySelectorAll('[data-message-id][data-role]').length,
      threadText: viewport?.textContent?.slice(0, 2000) ?? null,
      composerHtml: composer?.outerHTML?.slice(0, 2000) ?? null,
      alerts: [...document.querySelectorAll('[role="alert"]')].map((node: Element): string => node.textContent?.slice(0, 400) ?? ''),
    }
  }).catch((error: Error): Record<string, unknown> => ({ error: String(error) }))

  return ['--- renderer state ---', JSON.stringify(state, null, 2), '--- renderer console ---', ...consoleLines].join('\n')
}

/** Lifecycle belongs to the caller, so this also runs inside the OLD update window. */
export async function runDesktopChatSmoke(page: Page, options: DesktopChatSmokeOptions): Promise<DesktopChatReceipt> {
  const { phase, outDir, expectCommit } = options
  fs.mkdirSync(outDir, { recursive: true })
  const receiptPath = path.join(outDir, `desktop-chat-${phase}.json`)
  const evidencePath = path.join(outDir, `desktop-chat-${phase}-renderer.log`)
  const screenshot = path.join(outDir, `desktop-chat-${phase}.png`)
  const prompt = `Hello, can you hear me? Desktop smoke ${phase} ${randomUUID()}`
  const observe = options.observePrompts ?? ((): Promise<string[]> => readMockPrompts(options.mockUrl))
  // A send the app swallows and a send the app never made look identical from
  // the mock's side; the renderer's own console is the only witness to which.
  const consoleLines: string[] = []

  const onConsole = (message: ConsoleMessage): void => {
    if (consoleLines.length >= 200) {
      return
    }

    const type = message.type()

    if (type === 'debug') {
      return
    }

    consoleLines.push(`[${type}] ${message.text().slice(0, 500)}`)
  }

  page.on('console', onConsole)

  try {
    const composer = await waitForChatReady(page)
    const identity = await readChatIdentity(page)

    if (expectCommit) { assertChatCommit(identity, expectCommit, options.provenanceCommit) }
    const beforeIds = (await readTranscript(page)).map((message: TranscriptMessage): string => message.id)
    const before = (await observe()).length
    await clickComposer(composer, false)
    await expect(composer).toBeFocused()
    // The app persists its composer draft across launches, so a checkpoint that
    // types on top of a restored draft can submit the PREVIOUS checkpoint's text
    // (proved: the update window's turn carried the root checkpoint's prompt) and
    // the witness never matches. Clear it deliberately and refuse to type until
    // the composer is provably empty.
    await composer.press('ControlOrMeta+A')
    await composer.press('Delete')
    await expect.poll(async (): Promise<string> => composerText(composer), {
      timeout: 15_000, message: 'The composer must be empty before the checkpoint types (a restored draft must not survive)',
    }).toBe('')
    await composer.pressSequentially(prompt)
    // The composer clears on submit whether or not a turn was ever started, so
    // the clear-poll below passes vacuously when the editor refused the input.
    // Prove the typing landed before trusting anything downstream of Enter.
    await expect.poll(async (): Promise<string> => composerText(composer), {
      timeout: 15_000, message: 'The composer must hold the typed prompt before Enter (did the editor accept input?)',
    }).toContain(prompt)
    await composer.press('Enter')
    await expect.poll(async (): Promise<string> => composerText(composer),
    { timeout: 90_000, message: 'The submitted draft must clear before the idle control proves completion' }).toBe('')
    let witnessIndex = -1
    let receivedPrompt = ''
    await expect.poll(async (): Promise<boolean> => {
      const prompts = await observe()
      witnessIndex = prompts.findIndex((text: string, index: number): boolean => index >= before && text.includes(prompt))
      receivedPrompt = prompts[witnessIndex] ?? ''

      return witnessIndex >= before
    }, { timeout: 90_000, message: 'The mock must receive this checkpoint prompt after the send' }).toBe(true)
    await expect.poll(async (): Promise<boolean> => newCompletedPair(await readTranscript(page), beforeIds, prompt) !== null,
      { timeout: 90_000, message: 'A new completed assistant reply must follow the new user message' }).toBe(true)
    // An empty idle composer offers voice chat; older desktops retain Send.
    // Stop and the streaming marker must settle even when all reply text arrived.
    await expect(page.locator('[data-slot="composer-root"] button[aria-label="Start voice conversation"]:visible, [data-slot="composer-root"] button[type="submit"][aria-label="Send"]:visible')).toBeVisible({ timeout: 90_000 })
    const pair = newCompletedPair(await readTranscript(page), beforeIds, prompt)

    if (!pair) {
      throw new Error('New transcript pair disappeared before the turn completed')
    }

    await page.screenshot({ path: screenshot })

    const receipt: DesktopChatReceipt = {
      status: 'passed', phase, expectedCommit: expectCommit ?? null, identity, provenanceCommit: options.provenanceCommit ?? null, prompt,
      requestWitness: { index: witnessIndex, prompt: receivedPrompt, before },
      transcript: { beforeIds, ...pair }, turnComplete: true, screenshot,
    }

    fs.writeFileSync(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`)

    return receipt
  } catch (error) {
    await page.screenshot({ path: screenshot }).catch((): void => {})
    fs.writeFileSync(evidencePath, `${await rendererEvidence(page, consoleLines)}\n`)
    fs.writeFileSync(receiptPath, `${JSON.stringify({ status: 'failed', phase, expectedCommit: expectCommit ?? null, prompt, error: String(error) }, null, 2)}\n`)
    throw error
  } finally {
    page.off('console', onConsole)
  }
}