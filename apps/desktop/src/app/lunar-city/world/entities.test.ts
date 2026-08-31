import { describe, expect, it, vi } from 'vitest'

import type { EntityKey, LunarCitySnapshot, LunarEntity, Vec3 } from '../model'

import { applyLodSelection, createEntityRegistry, type EntityPresentationFactory } from './entities'

function entity(index: number, overrides: Partial<LunarEntity> = {}): LunarEntity {
  const key = `session:connection=${index}:worker` as EntityKey

  return {
    animation: 'idle',
    authority: 'authoritative',
    destination: 'garden',
    identity: {
      kind: 'session',
      connectionId: `connection-${index}`,
      profile: 'worker',
      sessionId: `session-${index}`
    },
    key,
    observedAt: 1,
    position: { x: index, y: 0, z: 0 },
    ...overrides
  }
}

function snapshot(...entities: LunarEntity[]): LunarCitySnapshot {
  return {
    entities: new Map(entities.map(value => [value.key, value])),
    observedAt: 1,
    revision: 1,
    sources: []
  }
}

function factory(): EntityPresentationFactory & {
  animated: ReturnType<typeof vi.fn>
  groups: Map<string, { dispose: ReturnType<typeof vi.fn>; sync: ReturnType<typeof vi.fn> }>
} {
  const groups = new Map<string, { dispose: ReturnType<typeof vi.fn>; sync: ReturnType<typeof vi.fn> }>()
  const animated = vi.fn(() => ({
    dispose: vi.fn(),
    setAnimation: vi.fn(),
    setPosition: vi.fn(),
    setStaticPose: vi.fn()
  }))

  return {
    animated,
    createAnimated: animated,
    createInstancedGroup: vi.fn((key: string) => {
      const group = { dispose: vi.fn(), sync: vi.fn() }
      groups.set(key, group)

      return group
    }),
    groups
  }
}

describe('EntityRegistry', () => {
  it('retains keyed objects and a single hardware-instanced idle group across identical snapshots', () => {
    const presentationFactory = factory()
    const registry = createEntityRegistry({ factory: presentationFactory, workerClips: new Set(['idle', 'walk']) })
    const workers = Array.from({ length: 100 }, (_, index) => entity(index))

    registry.reconcile(snapshot(...workers))
    const groupsAfterFirst = presentationFactory.groups.size
    registry.reconcile(snapshot(...workers))

    expect(registry.instancedGroup('worker:idle')?.count).toBe(100)
    expect(presentationFactory.groups.size).toBe(groupsAfterFirst)
    expect(presentationFactory.groups.get('worker:idle')?.sync).toHaveBeenLastCalledWith(
      expect.arrayContaining(workers.map(worker => expect.objectContaining({ key: worker.key })))
    )
    expect(presentationFactory.animated).not.toHaveBeenCalled()
  })

  it('keeps selected and moving workers individually animated while the rest remain instanced', () => {
    const presentationFactory = factory()
    const selected = entity(1)
    const moving = entity(2, { animation: 'walk' })
    const idle = entity(3)
    const registry = createEntityRegistry({ factory: presentationFactory, workerClips: new Set(['idle', 'walk']) })

    registry.setSelection(selected.key)
    registry.reconcile(snapshot(selected, moving, idle))

    expect(presentationFactory.animated).toHaveBeenCalledTimes(2)
    expect(registry.instancedGroup('worker:idle')?.count).toBe(1)
  })

  it('moves non-selected idle groups to their next declared LOD without dropping their exact members', () => {
    const presentationFactory = factory()
    const registry = createEntityRegistry({ factory: presentationFactory, workerClips: new Set(['idle', 'walk']) })
    const workers = [entity(1), entity(2)]

    registry.reconcile(snapshot(...workers))
    registry.applyLodPolicy(() => 1)

    expect(registry.instancedGroup('worker:idle')).toBeUndefined()
    expect(registry.instancedGroup('worker:idle:lod:1')?.count).toBe(2)
    expect(presentationFactory.groups.get('worker:idle')?.dispose).toHaveBeenCalledOnce()
  })

  it('releases removed presentation resources once without disturbing unrelated entities', () => {
    const presentationFactory = factory()
    const first = entity(1, { animation: 'walk' })
    const second = entity(2, { animation: 'walk' })
    const registry = createEntityRegistry({ factory: presentationFactory, workerClips: new Set(['idle', 'walk']) })

    registry.reconcile(snapshot(first, second))
    const firstVisual = presentationFactory.animated.mock.results[0]?.value
    const secondVisual = presentationFactory.animated.mock.results[1]?.value
    registry.reconcile(snapshot(second))
    registry.reconcile(snapshot(second))

    expect(firstVisual.dispose).toHaveBeenCalledOnce()
    expect(secondVisual.dispose).not.toHaveBeenCalled()
  })

  it('keeps exact observed counts and state distributions when its population is represented at aggregate detail', () => {
    const presentationFactory = factory()
    const registry = createEntityRegistry({ factory: presentationFactory, workerClips: new Set(['idle', 'walk']) })
    const workers = Array.from({ length: 250 }, (_, index) => entity(index, { animation: index % 2 ? 'idle' : 'walk' }))

    registry.reconcile(snapshot(...workers))

    expect(registry.aggregate('garden')).toEqual({ animations: { idle: 125, walk: 125 }, total: 250 })
  })

  it('uses a declared reduced pose and one bounded diagnostic when an entity asks for a missing GLB clip', () => {
    const presentationFactory = factory()
    const diagnostic = vi.fn()
    const worker = entity(1, { animation: 'made-up' })
    const registry = createEntityRegistry({
      diagnostic,
      factory: presentationFactory,
      workerClips: new Set(['idle'])
    })

    registry.setSelection(worker.key)
    registry.reconcile(snapshot(worker))
    registry.reconcile(snapshot(worker))

    expect(presentationFactory.animated.mock.results[0]?.value.setStaticPose).toHaveBeenCalledWith('idle')
    expect(diagnostic).toHaveBeenCalledOnce()
  })

  it('publishes a live focus anchor for the retained exact key and removes it before a same-named entity can be selected', () => {
    const focusAnchors = new Map<EntityKey, () => Vec3 | undefined>()
    const presentationFactory = factory()
    const first = entity(1, { animation: 'walk', position: { x: 1, y: 0, z: 0 } })
    const replacement = entity(2, {
      animation: 'walk',
      identity: { kind: 'session', connectionId: 'connection-2', profile: 'worker', sessionId: 'session-2' }
    })
    const registry = createEntityRegistry({
      focusAnchors,
      factory: presentationFactory,
      workerClips: new Set(['idle', 'walk'])
    })

    registry.reconcile(snapshot(first))
    registry.reconcile(snapshot({ ...first, position: { x: 8, y: 0, z: 0 } }))
    expect(focusAnchors.get(first.key)?.()).toEqual({ x: 8, y: 0, z: 0 })

    registry.reconcile(snapshot(replacement))
    expect(focusAnchors.get(first.key)).toBeUndefined()
    expect(focusAnchors.get(replacement.key)?.()).toEqual({ x: 2, y: 0, z: 0 })
  })

  it('publishes authoritative typed worker focus metadata that follows motion and fails closed for stale workers', () => {
    const focusMetadata = new Map<
      EntityKey,
      () => { cameraAnchor: Vec3; focusEntityKey: EntityKey; occlusionGroup: string } | undefined
    >()
    const presentationFactory = factory()
    const worker = entity(1, { position: { x: 2, y: 0, z: 3 } })
    const registry = createEntityRegistry({
      focusMetadata,
      factory: presentationFactory,
      workerClips: new Set(['idle', 'walk'])
    })

    registry.reconcile(snapshot(worker))
    expect(focusMetadata.get(worker.key)?.()).toEqual({
      cameraAnchor: { x: 2, y: 0, z: 3 },
      focusEntityKey: worker.key,
      occlusionGroup: 'workers'
    })

    registry.setPosition(worker.key, { x: 9, y: 0, z: -2 })
    expect(focusMetadata.get(worker.key)?.()?.cameraAnchor).toEqual({ x: 9, y: 0, z: -2 })

    registry.reconcile(snapshot({ ...worker, authority: 'stale' }))
    expect(focusMetadata.get(worker.key)?.()).toBeUndefined()
  })

  it('exposes only retained presentation state to navigation and flushes its changed position without touching snapshots', () => {
    const presentationFactory = factory()
    const worker = entity(1, { animation: 'walk' })
    const registry = createEntityRegistry({ factory: presentationFactory, workerClips: new Set(['idle', 'walk']) })

    registry.reconcile(snapshot(worker))
    const navigationEntity = registry.navigationEntity(worker.key)!
    navigationEntity.position.x = 12
    registry.syncMotion()

    expect(worker.position).toEqual({ x: 1, y: 0, z: 0 })
    expect(presentationFactory.animated.mock.results[0]?.value.setPosition).toHaveBeenLastCalledWith({
      x: 12,
      y: 0,
      z: 0
    })
  })

  it('keeps the authoritative arrival clip while navigation temporarily presents the declared walk clip', () => {
    const presentationFactory = factory()
    const worker = entity(1, { animation: 'work' })
    const registry = createEntityRegistry({
      factory: presentationFactory,
      workerClips: new Set(['idle', 'walk', 'work'])
    })

    registry.setSelection(worker.key)
    registry.reconcile(snapshot(worker))
    const navigationEntity = registry.navigationEntity(worker.key)!
    navigationEntity.animation = 'walk'
    registry.reconcile(snapshot({ ...worker, animation: 'work' }))
    registry.syncMotion()

    expect(presentationFactory.animated.mock.results[0]?.value.setAnimation).toHaveBeenLastCalledWith('walk')

    navigationEntity.animation = 'work'
    registry.setMoving(worker.key, false)
    registry.syncMotion()

    expect(presentationFactory.animated.mock.results[0]?.value.setAnimation).toHaveBeenLastCalledWith('work')
  })

  it('uses the supplied quality animation-distance policy to promote nearby workers and demote distant workers', () => {
    const presentationFactory = factory()
    const worker = entity(1, { animation: 'idle' })
    const registry = createEntityRegistry({ factory: presentationFactory, workerClips: new Set(['idle', 'walk']) })

    registry.reconcile(snapshot(worker))
    registry.applyLodPolicy(
      () => 0,
      () => true
    )
    expect(presentationFactory.animated).toHaveBeenCalledOnce()

    const visual = presentationFactory.animated.mock.results[0]?.value
    registry.applyLodPolicy(
      () => 1,
      () => false
    )

    expect(visual.dispose).toHaveBeenCalledOnce()
    expect(registry.instancedGroup('worker:idle:lod:1')?.count).toBe(1)
  })
})

describe('LOD selection', () => {
  function fakeLod() {
    const near = { setEnabled: vi.fn() }
    const far = { setEnabled: vi.fn() }

    return { far, near }
  }

  it.each([
    ['near camera', 1, false, 0, [true, false]],
    ['far camera', 99, false, 0, [false, true]],
    ['selected entity', 99, true, 1, [true, false]],
    ['degraded entity', 1, false, 1, [false, true]]
  ])('enables exactly one near/far LOD for %s', (_label, distance, selected, lodAdvance, expected) => {
    const { far, near } = fakeLod()

    applyLodSelection(
      [
        { distance: 0, node: near },
        { distance: 28, node: far }
      ],
      { distance, lodAdvance, selected }
    )

    expect(near.setEnabled).toHaveBeenLastCalledWith(expected[0])
    expect(far.setEnabled).toHaveBeenLastCalledWith(expected[1])
  })
})
