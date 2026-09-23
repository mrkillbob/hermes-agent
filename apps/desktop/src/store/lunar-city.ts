import type { WorldCondition, WorldEvent } from '@/app/lunar-city/world-events'

export type { WorldCondition, WorldEvent }

export interface WorldProjection {
  conditions: WorldCondition[]
  recentEvents: WorldEvent[]
  sourceError: string | null
  stale: boolean
  transitions: WorldEvent[]
}
