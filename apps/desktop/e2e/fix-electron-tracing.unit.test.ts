import { EventEmitter } from 'node:events'

import { beforeEach, expect, test, vi } from 'vitest'

const electron = vi.hoisted(() => ({ launch: vi.fn(), _playwright: { _allContexts: () => [] as unknown[] } }))
vi.mock('@playwright/test', () => ({ _electron: electron }))

beforeEach(() => {
  vi.resetModules()
  electron._playwright = { _allContexts: () => [] }
  electron.launch = vi.fn()
})

async function launchTrackedContext() {
  let closing = false
  const start = vi.fn().mockResolvedValue(undefined)

  const context = Object.assign(new EventEmitter(), {
    isClosed: () => closing,
    tracing: { start, startChunk: vi.fn().mockResolvedValue(undefined) }
  })

  electron.launch.mockResolvedValue({ _context: context })
  await import('./fix-electron-tracing')
  await electron.launch({})

  return { context, start, beginClose: () => { closing = true } }
}

test('does not hand a closing context to the next test before its close event arrives', async () => {
  const { context, beginClose } = await launchTrackedContext()

  expect(electron._playwright._allContexts()).toContain(context)
  beginClose()
  expect(electron._playwright._allContexts()).not.toContain(context)
})

test('keeps live Electron contexts visible with screenshot and snapshot tracing', async () => {
  const { context, start } = await launchTrackedContext()

  expect(electron._playwright._allContexts()).toContain(context)
  expect(start).toHaveBeenCalledWith({ screenshots: true, snapshots: true, sources: true })
  context.emit('close')
  expect(electron._playwright._allContexts()).not.toContain(context)
})
