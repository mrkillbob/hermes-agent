// @vitest-environment jsdom
import { errors, type Locator } from '@playwright/test'
import { afterEach, expect, test, vi } from 'vitest'

import { clickComposer, composerClickPosition, pointerTransportScale } from './scripts/desktop-chat-smoke.ts'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.replaceChildren()
})

test('finds a visible editor point when the center and left edge are clipped', () => {
  const editor = document.createElement('div')
  const divider = document.createElement('div')
  document.body.append(editor, divider)
  const rect = { left: -200, right: 1000, top: 30, bottom: 56 }

  vi.spyOn(editor, 'getBoundingClientRect').mockReturnValue(rect as DOMRect)
  const hit = vi.fn((x: number, y: number) => x > 300 && x < 580 && y > 32 && y < 36 ? editor : divider)

  vi.stubGlobal('innerWidth', 1000)
  vi.stubGlobal('innerHeight', 480)
  Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: hit })
  const position = composerClickPosition(editor)

  expect(position).not.toBeNull()
  expect(hit(rect.left + position!.x, rect.top + position!.y)).toBe(editor)
})

test('refuses an editor completely covered by a boot overlay', () => {
  const editor = document.createElement('div')
  const overlay = document.createElement('div')
  document.body.append(editor, overlay)
  vi.spyOn(editor, 'getBoundingClientRect').mockReturnValue({ left: 300, right: 700, top: 30, bottom: 56 } as DOMRect)
  Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => overlay })

  expect(composerClickPosition(editor)).toBeNull()
})

test('remeasures a click point after layout changes without swallowing a closed-page error', async () => {
  const evaluate = vi.fn().mockResolvedValueOnce({ x: 30, y: 4 }).mockResolvedValue({ x: 70, y: 8 })
  const click = vi.fn().mockRejectedValueOnce(new errors.TimeoutError('old point moved')).mockResolvedValue(undefined)
  const observer = { evaluate: vi.fn().mockResolvedValue([]), dispose: vi.fn().mockResolvedValue(undefined) }
  const composer = { evaluate, click, evaluateHandle: vi.fn().mockResolvedValue(observer) } as unknown as Locator

  await clickComposer(composer, true, 2000)
  expect(click.mock.calls.map(([options]) => options.position)).toEqual([{ x: 30, y: 4 }, { x: 70, y: 8 }])
  expect(click.mock.calls.every(([options]) => options.trial && !options.force)).toBe(true)

  const closed = new Error('Target page has been closed')

  click.mockReset().mockRejectedValue(closed)
  await expect(clickComposer(composer, false, 2000)).rejects.toThrow('Target page has been closed')
  expect(click).toHaveBeenCalledTimes(1)
})

test('accepts only repeated uniform pointer transport mismatches', () => {
  const sample = { type: 'mousemove', x: 592, y: 239, requestedX: 533.65, requestedY: 215.19, withinEditor: false }

  expect(pointerTransportScale([sample, sample])).toBeCloseTo(0.9, 2)
  expect(pointerTransportScale([sample])).toBeNull()
  expect(pointerTransportScale([sample, { ...sample, requestedY: 190 }])).toBeNull()
  expect(pointerTransportScale([{ ...sample, requestedX: sample.x, requestedY: sample.y },
    { ...sample, requestedX: sample.x, requestedY: sample.y }])).toBeNull()
})

test('rejects a corrected click intercepted after the verified hover', async () => {
  const initial = { type: 'mousemove', x: 100 / 0.9, y: 100 / 0.9, requestedX: 100, requestedY: 100, withinEditor: false }
  const record = { samples: [initial, initial], setPosition: vi.fn(), stop: vi.fn() }
  const observer = { evaluate: (fn: (value: typeof record) => unknown) => Promise.resolve(fn(record)), dispose: vi.fn() }
  let covered = false
  const move = vi.fn(async () => { record.samples.push({ ...initial, x: 100, y: 100, withinEditor: true }) })

  const click = vi.fn(async () => {
    covered = true
    record.samples.push({ ...initial, type: 'mousedown', x: 100, y: 100, withinEditor: false })
  })

  const composer = {
    evaluate: vi.fn(async (fn, offset) => fn === composerClickPosition ? covered ? null : { x: 10, y: 5 }
      : offset ? { x: 100, y: 100 } : {}),
    evaluateHandle: vi.fn().mockResolvedValue(observer),
    click: vi.fn().mockRejectedValue(new errors.TimeoutError('scaled transport')),
    boundingBox: vi.fn().mockResolvedValue({}),
    page: () => ({ mouse: { move, click } }),
  } as unknown as Locator

  await expect(clickComposer(composer, false, 300)).rejects.toThrow('Composer must accept a normal click')
  expect(move).toHaveBeenCalledTimes(1)
  expect(click).toHaveBeenCalledTimes(1)
})
