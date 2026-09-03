import type { ProjectCompoundPlacement } from './adapters/kanban'

export const ZONE_LAYOUT_STORAGE_KEY = 'hermes:lunar-city:zone-layout:v1'
export const MAX_RETAINED_ZONE_ROWS = 80

export interface ZoneLayoutEntry {
  key: string
  lastSeenAt: number
  slotId: string
}

export type ZoneLayout = ReadonlyMap<string, ZoneLayoutEntry>

interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

function storageOrUndefined(storage?: StorageLike): StorageLike | undefined {
  if (storage) {return storage}

  try {
    return typeof globalThis.localStorage === 'undefined' ? undefined : globalThis.localStorage
  } catch {
    return undefined
  }
}

function validEntry(value: unknown): value is ZoneLayoutEntry {
  if (!value || typeof value !== 'object') {return false}
  const entry = value as Partial<ZoneLayoutEntry>

  return (
    typeof entry.key === 'string' &&
    entry.key.length > 0 &&
    typeof entry.slotId === 'string' &&
    entry.slotId.length > 0 &&
    Number.isFinite(entry.lastSeenAt)
  )
}

export function loadZoneLayout(storage?: StorageLike): ZoneLayout {
  const source = storageOrUndefined(storage)

  if (!source) {return new Map()}

  try {
    const raw = source.getItem(ZONE_LAYOUT_STORAGE_KEY)

    if (!raw) {return new Map()}
    const parsed: unknown = JSON.parse(raw)

    if (!parsed || typeof parsed !== 'object' || !Array.isArray((parsed as { entries?: unknown }).entries))
      {return new Map()}

    const entries = (parsed as { entries: unknown[] }).entries.filter(validEntry).slice(0, MAX_RETAINED_ZONE_ROWS)

    return new Map(entries.map(entry => [entry.key, { ...entry }]))
  } catch {
    return new Map()
  }
}

export function saveZoneLayout(storage: StorageLike | undefined, layout: ZoneLayout): void {
  const target = storageOrUndefined(storage)

  if (!target) {return}

  try {
    const entries = [...layout.values()]
      .filter(validEntry)
      .sort((left, right) => right.lastSeenAt - left.lastSeenAt || left.key.localeCompare(right.key))
      .slice(0, MAX_RETAINED_ZONE_ROWS)

    target.setItem(ZONE_LAYOUT_STORAGE_KEY, JSON.stringify({ version: 1, entries }))
  } catch {
    // Visual persistence is best effort and must never affect live reconciliation.
  }
}

export function retainedCompoundsFromZoneLayout(layout: ZoneLayout): readonly ProjectCompoundPlacement[] {
  return [...layout.values()].flatMap(entry => {
    const separator = entry.key.indexOf('/')

    if (separator <= 0 || separator === entry.key.length - 1) {return []}

    return [
      {
        connectionId: entry.key.slice(0, separator),
        key: entry.key,
        projectId: entry.key.slice(separator + 1),
        slotId: entry.slotId,
        taskCount: 1,
        unplaced: false
      }
    ]
  })
}

export function mergeZoneLayout(
  previous: ZoneLayout,
  compounds: readonly ProjectCompoundPlacement[],
  lastSeenAt: number
): ZoneLayout {
  const next = new Map(previous)

  for (const compound of compounds) {
    if (compound.unplaced || !compound.slotId) {continue}
    next.set(compound.key, { key: compound.key, lastSeenAt, slotId: compound.slotId })
  }

  return next
}

export function zoneLayoutsEqual(left: ZoneLayout, right: ZoneLayout): boolean {
  if (left.size !== right.size) {return false}

  return [...left].every(([key, entry]) => {
    const other = right.get(key)

    return other?.slotId === entry.slotId && other.lastSeenAt === entry.lastSeenAt
  })
}
