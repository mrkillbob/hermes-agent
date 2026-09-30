// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'

import { composerClickPosition } from './scripts/desktop-chat-smoke.ts'

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
