// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LunarCity } from './index'

const { createWorld, destroyWorld, dispatchCamera, getCameraState, loadManifest, worldDeferred } = vi.hoisted(() => {
  let resolveWorld!: (handle: { destroy: () => void }) => void

  const deferred = new Promise<{ destroy: () => void }>(resolve => {
    resolveWorld = resolve
  })

  const destroy = vi.fn()
  const dispatch = vi.fn()
  const readCameraState = vi.fn(() => ({ focusedEntityKey: undefined, following: false }))

  const create = vi.fn((_canvas: unknown): Promise<{ destroy: () => void }> =>
    Promise.resolve({ destroy, dispatchCamera: dispatch, getCameraState: readCameraState })
  )

  return {
    createWorld: create,
    dispatchCamera: dispatch,
    destroyWorld: destroy,
    getCameraState: readCameraState,
    loadManifest: vi.fn(async () => ({ models: [] })),
    worldDeferred: { promise: deferred, resolve: resolveWorld }
  }
})

vi.mock('./manifest', () => ({ loadWorldManifest: loadManifest }))
vi.mock('./world/create-world', () => ({ createLunarCityWorld: createWorld }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('LunarCity', () => {
  it('opens the world with one ready 3D canvas and no runtime source-art image', async () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Lunar City' })).toBeTruthy()
    const canvas = screen.getByLabelText('Interactive 3D Lunar City')

    await waitFor(() => expect(canvas.getAttribute('data-world-status')).toBe('ready'))
    expect(globalThis.document.querySelectorAll('canvas')).toHaveLength(1)
    expect(globalThis.document.querySelector('img[src*="moon-settlement-approved.jpg"]')).toBeNull()
    expect(screen.getByRole('button', { name: /Open Research Lab/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Inspect Fox Scientist' }).getAttribute('data-character-kind')).toBe(
      'leader'
    )
    expect(screen.getByText('SIMULATION')).toBeTruthy()
  })

  it('destroys a world that finishes creating after the route unmounts', async () => {
    createWorld.mockReturnValueOnce(worldDeferred.promise)
    const { unmount } = render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    await act(async () => {
      await Promise.resolve()
    })
    unmount()
    await act(async () => {
      worldDeferred.resolve({ destroy: destroyWorld })
      await worldDeferred.promise
    })

    expect(destroyWorld).toHaveBeenCalledOnce()
  })

  it('lets the user enter a building and inspect its rooms', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Open Library/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Enter building' }))

    expect(screen.getByText('Inside Library')).toBeTruthy()
    expect(screen.getByText('Owl Librarian is managing this shift')).toBeTruthy()
    expect(screen.getByText('Consultation desk')).toBeTruthy()
    expect(screen.getByText('Quiet reading room')).toBeTruthy()
  })

  it('exposes the memory graph escape hatch', () => {
    const onOpenMemoryGraph = vi.fn()
    render(<LunarCity onOpenMemoryGraph={onOpenMemoryGraph} />)

    fireEvent.click(screen.getByRole('button', { name: 'Open memory graph' }))

    expect(onOpenMemoryGraph).toHaveBeenCalledOnce()
  })

  it('shows live task progress and moving workers without a state legend', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    expect(screen.getByText('MISSIONS')).toBeTruthy()
    expect(screen.getByRole('progressbar', { name: 'Survey the archive' })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /Inspect .* worker/ }).length).toBeGreaterThanOrEqual(5)
    expect(screen.getByRole('button', { name: /Inspect Pip worker/ }).getAttribute('data-worker-design')).toBe(
      'orbital'
    )
    expect(screen.queryByText('Worker states')).toBeNull()
    expect(screen.getByTestId('lunar-city-viewport').getAttribute('data-camera')).toBe('angled-simcity')
  })

  it('uses native Babylon camera controls instead of a CSS viewport zoom', () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    const viewport = screen.getByTestId('lunar-city-viewport')

    expect(viewport.getAttribute('data-zoom')).toBeNull()
    expect(screen.getByRole('button', { name: 'Zoom In' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Return to City' })).toBeTruthy()
  })

  it('routes the native camera controls to the existing Babylon world without recreating it', async () => {
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    await waitFor(() => expect(screen.getByLabelText('Interactive 3D Lunar City').dataset.worldStatus).toBe('ready'))
    fireEvent.click(screen.getByRole('button', { name: 'Rotate Left' }))
    fireEvent.click(screen.getByRole('button', { name: 'Return to City' }))

    expect(dispatchCamera).toHaveBeenCalledWith({ kind: 'orbit', deltaAlpha: -0.22, deltaBeta: 0 })
    expect(dispatchCamera).toHaveBeenCalledWith({ kind: 'return-to-city' })
    expect(createWorld).toHaveBeenCalledOnce()
  })

  it('advances task progress while the simulation is playing', async () => {
    vi.useFakeTimers()

    try {
      render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

      const progress = screen.getByRole('progressbar', { name: 'Survey the archive' })
      const initialProgress = progress.getAttribute('aria-valuenow')

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1200)
      })

      expect(progress.getAttribute('aria-valuenow')).not.toBe(initialProgress)
    } finally {
      vi.useRealTimers()
    }
  })
})
