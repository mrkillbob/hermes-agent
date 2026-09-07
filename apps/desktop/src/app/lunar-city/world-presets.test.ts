import { describe, expect, it } from 'vitest'

import { clampTimeOfDay, lightingFor } from './world-presets'

describe('world presets', () => {
  it('clamps time and keeps night lighting bounded', () => {
    expect(clampTimeOfDay(-1)).toBe(0)
    expect(clampTimeOfDay(2)).toBe(1)
    expect(lightingFor('luna', 0).keyIntensity).toBeGreaterThan(0)
    expect(lightingFor('luna', 0.5).keyIntensity).toBeGreaterThan(lightingFor('luna', 0).keyIntensity)
  })

  it('provides distinct planet palettes', () => {
    expect(lightingFor('mars', 0.5).preset.label).toBe('Mars')
    expect(lightingFor('terra', 0.5).clear).not.toEqual(lightingFor('luna', 0.5).clear)
  })
})
