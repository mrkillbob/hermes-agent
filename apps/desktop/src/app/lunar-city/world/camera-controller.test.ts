import { describe, expect, it, vi } from 'vitest'

import type { CameraLandmark, EntityKey, Vec3, WorldBounds } from '../model'

import {
  bindCameraInput,
  type CameraLike,
  createCameraController,
  poseFromLandmark,
  readPose
} from './camera-controller'

class FakeVector {
  constructor(
    public x: number,
    public y: number,
    public z: number
  ) {}

  set(x: number, y: number, z: number): void {
    this.x = x
    this.y = y
    this.z = z
  }
}

function key(value: string): EntityKey {
  return value as EntityKey
}

function fakeCamera(): CameraLike {
  return {
    alpha: -0.78,
    beta: 1.02,
    radius: 54,
    target: new FakeVector(0, 5, 0)
  }
}

const overview: CameraLandmark = {
  id: 'approved-overview',
  alpha: -0.78,
  beta: 1.02,
  radius: 54,
  target: { x: 0, y: 5, z: 0 },
  minBeta: 0.72,
  maxBeta: 1.3,
  minRadius: 18,
  maxRadius: 96
}

const bounds: WorldBounds = {
  min: { x: -60, y: -12, z: -60 },
  max: { x: 60, y: 36, z: 60 }
}

describe('createCameraController', () => {
  it('disables ArcRotate inertia so camera intents have deterministic boundaries', () => {
    const camera = { ...fakeCamera(), inertia: 0.86 }

    createCameraController(camera, overview, bounds)

    expect(camera.inertia).toBe(0)
  })

  it('clamps orbit, tilt, radius, and target to the manifest bounds', () => {
    const camera = fakeCamera()
    const controller = createCameraController(camera, overview, bounds)

    controller.dispatch({ kind: 'orbit', deltaAlpha: 99, deltaBeta: 99 })
    controller.dispatch({ kind: 'zoom', delta: -999 })
    controller.dispatch({ kind: 'pan', deltaX: 999, deltaZ: 999 })

    expect(camera.alpha).toBeGreaterThanOrEqual(-Math.PI)
    expect(camera.alpha).toBeLessThanOrEqual(Math.PI)
    expect(camera.beta).toBe(overview.maxBeta)
    expect(camera.radius).toBe(overview.minRadius)
    expect(camera.target).toMatchObject({ x: bounds.max.x, y: overview.target.y, z: bounds.max.z })
  })

  it('returns exactly to the approved overview without retaining the previous focus pose', () => {
    const camera = fakeCamera()
    const controller = createCameraController(camera, overview, bounds)

    controller.dispatch({ kind: 'orbit', deltaAlpha: 1.7, deltaBeta: -0.2 })
    controller.dispatch({ kind: 'pan', deltaX: 11, deltaZ: -9 })
    controller.dispatch({ kind: 'zoom', delta: -17 })
    controller.dispatch({ kind: 'return-to-city' })

    expect(readPose(camera)).toEqual(poseFromLandmark(overview))
    expect(controller.getState()).toEqual({ focusedEntityKey: undefined, following: false })
  })

  it('eases a typed focus target and follows its declared anchor without using display names', () => {
    const workerKey = key('session:local:worker:session-1')
    const worker = new FakeVector(5, 1, -3)
    const focusAnchors = new Map([[workerKey, () => worker as Vec3]])
    const camera = fakeCamera()
    const controller = createCameraController(camera, overview, bounds, { focusAnchors, transitionMs: 120 })

    controller.dispatch({ kind: 'focus', entityKey: workerKey, follow: true })
    controller.update(60)

    expect(camera.target.x).toBeGreaterThan(0)
    expect(camera.target.x).toBeLessThan(worker.x)
    worker.set(8, 0, 4)
    controller.update(120)

    expect(camera.target).toMatchObject({ x: 8, y: 0, z: 4 })
    expect(controller.getState()).toEqual({ focusedEntityKey: workerKey, following: true })
  })

  it('uses the manifest follow offset while keeping the followed anchor as the camera target', () => {
    const workerKey = key('session:local:worker:session-1')
    const camera = fakeCamera()

    const controller = createCameraController(camera, overview, bounds, {
      focusAnchors: new Map([[workerKey, () => ({ x: 8, y: 0, z: 4 })]]),
      followOffset: { x: 0, y: 5, z: -8 },
      transitionMs: 1
    })

    controller.dispatch({ kind: 'focus', entityKey: workerKey, follow: true })
    controller.update(1)

    expect(camera.target).toMatchObject({ x: 8, y: 0, z: 4 })
    expect(camera.alpha).toBeCloseTo(-Math.PI / 2)
    expect(camera.beta).toBeCloseTo(Math.acos(5 / Math.hypot(5, 8)))
    expect(camera.radius).toBe(overview.minRadius)
  })

  it('clears follow state when empty terrain is selected', () => {
    const workerKey = key('session:local:worker:session-1')

    const controller = createCameraController(fakeCamera(), overview, bounds, {
      focusAnchors: new Map([[workerKey, () => ({ x: 4, y: 0, z: 2 })]])
    })

    controller.dispatch({ kind: 'focus', entityKey: workerKey, follow: true })
    controller.dispatch({ kind: 'clear-focus' })

    expect(controller.getState()).toEqual({ focusedEntityKey: undefined, following: false })
  })
})

describe('bindCameraInput', () => {
  it('maps primary drag, secondary drag, wheel, pinch, typed picks, and empty terrain to shared intents', () => {
    const canvas = document.createElement('canvas')
    const dispatch = vi.fn()

    const pick = vi
      .fn()
      .mockReturnValueOnce({ kind: 'entity', entityKey: key('session:local:worker:session-1') })
      .mockReturnValueOnce(undefined)

    const release = bindCameraInput(canvas, { dispatch, pick })

    canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 10, clientY: 10, pointerId: 1 }))
    canvas.dispatchEvent(new PointerEvent('pointermove', { button: 0, clientX: 22, clientY: 18, pointerId: 1 }))
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 22, clientY: 18, pointerId: 1 }))
    canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 2, clientX: 20, clientY: 20, pointerId: 2 }))
    canvas.dispatchEvent(new PointerEvent('pointermove', { button: 2, clientX: 28, clientY: 26, pointerId: 2 }))
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 2, clientX: 28, clientY: 26, pointerId: 2 }))
    canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: 80 }))
    canvas.dispatchEvent(
      new PointerEvent('pointerdown', { pointerType: 'touch', clientX: 0, clientY: 0, pointerId: 3 })
    )
    canvas.dispatchEvent(
      new PointerEvent('pointerdown', { pointerType: 'touch', clientX: 40, clientY: 0, pointerId: 4 })
    )
    canvas.dispatchEvent(
      new PointerEvent('pointermove', { pointerType: 'touch', clientX: 60, clientY: 0, pointerId: 4 })
    )
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 5, clientY: 5, pointerId: 5 }))
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 6, clientY: 6, pointerId: 6 }))

    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ kind: 'orbit' }))
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ kind: 'pan' }))
    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({ kind: 'zoom' }))
    expect(dispatch).toHaveBeenCalledWith({
      kind: 'focus',
      entityKey: key('session:local:worker:session-1'),
      follow: true
    })
    expect(dispatch).toHaveBeenCalledWith({ kind: 'clear-focus' })

    release()
    dispatch.mockClear()
    canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: 80 }))
    expect(dispatch).not.toHaveBeenCalled()
  })
})
