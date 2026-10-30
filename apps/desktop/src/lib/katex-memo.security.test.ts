import type { Root } from 'hast'
import { unified } from 'unified'
import { VFile } from 'vfile'
import { expect, it } from 'vitest'

import { createMemoizedMathPlugin } from '@/lib/katex-memo'

it('does not grant math link trust through inherited settings or cache it', () => {
  const processor = unified().use([createMemoizedMathPlugin().rehypePlugin])
  const source = String.raw`\href{https://example.invalid/inherited-trust}{x}`

  const render = (): Root => {
    const tree: Root = {
      type: 'root',
      children: [
        {
          type: 'element',
          tagName: 'code',
          properties: { className: ['math-inline'] },
          children: [{ type: 'text', value: source }]
        }
      ]
    }

    return processor.runSync(tree, new VFile()) as Root
  }

  const previous = Object.getOwnPropertyDescriptor(Object.prototype, 'trust')
  let cold: Root
  let cached: Root

  try {
    Object.defineProperty(Object.prototype, 'trust', { configurable: true, writable: true, value: true })
    cold = render()
    cached = render()
  } finally {
    if (previous) {
      Object.defineProperty(Object.prototype, 'trust', previous)
    } else {
      Reflect.deleteProperty(Object.prototype, 'trust')
    }
  }

  expect(JSON.stringify(cold)).not.toContain('"tagName":"a"')
  expect(JSON.stringify(cold)).toContain('"katex"')
  expect(cached).toEqual(cold)
  expect(cached.children[0]).not.toBe(cold.children[0])
})
