// @vitest-environment jsdom
import type { Page } from '@playwright/test'
import { afterEach, expect, it, vi } from 'vitest'

import { installDuplicateSampler } from './oracle'

afterEach(() => {
  delete (window as any).__coreSampler
  document.body.replaceChildren()
  vi.unstubAllGlobals()
})

it('keeps a bounded content-free history and freezes it on the first duplicate', async () => {
  let changed = () => {}
  let sample = () => {}
  vi.stubGlobal(
    'MutationObserver',
    class {
      constructor(callback: () => void) {
        changed = callback
      }
      observe() {}
    }
  )
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => {
    sample = callback
    return 1
  })
  document.body.innerHTML =
    '<div data-slot="aui_thread-viewport"><div data-message-id="123-13-system" data-role="assistant" data-slot="aui_assistant-message-root"></div></div>'
  const viewport = document.body.firstElementChild as HTMLElement
  const row = viewport.firstElementChild as HTMLElement
  vi.spyOn(viewport, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList)
  Object.defineProperty(viewport, 'innerText', { configurable: true, value: 'A20-example private body' })
  Object.defineProperty(row, 'innerText', { configurable: true, value: 'A20-example private body' })
  const page = { evaluate: async (fn: any, argument: any) => fn(argument) } as Page
  await installDuplicateSampler(page, true)
  for (let i = 0; i < 25; i++) {
    changed()
    sample()
  }
  const sampler = (window as any).__coreSampler
  expect(sampler.frames).toHaveLength(20)
  expect(sampler.frames.at(-1).rows).toEqual([{ id: '123-13-system', role: 'assistant', markers: ['A20-example'] }])
  expect(JSON.stringify(sampler.frames)).not.toContain('private body')
  Object.defineProperty(viewport, 'innerText', { configurable: true, value: 'A20-example A20-example' })
  changed()
  sample()
  expect(sampler.violations).toHaveLength(1)
  const frozen = JSON.stringify(sampler.frames)
  Object.defineProperty(viewport, 'innerText', { configurable: true, value: 'A21-example' })
  changed()
  sample()
  expect(JSON.stringify(sampler.frames)).toBe(frozen)
})
