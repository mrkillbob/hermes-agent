import { describe, expect, it } from 'vitest'

import type { ModelManifestEntry, NavigationManifest, Vec3, WorldBounds } from '../model'

import { createManifestNavigationQuery, transformManifestPoint, worldBoundsFromModel } from './world-scene'

const model = {
  transform: {
    position: { x: 11, y: -3, z: 5 },
    rotation: { x: 0.37, y: -0.61, z: 0.82 },
    scale: { x: 2, y: 3, z: 4 }
  },
  bounds: {
    min: { x: -1, y: -2, z: -0.5 },
    max: { x: 4, y: 1, z: 3 }
  }
} as Pick<ModelManifestEntry, 'bounds' | 'transform'>

function expectVectorClose(actual: Vec3, expected: Vec3): void {
  expect(actual.x).toBeCloseTo(expected.x, 5)
  expect(actual.y).toBeCloseTo(expected.y, 5)
  expect(actual.z).toBeCloseTo(expected.z, 5)
}

function expectBoundsClose(actual: WorldBounds, expected: WorldBounds): void {
  expectVectorClose(actual.min, expected.min)
  expectVectorClose(actual.max, expected.max)
}

describe('manifest world transforms', () => {
  it('applies Babylon y-x-z rotation and non-uniform scale to a local camera anchor', () => {
    expectVectorClose(transformManifestPoint(model, { x: 3, y: -2, z: 1 }), {
      x: 15.75357037782669,
      y: -4.172778964042664,
      z: 13.001759648323059
    })
  })

  it('builds an occlusion world AABB from all eight rotated asymmetric local corners', () => {
    expectBoundsClose(worldBoundsFromModel(model), {
      min: { x: 1.5535336136817932, y: -12.51904046535492, z: 1.6066522002220154 },
      max: { x: 19.773607850074768, y: 5.084729433059692, z: 20.330265641212463 }
    })
  })
})

describe('manifest navigation query', () => {
  const navigation: NavigationManifest = {
    areas: ['walkable'],
    links: [
      { bidirectional: true, from: { x: 0, y: 0, z: 0 }, to: { x: 2, y: 0, z: 0 } },
      { bidirectional: false, from: { x: 2, y: 0, z: 0 }, to: { x: 4, y: 0, z: 0 } }
    ],
    meshUri: 'models/navigation.glb'
  }

  it('uses only declared navigation links and never invents a direct route through city geometry', () => {
    const query = createManifestNavigationQuery(navigation)

    expect(query.computePath({ x: 0, y: 0, z: 0 }, { x: 4, y: 0, z: 0 })).toEqual([
      { x: 0, y: 0, z: 0 },
      { x: 2, y: 0, z: 0 },
      { x: 4, y: 0, z: 0 }
    ])
    expect(query.computePath({ x: 4, y: 0, z: 0 }, { x: 0, y: 0, z: 0 })).toBeUndefined()
    expect(query.computePath({ x: 1, y: 0, z: 0 }, { x: 4, y: 0, z: 0 })).toBeUndefined()
  })
})
