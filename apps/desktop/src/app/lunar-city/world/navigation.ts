import type { DestinationId, EntityKey, Vec3 } from '../model'

export interface NavigationQuery {
  computePath(from: Vec3, to: Vec3): readonly Vec3[] | undefined
  dispose?(): void
}

export interface RecastPathLike {
  __destroy__?(): void
  delete?(): void
  destroy?(): void
  getPoint(index: number): Vec3 | undefined
  getPointCount(): number
}

export interface RecastNavMeshLike {
  computePath(from: unknown, to: unknown): RecastPathLike
  destroy?(): void
}

interface RecastWrapperLike {
  __destroy__?(): void
  delete?(): void
  destroy?(): void
}

export function disposeRecastWrapper(value: RecastWrapperLike | undefined): void {
  if (value?.destroy) {
    value.destroy()
  } else if (value?.delete) {
    value.delete()
  } else {
    value?.__destroy__?.()
  }
}

/** Adapts Recast's route-local navmesh to the narrow fail-closed query seam. */
export function createRecastNavigationQuery(
  navMesh: RecastNavMeshLike,
  vector: (x: number, y: number, z: number) => unknown
): NavigationQuery {
  let disposed = false

  return {
    computePath(from, to) {
      if (disposed) {
        return undefined
      }

      let start: RecastWrapperLike | undefined
      let end: RecastWrapperLike | undefined
      let path: RecastPathLike | undefined

      try {
        start = vector(from.x, from.y, from.z) as RecastWrapperLike
        end = vector(to.x, to.y, to.z) as RecastWrapperLike
        path = navMesh.computePath(start, end)
      } catch {
        disposeRecastWrapper(start)
        disposeRecastWrapper(end)

        return undefined
      }

      disposeRecastWrapper(start)
      disposeRecastWrapper(end)

      const points: Vec3[] = []

      try {
        for (let index = 0; index < path.getPointCount(); index += 1) {
          // Recast's NavPath owns its getPoint() vector views.  They are not
          // separately allocated by this adapter; destroying a borrowed view
          // corrupts the real WASM heap.  Copy coordinates, then release the
          // owning NavPath in the finally below.
          const point = path.getPoint(index)

          if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y) || !Number.isFinite(point.z)) {
            return undefined
          }

          points.push({ x: point.x, y: point.y, z: point.z })
        }
      } finally {
        disposeRecastWrapper(path)
      }

      return points.length > 0 ? points : undefined
    },
    dispose() {
      if (!disposed) {
        disposed = true
        navMesh.destroy?.()
      }
    }
  }
}

export interface NavigationEntity {
  animation: string
  key: EntityKey
  position: Vec3
}

export interface NavigationControllerOptions {
  destinations: Readonly<Record<string, Vec3>>
  diagnostic?: (message: string) => void
  query: NavigationQuery
  reducedMotion?: boolean
  staticPose?: string
  speedUnitsPerSecond?: number
  workerClips: ReadonlySet<string>
}

interface ActivePath {
  arrivalAnimation: string
  destination: DestinationId
  entity: NavigationEntity
  points: readonly Vec3[]
  pointIndex: number
  signature: string
}

function copied(point: Vec3): Vec3 {
  return { x: point.x, y: point.y, z: point.z }
}

function distance(left: Vec3, right: Vec3): number {
  return Math.hypot(right.x - left.x, right.y - left.y, right.z - left.z)
}

function moveToward(position: Vec3, target: Vec3, maximumDistance: number): number {
  const length = distance(position, target)

  if (length === 0) {
    return maximumDistance
  }

  const traveled = Math.min(length, maximumDistance)
  const ratio = traveled / length
  position.x += (target.x - position.x) * ratio
  position.y += (target.y - position.y) * ratio
  position.z += (target.z - position.z) * ratio

  return maximumDistance - traveled
}

function pointSignature(point: Vec3): string {
  return `${point.x},${point.y},${point.z}`
}

/**
 * Presentation-only navigation.  It never invents a route: an unavailable
 * query, destination, or path leaves the worker in an unavailable pose.
 */
export function createNavigationController(options: NavigationControllerOptions) {
  const active = new Map<EntityKey, ActivePath>()
  const lastSignature = new Map<EntityKey, string>()
  const diagnostics = new Set<string>()
  const staticPose = options.staticPose ?? 'idle'
  const speedUnitsPerSecond = options.speedUnitsPerSecond ?? 4
  let walkabilityRevision = 0
  let disposed = false
  let reducedMotion = options.reducedMotion ?? false

  const diagnoseOnce = (code: string): void => {
    if (!diagnostics.has(code)) {
      diagnostics.add(code)
      options.diagnostic?.(code)
    }
  }

  const failClosed = (entity: NavigationEntity, code: string): false => {
    active.delete(entity.key)
    entity.animation = 'unavailable'
    diagnoseOnce(code)

    return false
  }

  return {
    cancel(key: EntityKey): void {
      active.delete(key)
      lastSignature.delete(key)
    },
    dispose(): void {
      if (disposed) {
        return
      }

      disposed = true
      active.clear()
      options.query.dispose?.()
    },
    isMoving(key: EntityKey): boolean {
      return active.has(key)
    },
    move(entity: NavigationEntity, destination: DestinationId, arrivalAnimation = entity.animation): boolean {
      if (disposed || destination === 'unknown' || destination === 'unavailable') {
        return failClosed(entity, `navigation unavailable for ${destination}`)
      }

      const target = options.destinations[destination]

      if (!target) {
        return failClosed(entity, `navigation destination missing: ${destination}`)
      }

      if (reducedMotion) {
        active.delete(entity.key)
        entity.position = copied(target)
        entity.animation = options.workerClips.has(staticPose) ? staticPose : 'unavailable'

        return entity.animation !== 'unavailable'
      }

      if (!options.workerClips.has('walk')) {
        return failClosed(entity, 'navigation worker walk clip unavailable')
      }

      const signature = `${pointSignature(entity.position)}>${pointSignature(target)}@${walkabilityRevision}`

      if (lastSignature.get(entity.key) === signature && active.has(entity.key)) {
        return true
      }

      let points: readonly Vec3[] | undefined

      try {
        points = options.query.computePath(copied(entity.position), copied(target))
      } catch {
        return failClosed(entity, `navigation query failed: ${destination}`)
      }

      if (!points || points.length === 0) {
        return failClosed(entity, `navigation path unavailable: ${destination}`)
      }

      active.set(entity.key, {
        arrivalAnimation,
        destination,
        entity,
        points: points.map(copied),
        pointIndex: 0,
        signature
      })
      lastSignature.set(entity.key, signature)
      entity.animation = 'walk'

      return true
    },
    setWalkabilityRevision(revision: number): void {
      if (Number.isFinite(revision) && revision >= 0 && revision !== walkabilityRevision) {
        walkabilityRevision = revision

        for (const entry of [...active.values()]) {
          this.move(entry.entity, entry.destination, entry.arrivalAnimation)
        }
      }
    },
    setReducedMotion(reduced: boolean): void {
      if (disposed || reducedMotion === reduced) {
        return
      }

      reducedMotion = reduced

      if (!reduced) {
        return
      }

      for (const entry of [...active.values()]) {
        const target = options.destinations[entry.destination]

        if (target) {
          entry.entity.position = copied(target)
          entry.entity.animation = options.workerClips.has(staticPose) ? staticPose : 'unavailable'
        }

        active.delete(entry.entity.key)
      }
    },
    tick(elapsedMs: number): boolean {
      if (disposed || elapsedMs <= 0) {
        return active.size > 0
      }

      const initialDistance = (elapsedMs / 1_000) * speedUnitsPerSecond

      for (const entry of [...active.values()]) {
        let remaining = initialDistance

        while (remaining > 0 && entry.pointIndex < entry.points.length) {
          const next = entry.points[entry.pointIndex]!
          remaining = moveToward(entry.entity.position, next, remaining)

          if (distance(entry.entity.position, next) === 0) {
            entry.pointIndex += 1
          }
        }

        if (entry.pointIndex >= entry.points.length) {
          const destination = options.destinations[entry.destination]

          if (destination) {
            entry.entity.position = copied(destination)
          }

          entry.entity.animation = options.workerClips.has(entry.arrivalAnimation)
            ? entry.arrivalAnimation
            : options.workerClips.has(staticPose)
              ? staticPose
              : 'unavailable'
          active.delete(entry.entity.key)
        }
      }

      return active.size > 0
    },
    updateArrivalAnimation(key: EntityKey, animation: string): void {
      const entry = active.get(key)

      if (entry) {
        entry.arrivalAnimation = animation
      }
    }
  }
}
