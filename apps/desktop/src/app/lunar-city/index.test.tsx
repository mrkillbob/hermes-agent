// @vitest-environment jsdom
import { LOCAL_CONNECTION_ID } from '@hermes/shared'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Profiler } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type * as SessionRequestRouter from '@/store/session-request-router'

import { leaderModelIdForOwner } from './leader-runtime'
import type * as LeaderSessions from './leader-sessions'
import type { LunarCityWorldHandle } from './model'
import { $lunarCitySnapshot, createLunarCitySnapshot } from './store'

import { disposeLunarCityRuntime, LunarCity } from './index'

const {
  applySnapshot,
  createKanbanCitySource,
  createWorld,
  destroyWorld,
  dispatchCamera,
  getCameraState,
  kanbanSource,
  leaderAnimation,
  loadManifest,
  requestForSessionProfile,
  resolveLeaderSession,
  startReconciler,
  stopReconciler,
  worldHandle,
  worldDeferred
} = vi.hoisted(() => {
  const apply = vi.fn()
  const destroy = vi.fn()
  const dispatch = vi.fn()
  const readCameraState = vi.fn(() => ({ focusedEntityKey: undefined, following: false }))
  const setLeaderAnimation = vi.fn()
  const setQuality = vi.fn()
  type TestWorldHandle = {
    applySnapshot: typeof apply
    destroy: typeof destroy
    dispatchCamera: typeof dispatch
    getCameraState: typeof readCameraState
    setLeaderAnimation: typeof setLeaderAnimation
    setQuality: typeof setQuality
  }

  const handle: TestWorldHandle = {
    applySnapshot: apply,
    destroy,
    dispatchCamera: dispatch,
    getCameraState: readCameraState,
    setLeaderAnimation,
    setQuality
  }

  let resolveWorld!: (handle: TestWorldHandle) => void

  const deferred = new Promise<TestWorldHandle>(resolve => {
    resolveWorld = resolve
  })

  const source = { onFrame: vi.fn(), read: vi.fn(), start: vi.fn() }
  const stopLive = vi.fn()

  const create = vi.fn((_canvas: unknown) => Promise.resolve(handle))

  return {
    applySnapshot: apply,
    createKanbanCitySource: vi.fn(() => source),
    createWorld: create,
    dispatchCamera: dispatch,
    destroyWorld: destroy,
    getCameraState: readCameraState,
    kanbanSource: source,
    leaderAnimation: setLeaderAnimation,
    loadManifest: vi.fn(async () => ({ models: [] })),
    requestForSessionProfile: vi.fn(async () => ({ status: 'queued' })),
    resolveLeaderSession: vi.fn(async () => ({ runtimeId: 'runtime-owl', storedId: 'stored-owl' })),
    startReconciler: vi.fn(() => stopLive),
    stopReconciler: stopLive,
    worldHandle: handle,
    worldDeferred: { promise: deferred, resolve: resolveWorld }
  }
})

vi.mock('./manifest', () => ({ loadWorldManifest: loadManifest }))
vi.mock('./adapters/kanban', () => ({ createKanbanCitySource }))
vi.mock('./adapters/reconciler', () => ({ startLunarCityReconciler: startReconciler }))
vi.mock('./world/create-world', () => ({ createLunarCityWorld: createWorld }))
vi.mock('./leader-sessions', async importOriginal => ({
  ...(await importOriginal<typeof LeaderSessions>()),
  resolveLeaderSession
}))
vi.mock('@/store/session-request-router', async importOriginal => ({
  ...(await importOriginal<typeof SessionRequestRouter>()),
  requestForSessionProfile
}))

afterEach(() => {
  cleanup()
  $lunarCitySnapshot.set(createLunarCitySnapshot())
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

  it('starts the live writer only inside the Lunar City route with an explicit Kanban source scope', async () => {
    const { unmount } = render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    await waitFor(() => expect(screen.getByLabelText('Interactive 3D Lunar City').dataset.worldStatus).toBe('ready'))

    await waitFor(() =>
      expect(createKanbanCitySource).toHaveBeenCalledWith(
        expect.objectContaining({
          manifest: { models: [] },
          scope: { connectionId: LOCAL_CONNECTION_ID, profile: 'default' }
        })
      )
    )
    expect(startReconciler).toHaveBeenCalledWith({ optionalSources: [kanbanSource] })

    unmount()
    expect(stopReconciler).toHaveBeenCalledOnce()
  })

  it('applies many live snapshot publications to the ready world without recreating or rerendering the route', async () => {
    const commits = vi.fn()
    render(
      <Profiler id="lunar-city" onRender={commits}>
        <LunarCity onOpenMemoryGraph={vi.fn()} />
      </Profiler>
    )
    await waitFor(() => expect(screen.getByLabelText('Interactive 3D Lunar City').dataset.worldStatus).toBe('ready'))
    await waitFor(() => expect(startReconciler).toHaveBeenCalledOnce())
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    const rendersBeforePublications = commits.mock.calls.length
    const applicationsBeforePublications = applySnapshot.mock.calls.length

    const publications = Array.from({ length: 8 }, (_, index) =>
      createLunarCitySnapshot({ observedAt: 800 + index, revision: 2 + index })
    )

    await act(async () => {
      for (const next of publications) {
        $lunarCitySnapshot.set(next)
      }
    })

    expect(applySnapshot.mock.calls.slice(applicationsBeforePublications).map(([next]) => next)).toEqual(publications)
    expect(createWorld).toHaveBeenCalledOnce()
    expect(commits).toHaveBeenCalledTimes(rendersBeforePublications)
  })

  it('stops the reconciler, unsubscribes snapshots, then destroys the ready world', () => {
    const order: string[] = []
    const world = { destroy: () => order.push('world destroy') } as unknown as LunarCityWorldHandle

    disposeLunarCityRuntime(
      () => order.push('reconciler stop'),
      () => order.push('snapshot unsubscribe'),
      world
    )

    expect(order).toEqual(['reconciler stop', 'snapshot unsubscribe', 'world destroy'])
  })

  it('destroys a world that finishes creating after the route unmounts', async () => {
    createWorld.mockReturnValueOnce(worldDeferred.promise)
    const { unmount } = render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    await act(async () => {
      await Promise.resolve()
    })
    unmount()
    await act(async () => {
      worldDeferred.resolve(worldHandle)
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

  it('opens one exact profile-owned leader session without changing the city camera surface', async () => {
    const identity = { connectionId: 'source-a', kind: 'profile' as const, profile: 'owl' }

    const profile = {
      animation: 'rest',
      authority: 'authoritative' as const,
      destination: 'garden' as const,
      identity,
      key: 'profile-source-a-owl' as never,
      observedAt: 42
    }

    $lunarCitySnapshot.set({
      entities: new Map([[profile.key, profile]]),
      observedAt: 42,
      revision: 1,
      sources: []
    })
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Talk to owl leader' }))

    await waitFor(() => expect(resolveLeaderSession).toHaveBeenCalledWith({ connectionId: 'source-a', profile: 'owl' }))
    expect(screen.getByRole('dialog', { name: 'owl leader conversation' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Rotate Left' }))
    expect(dispatchCamera).toHaveBeenCalledWith({ kind: 'orbit', deltaAlpha: -0.22, deltaBeta: 0 })

    fireEvent.change(screen.getByRole('textbox', { name: 'Message owl leader' }), {
      target: { value: 'Continue safely' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() =>
      expect(requestForSessionProfile).toHaveBeenCalledWith(
        { connectionId: 'source-a', profile: 'owl' },
        expect.any(Function),
        'prompt.submit',
        { session_id: 'runtime-owl', text: 'Continue safely' }
      )
    )
    expect(createWorld).toHaveBeenCalledOnce()
  })

  it('projects an exact session-resolution failure to only that leader unavailable clip', async () => {
    const identity = { connectionId: 'source-a', kind: 'profile' as const, profile: 'owl' }

    const profile = {
      animation: 'rest',
      authority: 'authoritative' as const,
      destination: 'garden' as const,
      identity,
      key: 'profile-source-a-owl' as never,
      observedAt: 42
    }

    resolveLeaderSession.mockRejectedValueOnce(new Error('owner route unavailable'))
    $lunarCitySnapshot.set({
      entities: new Map([[profile.key, profile]]),
      observedAt: 42,
      revision: 1,
      sources: []
    })
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Talk to owl leader' }))

    await waitFor(() =>
      expect(screen.getByRole('status', { name: 'owl leader conversation error' }).textContent).toContain(
        'owner route unavailable'
      )
    )
    expect(leaderAnimation).toHaveBeenCalledWith(
      leaderModelIdForOwner({ connectionId: 'source-a', profile: 'owl' }),
      'unavailable'
    )
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
