import { describe, expect, it, vi } from 'vitest'

import type { EntityKey, Vec3 } from '../model'

import {
  createNavigationController,
  createRecastNavigationQuery,
  type NavigationEntity,
  type NavigationQuery
} from './navigation'

const workerKey = 'session:worker' as EntityKey

function worker(position: Vec3 = { x: 1, y: 0, z: 2 }): NavigationEntity {
  return { animation: 'idle', key: workerKey, position: { ...position } }
}

function query(
  path: readonly Vec3[] = [
    { x: 1, y: 0, z: 2 },
    { x: 9, y: 0, z: 2 }
  ]
): NavigationQuery {
  return { computePath: vi.fn(() => path), dispose: vi.fn() }
}

const destinations = { review: { x: 9, y: 0, z: 2 } }

describe('NavigationController', () => {
  it('computes from the entity’s actual position, uses the declared walk clip, and advances only on ticks', () => {
    const navQuery = query()
    const controller = createNavigationController({
      destinations,
      query: navQuery,
      workerClips: new Set(['idle', 'walk'])
    })
    const entity = worker()

    expect(controller.move(entity, 'review')).toBe(true)
    expect(navQuery.computePath).toHaveBeenCalledWith({ x: 1, y: 0, z: 2 }, { x: 9, y: 0, z: 2 })
    expect(entity.animation).toBe('walk')
    expect(entity.position).toEqual({ x: 1, y: 0, z: 2 })

    controller.tick(500)

    expect(entity.position.x).toBeGreaterThan(1)
    expect(entity.position.x).toBeLessThan(9)
  })

  it('does not recompute an unchanged origin, destination, and walkability revision', () => {
    const navQuery = query()
    const controller = createNavigationController({
      destinations,
      query: navQuery,
      workerClips: new Set(['idle', 'walk'])
    })
    const entity = worker()

    controller.move(entity, 'review')
    controller.move(entity, 'review')
    controller.setWalkabilityRevision(1)
    controller.move(entity, 'review')

    expect(navQuery.computePath).toHaveBeenCalledTimes(2)
  })

  it.each([
    ['missing destination', {} as Record<string, Vec3>, query()],
    ['empty path', destinations, query([])],
    [
      'query failure',
      destinations,
      {
        computePath: vi.fn(() => {
          throw new Error('no navmesh')
        })
      }
    ]
  ])('fails closed for %s and never falls back to a straight line', (_name, targetDestinations, navQuery) => {
    const diagnostic = vi.fn()
    const controller = createNavigationController({
      destinations: targetDestinations,
      query: navQuery,
      workerClips: new Set(['idle', 'walk']),
      diagnostic
    })
    const entity = worker()

    expect(controller.move(entity, 'review')).toBe(false)
    controller.tick(1_000)

    expect(entity.position).toEqual({ x: 1, y: 0, z: 2 })
    expect(entity.animation).toBe('unavailable')
    expect(diagnostic).toHaveBeenCalledTimes(1)
  })

  it('uses the manifest static pose and exact destination under reduced motion', () => {
    const navQuery = query()
    const controller = createNavigationController({
      destinations,
      query: navQuery,
      reducedMotion: true,
      staticPose: 'rest',
      workerClips: new Set(['idle', 'walk', 'rest'])
    })
    const entity = worker()

    expect(controller.move(entity, 'review')).toBe(true)
    expect(entity.position).toEqual(destinations.review)
    expect(entity.animation).toBe('rest')
    expect(navQuery.computePath).not.toHaveBeenCalled()
  })

  it('disposes its navigation query once and ignores later work', () => {
    const navQuery = query()
    const controller = createNavigationController({
      destinations,
      query: navQuery,
      workerClips: new Set(['idle', 'walk'])
    })

    controller.dispose()
    controller.dispose()

    expect(controller.move(worker(), 'review')).toBe(false)
    expect(navQuery.dispose).toHaveBeenCalledOnce()
  })
})

describe('Recast NavigationQuery adapter', () => {
  it('reads an actual Recast path and disposes the underlying navmesh once', () => {
    const destroy = vi.fn()
    const navMesh = {
      computePath: vi.fn(() => ({
        getPoint: (index: number) =>
          [
            { x: 1, y: 0, z: 2 },
            { x: 9, y: 0, z: 2 }
          ][index],
        getPointCount: () => 2
      })),
      destroy
    }
    const query = createRecastNavigationQuery(navMesh, (x, y, z) => ({ x, y, z }))

    expect(query.computePath({ x: 1, y: 0, z: 2 }, { x: 9, y: 0, z: 2 })).toEqual([
      { x: 1, y: 0, z: 2 },
      { x: 9, y: 0, z: 2 }
    ])
    query.dispose?.()
    query.dispose?.()
    expect(destroy).toHaveBeenCalledOnce()
  })
})
