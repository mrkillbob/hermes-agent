import type { WorldPresetId } from '../model'
import { WORLD_PRESETS } from '../world-presets'

export interface WorldControlsProps {
  onPresetChange(preset: WorldPresetId): void
  onTimeOfDayChange(value: number): void
  preset: WorldPresetId
  timeOfDay: number
}

export function WorldControls({ onPresetChange, onTimeOfDayChange, preset, timeOfDay }: WorldControlsProps) {
  return (
    <section aria-label="World controls" className="lunar-city-world-controls">
      <label>
        World preset
        <select
          aria-label="World preset"
          onChange={event => onPresetChange(event.target.value as WorldPresetId)}
          value={preset}
        >
          {Object.entries(WORLD_PRESETS).map(([id, value]) => (
            <option key={id} value={id}>
              {value.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Time of day
        <input
          aria-label="Time of day"
          max="1"
          min="0"
          onChange={event => onTimeOfDayChange(Number(event.target.value))}
          step="0.01"
          type="range"
          value={timeOfDay}
        />
        <span>
          {Math.round(timeOfDay * 24)
            .toString()
            .padStart(2, '0')}
          :00
        </span>
      </label>
    </section>
  )
}
