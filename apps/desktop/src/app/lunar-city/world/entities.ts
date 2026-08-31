import type { AuthorityState, DestinationId, EntityKey, LunarCitySnapshot, LunarEntity, Vec3 } from '../model'

export interface EntityVisual {
  dispose?(): void
  setAnimation?(clip: string): void
  setLod?(lodIndex: number): void
  setPosition?(position: Vec3): void
  setStaticPose?(pose: string): void
}

export interface InstancedEntityMember {
  animation: string
  key: EntityKey
  position: Vec3
  variant: string | undefined
}

export interface InstancedEntityGroup {
  dispose?(): void
  sync(members: readonly InstancedEntityMember[]): void
}

export interface EntityPresentationFactory {
  createAnimated(entity: LunarEntity, variant: string | undefined): EntityVisual
  createInstancedGroup(groupKey: string): InstancedEntityGroup
}

export interface LodNode {
  setEnabled?(enabled: boolean): void
}

export interface LodEntry {
  distance: number
  node: LodNode
}

export interface LodSelection {
  distance: number
  lodAdvance: number
  selected: boolean
}

export function selectLodIndex(entries: readonly Pick<LodEntry, 'distance'>[], selection: LodSelection): number {
  const ordered = [...entries].sort((left, right) => left.distance - right.distance)

  if (ordered.length === 0) {
    return -1
  }

  let index = 0

  if (!selection.selected) {
    for (let candidate = 1; candidate < ordered.length; candidate += 1) {
      if (selection.distance >= ordered[candidate]!.distance) {
        index = candidate
      }
    }

    index = Math.min(ordered.length - 1, index + Math.max(0, selection.lodAdvance))
  }

  return index
}

export interface EntityRegistryOptions {
  diagnostic?: (message: string) => void
  factory: EntityPresentationFactory
  focusAnchors?: Map<EntityKey, () => Vec3 | undefined>
  workerClips: ReadonlySet<string>
}

interface RetainedEntity {
  animation: string
  authority: AuthorityState
  entity: LunarEntity
  lodIndex: number
  moving: boolean
  position: Vec3
  visual: EntityVisual | undefined
}

export interface AggregatePopulation {
  animations: Readonly<Record<string, number>>
  total: number
}

export interface NavigationPresentationEntity {
  animation: string
  key: EntityKey
  position: Vec3
}

function copied(position: Vec3 | undefined): Vec3 {
  return position ? { x: position.x, y: position.y, z: position.z } : { x: 0, y: 0, z: 0 }
}

function groupKey(record: RetainedEntity): string {
  const base = record.entity.variant
    ? `worker:${record.entity.variant}:${record.animation}`
    : `worker:${record.animation}`

  return record.lodIndex === 0 ? base : `${base}:lod:${record.lodIndex}`
}

function isIndividuallyAnimated(record: RetainedEntity, selection: EntityKey | undefined): boolean {
  return record.entity.key === selection || record.moving || record.animation === 'walk'
}

function immutableAggregate(total: number, animations: Map<string, number>): AggregatePopulation {
  return Object.freeze({ animations: Object.freeze(Object.fromEntries(animations)), total })
}

/**
 * Enables one and only one declared LOD subtree.  Selection deliberately pins
 * to the near representation, so an authoritative selected worker cannot be
 * removed by a quality downgrade.
 */
export function applyLodSelection(entries: readonly LodEntry[], selection: LodSelection): number {
  const ordered = [...entries].sort((left, right) => left.distance - right.distance)
  const index = selectLodIndex(ordered, selection)

  for (const [candidate, entry] of ordered.entries()) {
    entry.node.setEnabled?.(candidate === index)
  }

  return index
}

/**
 * Retains presentation objects by opaque identity.  Snapshot objects are read
 * only: all mutable position, animation, and visual resources live here.
 */
export function createEntityRegistry(options: EntityRegistryOptions) {
  const records = new Map<EntityKey, RetainedEntity>()
  const groups = new Map<string, InstancedEntityGroup>()
  const aggregates = new Map<DestinationId, AggregatePopulation>()
  const diagnostics = new Set<string>()
  const focusAnchors = options.focusAnchors
  let selected: EntityKey | undefined
  let disposed = false

  const diagnoseOnce = (message: string): void => {
    if (!diagnostics.has(message)) {
      diagnostics.add(message)
      options.diagnostic?.(message)
    }
  }

  const release = (record: RetainedEntity): void => {
    record.visual?.dispose?.()
    record.visual = undefined
    focusAnchors?.delete(record.entity.key)
  }

  const publishAnchor = (record: RetainedEntity): void => {
    focusAnchors?.set(record.entity.key, () =>
      record.authority === 'authoritative' ? copied(record.position) : undefined
    )
  }

  const syncPresentation = (): void => {
    if (disposed) {
      return
    }

    const nextGroups = new Map<string, InstancedEntityMember[]>()
    const nextAggregates = new Map<DestinationId, { animations: Map<string, number>; total: number }>()

    for (const record of records.values()) {
      const aggregate = nextAggregates.get(record.entity.destination) ?? {
        animations: new Map<string, number>(),
        total: 0
      }

      aggregate.total += 1
      aggregate.animations.set(record.animation, (aggregate.animations.get(record.animation) ?? 0) + 1)
      nextAggregates.set(record.entity.destination, aggregate)

      if (isIndividuallyAnimated(record, selected)) {
        if (!record.visual) {
          record.visual = options.factory.createAnimated(record.entity, record.entity.variant)
        }

        record.visual.setPosition?.(copied(record.position))
        record.visual.setLod?.(record.lodIndex)

        if (options.workerClips.has(record.animation)) {
          record.visual.setAnimation?.(record.animation)
        } else {
          record.visual.setStaticPose?.('idle')
          diagnoseOnce(`worker animation clip unavailable: ${record.animation}`)
        }

        continue
      }

      record.visual?.dispose?.()
      record.visual = undefined
      const key = groupKey(record)
      const members = nextGroups.get(key) ?? []
      members.push({
        animation: record.animation,
        key: record.entity.key,
        position: copied(record.position),
        variant: record.entity.variant
      })
      nextGroups.set(key, members)
    }

    for (const [key, group] of groups) {
      if (!nextGroups.has(key)) {
        group.dispose?.()
        groups.delete(key)
      }
    }

    for (const [key, members] of nextGroups) {
      const group = groups.get(key) ?? options.factory.createInstancedGroup(key)
      groups.set(key, group)
      group.sync(members)
    }

    aggregates.clear()

    for (const [destination, aggregate] of nextAggregates) {
      aggregates.set(destination, immutableAggregate(aggregate.total, aggregate.animations))
    }
  }

  return {
    aggregate(destination: DestinationId): AggregatePopulation | undefined {
      return aggregates.get(destination)
    },
    applyLodPolicy(resolveIndex: (key: EntityKey, position: Vec3, isSelected: boolean) => number): void {
      if (disposed) {
        return
      }

      let changed = false

      for (const record of records.values()) {
        const nextIndex = Math.max(
          0,
          Math.floor(resolveIndex(record.entity.key, record.position, record.entity.key === selected))
        )

        if (record.lodIndex !== nextIndex) {
          record.lodIndex = nextIndex
          changed = true
        }
      }

      if (changed) {
        syncPresentation()
      }
    },
    dispose(): void {
      if (disposed) {
        return
      }

      disposed = true

      for (const record of records.values()) {
        release(record)
      }

      for (const group of groups.values()) {
        group.dispose?.()
      }

      records.clear()
      groups.clear()
      aggregates.clear()
    },
    entity(key: EntityKey): Readonly<RetainedEntity> | undefined {
      return records.get(key)
    },
    instancedGroup(key: string): { count: number } | undefined {
      const group = groups.get(key)

      if (!group) {
        return undefined
      }

      let count = 0
      const originalSync = group.sync

      // Group implementations are deliberately opaque.  Registry ownership
      // retains the truthful count instead of reading renderer-private state.
      for (const record of records.values()) {
        if (!isIndividuallyAnimated(record, selected) && groupKey(record) === key) {
          count += 1
        }
      }

      void originalSync

      return { count }
    },
    navigationEntity(key: EntityKey): NavigationPresentationEntity | undefined {
      const record = records.get(key)

      if (!record) {
        return undefined
      }

      return {
        get animation() {
          return record.animation
        },
        set animation(value: string) {
          record.animation = value
        },
        get key() {
          return record.entity.key
        },
        get position() {
          return record.position
        },
        set position(value: Vec3) {
          record.position = copied(value)
        }
      }
    },
    reconcile(snapshot: LunarCitySnapshot): void {
      if (disposed) {
        return
      }

      const incomingKeys = new Set(snapshot.entities.keys())

      for (const [key, record] of records) {
        if (!incomingKeys.has(key)) {
          release(record)
          records.delete(key)
        }
      }

      for (const entity of snapshot.entities.values()) {
        const previous = records.get(entity.key)

        const record: RetainedEntity = previous ?? {
          animation: entity.animation,
          authority: entity.authority,
          entity,
          lodIndex: 0,
          moving: false,
          position: copied(entity.position),
          visual: undefined
        }

        record.entity = entity
        record.animation = entity.animation
        record.authority = entity.authority
        record.position = copied(entity.position ?? record.position)
        record.moving ||= entity.animation === 'walk'
        records.set(entity.key, record)
        publishAnchor(record)
      }

      if (selected && !records.has(selected)) {
        selected = undefined
      }

      syncPresentation()
    },
    setMoving(key: EntityKey, moving: boolean): void {
      const record = records.get(key)

      if (!record || disposed) {
        return
      }

      record.moving = moving
      syncPresentation()
    },
    setPosition(key: EntityKey, position: Vec3): void {
      const record = records.get(key)

      if (!record || disposed) {
        return
      }

      record.position = copied(position)
      record.visual?.setPosition?.(copied(record.position))
    },
    setSelection(key: EntityKey | undefined): void {
      selected = key
      syncPresentation()
    },
    syncMotion(): void {
      if (disposed) {
        return
      }

      for (const record of records.values()) {
        record.visual?.setPosition?.(copied(record.position))

        if (record.visual && options.workerClips.has(record.animation)) {
          record.visual.setAnimation?.(record.animation)
        }
      }
    }
  }
}
