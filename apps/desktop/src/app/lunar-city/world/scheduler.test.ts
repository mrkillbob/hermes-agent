import { describe, expect, it, vi } from 'vitest'

import { createFrameScheduler, type FrameTick } from './scheduler'

function schedulerHarness() {
  const renderer = { stopRenderLoop: vi.fn() }
  const frames: FrameTick[] = []
  const onFrame = vi.fn((frame: FrameTick) => {
    frames.push(frame)

    return false
  })
  const requestFrame = vi.fn(() => 7)
  const cancelFrame = vi.fn()
  const setTimer = vi.fn(() => 8)
  const clearTimer = vi.fn()
  let now = 0
  const scheduler = createFrameScheduler({
    cancelFrame,
    clearTimer,
    now: () => now,
    onFrame,
    renderer,
    requestFrame,
    setTimer
  })

  return {
    cancelFrame,
    clearTimer,
    frames,
    now: (value: number) => {
      now = value
    },
    onFrame,
    renderer,
    requestFrame,
    scheduler,
    setTimer
  }
}

describe('FrameScheduler', () => {
  it('caps interaction at 30 FPS for five seconds then returns to 15 FPS ambient cadence', () => {
    const harness = schedulerHarness()
    harness.scheduler.noteInteraction(0)

    for (let now = 0; now <= 5_000; now += 1) {
      harness.scheduler.tick(now)
    }

    const interactiveFrames = harness.frames.filter(frame => frame.targetFps === 30)
    expect(interactiveFrames.length).toBeLessThanOrEqual(151)
    expect(interactiveFrames.length).toBeGreaterThan(140)

    harness.scheduler.requestVisualAt(6_000)
    harness.scheduler.tick(6_000)

    expect(harness.frames.at(-1)).toMatchObject({ targetFps: 15 })
  })

  it('parks an unchanged city and draws ambient frames only when a visual deadline is due', () => {
    const harness = schedulerHarness()

    harness.scheduler.tick(0)
    harness.scheduler.requestVisualAt(100)
    harness.scheduler.tick(50)
    expect(harness.onFrame).not.toHaveBeenCalled()

    harness.scheduler.tick(100)
    expect(harness.onFrame).toHaveBeenCalledOnce()
    expect(harness.setTimer).toHaveBeenCalledOnce()
  })

  it('reaches zero frames while hidden and resumes with one current-snapshot frame', () => {
    const harness = schedulerHarness()
    harness.scheduler.noteInteraction(0)
    harness.scheduler.setVisible(false)
    harness.scheduler.tick(100)

    expect(harness.onFrame).not.toHaveBeenCalled()
    expect(harness.renderer.stopRenderLoop).toHaveBeenCalledOnce()
    expect(harness.cancelFrame).toHaveBeenCalledOnce()

    harness.scheduler.setVisible(true)
    harness.scheduler.tick(100)

    expect(harness.onFrame).toHaveBeenCalledOnce()
  })

  it('cancels frame, timer, and later wake work on disposal', () => {
    const harness = schedulerHarness()
    harness.scheduler.noteInteraction(0)
    harness.scheduler.requestVisualAt(200)
    harness.scheduler.dispose()
    harness.scheduler.dispose()
    harness.scheduler.tick(500)

    expect(harness.cancelFrame).toHaveBeenCalledOnce()
    expect(harness.clearTimer).toHaveBeenCalledOnce()
    expect(harness.onFrame).not.toHaveBeenCalled()
  })
})
