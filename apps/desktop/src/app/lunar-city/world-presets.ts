import type { WorldPresetId } from './model'

export interface WorldPreset {
  ambient: readonly [number, number, number]
  clear: readonly [number, number, number]
  fog: readonly [number, number, number]
  key: readonly [number, number, number]
  rim: readonly [number, number, number]
  label: string
}

export const WORLD_PRESETS: Readonly<Record<WorldPresetId, WorldPreset>> = {
  luna: {
    ambient: [0.46, 0.32, 0.28],
    clear: [0.005, 0.008, 0.02],
    fog: [0.09, 0.06, 0.08],
    key: [1, 0.86, 0.68],
    rim: [0.55, 0.72, 0.95],
    label: 'Luna'
  },
  mars: {
    ambient: [0.5, 0.25, 0.18],
    clear: [0.08, 0.018, 0.008],
    fog: [0.22, 0.07, 0.035],
    key: [1, 0.52, 0.3],
    rim: [0.75, 0.4, 0.3],
    label: 'Mars'
  },
  terra: {
    ambient: [0.28, 0.38, 0.46],
    clear: [0.015, 0.05, 0.09],
    fog: [0.06, 0.12, 0.16],
    key: [0.75, 0.88, 1],
    rim: [0.35, 0.65, 0.95],
    label: 'Terra'
  }
}

export function clampTimeOfDay(value: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0.5))
}

export function lightingFor(presetId: WorldPresetId, timeOfDay: number) {
  const preset = WORLD_PRESETS[presetId]
  const time = clampTimeOfDay(timeOfDay)
  const daylight = Math.max(0.12, Math.sin(time * Math.PI))

  return {
    ambient: preset.ambient.map(channel => Number((channel * (0.55 + daylight * 0.45)).toFixed(4))) as [
      number,
      number,
      number
    ],
    clear: preset.clear.map(channel => Number((channel * (0.45 + daylight * 0.55)).toFixed(4))) as [
      number,
      number,
      number
    ],
    fog: preset.fog.map(channel => Number((channel * (0.6 + daylight * 0.4)).toFixed(4))) as [number, number, number],
    keyIntensity: Number((0.22 + daylight * 0.78).toFixed(4)),
    preset,
    rimIntensity: Number((0.16 + (1 - daylight) * 0.2).toFixed(4)),
    timeOfDay: time
  }
}
