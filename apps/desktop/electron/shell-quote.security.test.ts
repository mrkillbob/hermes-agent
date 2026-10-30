import { createRequire } from 'node:module'

import { expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const { quote, parse } = require('shell-quote')

it('rejects line breaks in comment tokens while preserving ordinary command arguments', () => {
  for (const lineBreak of ['\n', '\r', '\u2028', '\u2029']) {
    expect(() => quote(['echo', { comment: `note${lineBreak}echo injected` }])).toThrow(TypeError)
    expect(() => quote(['echo', { comment: 'note' }, `a${lineBreak}echo injected;#`])).toThrow(TypeError)
  }

  const args = ['npm:dev:renderer', 'path with spaces', 'accent-é', 'a+b', '(group)']
  expect(parse(quote(args))).toEqual(args)
})
