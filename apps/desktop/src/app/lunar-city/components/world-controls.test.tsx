import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { WorldControls } from './world-controls'

describe('WorldControls', () => {
  it('emits preset and time changes through accessible controls', () => {
    const onPresetChange = vi.fn()
    const onTimeOfDayChange = vi.fn()
    render(
      <WorldControls
        onPresetChange={onPresetChange}
        onTimeOfDayChange={onTimeOfDayChange}
        preset="luna"
        timeOfDay={0.5}
      />
    )
    fireEvent.change(screen.getByLabelText('World preset'), { target: { value: 'mars' } })
    fireEvent.change(screen.getByLabelText('Time of day'), { target: { value: '0.25' } })
    expect(onPresetChange).toHaveBeenCalledWith('mars')
    expect(onTimeOfDayChange).toHaveBeenCalledWith(0.25)
  })
})
