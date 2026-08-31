// @vitest-environment jsdom
import { LOCAL_CONNECTION_ID } from '@hermes/shared'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Profiler } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type * as SessionRequestRouter from '@/store/session-request-router'

import { entityKey } from './identity'
import { leaderModelIdForOwner } from './leader-runtime'
import type * as LeaderSessions from './leader-sessions'
import type { LunarCityIntent, LunarCityWorldHandle } from './model'
import { $lunarCitySnapshot, applyLunarDelta, createLunarCitySnapshot } from './store'

import { disposeLunarCityRuntime, LunarCity } from './index'

const {
  applySnapshot,
  createKanbanCitySource,
  createWorld,
  destroyWorld,
  dispatchCamera,
  emitWorldIntent,
  getCameraState,
  kanbanSource,
  leaderAnimation,
  loadManifest,
  requestForSessionProfile,
  resolveLeaderSession,
  setQuality,
  setReducedMotion,
  startReconciler,
  stopReconciler,
  worldHandle,
  worldDeferred
} = vi.hoisted(() => {
  const apply = vi.fn()
  const destroy = vi.fn()
  const dispatch = vi.fn()
  const readCameraState = vi.fn(() => ({ focusedEntityKey: undefined, following: false }))
  const readEntityCameraOrder = vi.fn(() => [] as never[])
  const setLeaderAnimation = vi.fn()
  const setQuality = vi.fn()
  const setReducedMotion = vi.fn()
  type TestWorldHandle = {
    applySnapshot: typeof apply
    destroy: typeof destroy
    dispatchCamera: typeof dispatch
    getCameraState: typeof readCameraState
    getEntityCameraOrder: typeof readEntityCameraOrder
    setLeaderAnimation: typeof setLeaderAnimation
    setQuality: typeof setQuality
    setReducedMotion: typeof setReducedMotion
  }

  const handle: TestWorldHandle = {
    applySnapshot: apply,
    destroy,
    dispatchCamera: dispatch,
    getCameraState: readCameraState,
    getEntityCameraOrder: readEntityCameraOrder,
    setLeaderAnimation,
    setQuality,
    setReducedMotion
  }

  let resolveWorld!: (handle: TestWorldHandle) => void

  const deferred = new Promise<TestWorldHandle>(resolve => {
    resolveWorld = resolve
  })

  const source = { onFrame: vi.fn(), read: vi.fn(), start: vi.fn() }
  const stopLive = vi.fn()

  let worldIntent: (intent: LunarCityIntent) => void = () => undefined

  const create = vi.fn((_canvas: unknown, _manifest: unknown, onIntent: (intent: LunarCityIntent) => void) => {
    worldIntent = onIntent

    return Promise.resolve(handle)
  })

  return {
    applySnapshot: apply,
    createKanbanCitySource: vi.fn(() => source),
    createWorld: create,
    dispatchCamera: dispatch,
    destroyWorld: destroy,
    emitWorldIntent: (intent: LunarCityIntent) => worldIntent(intent),
    getCameraState: readCameraState,
    getEntityCameraOrder: readEntityCameraOrder,
    kanbanSource: source,
    leaderAnimation: setLeaderAnimation,
    loadManifest: vi.fn(async () => ({ models: [] })),
    requestForSessionProfile: vi.fn(async () => ({ status: 'queued' })),
    resolveLeaderSession: vi.fn(async () => ({ runtimeId: 'runtime-owl', storedId: 'stored-owl' })),
    setQuality,
    setReducedMotion,
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
  vi.unstubAllGlobals()
})

function publishAccessibleEntities(): { leaderKey: never; workerKey: never } {
  const workerIdentity = {
    connectionId: 'source-a',
    kind: 'session' as const,
    profile: 'research',
    sessionId: 'pip'
  }

  const leaderIdentity = { connectionId: 'source-a', kind: 'profile' as const, profile: 'fox-scientist' }
  const workerKey = entityKey(workerIdentity)
  const leaderKey = entityKey(leaderIdentity)

  $lunarCitySnapshot.set({
    entities: new Map([
      [
        workerKey,
        {
          animation: 'work' as const,
          authority: 'authoritative' as const,
          destination: 'lab' as const,
          identity: workerIdentity,
          key: workerKey,
          observedAt: 42
        }
      ],
      [
        leaderKey,
        {
          animation: 'rest' as const,
          authority: 'authoritative' as const,
          destination: 'lab' as const,
          identity: leaderIdentity,
          key: leaderKey,
          observedAt: 42,
          presentation: {
            configuredTitle: 'Fox Scientist',
            groups: [{ id: 'research', name: 'Research Lab' }],
            metadata: { observedAt: 42, source: 'profiles:source-a', state: 'fresh' as const },
            placement: { lodHint: 0, overflow: false, primaryGroupId: 'research', slot: 0 }
          }
        }
      ]
    ]),
    observedAt: 42,
    revision: 3,
    sources: [
      { authority: 'authoritative', observedAt: 42, source: 'profiles:source-a' },
      { authority: 'authoritative', observedAt: 42, source: 'session:source-a' }
    ]
  })

  return { leaderKey: leaderKey as never, workerKey: workerKey as never }
}

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

  it('opens exact worker controls from the Babylon entity pick without replacing the city canvas', async () => {
    const identity = { connectionId: 'source-b', kind: 'session', profile: 'builder', sessionId: 'session-9' } as const
    const key = entityKey(identity)
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)
    await waitFor(() => expect(screen.getByLabelText('Interactive 3D Lunar City').dataset.worldStatus).toBe('ready'))

    act(() => {
      applyLunarDelta({
        observedAt: 100,
        removals: [],
        revision: 1,
        sources: [{ authority: 'authoritative', observedAt: 100, source: 'session:source-b' }],
        upserts: [
          {
            animation: 'working',
            authority: 'authoritative',
            destination: 'project',
            identity,
            key,
            observedAt: 100
          }
        ]
      })
      emitWorldIntent({ entityKey: key, kind: 'select-focus' })
    })

    expect(screen.getByRole('region', { name: 'Lunar City worker controls' })).toBeTruthy()
    expect(screen.getByText('source-b')).toBeTruthy()
    expect(screen.getAllByLabelText('Interactive 3D Lunar City')).toHaveLength(1)
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
    // The route and canvas remain stable. The isolated semantic entity list
    // coalesces the batch into one React commit instead of one per source publication.
    expect(commits.mock.calls.length - rendersBeforePublications).toBeLessThanOrEqual(1)
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

  it('mounts text-first entity, source, quality, and camera controls over the same world authority', async () => {
    const { workerKey } = publishAccessibleEntities()
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    await waitFor(() => expect(screen.getByLabelText('Interactive 3D Lunar City').dataset.worldStatus).toBe('ready'))
    const pip = screen.getByRole('button', { name: /Session Pip.*Working.*Research Lab.*Authoritative/i })

    fireEvent.click(pip)
    expect(dispatchCamera).toHaveBeenCalledWith({ entityKey: workerKey, follow: false, kind: 'focus' })
    expect(screen.getByRole('region', { name: 'Lunar City worker controls' })).toBeTruthy()
    expect(screen.getByRole('region', { name: 'Source health' }).textContent).toMatch(/Authoritative.*Healthy/i)

    const quality = screen.getByRole('combobox', { name: /3D quality/i })
    expect((quality as HTMLSelectElement).value).toBe('efficient')
    fireEvent.change(quality, { target: { value: 'balanced' } })
    expect(setQuality).toHaveBeenCalledWith('balanced')

    act(() => emitWorldIntent({ kind: 'camera-state', state: { focusedEntityKey: workerKey, following: false } }))
    expect(screen.getByRole('status', { name: 'Camera position' }).textContent).toBe('Focused on Session Pip')
    expect(screen.getByRole('status', { name: 'Camera position' }).textContent).not.toContain(String(workerKey))
  })

  it('synchronously retires a lost world and restores exactly once from the latest immutable snapshot', async () => {
    const { workerKey } = publishAccessibleEntities()
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)
    const canvas = screen.getByLabelText('Interactive 3D Lunar City')
    await waitFor(() => expect(canvas.dataset.worldStatus).toBe('ready'))

    const latest = { ...$lunarCitySnapshot.get(), observedAt: 900, revision: 9 }
    $lunarCitySnapshot.set(latest)
    const lost = new Event('webglcontextlost', { cancelable: true })
    fireEvent(canvas, lost)

    expect(lost.defaultPrevented).toBe(true)
    expect(destroyWorld).toHaveBeenCalledOnce()
    await waitFor(() => expect(createWorld).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(canvas.dataset.worldStatus).toBe('ready'))
    expect(applySnapshot).toHaveBeenLastCalledWith(latest)
    expect(startReconciler).toHaveBeenCalledOnce()

    fireEvent(canvas, new Event('webglcontextlost', { cancelable: true }))
    await act(async () => Promise.resolve())
    expect(createWorld).toHaveBeenCalledTimes(2)
    expect(
      screen.getByRole('button', { name: new RegExp(String(workerKey).replace(/[.*+?^${}()|[\]\\]/gu, '\\$&')) })
    ).toBeTruthy()
  })

  it('keeps exact leader chat, entities, quality, and authorized controls usable when restoration fails', async () => {
    publishAccessibleEntities()
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)
    const canvas = screen.getByLabelText('Interactive 3D Lunar City')
    await waitFor(() => expect(canvas.dataset.worldStatus).toBe('ready'))
    createWorld.mockRejectedValueOnce(new Error('replacement engine unavailable'))

    fireEvent(canvas, new Event('webglcontextlost', { cancelable: true }))

    await waitFor(() => expect(screen.getByText(/3D world renderer unavailable/i)).toBeTruthy())
    expect(screen.getByRole('button', { name: /Session Pip.*Working.*Research Lab/i })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: /3D quality/i })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Talk to fox-scientist leader' }))
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'fox-scientist leader conversation' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Session Pip.*Working.*Research Lab/i }))
    expect(screen.getByRole('region', { name: 'Lunar City worker controls' })).toBeTruthy()
    expect(createWorld).toHaveBeenCalledTimes(2)
  })

  it('destroys a late replacement and publishes nothing after route teardown', async () => {
    publishAccessibleEntities()
    let resolveReplacement!: (handle: LunarCityWorldHandle) => void

    const replacementPromise = new Promise<LunarCityWorldHandle>(resolve => {
      resolveReplacement = resolve
    })

    const replacement = {
      ...worldHandle,
      applySnapshot: vi.fn(),
      destroy: vi.fn()
    } as unknown as LunarCityWorldHandle

    const { unmount } = render(<LunarCity onOpenMemoryGraph={vi.fn()} />)
    const canvas = screen.getByLabelText('Interactive 3D Lunar City')
    await waitFor(() => expect(canvas.dataset.worldStatus).toBe('ready'))
    createWorld.mockReturnValueOnce(replacementPromise as never)

    fireEvent(canvas, new Event('webglcontextlost', { cancelable: true }))
    unmount()
    resolveReplacement(replacement)
    await act(async () => replacementPromise)

    expect(replacement.destroy).toHaveBeenCalledOnce()
    expect(replacement.applySnapshot).not.toHaveBeenCalled()
    expect(stopReconciler).toHaveBeenCalledOnce()
  })

  it('applies reduced-motion presentation to the world while retaining selection and leader conversation', async () => {
    publishAccessibleEntities()

    const media = {
      addEventListener: vi.fn(),
      matches: true,
      media: '(prefers-reduced-motion: reduce)',
      onchange: null,
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn()
    }

    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => media)
    )
    render(<LunarCity onOpenMemoryGraph={vi.fn()} />)

    await waitFor(() => expect(screen.getByLabelText('Interactive 3D Lunar City').dataset.worldStatus).toBe('ready'))
    expect(setReducedMotion).toHaveBeenCalledWith(true)
    expect(screen.getByText(/Reduced motion: destinations snap into place/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Session Pip.*Working.*Research Lab/i }))
    expect(screen.getByRole('region', { name: 'Lunar City worker controls' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Talk to fox-scientist leader' }))
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'fox-scientist leader conversation' })).toBeTruthy())
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
