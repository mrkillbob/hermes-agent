import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

import { test } from 'vitest'

// Exercise the actual PostCSS consumer's resolved source-map dependency.
const require = createRequire(import.meta.url)
const viteRequire = createRequire(require.resolve('vite'))
const postcss = viteRequire('postcss')
const { SourceMapConsumer } = createRequire(viteRequire.resolve('postcss'))('source-map-js')

const basicMap = {
  version: 3,
  sources: ['original.css'],
  names: [],
  mappings: 'AAAA',
  sourcesContent: ['a { color: red }']
}

const indexed = (line: unknown, column: unknown = 0, map: unknown = basicMap) => ({
  version: 3,
  sections: [{ offset: { line, column }, map }]
})

for (const [name, line, column] of [
  ['huge line', 1e12, 0],
  ['negative line', -1, 0],
  ['fractional line', 0.5, 0],
  ['nonfinite line', Infinity, 0],
  ['string line', '1', 0],
  ['fractional column', 0, 0.5],
  ['nonfinite column', 0, NaN]
] as const) {
  test(`indexed source maps reject ${name} before mapping iteration`, () => {
    assert.throws(() => new SourceMapConsumer(indexed(line, column)), /offset/i)
  })
}

test('nested indexed maps cannot split an oversized offset across sections', () => {
  assert.throws(() => new SourceMapConsumer(indexed(6_000_000, 0, indexed(6_000_000))), /offset/i)
})

test('ordinary indexed maps preserve source positions and PostCSS map composition', () => {
  const previous = indexed(1)
  const consumer = new SourceMapConsumer(previous)

  assert.deepEqual(consumer.originalPositionFor({ line: 2, column: 1 }), {
    source: 'original.css',
    line: 1,
    column: 0,
    name: null
  })

  const result = postcss().process('a { color: red }', {
    from: 'generated.css',
    to: 'output.css',
    map: { prev: basicMap, inline: false, annotation: false }
  })

  const composed = new SourceMapConsumer(result.map!.toJSON())

  assert.equal(result.css, 'a { color: red }')
  assert.equal(composed.originalPositionFor({ line: 1, column: 1 }).source, 'original.css')
})
