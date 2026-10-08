import { createRequire } from 'node:module'

import { expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const { commonTrailingPseudos } = require('@tailwindcss/typography/src/utils.js')

it('preserves shared and distinct trailing pseudos in prose selectors', () => {
  expect(commonTrailingPseudos('a::before, b::before')).toEqual(['::before', 'a, b'])
  expect(commonTrailingPseudos('a::before, b::after')).toEqual([null, 'a::before, b::after'])
  expect(commonTrailingPseudos(String.raw`.a\+b::after, #c::after`)).toEqual(['::after', String.raw`.a\+b, #c`])
})
