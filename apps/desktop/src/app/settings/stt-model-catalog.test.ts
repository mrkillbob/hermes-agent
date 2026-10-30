import { describe, expect, it } from 'vitest'

import { ENUM_OPTIONS } from './constants'
import catalog from './stt-model-catalog.fixture.json'

// The fixture mirrors the runtime STT_MODEL_CATALOG (tests/hermes_cli/test_stt_picker.py pins it
// against the Python side), so Desktop suggestions cannot drift from what the backend accepts.
describe('Desktop STT model suggestions', () => {
  it.each(Object.entries(catalog))('%s lists the runtime catalog', (key, models) => {
    // Local sizes are listed smallest-first in the UI; the catalog puts the default first.
    const same = (list: readonly string[]) => (key === 'stt.local.model' ? [...list].sort() : list)

    expect(same(ENUM_OPTIONS[key] ?? [])).toEqual(same(models))
  })
})
