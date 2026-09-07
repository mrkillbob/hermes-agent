import { describe, expect, it } from 'vitest'
import type { ProjectCompoundPlacement } from './adapters/kanban'
import {
  loadZoneLayout,
  mergeZoneLayout,
  retainedCompoundsFromZoneLayout,
  saveZoneLayout,
  ZONE_LAYOUT_STORAGE_KEY
} from './zone-layout'

function memoryStorage(initial?: string) {
  const values = new Map<string, string>(initial ? [[ZONE_LAYOUT_STORAGE_KEY, initial]] : [])
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    values
  }
}

const compound = (key: string, slotId: string): ProjectCompoundPlacement => ({
  connectionId: 'local',
  key,
  projectId: key.slice(key.indexOf('/') + 1),
  slotId,
  taskCount: 2,
  unplaced: false
})

describe('zone layout persistence', () => {
  it('ignores malformed storage without throwing or inventing a placement', () => {
    expect(loadZoneLayout(memoryStorage('{not-json'))).toEqual(new Map())
  })

  it('round-trips exact keys and bounded slot retention', () => {
    const storage = memoryStorage()
    const layout = mergeZoneLayout(new Map(), [compound('local/project-a', 'slot-a')], 42)
    saveZoneLayout(storage, layout)
    const loaded = loadZoneLayout(storage)
    expect(loaded.get('local/project-a')).toEqual({ key: 'local/project-a', slotId: 'slot-a', lastSeenAt: 42 })
    expect(retainedCompoundsFromZoneLayout(loaded)[0]?.slotId).toBe('slot-a')
  })

  it('does not replace a retained slot with an unplaced live result', () => {
    const prior = mergeZoneLayout(new Map(), [compound('local/project-a', 'slot-a')], 42)
    const next = mergeZoneLayout(prior, [{ ...compound('local/project-a', 'slot-b'), unplaced: true }], 99)
    expect(next.get('local/project-a')?.slotId).toBe('slot-a')
  })
})
